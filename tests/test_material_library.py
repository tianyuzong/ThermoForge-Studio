import json
import h5py
import pytest
from strength.material_library import builtin_materials
from strength.schemas import Material, AgentDecision


def test_catalogue_si_and_independent_copies():
    cards=builtin_materials()
    assert len({c['id'] for c in cards})==len(cards)
    for card in cards:
        Material.model_validate(card['material'])
        assert card['condition'] and card['notes']
        for ref in card['references']:
            assert ref['url'].startswith('https://')
            assert ref['url'] in card['material']['source']
    abs_card=next(c for c in cards if c['id']=='builtin-abs-gp22')
    assert abs_card['material']['young_modulus_Pa']==2.3e9
    assert abs_card['material']['yield_strength_Pa']==45e6
    assert abs_card['material']['density_kg_m3']==1040
    copper=next(c['material'] for c in cards if c['id']=='builtin-cu-c11000')
    assert copper['young_modulus_Pa']==pytest.approx(117.210873e9,rel=1e-7)
    assert copper['yield_strength_Pa'] is None
    abs_card['material']['young_modulus_Pa']=1
    assert next(c for c in builtin_materials() if c['id']==abs_card['id'])['material']['young_modulus_Pa']==2.3e9


def test_presets_custom_cards_and_saved_sources(client,models):
    from strength import store
    from test_physics import axial_case
    cards=client.get('/api/v1/materials').json()
    plastic=next(c['material'] for c in cards if c['id']=='builtin-abs-gp22')
    custom={**plastic,'name':'用户 ABS 测试批次','young_modulus_Pa':2.1e9,'source':'用户批次试验记录'}
    saved=client.post('/api/v1/materials',json={'material':custom}).json()
    loaded=next(c for c in client.get('/api/v1/materials').json() if c['id']==saved['id'])
    assert loaded['category']=='我的材料' and loaded['builtin'] is False
    assert loaded['material']==custom
    p=client.post('/api/v1/projects',json={'name':'材料来源快照验证'}).json()
    store.put('model',models['bar']);store.update('project',p['id'],model_id=models['bar']['id'])
    case=axial_case().model_dump();case['material']=plastic
    r=client.post(f'/api/v1/projects/{p["id"]}/cases',json={'case':case})
    assert r.status_code==200,r.text
    assert client.patch(f'/api/v1/projects/{p["id"]}',json={'material':custom}).status_code==200
    snapshot=client.get(f'/api/v1/projects/{p["id"]}/cases').json()[0]['case']['material']
    assert snapshot==plastic
    assert '泊松比为建模近似' in snapshot['source']


def test_planner_receives_same_builtin_and_custom_catalogue(client,models,monkeypatch):
    from strength import agent
    from test_agent_api import project,send,wait
    captured={}
    def plan(context,*args):
        captured.update(context)
        return AgentDecision(action='clarify',message='选择材料',questions=['请指定塑料牌号。'])
    monkeypatch.setattr(agent,'plan',plan)
    p=project(client,models)
    task,_=send(client,p,'换成塑料，先只给方案。',execution_mode='draft')
    assert wait(client,task['id'])['status']=='needs_input'
    assert captured['material_library']==client.get('/api/v1/materials').json()
    assert any(c['id']=='builtin-pa66-tecamid' for c in captured['material_library'])


@pytest.mark.parametrize('key',['builtin-abs-gp22','builtin-al6061-t6','builtin-cu-c11000'])
def test_real_cpu_bar_uses_preset_units_and_optional_yield(models,key):
    from test_physics import axial_case,solve
    card=next(c for c in builtin_materials() if c['id']==key)
    case=axial_case();case.material=Material.model_validate(card['material'])
    summary,folder=solve('bar',case)
    expected=1000*.1/(1e-4*case.material.young_modulus_Pa)
    with h5py.File(folder/'fields.h5') as fields:
        assert fields['displacements'][:,0].max()==pytest.approx(expected,rel=.01)
    assert summary['maximum_von_mises_Pa']==pytest.approx(1e7,rel=.01)
    if case.material.yield_strength_Pa is None:
        assert summary['minimum_safety_factor'] is None
    else:
        assert summary['minimum_safety_factor']==pytest.approx(case.material.yield_strength_Pa/1e7,rel=.01)
    assert json.loads((folder/'config.json').read_text(encoding='utf-8'))['case']['material']['source']==card['material']['source']
