import copy
import json

import numpy as np
import pytest
import trimesh

import thermal_legacy.agent as agent
import thermal_legacy.agent_skill as agent_skill
from thermal_legacy.schemas import Simulation


@pytest.fixture
def blocks(monkeypatch, tmp_path):
    a = trimesh.creation.box(extents=[.02,.02,.02]); a.apply_translation([-.02,0,0])
    b = trimesh.creation.box(extents=[.02,.02,.02]); b.apply_translation([.02,0,0])
    mesh = trimesh.util.concatenate([a,b])
    folder = tmp_path / 'blocks'; folder.mkdir()
    metadata = dict(triangles=24, name='blocks.stl', kind='stl', bounds_m=mesh.bounds.tolist(), dimensions_m=mesh.extents.tolist(),
                    components=[dict(component_id=i, bounds_m=part.bounds.tolist()) for i,part in enumerate([a,b])])
    display = dict(points=mesh.vertices.ravel().tolist(), faces=mesh.faces.ravel().tolist(), is_outer=[1]*24)
    for name,value in [('metadata.json',metadata),('display.json',display)]:
        (folder/name).write_text(json.dumps(value),encoding='utf-8')
    np.savez(folder/'shells.npz',shell_id=np.r_[np.zeros(12,dtype=int),np.ones(12,dtype=int)])
    monkeypatch.setattr(agent,'MODELS',tmp_path)
    return Simulation(model_id='blocks',mesh_size_m=.002).model_dump(),mesh


def test_heating_selection_is_independent_of_cooling_and_negated_flags(blocks):
    cfg,mesh = blocks
    result = agent.plan_request('blocks','顶部20W面热源，仿真600秒。全部外表面对流散热，换热系数10，包括受热面。关闭辐射和空气间隙耦合。',cfg)
    assert result['ok'], result['questions']
    c=result['config']
    assert c['heat_sources'][0]['faces']==np.flatnonzero(mesh.face_normals[:,2]>.99).tolist()
    assert c['heat_convection'] and not c['radiation_enabled'] and not c['air_gap_enabled']
    assert cfg['heat_sources']==[]


def test_component_material_sentences_do_not_target_the_heater_or_change_base(blocks):
    cfg,_=blocks
    result=agent.plan_request('blocks','组件1设为铜，组件2设为铝。在组件2的外表面施加20W面热源，从10秒到90秒。仿真120秒。基础材料保持不变。',cfg)
    assert result['ok'],result['questions']
    c=result['config'];h=c['heat_sources'][0]
    assert h['faces']==list(range(12,24))
    assert h['start_s']==10 and h['end_s']==90
    assert c['base_material']==cfg['base_material']
    assert [x['component_id'] for x in c['component_materials']]==[0,1]


def test_point_coordinates_and_explicit_mesh_are_preserved(blocks):
    cfg,_=blocks
    result=agent.plan_request('blocks','内部点热源，位置x=20mm，y=0mm，z=0mm，功率0.05kW，从30秒到90秒。仿真120秒，网格尺寸1mm。',cfg)
    assert result['ok'],result['questions']
    c=result['config'];h=c['heat_sources'][0]
    assert h['position_m']==[.02,0,0] and h['faces']==[]
    assert h['power_W']==50 and h['start_s']==30 and h['end_s']==90
    assert c['mesh_size_m']==.001


def test_power_only_edit_preserves_windows_selection_and_other_heat(blocks):
    cfg,_=blocks
    cfg=Simulation(**{**cfg,'heat_sources':[dict(power_W=10,faces=[1],start_s=20,end_s=30),dict(power_W=8,faces=[2],start_s=40,end_s=50)]}).model_dump()
    result=agent.plan_request('blocks','把第一个热源功率改为25W，其他参数保持不变。',cfg)
    assert result['ok'],result['questions']
    h=result['config']['heat_sources']
    assert h[0]=={**cfg['heat_sources'][0],'power_W':25}
    assert h[1]==cfg['heat_sources'][1]


def test_heat_transfer_coefficient_is_not_heat_power(blocks):
    cfg,_=blocks
    result=agent.plan_request('blocks','全部外表面对流换热系数10 W/(m²·K)，没有热源。',cfg)
    assert not result['ok'] and result['config']['heat_sources']==[]


def test_negative_power_is_rejected_instead_of_losing_its_sign(blocks):
    cfg,_=blocks
    result=agent.plan_request('blocks','顶部-20W面热源',cfg)
    assert not result['ok']
    assert result['config']['heat_sources'][0]['power_W']==-20


