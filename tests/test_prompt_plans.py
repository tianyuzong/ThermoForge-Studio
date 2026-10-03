import json
from types import SimpleNamespace
import pytest
from strength import store
from forge import plans,runs
from forge.schemas import AnalysisCase
from forge.provenance import digest
from test_forge_numerics import material

REQUEST=SimpleNamespace(base_url='http://127.0.0.1:8877/')


@pytest.fixture
def project(models,monkeypatch):
    geo=store.put('model',models['bar']);pid=store.uid()
    case=AnalysisCase(name='导热杆',analysis_type='thermal_transient',material=material(),mesh={'size_m':.01},
        thermal={'initial_C':25,'duration_s':10,'dt_s':2,'save_s':2,'boundaries':[{'kind':'body_power','body_id':1,'value':39}]})
    p=store.put('project',{'id':pid,'name':'任意导热杆项目','model_id':geo['id'],'example_case':case.model_dump()})
    monkeypatch.setattr(plans,'launch',lambda id:None)
    monkeypatch.setattr(runs,'launch',lambda id:None)
    monkeypatch.setattr(runs,'build_nodes',lambda *args:pytest.fail('generic prompt loaded motor demo'))
    return p


def decision(**kw):
    return plans.Decision(title='当前零件热分析',message='按已保存参数',studies=[plans.PlannedStudy(key='heat',label='升温',base_case_id='example',case_json=json.dumps(kw))])


def saved_plan(project,monkeypatch,value=None,prompt='对当前零件计算升温过程并生成报告',execute=False):
    p=plans.create(project['id'],plans.PlanRequest(request_id=store.uid(),prompt=prompt,execute=execute),REQUEST)
    monkeypatch.setattr(plans.agent,'cli_plan',lambda *a,**k:value or decision())
    plans.execute(p['id']);return plans.get(p['id'])


def test_non_motor_scoped_snapshot_and_partial_override(project,monkeypatch):
    p=saved_plan(project,monkeypatch,decision(thermal={'duration_s':20}))
    n=p['nodes'][0]
    assert p['status']=='ready' and len(p['nodes'])==1
    assert n['model_id']==project['model_id'] and n['project_id']==project['id']
    assert n['case']['thermal']['duration_s']==20 and n['case']['thermal']['boundaries'][0]['value']==39
    assert n['case']['material']==project['example_case']['material']
    run=runs.create(runs.RunRequest(request_id=store.uid(),project_id=project['id'],plan_id=p['id']),REQUEST)
    assert run['suite_version']=='project-plan-1' and run['nodes'][0]['key']=='heat'
    assert (runs.folder(run['id'])/'plan-context.json').is_file()
    assert not runs.demo_refinement(run,{'key':'thermo_mechanical'})


def test_distinct_effective_conditions_do_not_deduplicate(project,monkeypatch):
    p=saved_plan(project,monkeypatch);q=saved_plan(project,monkeypatch,decision(thermal={'duration_s':50}))
    def run(plan,force=False):return runs.create(runs.RunRequest(request_id=store.uid(),project_id=project['id'],plan_id=plan['id'],force=force),REQUEST)
    a=run(p);assert run(p)['id']==a['id']
    assert run(q)['id']!=a['id'];assert run(p,True)['id']!=a['id']


def test_complete_plan_auto_submits_without_browser(project,monkeypatch):
    p=saved_plan(project,monkeypatch,execute=True)
    assert p['status']=='submitted' and runs.get(p['run_id'])['origin_project_id']==project['id']


@pytest.mark.parametrize('field,value',[('questions',['请提供热源功率']),('unsupported',['不支持塑性低周疲劳'])])
def test_missing_or_unsupported_never_submits(project,monkeypatch,field,value):
    d=decision();setattr(d,field,value)
    p=saved_plan(project,monkeypatch,d,execute=True)
    assert p['status']=='needs_input' and not p.get('run_id') and p['questions']==value


def test_wrong_geometry_is_not_concealed_by_freezing(project,monkeypatch):
    p=saved_plan(project,monkeypatch,decision(geometry_sha256='wrong'),execute=True)
    assert p['status']=='needs_input' and '另一版几何' in p['questions'][0]


def test_stale_geometry_and_cross_project_plan_rejected(project,monkeypatch):
    p=saved_plan(project,monkeypatch)
    with pytest.raises(ValueError,match='另一个项目'):plans.prepared_nodes(p['id'],'other')
    geo=store.get('model',project['model_id']);geo['named_selections']['新选区']=[];store.put('model',geo)
    with pytest.raises(ValueError,match='选区已改变'):plans.prepared_nodes(p['id'],project['id'])


@pytest.mark.parametrize('payload',[{'base_case_id':'other-project-study'}, {'case_json':'[]'}, {'case_json':json.dumps({'thermal':{'boundaries':[]}})}, {'case_json':json.dumps({'component_materials':{'999':material().model_dump()}})}])
def test_bad_conditions_are_clarifications(project,monkeypatch,payload):
    d=decision();d.studies[0]=d.studies[0].model_copy(update=payload)
    p=saved_plan(project,monkeypatch,d,execute=True)
    assert p['status']=='needs_input' and not p.get('run_id')


