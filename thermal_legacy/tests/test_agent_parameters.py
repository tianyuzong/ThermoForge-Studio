import copy
import json

import pytest

import thermal_legacy.agent as agent
import thermal_legacy.agent_skill as agent_skill
from thermal_legacy.agent_parameters import LABELS, parameter_contract
from thermal_legacy.schemas import Simulation
from test_agent_skill import blocks


def current(blocks):
    cfg,_=blocks
    cfg['heat_sources']=[dict(power_W=20,faces=agent._surface_selections('blocks')['top']['faces'])]
    return Simulation.model_validate(cfg).model_dump()


@pytest.mark.parametrize('applied', [0, 2])
@pytest.mark.parametrize('prompt', [
    '第一个热源的温控回差设为2摄氏度',
    '第一个热源 thermostat：target_C=60、hysteresis_C=2、min_power_W=0、max_power_W=5',
    '第一个热源温控设置为 {"target_C":60,"hysteresis_C":2,"min_power_W":0,"max_power_W":5}',
])
def test_followup_repairs_missing_thermostat_hysteresis(blocks, prompt, applied):
    cfg=current(blocks)
    cfg['heat_sources'][0]['thermostat']=dict(target_C=60,min_power_W=0,max_power_W=5)
    other=copy.deepcopy(cfg)
    context=dict(history=[dict(role='user',content='启用温控，目标温度60摄氏度，回差2摄氏度，最小功率0W，最大功率5W'),
                          dict(role='assistant',content='hysteresis_C: Field required')],applied_message_count=applied)
    result=agent.plan_request('blocks',prompt,cfg,context)
    assert result['ok'],result['questions']
    other['heat_sources'][0]['thermostat']['hysteresis_C']=2
    assert result['config']==other


