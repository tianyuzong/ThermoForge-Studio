import io
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

import thermal_legacy.agent as agent
from thermal_legacy.schemas import Simulation


@pytest.fixture
def model(monkeypatch, tmp_path):
    model_id = 'test-box'
    folder = tmp_path / model_id
    folder.mkdir()
    mesh = trimesh.creation.box()
    display = dict(points=mesh.vertices.ravel().tolist(), faces=mesh.faces.ravel().tolist(),
                   is_outer=[1] * len(mesh.faces))
    metadata = dict(name='box.stl', kind='stl', triangles=len(mesh.faces), components=[],
                    dimensions_m=[1,1,1], bounds_m=[[-.5,-.5,-.5],[.5,.5,.5]])
    (folder/'display.json').write_text(json.dumps(display), encoding='utf-8')
    (folder/'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
    monkeypatch.setattr(agent, 'MODELS', tmp_path)
    return model_id, mesh, folder


def test_named_top_selection_resolves_to_actual_outward_faces(model):
    model_id, mesh, _ = model
    selections = agent._surface_selections(model_id)
    top = selections['top']['faces']
    assert len(top) == 2
    assert selections['top']['area_m2'] == pytest.approx(1)
    np.testing.assert_allclose(mesh.triangles_center[top, 2], .5)
    assert np.all(mesh.face_normals[top, 2] > .99)
    assert len(selections['all_outer']['faces']) == 12


def test_selection_excludes_cavity_faces(model):
    model_id, mesh, folder = model
    display = json.loads((folder/'display.json').read_text())
    top = np.flatnonzero(mesh.face_normals[:, 2] > .99)
    for face in top:
        display['is_outer'][int(face)] = 0
    (folder/'display.json').write_text(json.dumps(display))
    selections = agent._surface_selections(model_id)
    assert selections['top']['faces'] == []
    assert not set(top).intersection(selections['all_outer']['faces'])


def test_skill_prompt_has_rule_candidate_and_omits_raw_face_lists(model):
    model_id, _, _ = model
    prompt = agent._codex_prompt(model_id, '顶部20W热源，全部外表面对流', Simulation(model_id=model_id).model_dump())
    payload = json.loads(prompt.split('输入数据：\n',1)[1])
    top = payload['available_selections']['top']
    assert top['face_count'] == 2
    assert 'faces' not in top
    heat = payload['candidate']['heat_sources'][0]
    assert heat['power_W'] == 20 and heat['surface_selection'] == 'top'
    assert 'faces' not in heat
    assert 'simulation_schema' not in payload


def test_new_heat_and_cooling_resolve_separately_from_empty_config(model):
    model_id, _, _ = model
    current = Simulation(model_id=model_id, duration_s=600, dt_s=5, save_s=5).model_dump()
    returned = dict(model_id=model_id, heat_sources=[dict(name='顶部热源',power_W=20,start_s=0,end_s=600,surface_selection='top')],
                    cooling=[dict(h=10,ambient_C=25,surface_selection='all_outer')])
    cfg = agent._validated_codex_text(json.dumps(returned), current, model_id)
    assert current['heat_sources'] == []
    assert len(cfg['heat_sources'][0]['faces']) == 2
    assert len(cfg['cooling'][0]['faces']) == 12
    assert 'surface_selection' not in cfg['heat_sources'][0]
    assert cfg['heat_sources'][0]['power_W'] == 20
    assert cfg['duration_s'] == 600 and cfg['dt_s'] == 5
    assert any('20 W' in change and '2 个三角面' in change for change in agent._codex_changes(cfg))


@pytest.mark.parametrize('returned', [{}, {'heat_sources': []}])
def test_missing_heat_is_rejected_during_planning(model, returned):
    model_id, _, _ = model
    with pytest.raises(ValueError, match='未生成热源'):
        agent._validated_codex_text(json.dumps(returned), Simulation(model_id=model_id).model_dump(), model_id)


def test_partial_update_keeps_existing_heat_and_binds_model(model):
    model_id, _, _ = model
    current = Simulation(model_id=model_id, heat_sources=[dict(power_W=20,faces=[0])]).model_dump()
    cfg = agent._validated_codex_text('{"duration_s":600}',current,model_id)
    assert cfg['heat_sources'] == current['heat_sources']
    assert cfg['duration_s'] == 600
    with pytest.raises(ValueError, match='其他模型'):
        agent._validated_codex_text('{"model_id":"another"}',current,model_id)


def test_unknown_or_conflicting_selection_and_invalid_ids_are_rejected(model):
    model_id, _, _ = model
    current = Simulation(model_id=model_id).model_dump()
    for fields, message in [({'surface_selection':'unknown'}, '没有可用'),
                            ({'surface_selection':'top','faces':[0]}, '冲突'),
                            ({'faces':[999]}, '不存在')]:
        response = {'heat_sources':[dict(power_W=20, **fields)]}
        with pytest.raises(ValueError, match=message):
            agent._validated_codex_text(json.dumps(response),current,model_id)


def test_api_provider_also_resolves_named_heat(model, monkeypatch):
    model_id, _, _ = model
    monkeypatch.setenv('OPENAI_API_KEY','test-placeholder')
    response_text = json.dumps({'model_id':model_id,'heat_sources':[{'power_W':20,'surface_selection':'top'}]})
    def response(request, timeout):
        body = json.loads(request.data)
        assert body['text']['format']['schema']['properties']['heat_sources']['minItems'] == 1
        return io.BytesIO(json.dumps({'output_text':response_text}).encode())
    monkeypatch.setattr(agent.urllib.request,'urlopen',response)
    result = agent._codex_api_plan_request(model_id,'顶部20W',Simulation(model_id=model_id).model_dump())
    assert result['ok']
    assert len(result['config']['heat_sources'][0]['faces']) == 2