def test_power_replacement_targets_the_requested_existing_heater(blocks):
    cfg,_=blocks
    cfg=Simulation(**{**cfg,'heat_sources':[dict(power_W=10,faces=[0]),dict(power_W=8,faces=[2])]}).model_dump()
    result=agent.plan_request('blocks','把第二个热源从8W调整到35W，其他保持不变。',cfg)
    assert result['ok'],result['questions']
    assert result['config']['heat_sources']==[cfg['heat_sources'][0],{**cfg['heat_sources'][1],'power_W':35}]


@pytest.mark.parametrize('text,expected',[('启用辐射，随后关闭辐射。',False),('关闭辐射，然后开启辐射。',True)])
def test_last_explicit_feature_setting_wins(blocks,text,expected):
    cfg,_=blocks
    result=agent.plan_request('blocks','顶部20W热源。'+text,cfg)
    assert result['ok'] and result['config']['radiation_enabled'] is expected


def test_coordinate_selection_and_component_direction_use_shared_real_faces(blocks):
    cfg,mesh=blocks
    selections=agent._surface_selections('blocks',['x=0.03'])
    assert selections['x=0.03']['faces']==np.flatnonzero(mesh.triangles_center[:,0]>.0299).tolist()
    assert selections['component:2:top']['faces']==[i for i in range(12,24) if mesh.face_normals[i,2]>.99]


def test_skill_can_correct_rule_candidate_and_add_local_cooling(blocks):
    cfg,mesh=blocks
    p=agent_skill.prepare(agent,'blocks','顶部20W热源，底部对流换热系数30，环境温度25°C。',cfg)
    review=dict(patch=[dict(path='/default_h',value_json='0'),dict(path='/cooling',value_json=json.dumps([dict(h=30,ambient_C=25,surface_selection='bottom')]))],questions=[])
    c,q=agent_skill.compile_review(agent,p,json.dumps(review))
    assert not q and c['default_h']==0
    assert c['cooling'][0]['faces']==np.flatnonzero(mesh.face_normals[:,2]<-.99).tolist()
    assert cfg['heat_sources']==[] and cfg['cooling']==[]


def test_empty_review_still_checks_heat_and_preserves_input(blocks):
    cfg,_=blocks
    p=agent_skill.prepare(agent,'blocks','材料设为铝',cfg)
    with pytest.raises(ValueError,match='未生成热源'):
        agent_skill.compile_review(agent,p,'{"patch":[],"questions":[]}')
    assert cfg['heat_sources']==[]


@pytest.mark.parametrize('path,value',[
    ('/model_id','"another"'),('/heat_sources/0/faces','[999]'),
    ('/heat_sources','[{"power_W":20,"faces":[0]}]'),('/heat_sources/5/power_W','25'),
    ('/heat_sources/0/surface_selection','"invented-region"'),
])
def test_bad_model_or_geometry_edits_are_rejected(blocks,path,value):
    cfg,_=blocks
    p=agent_skill.prepare(agent,'blocks','顶部20W热源',cfg)
    original=copy.deepcopy(p['candidate'])
    with pytest.raises(ValueError):
        agent_skill.compile_review(agent,p,json.dumps(dict(patch=[dict(path=path,value_json=value)],questions=[])))
    assert p['candidate']==original


def test_existing_arbitrary_selection_is_kept_out_of_prompt(blocks):
    cfg,_=blocks
    cfg=Simulation(**{**cfg,'heat_sources':[dict(power_W=20,faces=[1,4,16])]}).model_dump()
    p=agent_skill.prepare(agent,'blocks','功率改为25W',cfg)
    compact=p['public']['candidate']['heat_sources'][0]
    assert 'faces' not in compact and compact['surface_selection'].startswith(('candidate:','current:'))
    c,q=agent_skill.compile_review(agent,p,'{"patch":[],"questions":[]}')
    assert c['heat_sources'][0]['faces']==[1,4,16] and not q


def test_clarification_does_not_produce_a_runnable_draft(blocks):
    cfg,_=blocks
    p=agent_skill.prepare(agent,'blocks','给螺孔内侧20W加热',cfg)
    c,q=agent_skill.compile_review(agent,p,'{"patch":[],"questions":["请刷选螺孔内侧受热面。"]}')
    assert c is None and q


def test_diff_summary_includes_boundary_changes(blocks):
    cfg,_=blocks
    new={**cfg,'radiation_enabled':True,'air_gap_enabled':False,'default_h':25,'heat_convection':True}
    text=' '.join(agent._codex_changes(new,cfg))
    assert '辐射' in text and '空气间隙' in text and '25' in text and '热源面' in text