def test_initial_thermostat_accepts_huicha(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','第一个热源启用温控，目标温度60摄氏度，回差2摄氏度，最小功率0W，最大功率5W',cfg)
    assert result['ok'],result['questions']
    assert result['config']['heat_sources'][0]['thermostat']==dict(target_C=60,hysteresis_C=2,min_power_W=0,max_power_W=5)
    assert result['config']['heat_sources'][0]['power_W']==20


def test_contract_exposes_every_field_including_empty_optional_structures():
    schema=Simulation.model_json_schema()
    contract=parameter_contract()
    for name,model in {'Simulation':schema,**schema['$defs']}.items():
        expected=set(model['properties'])
        assert set(LABELS[name])==expected
        if 'faces' in expected: expected.remove('faces');expected.add('surface_selection')
        assert set(contract[name]['fields'])==expected
    assert contract['Thermostat']['fields']['hysteresis_C']['exclusiveMinimum']==0
    assert contract['Simulation']['fields']['analysis_mode']['enum']==['transient','steady']


def test_material_properties_phase_change_and_units(blocks):
    cfg=current(blocks)
    prompt=('基础材料使用自定义材料。导热系数180 W/(m·K)，密度2.7 g/cm³，比热容0.9 kJ/(kg·K)，线膨胀系数23 ppm/K。'
            '基础材料启用相变，起熔温度40°C，相变温区宽度5K，潜热200kJ/kg。')
    result=agent.plan_request('blocks',prompt,cfg)
    assert result['ok'],result['questions']
    m=result['config']['base_material']
    assert (m['k'],m['rho'],m['cp'])==(180,2700,900)
    assert m['thermal_expansion_CTE_per_K']==pytest.approx(23e-6)
    assert m['phase_change']==dict(melting_C=40,mushy_C=5,latent_J_kg=200000)
    assert result['config']['heat_sources']==cfg['heat_sources']


def test_global_numerical_boundary_and_coupling_fields(blocks):
    cfg=current(blocks)
    prompt=('算例名称“综合校验”。采用稳态，后来改为瞬态。初始温度293.15K，全局环境温度25°C。'
            '默认换热系数8，受热面参与默认对流。全局辐射开启，全局发射率0.7，辐射环境温度30°C。'
            '空气间隙耦合开启，空气间隙导热系数0.03 W/(m·K)，最大空气间隙20mm，接触热阻2e-4 m²K/W。'
            '仿真总时长2分钟，时间步长0.5秒，每2秒保存，网格尺寸0.2cm。')
    result=agent.plan_request('blocks',prompt,cfg)
    assert result['ok'],result['questions']
    c=result['config']
    expected=dict(name='综合校验',analysis_mode='transient',initial_C=20,ambient_C=25,default_h=8,heat_convection=True,
        radiation_enabled=True,emissivity=.7,radiation_ambient_C=30,air_gap_enabled=True,air_gap_k_W_mK=.03,
        air_gap_max_m=.02,contact_resistance_m2K_W=.0002,duration_s=120,dt_s=.5,save_s=2,mesh_size_m=.002)
    assert {k:c[k] for k in expected}==expected
    assert c['base_material']==cfg['base_material'] and c['heat_sources']==cfg['heat_sources']


def test_local_boundaries_do_not_overwrite_global_values(blocks):
    cfg=current(blocks)
    cfg.update(default_h=8,ambient_C=25,radiation_enabled=True,emissivity=.7,radiation_ambient_C=30)
    result=agent.plan_request('blocks','底部对流换热系数30，局部环境温度20°C，局部辐射关闭，局部发射率0.6。右侧对流换热系数40，局部环境温度15°C，局部辐射开启，局部发射率0.9。',cfg)
    assert result['ok'],result['questions']
    c=result['config']
    assert all(c[k]==cfg[k] for k in ('default_h','ambient_C','radiation_enabled','emissivity','radiation_ambient_C'))
    assert [(r['h'],r['ambient_C'],r['radiation'],r['emissivity']) for r in c['cooling']]==[(30,20,False,.6),(40,15,True,.9)]
    selections=agent._surface_selections('blocks')
    assert c['cooling'][0]['faces']==selections['bottom']['faces'] and c['cooling'][1]['faces']==selections['right']['faces']
    assert c['heat_sources']==cfg['heat_sources']


def test_component_property_changes_preserve_other_materials(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','组件1材料设为铜，导热系数300，比热容400。组件2材料设为铝，密度2800。基础材料保持不变。',cfg)
    assert result['ok'],result['questions']
    assert result['config']['base_material']==cfg['base_material']
    a,b=result['config']['component_materials']
    assert (a['material']['k'],a['material']['cp'],a['material']['rho'])==(300,400,8910)
    assert b['material']['rho']==2800 and b['material']['k']==205


def test_profile_and_thermostat_are_distinct_from_nominal_power(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','第一个热源使用功率曲线0秒:10W、60秒:30W、120秒:0W。第一个热源启用温控，目标温度60°C，滞回2K，最小功率0W，最大功率40W。',cfg)
    assert result['ok'],result['questions']
    h=result['config']['heat_sources'][0]
    assert h['power_W']==20 and h['faces']==cfg['heat_sources'][0]['faces']
    assert h['power_profile']==[dict(time_s=0,power_W=10),dict(time_s=60,power_W=30),dict(time_s=120,power_W=0)]
    assert h['thermostat']==dict(target_C=60,hysteresis_C=2,min_power_W=0,max_power_W=40)


def test_material_region_can_be_compiled_with_all_properties(blocks):
    cfg=current(blocks)
    prepared=agent_skill.prepare(agent,'blocks','增加一个明确范围的材料区域',cfg)
    region=dict(name='左块材料区',min_m=[-.03,-.01,-.01],max_m=[-.01,.01,.01],material=dict(name='自定义',k=12,rho=1000,cp=1500,thermal_expansion_CTE_per_K=1e-5,phase_change=dict(melting_C=40,mushy_C=5,latent_J_kg=200000)))
    review=dict(patch=[dict(path='/regions',value_json=json.dumps([region]))],questions=[])
    c,q=agent_skill.compile_review(agent,prepared,json.dumps(review))
    from thermal_legacy.schemas import Region
    assert not q and c['regions']==[Region.model_validate(region).model_dump()] and c['heat_sources']==cfg['heat_sources']


def test_changes_show_optional_fields_and_their_removal(blocks):
    cfg=current(blocks)
    updated=copy.deepcopy(cfg)
    updated['base_material']['phase_change']=dict(melting_C=40,mushy_C=5,latent_J_kg=200000)
    updated['base_material']['thermal_expansion_CTE_per_K']=1e-5
    updated['heat_sources'][0]['radius_m']=.003
    updated['heat_sources'][0]['thermostat']=dict(target_C=60,hysteresis_C=2,min_power_W=0,max_power_W=40)
    text=' '.join(agent._codex_changes(updated,cfg))
    assert all(term in text for term in ['线膨胀系数','起熔温度','相变温区宽度','潜热','作用半径','目标温度','温控滞回','最大功率'])
    removed=' '.join(agent._codex_changes(cfg,updated))
    assert '相变：已清除' in removed and '温控：已清除' in removed


def test_last_corrections_win_across_categories(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','基础材料导热系数180。全局环境温度25°C。时间步长1秒。后来基础材料导热系数改为160，全局环境温度改成28°C，时间步长改为2秒。',cfg)
    assert result['ok'],result['questions']
    c=result['config'];assert c['base_material']['k']==160 and c['ambient_C']==28 and c['dt_s']==2


def test_partial_boundary_disable_and_delete_preserve_other_zones(blocks):
    cfg=current(blocks)
    cfg=agent.plan_request('blocks','底部对流换热系数30。右侧对流换热系数40。',cfg)['config']
    result=agent.plan_request('blocks','关闭底部对流，其他条件保持不变',cfg)
    assert result['ok'] and result['config']['cooling'][0]['h']==0
    assert result['config']['cooling'][1]==cfg['cooling'][1]
    removed=agent.plan_request('blocks','删除底部散热区',cfg)
    assert removed['ok'] and removed['config']['cooling']==cfg['cooling'][1:]


def test_component_override_removal_is_scoped(blocks):
    cfg=current(blocks)
    cfg=agent.plan_request('blocks','组件1材料设为铜。组件2材料设为铝。',cfg)['config']
    result=agent.plan_request('blocks','清除组件2材料覆盖',cfg)
    assert result['ok'] and result['config']['component_materials']==cfg['component_materials'][:1]


def test_contact_resistance_unit_mismatch_is_not_silently_accepted(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','接触热阻设为2 K/W',cfg)
    assert not result['ok'] and any('接触面积' in q for q in result['questions'])


def test_unlocated_local_boundary_does_not_modify_global_value(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','局部环境温度改为20°C',cfg)
    assert not result['ok'] and result['config']['ambient_C']==cfg['ambient_C']


def test_region_description_cannot_change_base_material_in_local_mode(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','增加一个材料区域，区域材料导热系数12，密度1000，比热1500',cfg)
    assert not result['ok'] and result['config']['base_material']==cfg['base_material']


def test_invalid_phase_value_is_reported_with_field_path(blocks):
    cfg=current(blocks)
    result=agent.plan_request('blocks','基础材料启用相变，起熔温度40°C，相变温区宽度5K，潜热-200J/kg',cfg)
    assert not result['ok'] and any('latent_J_kg' in q for q in result['questions'])


def test_kelvin_power_units_and_scoped_control_disable(blocks):
    cfg=current(blocks)
    cfg['heat_sources'].append(copy.deepcopy(cfg['heat_sources'][0]))
    cfg['heat_sources'][1]['thermostat']=dict(target_C=50,hysteresis_C=1,min_power_W=0,max_power_W=50)
    result=agent.plan_request('blocks','第一个热源启用温控，目标温度333.15K，滞回2K，最小功率0W，最大功率0.04kW。第二个热源关闭温控。',cfg)
    assert result['ok'],result['questions']
    assert result['config']['heat_sources'][0]['thermostat']==dict(target_C=60,hysteresis_C=2,min_power_W=0,max_power_W=40)
    assert result['config']['heat_sources'][1]['thermostat'] is None


@pytest.mark.parametrize('prompt,word',[('基础材料启用相变','潜热'),('第一个热源启用温控','最大功率'),('第一个热源启用功率曲线','采样点'),('底部施加80°C固定温度边界','固定温度边界'),('耦合CFD流场，流速1m/s','CFD')])
def test_missing_or_unsupported_physics_requires_clarification(blocks,prompt,word):
    result=agent.plan_request('blocks',prompt,current(blocks))
    assert not result['ok'] and any(word in q for q in result['questions'])


def test_combined_clear_and_from_to_corrections_across_subsystems(blocks):
    cfg=current(blocks)
    cfg=agent.plan_request('blocks','底部对流换热系数30。组件1材料设为铝。',cfg)['config']
    prompt=('清空已有热源、局部散热区、组件材料覆盖和材料区域。'
            '第一个热源顶部20W，第二个热源右侧8W。'
            '基础材料导热系数从391改为160 W/(m·K)，全局环境温度从25改为27°C。'
            '第一个热源使用功率曲线：0秒20W，60秒35W，120秒0W。')
    result=agent.plan_request('blocks',prompt,cfg)
    assert result['ok'],result['questions']
    c=result['config'];assert len(c['heat_sources'])==2
    assert c['base_material']['k']==160 and c['ambient_C']==27
    assert c['cooling']==[] and c['component_materials']==[] and c['regions']==[]
    assert c['heat_sources'][0]['power_profile']==[dict(time_s=0,power_W=20),dict(time_s=60,power_W=35),dict(time_s=120,power_W=0)]
    assert c['heat_sources'][1]['power_W']==8


def test_preserve_control_request_does_not_disable_it(blocks):
    cfg=current(blocks)
    cfg['heat_sources'][0]['thermostat']=dict(target_C=50,hysteresis_C=1,min_power_W=0,max_power_W=30)
    result=agent.plan_request('blocks','不要改第一个热源的温控，其他参数保持不变',cfg)
    assert result['ok'] and result['config']['heat_sources']==cfg['heat_sources']
