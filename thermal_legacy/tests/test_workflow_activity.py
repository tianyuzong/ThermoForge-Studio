import json
import pytest
import thermal_legacy.agent as agent; import thermal_legacy.workflows as workflows
from thermal_legacy.runtime import read_json
from thermal_legacy.workflow_agent import Blueprint, WorkflowRequest
from test_workflows import store,persist,row
from thermal_legacy.scenario_cases import update_case_parameters


def request_and_plan(store,monkeypatch):
    wid,p,jobs,cfg=store;persist(store,[],phase='planning')
    spec=dict(key='base',name='稳态基准',prompt='沿用配置',inherit_from=None,initial_from=None)
    blueprint=Blueprint.model_validate(dict(name='测试计划',cases=[spec],questions=[],limitations=[]))
    def plan(*args):
        events=read_json(p/'workflow.json')['activity']
        assert events[-1]['actor']=='agent' and events[-1]['status']=='started'
        return blueprint
    monkeypatch.setattr(workflows,'plan',plan)
    return WorkflowRequest(model_id='test',prompt='沿用当前配置',config=cfg)


def test_activity_records_real_agent_request_and_returned_changes(store,monkeypatch):
    request=request_and_plan(store,monkeypatch);wid,p,jobs,cfg=store
    def review(*args):
        last=read_json(p/'workflow.json')['activity'][-1]
        assert (last['actor'],last['stage'],last['status'])==('agent','configuration','started')
        return dict(ok=True,config=cfg,changes=['网格：6 mm'],questions=[],warnings=[],reasoning='PRIVATE_REASONING')
    monkeypatch.setattr(agent,'codex_plan_request',review)
    workflows.prepare(wid,request)
    w=workflows.get(wid)
    events=w['activity'];assert w['phase']=='ready' and len(w['planned_cases'])==1
    done=[e for e in events if e['actor']=='agent' and e['stage']=='configuration' and e['status']=='completed']
    assert done[0]['details']==['网格：6 mm']
    assert all(e['at']>0 for e in events) and len({e['id'] for e in events})==len(events)
    assert 'PRIVATE_REASONING' not in json.dumps(events)
    assert events[-1]['actor']=='host' and events[-1]['stage']=='confirmation'


def test_agent_questions_are_preserved_without_false_success(store,monkeypatch):
    request=request_and_plan(store,monkeypatch);wid,p,jobs,cfg=store
    monkeypatch.setattr(agent,'codex_plan_request',lambda *a:dict(ok=False,questions=['模块接触区域在哪里？'],changes=[],warnings=[]))
    workflows.prepare(wid,request)
    w=workflows.get(wid)
    assert w['phase']=='needs_input' and w['cases'][0]['questions']==['模块接触区域在哪里？']
    assert w['activity'][-1]['status']=='needs_input'
    assert not any(e['stage']=='configuration' and e['status']=='completed' for e in w['activity'])


def test_agent_error_is_recorded_as_failure_not_a_completed_call(store,monkeypatch):
    request=request_and_plan(store,monkeypatch);wid,p,jobs,cfg=store
    def fail(*a):raise ValueError('连接超时')
    monkeypatch.setattr(agent,'codex_plan_request',fail)
    workflows.prepare(wid,request)
    w=workflows.get(wid)
    assert w['phase']=='failed' and w['activity'][-1]['actor']=='host'
    assert w['activity'][-1]['status']=='failed' and '连接超时' in w['activity'][-1]['text']


def test_old_task_is_read_without_fabricating_event_timestamps(store):
    wid,p,jobs,cfg=store;persist(store,[row('base',cfg)],phase='completed')
    original=(p/'workflow.json').read_bytes()
    assert 'activity' not in workflows.get(wid)
    assert (p/'workflow.json').read_bytes()==original


def test_events_do_not_replace_case_configuration(store):
    wid,p,jobs,cfg=store;persist(store,[row('base',cfg)],phase='ready')
    workflows.activity(wid,'host','verification','已核验',details=['面积有效'])
    raw=read_json(p/'workflow.json')
    assert raw['cases'][0]['config']==cfg
    assert 'config' not in workflows.get(wid)['cases'][0]


def test_planning_clears_stale_result_ids_without_cancelling_temperature_dependencies(store,monkeypatch):
    wid,p,jobs,cfg=store;persist(store,[],phase='planning')
    cfg={**cfg,'initial_from_job':'old_result'}
    request=WorkflowRequest(model_id='test',prompt='两个停风工况继承基准；环境循环独立初温',config=cfg)
    blueprint=Blueprint.model_validate(dict(name='依赖校验',questions=[],limitations=[],cases=[
        dict(key='base',name='基准',prompt='沿用配置',inherit_from=None,initial_from=None),
        dict(key='fan5',name='停风5秒步长',prompt='继承基准完整温度场',inherit_from='base',initial_from='base'),
        dict(key='fan2',name='停风2秒步长',prompt='独立继承基准完整温度场',inherit_from='base',initial_from='base'),
        dict(key='cycle',name='环境循环',prompt='无热源，独立初温',inherit_from='base',initial_from=None)]))
    calls=[]
    def review(mid,prompt,current,conversation):
        context=conversation['workflow_context'];dependency=context['deferred_initial_from']
        if dependency:
            assert '清除续算' not in prompt
            assert '清除旧结果绑定' in prompt and '不取消上述计划依赖' in prompt
            assert '保留计划内温度场依赖' in context['binding_policy']
        else:
            assert '清除续算' in prompt and '没有计划内温度场依赖' in prompt
        candidate={**current,'initial_from_job':'another_stale_result'}
        update_case_parameters(agent,mid,prompt,candidate,[])
        assert candidate['initial_from_job'] is None
        calls.append(dependency)
        return dict(ok=True,config=candidate,changes=[],questions=[],warnings=[])
    monkeypatch.setattr(agent,'codex_plan_request',review)
    workflows.prepare(wid,request,blueprint)
    w=workflows.get(wid)
    assert w['phase']=='ready' and calls==[None,'base','base',None]
    assert [c['initial_from'] for c in w['cases']]==[None,'base','base',None]
    assert all(c['config']['initial_from_job'] is None for c in read_json(p/'workflow.json')['cases'])
    assert cfg['initial_from_job']=='old_result'
