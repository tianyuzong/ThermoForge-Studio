import copy
import json

import numpy as np
import pytest
import trimesh

import thermal_legacy.agent as agent
import thermal_legacy.agent_skill as agent_skill
from thermal_legacy.schemas import Simulation


@pytest.fixture
def scene(monkeypatch, tmp_path):
    mesh = trimesh.creation.box(extents=[.04, .02, .02])
    mesh.apply_translation([.06, .02, .01])
    folder = tmp_path / 'intent'; folder.mkdir()
    metadata = dict(triangles=len(mesh.faces), bounds_m=mesh.bounds.tolist(),
                    dimensions_m=mesh.extents.tolist(), components=[])
    display = dict(points=mesh.vertices.tolist(), faces=mesh.faces.tolist(), is_outer=[1]*len(mesh.faces))
    for name, value in [('metadata.json', metadata), ('display.json', display)]:
        (folder/name).write_text(json.dumps(value), encoding='utf-8')
    monkeypatch.setattr(agent, 'MODELS', tmp_path)
    cfg = Simulation(model_id='intent', mesh_size_m=.002, duration_s=180,
        heat_sources=[dict(name='已有顶部热源',power_W=20,faces=[4,6],start_s=10,end_s=100),
                      dict(name='已有侧面热源',power_W=8,faces=[0,2],start_s=30,end_s=120)]).model_dump()
    return cfg, folder


@pytest.mark.parametrize('verb', ['单独加一个', '另外添加一个', '再加一个', '增加一个', '新增一个'])
def test_add_center_heat_preserves_existing_sources(scene, verb):
    cfg, _ = scene
    original = copy.deepcopy(cfg)
    result = agent.plan_request('intent', f'{verb}热源放在整个模型中间，功率15W，半径2mm，从20秒到90秒。其他参数保持不变。', cfg)
    assert result['ok'], result['questions']
    assert cfg == original
    heat = result['config']['heat_sources']
    assert heat[:2] == cfg['heat_sources'] and len(heat) == 3
    assert heat[2]['source_type'] == 'point' and heat[2]['placement'] == 'embedded'
    np.testing.assert_allclose(heat[2]['position_m'], [.06,.02,.01])
    assert heat[2]['faces'] == [] and heat[2]['power_W'] == 15
    assert heat[2]['radius_m'] == .002 and heat[2]['start_s'] == 20 and heat[2]['end_s'] == 90


def test_missing_new_heat_power_asks_specifically_and_preserves_current(scene):
    cfg, _ = scene
    result = agent.plan_request('intent', '单独加一个热源放在整个模型中间，其他热源不动。', cfg)
    assert not result['ok']
    assert any('功率' in q for q in result['questions'])
    assert result['config']['heat_sources'] == cfg['heat_sources']


def test_position_only_edit_preserves_power_and_other_sources(scene):
    cfg, _ = scene
    result = agent.plan_request('intent', '把第二个热源移到整个模型内部中心，其余参数保持不变。', cfg)
    assert result['ok'], result['questions']
    first, second = result['config']['heat_sources']
    assert first == cfg['heat_sources'][0]
    assert second['power_W'] == 8 and second['start_s'] == 30 and second['end_s'] == 120
    np.testing.assert_allclose(second['position_m'], [.06,.02,.01])
    assert second['source_type'] == 'point' and second['placement'] == 'embedded' and not second['faces']


def test_ambiguous_position_edit_asks_which_heat(scene):
    cfg, _ = scene
    result = agent.plan_request('intent', '把热源移到模型中间', cfg)
    assert not result['ok'] and result['config']['heat_sources'] == cfg['heat_sources']
    assert any('哪个热源' in q for q in result['questions'])


def test_negated_add_does_not_append(scene):
    cfg, _ = scene
    result = agent.plan_request('intent', '不要新增热源，只把第二个热源功率改为25W。', cfg)
    assert result['ok'] and result['config']['heat_sources'] == [cfg['heat_sources'][0], {**cfg['heat_sources'][1], 'power_W':25}]


