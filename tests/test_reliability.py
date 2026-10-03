import threading,uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from strength import store,service,agent,backends
from strength.schemas import AgentDecision,Selection,TaskRequest
from test_agent_api import project,send,wait


def test_format_repair_is_bounded(client,models,monkeypatch):
    calls=[]
    def malformed(*args): calls.append(1); raise ValueError('malformed structured response')
    monkeypatch.setattr(agent,'plan',malformed)
    p=project(client,models);t,_=send(client,p,'安装孔固定，顶面向下 1000 N',provider='cli')
    t=wait(client,t['id'])
    assert len(calls)==3 and t['status']=='needs_input' and not t['job_ids']


def test_ambiguous_candidates_are_evidence_bound(client,models,monkeypatch):
    monkeypatch.setattr(agent,'plan',lambda *a:AgentDecision(action='clarify',message='需要选面',questions=['请明确受力面'],cases=[],compare_with_previous=False,
        candidate_selections=[Selection(kind='named',name='顶面'),Selection(kind='named',name='前侧面')]))
    t,_=send(client,project(client,models),'在上面加载',provider='cli');t=wait(client,t['id'])
    assert t['status']=='needs_input' and len(t['candidate_selections'])==2
    assert t['candidate_selections'][0]['face_ids']==models['bracket']['named_selections']['顶面']


def test_cancel_during_planning_cannot_submit(client,models,monkeypatch):
    started=threading.Event();release=threading.Event()
    def planner(context,*args):
        started.set();release.wait(5);return agent.local_plan(context)
    monkeypatch.setattr(agent,'plan',planner)
    t,_=send(client,project(client,models),'安装孔固定，顶面向下 1000 N',provider='cli')
    assert started.wait(5)
    client.post('/api/v1/agent/tasks/'+t['id']+'/cancel');release.set();service.THREADS[t['id']].join(5)
    t=client.get('/api/v1/agent/tasks/'+t['id']).json()
    assert t['status']=='cancelled' and t['job_ids']==[]


def test_concurrent_duplicate_requests_create_one_task(client,models,monkeypatch):
    launches=[];monkeypatch.setattr(service,'launch',lambda *a:launches.append(a))
    req=TaskRequest(project_id=project(client,models),prompt='安装孔固定，顶面向下 1000 N',provider='local',request_id=uuid.uuid4().hex)
    with ThreadPoolExecutor(max_workers=8) as pool: ids=list(pool.map(lambda _:service.create_task(req)['id'],range(8)))
    assert len(set(ids))==1 and len(launches)==1
    service.recover()
    assert store.get('task',ids[0])['status']=='interrupted'
    assert not store.get('task',ids[0])['job_ids']


def test_gpu_unavailable_never_silently_uses_cpu(client,monkeypatch):
    monkeypatch.delenv('STRENGTH_CCX_CUDA',raising=False)
    with pytest.raises(ValueError,match='未安装'): backends.resolve_backend('calculix_cuda')
    cap=backends.capabilities()
    assert not next(b for b in cap['backends'] if b['id']=='calculix_cuda')['available']
    assert backends.resolve_backend('calculix_cpu').environment()['PASTIX_GPU']=='0'


def test_api_adapter_uses_strict_schema_without_tools(monkeypatch,tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY','unit-test-key');monkeypatch.setenv('OPENAI_MODEL','configured-test-model')
    decision=AgentDecision(action='clarify',message='缺少载荷',questions=['载荷多少？'],cases=[],compare_with_previous=False)
    def post(url,**kwargs):
        data=kwargs['json'];assert data['model']=='configured-test-model' and data['store'] is False
        assert data['text']['format']['strict'] is True and 'tools' not in data
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'output':[{'content':[{'type':'output_text','text':decision.model_dump_json()}]}]}
        return Response()
    monkeypatch.setattr(agent.httpx,'post',post)
    assert agent.api_plan({'conversation':[]},tmp_path,lambda:False).action=='clarify'


def test_missing_direction_and_inactive_displacement_rejected(models):
    from strength.geometry import resolve_selection
    from test_physics import axial_case
    from strength.schemas import LoadCase
    with pytest.raises(ValueError,match='缺少'): resolve_selection(Selection(kind='direction'),models['bar'])
    c=axial_case().model_dump();c['loads']=[]
    c['supports'][0]['displacement_m']=[0,1e-5,0]
    with pytest.raises(ValueError,match='非零'): LoadCase.model_validate(c)
