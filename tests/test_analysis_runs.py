import json,hashlib
from pathlib import Path
from types import SimpleNamespace
import numpy as np,h5py,pytest
from strength import store
from strength.config import DATA
from forge import runs
from forge.schemas import AnalysisCase
from forge.provenance import case_signature,file_hash


@pytest.fixture
def run_env(monkeypatch,tmp_path):
    monkeypatch.setattr(runs,'DATA',tmp_path)
    monkeypatch.setattr(runs,'solver_signature',lambda:'solver-v1')
    monkeypatch.setattr(runs,'launch',lambda id:None)
    monkeypatch.setattr(runs,'build_nodes',lambda signature:[{'key':'static','status':'pending','case':{},'study_id':None}])
    return tmp_path


def submit(token,force=False,prompt='运行全套分析'):
    return runs.create(runs.RunRequest(request_id=token,prompt=prompt,force=force,template='motor-bearing'),SimpleNamespace(base_url='http://127.0.0.1:8877/'))


def test_idempotency_and_active_dedup(run_env):
    a=submit('a');assert submit('a')['id']==a['id'];assert submit('b')['id']==a['id']
    store.update('analysis_run',a['id'],status='completed')
    assert submit('c')['id']!=a['id']


def test_prompt_does_not_ignore_custom_conditions(run_env):
    for prompt in ('运行全套分析，改为 2000N','只运行全套分析中的热学','运行全套分析，把力增加一倍','你好'):
        with pytest.raises(ValueError):submit(store.uid(),True,prompt)
    assert runs.intent('强制重新计算全套分析')
    assert not runs.intent('运行电机全套分析，包含力学、热学、疲劳、热结构耦合和局部轴承接触，生成动画、视频和中文报告。')


def test_cancel_only_owned_studies(run_env,monkeypatch):
    run=submit(store.uid(),True)
    for id in ('owned','reused','unrelated'):store.put('study',{'id':id,'status':'solving'})
    store.update('analysis_run',run['id'],owned_studies=['owned'])
    called=[];monkeypatch.setattr(runs.api,'cancel',lambda sid,cascade:called.append(sid) if not cascade else pytest.fail('cancel escaped run ownership'))
    runs.cancel(run['id']);assert called==['owned'];assert runs.get(run['id'])['cancel_requested']


def test_recovery_and_resume_generation(run_env):
    run=submit(store.uid(),True);runs.recover();assert runs.get(run['id'])['status']=='interrupted'
    resumed=runs.resume(run['id']);assert resumed['generation']==1 and not resumed['cancel_requested']
    store.update('analysis_run',run['id'],status='cancelled');runs.recover();assert runs.get(run['id'])['status']=='cancelled'


def test_resume_rejects_changed_solver(run_env,monkeypatch):
    run=submit(store.uid(),True);store.update('analysis_run',run['id'],status='failed')
    monkeypatch.setattr(runs,'solver_signature',lambda:'solver-v2')
    with pytest.raises(ValueError,match='版本'):runs.resume(run['id'])


def test_study_failure_keeps_independent_nodes(run_env,monkeypatch):
    run=submit(store.uid(),True);store.update('analysis_run',run['id'],nodes=[{'key':str(i),'status':'pending'} for i in range(6)])
    visited=[]
    def solve(id,i):
        visited.append(i)
        if i==1:raise ValueError('invalid material')
        n=runs.get(id)['nodes'][i];n['status']='completed';runs.save_node(id,i,n)
    monkeypatch.setattr(runs,'solve_node',solve)
    class Process:
        returncode=0
        def poll(self):return 0
    monkeypatch.setattr(runs.subprocess,'Popen',lambda *a,**k:Process())
    runs.execute(run['id']);result=runs.get(run['id'])
    assert visited==list(range(6));assert result['status']=='partial';assert result['nodes'][1]['status']=='failed'


def test_result_integrity_and_configuration(run_env):
    c=AnalysisCase.model_validate_json((Path(__file__).parents[1]/'examples/motor-unified/static.json').read_text(encoding='utf-8'))
    geo={'id':'geo','source_sha256':'abc','components':[],'surfaces':[]};c.geometry_sha256='abc'
    source=run_env/'studies'/'s';source.mkdir(parents=True)
    points=np.zeros((10,3));tets=np.arange(10).reshape(1,10)
    np.savez(source/'mesh.npz',points=points,tets=tets)
    with h5py.File(source/'fields.h5','w') as f:
        f['points']=points;f['tets']=tets;f['times_s']=[0.]
    config={'case':c.model_dump(),'solver_signature':'solver-v1','case_signature':case_signature(c,geo)}
    config['sha256']=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    store.write_json(source/'config.json',config);store.write_json(source/'raw-fields.sha256.json',{'sha256':file_hash(source/'fields.h5')})
    for name in ('summary.json','report.pdf','metrics.csv','audit.json'):(source/name).write_text('test',encoding='utf-8')
    study={'id':'s','status':'completed','model_id':'geo','summary':{'frames':1}}
    store.write_json(source/'summary.json',study['summary'])
    assert runs.intact(study,c,geo,'solver-v1')
    assert not runs.intact(study,c,geo,'solver-v2')
    c.loads[0].vector[2]*=2;assert not runs.intact(study,c,geo,'solver-v1')
    c.loads[0].vector[2]/=2
    (source/'report.pdf').unlink();(source/'metrics.csv').unlink()
    assert runs.intact(study,c,geo,'solver-v1')  # exports can be recreated without a solve
    with (source/'fields.h5').open('ab') as f:f.write(b'corrupt')
    assert not runs.intact(study,c,geo,'solver-v1')


