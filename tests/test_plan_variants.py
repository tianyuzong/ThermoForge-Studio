import copy
import pytest
from strength import agent
from strength.schemas import AgentDecision, CaseVariant, ForceUpdate, MeshSpec, ResultQuery
from test_physics import axial_case


def decision(*variants):
    return AgentDecision(action='plan', message='按已确认条件继续', questions=[], cases=[],
                         case_variants=list(variants), compare_with_previous=False)


def test_variants_inherit_conditions_and_modify_only_named_force(tmp_path, monkeypatch):
    base=axial_case().model_dump()
    base['loads'][0]['name']='轴端力'
    base['loads'] += [dict(base['loads'][0], name='自平衡力偶', vector=[0, 0, 2.3]),
                      dict(base['loads'][0], name='重力', kind='gravity', selection=None, vector=[0,0,-9.80665])]
    context={'current_case':base}; original=copy.deepcopy(context)
    raw=decision(CaseVariant(name='一千五百牛', force_updates=[ForceUpdate(load_name='轴端力', vector=[1500,0,0])],
                    result_requests=[ResultQuery(kind='probe', field='displacement_x', point_m=[.1,0,0])]),
                 CaseVariant(name='一千牛收敛', mesh=MeshSpec(size_m=.027, convergence=True)))
    monkeypatch.setattr(agent,'cli_plan',lambda *args:raw)
    result=agent.plan(context,'cli',tmp_path,lambda:False)
    first, second=[c.model_dump() for c in result.cases]
    expected=copy.deepcopy(base); expected['name']=first['name']; expected['loads'][0]['vector']=[1500,0,0]
    expected['result_requests']=first['result_requests']
    assert first==expected
    assert second['loads']==base['loads'] and second['mesh']['size_m']==.027
    assert context==original and result.case_variants==[]
    assert (tmp_path/'expanded-decision.json').exists()
    # Each expanded configuration is independent of its sibling and the input.
    result.cases[0].loads[1].vector[2]=999
    assert result.cases[1].loads[1].vector[2]==2.3 and context==original


@pytest.mark.parametrize('kind',['unknown','ambiguous','gravity','duplicate'])
def test_invalid_load_updates_are_rejected(kind):
    base=axial_case().model_dump();base['loads'][0]['name']='载荷'
    if kind=='ambiguous':base['loads'].append(copy.deepcopy(base['loads'][0]))
    if kind=='gravity':base['loads'][0].update(kind='gravity',selection=None)
    update=ForceUpdate(load_name='不存在' if kind=='unknown' else '载荷',vector=[2000,0,0])
    item=CaseVariant(name='工况',force_updates=[update,update] if kind=='duplicate' else [update])
    with pytest.raises(ValueError):agent.expand_case_variants(decision(item),{'current_case':base})


def test_variants_cannot_mix_formats_or_invent_a_base():
    item=CaseVariant(name='工况')
    with pytest.raises(ValueError,match='current_case'):agent.expand_case_variants(decision(item),{})
    with pytest.raises(ValueError,match='不能与完整'):
        AgentDecision(action='plan',message='',questions=[],cases=[axial_case()],case_variants=[item],compare_with_previous=False)


def test_context_removes_only_exact_duplicates():
    base=axial_case().model_dump();other=copy.deepcopy(base);other['name']='另一已保存工况'
    context={'current_case':base,'saved_cases':[copy.deepcopy(base),other],
             'geometry':{'surfaces':[{'id':211,'center_m':[-.155,0,0]}]},
             'conversation':[{'role':'user','text':'选面211','time':1},{'role':'user','text':'选面211','time':2}]}
    original=copy.deepcopy(context);compacted=agent.planning_context(context)
    assert context==original and compacted['saved_cases']==[other]
    assert compacted['current_case']==base and compacted['geometry']==context['geometry']
    assert len(compacted['conversation'])==1
