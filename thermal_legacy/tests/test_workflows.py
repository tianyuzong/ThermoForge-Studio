import copy,json,threading
from pathlib import Path
import numpy as np
import pytest
import trimesh
import thermal_legacy.agent as agent; import thermal_legacy.workflows as workflows; import thermal_legacy.workflow_report as workflow_report; import thermal_legacy.agent_skill as agent_skill
from thermal_legacy.runtime import write_json, read_json
from thermal_legacy.schemas import Simulation
from thermal_legacy.workflow_agent import Blueprint, WorkflowRequest
from thermal_legacy.host_geometry import orient_faces, model_evidence
from test_agent_skill import blocks


def test_orientation_repairs_closed_shell_preserving_face_identity():
    m=trimesh.creation.box(extents=[.08,.036,.01]);faces=m.faces.copy();faces[::2]=faces[::2][:,[0,2,1]]
    fixed,e=orient_faces(m.vertices,faces)
    repaired=trimesh.Trimesh(m.vertices,fixed,process=False)
    assert e['closed'] and e['orientation_verified'] and e['reoriented_faces']>0
    assert repaired.is_winding_consistent and repaired.volume==pytest.approx(m.volume)
    np.testing.assert_array_equal(np.sort(fixed,axis=1),np.sort(faces,axis=1))
    np.testing.assert_array_equal(faces[1],m.faces[1])


def test_open_surface_is_not_claimed_verified():
    m=trimesh.creation.box();_,e=orient_faces(m.vertices,m.faces[:-1]);assert not e['closed'] and not e['orientation_verified']


def test_agent_gets_real_selection_area_and_capabilities(blocks):
    cfg,_=blocks
    prepared=agent_skill.prepare(agent,'blocks','顶部面热源20W。',cfg)
    public=prepared['public']
    assert public['available_selections']['top']['area_m2']==pytest.approx(2*.02*.02)
    assert '全部转折点' in public['host_capabilities']['output_times']
    cycle=public['host_capabilities']['same_position_cycle']
    assert cycle['available'] and '总正应变' in cycle['total_strain'] and '机械弹性' in cycle['elastic_strain']
    assert 'Simulation' in cycle['owner']
    assert public['host_geometry_evidence']['topology']['closed']


@pytest.mark.parametrize('refs',[['unknown',None],[None,'b'],['a',None]])
def test_invalid_plan_dependencies_rejected(refs):
    with pytest.raises(ValueError):Blueprint.model_validate(dict(name='bad',questions=[],limitations=[],cases=[dict(key='a',name='a',prompt='x',inherit_from=refs[0],initial_from=refs[1])]))


@pytest.fixture
def store(monkeypatch,tmp_path):
    jobs=tmp_path/'jobs';jobs.mkdir();wdir=tmp_path/'workflows';wdir.mkdir()
    monkeypatch.setattr(workflows,'JOBS',jobs);monkeypatch.setattr(workflow_report,'JOBS',jobs);monkeypatch.setattr(workflows,'WORKFLOWS',wdir)
    wid='a'*32;p=wdir/wid;p.mkdir();monkeypatch.setattr(workflows,'THREADS',{wid:threading.current_thread()})
    cfg=Simulation(model_id='test',environment_only=True,initial_C=20,ambient_C=25,duration_s=2,dt_s=1,save_s=1,mesh_size_m=.01).model_dump()
    return wid,p,jobs,cfg


def persist(store,cases,phase='running'):
    wid,p,_,_=store;write_json(p/'workflow.json',dict(id=wid,name='workflow',phase=phase,model_id='test',
        cases=cases,cancel_requested=False,plan_complete=True,geometry={},limitations=[]))


def row(key,cfg,initial=None,inherit=None):
    return dict(key=key,name=key,prompt='test',config=copy.deepcopy(cfg),initial_from=initial,inherit_from=inherit,status='ready',reused=False,job_id=None)


def write_result(jobs,jid,cfg,initial=20):
    p=jobs/jid;p.mkdir(exist_ok=True);write_json(p/'config.json',cfg);write_json(p/'status.json',dict(phase='completed',progress=100))
    times=sorted({0,2,*[x['time_s'] for x in cfg.get('ambient_profile',[])]})
    write_json(p/'audit.json',dict(stats=[dict(time_s=t) for t in times],energy={'energy_balance_error_J':0},warnings=[]))
    np.savez(p/'mesh.npz',points=np.array([[0.,0,0],[1,0,0],[0,1,0],[0,0,1]]),tets=np.array([[0,1,2,3]]))
    np.save(p/'temperatures.npy',np.array([[initial]*4,[initial+1]*4],dtype=np.float32));(p/'report.pdf').write_bytes(b'%PDF-test')