def test_file_path_cannot_escape_run(run_env):
    run=submit(store.uid(),True);outside=run_env/'secret.json';outside.write_text('{}')
    with pytest.raises(Exception) as e:runs.files(run['id'],'../../secret.json')
    assert e.value.status_code==404


def test_derived_results_do_not_upgrade_old_solver_provenance(run_env):
    source=run_env/'studies'/'parent';source.mkdir(parents=True)
    def parent(signature):
        c={'solver_signature':signature}
        c['sha256']=hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()
        store.write_json(source/'config.json',c)
    derived={'solver_signature':'solver-v1','reprocess_from':'parent'}
    parent(None);assert not runs.source_versions_match(derived,'solver-v1')
    parent('solver-v0');assert not runs.source_versions_match(derived,'solver-v1')
    parent('solver-v1');assert runs.source_versions_match(derived,'solver-v1')
    derived['resume_from']='missing';assert not runs.source_versions_match(derived,'solver-v1')


def test_interrupted_refinement_resumes_its_child(run_env,monkeypatch):
    from forge import run_validation
    case=AnalysisCase.model_validate_json((Path(__file__).parents[1]/'examples/motor-unified/thermo-contact-transient.json').read_text(encoding='utf-8'))
    geo=store.put('model',{'id':'refine-geo','source_sha256':'abc','components':[],'surfaces':[]})
    coarse=store.put('study',{'id':'coarse','status':'completed','model_id':geo['id'],'case':case.model_dump()})
    run=submit(store.uid(),True)
    node={'key':'thermo_mechanical','status':'running','model_id':geo['id'],'project_id':'p','case':case.model_dump(),'study_id':'coarse',
          'refinement_coarse_id':'coarse','refinements':[],'preset_key':'test','reused':False}
    store.update('analysis_run',run['id'],nodes=[node],owned_studies=['coarse'])
    monkeypatch.setattr(runs,'intact',lambda s,c,g,v:s['status']=='completed' and s['case']['thermal']['dt_s']==c.thermal.dt_s)
    monkeypatch.setattr(runs,'valid_validation',lambda v:bool(v and v.get('passed')))
    calls=[]
    def own(id,index,c,tag,resume_from=None):
        calls.append((c.thermal.dt_s,resume_from))
        return store.put('study',{'id':'fine-'+str(len(calls)),'status':'queued','model_id':geo['id'],'case':c.model_dump()})
    monkeypatch.setattr(runs,'own_study',own)
    def interrupt(id,study):raise InterruptedError('service stopped')
    monkeypatch.setattr(runs,'wait_study',interrupt)
    with pytest.raises(InterruptedError):runs.solve_node(run['id'],0)
    active=runs.get(run['id'])['nodes'][0]['active_refinement']
    assert active['study_id']=='fine-1' and active['dt_s']==15
    store.update('study','fine-1',status='interrupted')
    monkeypatch.setattr(runs,'wait_study',lambda id,s:store.update('study',s['id'],status='completed'))
    monkeypatch.setattr(run_validation,'compare',lambda *a:{'passed':True})
    runs.solve_node(run['id'],0)
    result=runs.get(run['id'])['nodes'][0]
    assert calls==[(15,None),(15,'fine-1')]
    assert result['status']=='completed' and result['study_id']=='fine-2'
    assert result['active_refinement'] is None and result['refinement_level']==1


def test_fresh_fine_result_requires_its_own_comparison(run_env,monkeypatch):
    from forge import run_validation
    case=AnalysisCase.model_validate_json((Path(__file__).parents[1]/'examples/motor-unified/thermo-contact-transient.json').read_text(encoding='utf-8'))
    coarse=store.put('study',{'id':'reference-coarse','status':'completed','case':case.model_dump()})
    case.thermal.dt_s=15
    fine={'id':'new-fine','case':case.model_dump()}
    node={'validation':{'passed':True,'coarse':coarse['id'],'fine':'previous-fine'}}
    monkeypatch.setattr(runs,'valid_validation',lambda value:bool(value and value.get('passed')))
    monkeypatch.setattr(runs,'intact',lambda s,c,g,v:c.thermal.dt_s==30)
    calls=[]
    def compare(a,b):
        calls.append((a.name,b.name));return {'passed':True,'coarse':a.name,'fine':b.name}
    monkeypatch.setattr(run_validation,'compare',compare)
    bound=runs.bind_validation(node,fine,{},'solver-v1')
    assert calls==[('reference-coarse','new-fine')] and bound['fine']=='new-fine'
    monkeypatch.setattr(runs,'intact',lambda *a:False)
    assert runs.bind_validation(node,fine,{},'solver-v1') is None
