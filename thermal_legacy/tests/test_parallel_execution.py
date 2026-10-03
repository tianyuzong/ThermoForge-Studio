import copy
import pytest
from fastapi import HTTPException
import thermal_legacy.compute_policy as compute_policy; import thermal_legacy.workflows as workflows; import thermal_legacy.workflow_report as workflow_report; import thermal_legacy.agent as agent
from thermal_legacy.runtime import read_json
from test_workflows import store,persist,row,write_result


def test_memory_and_slot_limits_are_enforced(monkeypatch):
    gib=1024**3
    monkeypatch.delenv('THERMAL_MAX_PARALLEL_JOBS',raising=False)
    monkeypatch.setattr(compute_policy.os,'cpu_count',lambda:12)
    monkeypatch.setattr(compute_policy,'memory_bytes',lambda:(64*gib,40*gib))
    assert compute_policy.execution_policy()['max_parallel_jobs']==2
    assert compute_policy.can_admit_job(1)
    assert not compute_policy.can_admit_job(2)
    monkeypatch.setattr(compute_policy,'memory_bytes',lambda:(64*gib,12*gib))
    assert not compute_policy.can_admit_job(1)
    assert compute_policy.can_admit_job(0)
    monkeypatch.setattr(compute_policy,'memory_bytes',lambda:(None,None))
    assert compute_policy.execution_policy()['max_parallel_jobs']==1


def test_worker_thread_limits_preserve_other_environment(monkeypatch):
    monkeypatch.setenv('THERMAL_DEVICE','cpu')
    monkeypatch.setenv('THERMAL_CPU_THREADS','2')
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','12')
    env=compute_policy.worker_environment()
    assert env['THERMAL_DEVICE']=='cpu'
    assert all(env[k]=='2' for k in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS'))
    assert compute_policy.os.environ['OPENBLAS_NUM_THREADS']=='12'


def setup_parallel(monkeypatch):
    monkeypatch.setattr(workflows,'execution_policy',lambda:{'max_parallel_jobs':2})
    monkeypatch.setattr(workflows,'reusable',lambda cfg:None)
    monkeypatch.setattr(workflow_report,'generate',lambda *a:None)


def test_independent_jobs_overlap_and_children_wait_for_verified_parent(store,monkeypatch):
    wid,p,jobs,cfg=store;setup_parallel(monkeypatch)
    rows=[row('base',cfg),row('fan5',cfg,initial='base'),row('fan2',cfg,initial='base'),row('cycle',cfg)]
    for c in rows:c['config']['name']=c['key']
    persist(store,rows)
    tick=[0];running={};submitted=[];verified=[];bound=[];overlap=[]
    real_verify=workflows.verify_result
    def verify(jid,*args):
        result=real_verify(jid,*args);verified.append(jid);return result
    def bind(mid,prompt,current):
        assert 'base' in verified
        bound.append(current['name']);return {'ok':True,'config':{**current,'initial_from_job':'base'}}
    def submit(config):
        jid=config.name
        if jid.startswith('fan'):assert 'base' in verified and config.initial_from_job=='base'
        submitted.append(jid);running[jid]=(tick[0],config.model_dump())
        overlap.append(set(running));assert len(running)<=2
        return {'id':jid}
    def status(jid):
        start,current=running[jid]
        if tick[0]-start<(2 if jid=='base' else 1):return {'phase':'running','progress':50}
        write_result(jobs,jid,current,initial=21 if jid.startswith('fan') else 20)
        del running[jid];return {'phase':'completed'}
    monkeypatch.setattr(workflows,'verify_result',verify)
    monkeypatch.setattr(agent,'plan_request',bind)
    monkeypatch.setattr(workflows.time,'sleep',lambda s:tick.__setitem__(0,tick[0]+1))
    workflows.execute(wid,submit,status,lambda jid:None)
    w=read_json(p/'workflow.json')
    assert w['phase']=='completed',w
    assert {'base','cycle'} in overlap and {'fan5','fan2'} in overlap
    assert sorted(bound)==['fan2','fan5'] and len(submitted)==4
    assert all(c['verification']['identical_mesh'] for c in w['cases'][1:3])


def test_cancellation_stops_all_parallel_jobs(store,monkeypatch):
    wid,p,_,cfg=store;setup_parallel(monkeypatch)
    persist(store,[row('one',cfg),row('two',cfg),row('three',cfg)])
    calls=[];stopped=[]
    def submit(config):
        jid=str(len(calls));calls.append(jid);return {'id':jid}
    monkeypatch.setattr(workflows.time,'sleep',lambda s:workflows.cancel(wid))
    workflows.execute(wid,submit,lambda jid:{'phase':'running'},stopped.append)
    w=read_json(p/'workflow.json')
    assert w['phase']=='cancelled' and calls==stopped==['0','1']
    assert all(c['status']=='cancelled' for c in w['cases'])


def test_busy_admission_retries_without_submitting_duplicates(store,monkeypatch):
    wid,p,jobs,cfg=store;setup_parallel(monkeypatch);persist(store,[row('one',cfg)])
    attempts=[]
    def submit(config):
        attempts.append(1)
        if len(attempts)==1:raise HTTPException(409,'已有算例正在计算，等待并行槽位或可用内存')
        write_result(jobs,'one',config.model_dump());return {'id':'one'}
    monkeypatch.setattr(workflows.time,'sleep',lambda s:None)
    workflows.execute(wid,submit,lambda jid:{'phase':'completed'},lambda jid:None)
    assert len(attempts)==2 and read_json(p/'workflow.json')['phase']=='completed'


def test_server_admission_applies_global_limit_across_workflows(tmp_path,monkeypatch):
    import thermal_legacy.server as server
    from thermal_legacy.schemas import Simulation
    jobs=tmp_path/'jobs';jobs.mkdir();model=tmp_path/'model';model.mkdir()
    from thermal_legacy.runtime import write_json
    write_json(model/'metadata.json',{'mesh_ready':True})
    monkeypatch.setattr(server,'JOBS',jobs)
    monkeypatch.setattr(server,'validate_geometry',lambda cfg:model)
    monkeypatch.setattr(server,'can_admit_job',lambda n:n<2)
    monkeypatch.setattr(server,'processes',{})
    class Process:
        def poll(self):return None
    monkeypatch.setattr(server,'spawn',lambda kind,jid:server.processes.__setitem__(jid,Process()))
    cfg=Simulation(model_id='test',environment_only=True)
    server.run(cfg);server.run(cfg)
    with pytest.raises(HTTPException) as error:server.run(cfg)
    assert error.value.status_code==409 and len(list(jobs.iterdir()))==2