def test_surface_center_does_not_select_entire_surface(scene):
    cfg, _ = scene
    result = agent.plan_request('intent', '单独加一个15W面热源放在顶部表面中间。', cfg)
    assert not result['ok']
    assert any('范围' in q or '半径' in q for q in result['questions'])


def test_skill_receives_center_and_compiles_add_without_old_heat_changes(scene):
    cfg, _ = scene
    prepared = agent_skill.prepare(agent, 'intent', '单独加一个热源放在中间，功率15W', cfg)
    np.testing.assert_allclose(prepared['public']['geometry']['center_m'], [.06,.02,.01])
    assert prepared['public']['geometry']['center_in_solid'] is True
    result, questions = agent_skill.compile_review(agent, prepared, '{"patch":[],"questions":[]}')
    assert not questions and len(result['heat_sources']) == 3
    assert result['heat_sources'][:2] == cfg['heat_sources']


def test_center_in_void_is_identified_but_not_silently_moved(scene):
    cfg, folder = scene
    outer = trimesh.creation.box(extents=[.04,.02,.02]); outer.apply_translation([.06,.02,.01])
    inner = trimesh.creation.box(extents=[.02,.01,.01]); inner.apply_translation([.06,.02,.01]); inner.invert()
    mesh = trimesh.util.concatenate([outer, inner])
    metadata = json.loads((folder/'metadata.json').read_text()); metadata['triangles'] = len(mesh.faces)
    (folder/'metadata.json').write_text(json.dumps(metadata))
    (folder/'display.json').write_text(json.dumps(dict(points=mesh.vertices.tolist(), faces=mesh.faces.tolist())))
    result = agent.plan_request('intent', '单独加一个热源放在模型内部中间，功率15W', cfg)
    assert not result['ok'] and any('空腔' in q or '实体' in q for q in result['questions'])
    np.testing.assert_allclose(result['config']['heat_sources'][-1]['position_m'], [.06,.02,.01])
    prepared = agent_skill.prepare(agent, 'intent', '单独加一个15W热源放在内部中间', cfg)
    assert prepared['public']['geometry']['center_in_solid'] is False
    with pytest.raises(ValueError, match='空腔|实体'):
        agent_skill.compile_review(agent, prepared, '{"patch":[],"questions":[]}')


def test_two_sources_across_sentences_with_later_corrections(scene):
    cfg, _ = scene
    prompt = ('清空已有热源。第一个热源放在顶部，功率30W。从第0秒开始，到第120秒关闭。'
              '第二个热源放在右侧，功率12W。从第30秒开始，到第150秒关闭。'
              '我刚拿了3个快递，回来看了一段90秒的视频。'
              '把第一个热源功率改成35W，把第二个热源功率改成8W。其他设置保持不变。')
    result = agent.plan_request('intent', prompt, cfg)
    assert result['ok'], result['questions']
    heats = result['config']['heat_sources']
    assert len(heats) == 2
    assert [(h['power_W'],h['start_s'],h['end_s']) for h in heats] == [(35,0,120),(8,30,150)]
    selections = agent._surface_selections('intent')
    assert heats[0]['faces'] == selections['top']['faces']
    assert heats[1]['faces'] == selections['right']['faces']


@pytest.mark.parametrize('prompt,field,value', [
    ('把第二个热源改为第90秒关闭', 'end_s', 90),
    ('把第二个热源改为第40秒开启', 'start_s', 40),
    ('把第二个热源半径改为3mm', 'radius_m', .003),
    ('把“已有侧面热源”的功率改为18W', 'power_W', 18),
])
def test_non_power_or_named_edits_touch_only_requested_parameter(scene,prompt,field,value):
    cfg, _ = scene
    result = agent.plan_request('intent', prompt, cfg)
    assert result['ok'],result['questions']
    assert result['config']['heat_sources'] == [cfg['heat_sources'][0],{**cfg['heat_sources'][1],field:value}]


@pytest.mark.parametrize('prompt', ['删除第二个热源', '移除“已有侧面热源”'])
def test_remove_specific_heat_keeps_the_other(scene,prompt):
    cfg, _ = scene
    result = agent.plan_request('intent',prompt,cfg)
    assert result['ok'],result['questions']
    assert result['config']['heat_sources'] == cfg['heat_sources'][:1]