def test_dependencies_sorted_and_missing_cycle_rejected(project):
    context=plans.context_for(project['id'],'热续算')
    d=decision();second=d.studies[0].model_copy(update={'key':'next','initial_temperature_from':'heat'})
    d.studies.insert(0,second)
    nodes=plans.compile_nodes(d,context);assert [n['key'] for n in nodes]==['heat','next']
    assert nodes[1]['case']['thermal']['initial_from_study'] is None
    d.studies[1].depends_on=['next']
    with pytest.raises(ValueError,match='成环'):plans.compile_nodes(d,context)
    d.studies[1].depends_on=['absent']
    with pytest.raises(ValueError,match='不存在'):plans.compile_nodes(d,context)


def test_dependency_failure_blocks_only_dependent_nodes(project,monkeypatch):
    d=decision();d.studies += [plans.PlannedStudy(key='next',label='热续算',base_case_id='example',initial_temperature_from='heat'),plans.PlannedStudy(key='independent',label='独立升温',base_case_id='example')]
    p=saved_plan(project,monkeypatch,d,execute=True);r=runs.get(p['run_id'])
    nodes=r['nodes'];heat=next(n for n in nodes if n['key']=='heat');heat.update(status='failed',study_id='failed-study');runs.patch(r['id'],nodes=nodes)
    index=next(i for i,n in enumerate(nodes) if n['key']=='next')
    assert not runs.resolve_dependencies(r['id'],index)
    assert runs.get(r['id'])['nodes'][index]['status']=='blocked'
    assert runs.resolve_dependencies(r['id'],next(i for i,n in enumerate(nodes) if n['key']=='independent'))
    heat.update(status='completed',study_id='real-thermal-result');runs.patch(r['id'],nodes=nodes)
    assert runs.resolve_dependencies(r['id'],index)
    assert runs.get(r['id'])['nodes'][index]['case']['thermal']['initial_from_study']=='real-thermal-result'


def test_cancelled_provider_response_cannot_submit(project,monkeypatch):
    p=plans.create(project['id'],plans.PlanRequest(request_id=store.uid(),prompt='当前零件升温'),REQUEST)
    def parse(*a,**k):plans.cancel(p['id']);return decision()
    monkeypatch.setattr(plans.agent,'cli_plan',parse);plans.execute(p['id'])
    assert plans.get(p['id'])['status']=='cancelled' and not plans.get(p['id']).get('run_id')


def test_offline_prose_not_misrepresented_as_understood(project):
    d=plans.local_decision('对这个任意零件做热学分析并施加 200 W',plans.context_for(project['id'],''))
    assert d.questions and not d.studies
    d=plans.local_decision('运行已保存研究 example',plans.context_for(project['id'],''))
    assert len(d.studies)==1 and d.studies[0].base_case_id=='example'


def test_plain_command_never_loads_demo(project):
    with pytest.raises(ValueError,match='analysis-plans'):runs.create(runs.RunRequest(request_id=store.uid(),project_id=project['id'],prompt='运行全套分析'),REQUEST)


def test_followup_preserves_original_request_and_recovery(project,monkeypatch):
    p=saved_plan(project,monkeypatch,plans.Decision(title='补充',message='条件不足',questions=['功率是多少？']))
    follow=plans.create(project['id'],plans.PlanRequest(request_id=store.uid(),prompt='功率为 40 W',previous_plan_id=p['id']),REQUEST)
    c=json.loads((plans.folder(follow['id'])/'context.json').read_text(encoding='utf-8'))
    assert len(c['conversation'])==3 and c['conversation'][0]['text']==p['prompt']
    plans.recover();assert plans.get(follow['id'])['status']=='interrupted'
    assert plans.get(p['id'])['status']=='needs_input'


def test_generic_video_catalog_uses_physics_not_node_name():
    from forge.run_exports import catalog
    assert catalog({'key':'user_named_rod_heat','case':{'analysis_type':'thermal_transient'}},{})==['temperature']
    assert 'contact_pressure' in catalog({'key':'rolling','case':{'analysis_type':'contact_cycle'}},{})


def test_plan_http_schema_and_context_routes(client,project):
    response=client.get('/api/v1/analysis-plans/schema')
    assert response.status_code==200 and 'studies' in response.json()['properties']
    response=client.get(f'/api/v1/projects/{project["id"]}/analysis-context')
    assert response.status_code==200 and response.json()['geometry']['id']==project['model_id']


def test_body_heat_source_must_belong_to_model(project,monkeypatch):
    p=saved_plan(project,monkeypatch,decision(thermal={'boundaries':[{'kind':'body_power','body_id':999,'value':10}]}),execute=True)
    assert p['status']=='needs_input' and '不存在的组件' in p['questions'][0]


def test_report_selection_describes_direction_without_empty_faces():
    from forge.report import selection_description
    from strength.schemas import Selection
    assert selection_description(Selection(kind='direction',direction='+x'))=='全模型选区，朝向 +x 的面'
