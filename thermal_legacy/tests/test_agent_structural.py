import copy,json
import pytest
import thermal_legacy.agent as agent; import thermal_legacy.agent_skill as agent_skill
from thermal_legacy.schemas import Simulation, Material
from test_agent_skill import blocks


def test_conversation_sets_mechanics_and_environment_without_heat(blocks):
    cfg,_=blocks
    prompt=('纯环境温变工况，无热源。基础材料弹性模量200GPa，泊松比0.3，屈服强度250MPa，线膨胀系数12ppm/K。'
            '初始温度20摄氏度，环境温度负40摄氏度。默认换热系数10。'
            '启用热应力分析，底面固定xyz方向，无应力参考温度20摄氏度。'
            '允许最低温度-45摄氏度，允许最高温度85摄氏度，允许最大位移0.1mm，强度安全系数1.5。')
    r=agent.plan_request('blocks',prompt,cfg)
    assert r['ok'],r['questions']
    c=r['config'];m=c['base_material']
    assert m['young_modulus_Pa']==200e9 and m['poisson_ratio']==.3 and m['yield_strength_Pa']==250e6
    assert c['environment_only'] and c['heat_sources']==[] and c['ambient_C']==-40
    assert c['structural']['mode']=='constrained' and c['structural']['reference_C']==20
    assert c['structural']['supports'][0]['faces']==agent._surface_selections('blocks')['bottom']['faces']
    assert c['design_limits']['maximum_displacement_m']==.0001
    nxt=agent.plan_request('blocks','改为自由热弹性状态，环境温度85摄氏度',c)
    assert nxt['ok'],nxt['questions']
    assert nxt['config']['structural']['supports']==[] and nxt['config']['ambient_C']==85


def test_support_does_not_move_existing_heat_source(blocks):
    cfg,_=blocks
    cfg=agent.plan_request('blocks','顶部20W热源',cfg)['config']
    r=agent.plan_request('blocks','基础材料弹性模量200GPa，泊松比0.3。启用热应力分析，底面固定，无应力参考温度25摄氏度',cfg)
    assert r['ok'],r['questions']
    assert r['config']['heat_sources']==cfg['heat_sources']


def test_missing_modulus_and_support_not_guessed(blocks):
    cfg,_=blocks
    r=agent.plan_request('blocks','纯环境温变工况。启用热应力分析',cfg)
    assert not r['ok']
    assert any('弹性模量' in q for q in r['questions'])
    assert any('自由状态' in q for q in r['questions'])


def test_codex_support_selector_roundtrip_and_environment(blocks):
    cfg,_=blocks
    prompt='纯环境温变工况。基础材料弹性模量200GPa，泊松比0.3。自由热弹性分析'
    prepared=agent_skill.prepare(agent,'blocks',prompt,cfg)
    review=dict(patch=[dict(path='/structural',value_json=json.dumps(dict(mode='constrained',reference_C=25,
        supports=[dict(name='底部',surface_selection='bottom',axes='xyz')])))],questions=[])
    c,q=agent_skill.compile_review(agent,prepared,json.dumps(review))
    assert not q and c['environment_only'] and c['structural']['supports'][0]['faces']
    p=agent_skill.prepare(agent,'blocks','保持当前配置',c)
    assert 'faces' not in p['public']['current']['structural']['supports'][0]


def test_component_mechanical_properties_remain_scoped(blocks):
    cfg,_=blocks
    r=agent.plan_request('blocks','纯环境温变工况。组件2弹性模量70GPa，泊松比0.33，屈服强度150MPa',cfg)
    assert r['ok'],r['questions']
    assert r['config']['base_material']['young_modulus_Pa'] is None
    assert r['config']['component_materials'][0]['material']['young_modulus_Pa']==70e9


def test_directional_support_is_preserved(blocks):
    cfg,_=blocks
    p='纯环境温变工况。基础材料弹性模量200GPa，泊松比0.3。启用热应力分析，底面固定z方向。'
    r=agent.plan_request('blocks',p,cfg)
    assert r['ok'],r['questions']
    assert r['config']['structural']['supports'][0]['axes']=='z'


def test_followup_resolves_missing_mechanics_and_scope(blocks):
    cfg,_=blocks
    first='纯环境温变工况，启用热应力分析'
    r=agent.plan_request('blocks',first,cfg)
    assert not r['ok']
    context=dict(history=[dict(role='user',content=first),dict(role='assistant',content='请提供材料力学参数及支撑')],applied_message_count=0)
    r=agent.plan_request('blocks','基础材料弹性模量200GPa，泊松比0.3。底面固定。',cfg,context)
    assert r['ok'],r['questions']