def test_negated_removal_preserves_both_sources(scene):
    cfg, _ = scene
    result = agent.plan_request('intent','不要删除第二个热源，只把第一个热源改成25W',cfg)
    assert result['ok'],result['questions']
    assert result['config']['heat_sources'] == [{**cfg['heat_sources'][0],'power_W':25},cfg['heat_sources'][1]]


def test_append_two_sources_with_distinct_scopes(scene):
    cfg, _ = scene
    result = agent.plan_request('intent', '另外加一个5W热源在底部，从10秒到70秒。再加个点热源放在内部中间，功率12W，半径0.3cm，第80秒开启，第160秒关闭。', cfg)
    assert result['ok'],result['questions']
    heats = result['config']['heat_sources']
    assert len(heats)==4 and heats[:2]==cfg['heat_sources']
    assert heats[2]['faces'] == agent._surface_selections('intent')['bottom']['faces']
    assert (heats[2]['power_W'],heats[2]['start_s'],heats[2]['end_s']) == (5,10,70)
    assert (heats[3]['power_W'],heats[3]['start_s'],heats[3]['end_s'],heats[3]['radius_m']) == (12,80,160,.003)


def test_component_center_is_not_whole_model_center(scene):
    cfg, folder = scene
    metadata = json.loads((folder/'metadata.json').read_text())
    metadata['components'] = [dict(component_id=0,bounds_m=[[.04,.01,0],[.06,.03,.02]])]
    (folder/'metadata.json').write_text(json.dumps(metadata))
    result = agent.plan_request('intent','另外加一个15W点热源，放在组件1内部中心',cfg)
    assert result['ok'],result['questions']
    np.testing.assert_allclose(result['config']['heat_sources'][-1]['position_m'],[.05,.02,.01])


@pytest.mark.parametrize('prompt,position', [
    ('把第二个热源往左挪5mm', [.055,.024,.012]),
    ('把第二个热源位置改为x=50mm', [.05,.024,.012]),
])
def test_point_movement_preserves_unmentioned_coordinates(scene,prompt,position):
    cfg, _ = scene
    cfg['heat_sources'][1].update(source_type='point',placement='embedded',position_m=[.06,.024,.012],faces=[])
    result = agent.plan_request('intent',prompt,cfg)
    assert result['ok'],result['questions']
    expected = copy.deepcopy(cfg['heat_sources'])
    np.testing.assert_allclose(result['config']['heat_sources'][1]['position_m'],position)
    expected[1]['position_m'] = result['config']['heat_sources'][1]['position_m']
    assert result['config']['heat_sources'] == expected


def test_unknown_named_target_never_changes_first_heat(scene):
    cfg, _ = scene
    result = agent.plan_request('intent','把“还没创建的热源”改为25W',cfg)
    assert not result['ok'] and result['config']['heat_sources']==cfg['heat_sources']


def test_heat_name_is_not_a_location_instruction(scene):
    cfg, _ = scene
    cfg['heat_sources'][1]['name'] = '中心测试热源'
    result = agent.plan_request('intent','把“中心测试热源”功率改为25W',cfg)
    assert result['ok'] and result['config']['heat_sources']==[cfg['heat_sources'][0],{**cfg['heat_sources'][1],'power_W':25}]


def test_explicit_time_fields_without_repeating_power(scene):
    cfg, _ = scene
    result = agent.plan_request('intent','第二个热源开始时间设为40秒，结束时间设为90秒',cfg)
    assert result['ok'] and result['config']['heat_sources']==[cfg['heat_sources'][0],{**cfg['heat_sources'][1],'start_s':40,'end_s':90}]


def test_surface_center_point_is_on_requested_surface(scene):
    cfg, _ = scene
    result = agent.plan_request('intent','另外加一个15W点热源放在顶部中央，半径2mm',cfg)
    assert result['ok'],result['questions']
    heat = result['config']['heat_sources'][-1]
    np.testing.assert_allclose(heat['position_m'], [.06,.02,.02])
    assert heat['source_type']=='point' and heat['placement']=='surface'