def test_workflow_executes_dependent_case_with_agent_binding_and_reuses_baseline(store,monkeypatch):
    wid,p,jobs,cfg=store
    base=copy.deepcopy(cfg);base['analysis_mode']='steady';write_result(jobs,'parent',base)
    cases=[row('base',base),row('fan',cfg,initial='base')];persist(store,cases)
    calls=[];bindings=[]
    def bind(mid,prompt,current):
        bindings.append(prompt);return dict(ok=True,config={**current,'initial_from_job':'parent'})
    def submit(config):
        calls.append(config.model_dump());write_result(jobs,'child',config.model_dump(),initial=21);return {'id':'child'}
    monkeypatch.setattr(agent,'plan_request',bind);monkeypatch.setattr(workflow_report,'generate',lambda *a:None)
    workflows.execute(wid,submit,lambda j:dict(phase='completed'),lambda j:None)
    w=read_json(p/'workflow.json')
    assert w['phase']=='completed' and w['cases'][0]['reused'],[c.get('detail') for c in w['cases']]
    assert len(calls)==1 and calls[0]['initial_from_job']=='parent' and 'parent' in bindings[0]
    assert w['cases'][1]['verification']['identical_mesh']
    assert read_json(jobs/'parent/config.json')['initial_from_job'] is None


def test_failed_result_skips_temperature_dependency_but_not_independent_cycle(store,monkeypatch):
    wid,p,jobs,cfg=store;persist(store,[row('base',cfg),row('fan',cfg,initial='base'),row('cycle',cfg,inherit='base')])
    submitted=[]
    def submit(c):
        jid='job'+str(len(submitted));submitted.append(jid)
        if len(submitted)>1:write_result(jobs,jid,c.model_dump())
        return {'id':jid}
    def status(j):return dict(phase='failed' if j=='job0' else 'completed')
    monkeypatch.setattr(workflow_report,'generate',lambda *a:None)
    workflows.execute(wid,submit,status,lambda j:None)
    w=read_json(p/'workflow.json');assert w['phase']=='partial'
    assert [c['status'] for c in w['cases']]==['failed','skipped','completed'],[c.get('detail') for c in w['cases']]
    assert len(submitted)==2


def test_mismatching_initial_field_fails_verification(store):
    _,_,jobs,cfg=store;write_result(jobs,'a',cfg);write_result(jobs,'b',cfg,initial=100)
    with pytest.raises(ValueError,match='完整父温度场'):workflows.verify_result('b',cfg,'a')


def test_missing_profile_turning_point_fails_verification(store):
    _,_,jobs,cfg=store;cfg['ambient_profile']=[dict(time_s=0,ambient_C=20),dict(time_s=1,ambient_C=30),dict(time_s=2,ambient_C=20)]
    write_result(jobs,'a',cfg);a=read_json(jobs/'a/audit.json');a['stats']=[dict(time_s=0),dict(time_s=2)];write_json(jobs/'a/audit.json',a)
    with pytest.raises(ValueError,match='转折点'):workflows.verify_result('a',cfg)


def test_restart_retains_cases_and_marks_interrupted(store,monkeypatch):
    wid,p,_,cfg=store;persist(store,[row('base',cfg)]);monkeypatch.setattr(workflows,'THREADS',{})
    state=workflows.get(wid);assert state['phase']=='interrupted' and 'config' not in state['cases'][0]
    assert 'config' in read_json(p/'workflow.json')['cases'][0]


def test_signature_reuse_does_not_ignore_physics(store):
    _,_,jobs,cfg=store;write_result(jobs,'a',cfg)
    same={**cfg,'name':'renamed'};assert workflows.reusable(same)=='a'
    assert workflows.reusable({**cfg,'ambient_C':100}) is None
    assert workflows.reusable({**cfg,'dt_s':.5}) is None


def test_incomplete_plan_cannot_run(store):
    wid,p,_,cfg=store;persist(store,[row('a',cfg)],phase='failed');w=read_json(p/'workflow.json');w['plan_complete']=False;write_json(p/'workflow.json',w)
    with pytest.raises(ValueError,match='尚未通过'):workflows.start(wid,lambda *a:None,lambda *a:None,lambda *a:None)


def test_cancel_preserves_completed_jobs_and_prevents_new_work(store,monkeypatch):
    wid,p,jobs,cfg=store;persist(store,[row('base',cfg)]);workflows.cancel(wid)
    calls=[];workflows.execute(wid,lambda c:calls.append(c),lambda j:None,lambda j:None)
    assert not calls and read_json(p/'workflow.json')['phase']=='cancelled'


def test_workflow_identifier_cannot_escape_storage(store):
    with pytest.raises(ValueError):workflows.folder('../jobs')


def test_forward_dependencies_are_topologically_ordered():
    b=Blueprint.model_validate(dict(name='valid',questions=[],limitations=[],cases=[
        dict(key='fine',name='fine',prompt='6mm',inherit_from='base',initial_from=None),
        dict(key='base',name='base',prompt='8mm',inherit_from=None,initial_from=None)]))
    assert [c.key for c in b.cases]==['base','fine']


def test_current_is_a_configuration_baseline_not_a_fabricated_result():
    b=Blueprint.model_validate(dict(name='valid',questions=[],limitations=[],cases=[
        dict(key='base',name='base',prompt='8mm',inherit_from='current',initial_from=None)]))
    assert b.cases[0].inherit_from is None
    with pytest.raises(ValueError,match='未知'):
        Blueprint.model_validate(dict(name='invalid',questions=[],limitations=[],cases=[
            dict(key='base',name='base',prompt='8mm',inherit_from=None,initial_from='current')]))
