import json
import numpy as np
import pytest
from strength.config import DATA
from strength.geometry import build_mesh
from strength.fem import assemble_loads, build_constraints
from strength.solver import write_deck,run_ccx,parse_dat,assess
from strength.gpu_solver import solve_gpu,clear_cache
from strength.cuda_runtime import self_test, runtime
from strength.schemas import LoadCase,DEMO_MATERIAL
from test_physics import axial_case,selection

def test_gpu_selftest():assert self_test()['passed']

@pytest.mark.parametrize('kind',['force','pressure','constrained_pressure','gravity','displacement','beam','bracket'])
def test_gpu_matches_calculix(models,tmp_path,kind):
    clear_cache();c=axial_case();model='bar'
    if kind=='pressure':c.loads[0]=type(c.loads[0])(kind='pressure',selection=selection('+x'),pressure_Pa=1e7)
    if kind=='constrained_pressure':c.loads[0]=type(c.loads[0])(kind='pressure',selection=selection('-x'),pressure_Pa=1e7)
    if kind=='gravity':c.loads[0]=type(c.loads[0])(kind='gravity',vector=[9.80665,0,0])
    if kind=='displacement':
        c.loads=[];c.supports.append(type(c.supports[0])(selection=selection('+x'),components=['x'],displacement_m=[1e-5,0,0]))
    if kind=='beam':
        model='beam';c=LoadCase(material=DEMO_MATERIAL,supports=[{'selection':selection('-x')}],loads=[{'kind':'force','selection':selection('+x'),'vector':[0,0,-1]}],mesh={'size_m':.004})
    if kind=='bracket':
        model='bracket';c=LoadCase(material=DEMO_MATERIAL,supports=[{'selection':{'kind':'named','name':'安装孔'}}],loads=[{'kind':'force','selection':{'kind':'named','name':'顶面'},'vector':[0,0,-1000]}],mesh={'size_m':.005})
    info=build_mesh(DATA/'models'/model,c,tmp_path/'mesh.npz');m=dict(np.load(tmp_path/'mesh.npz'))
    f,a=assemble_loads(m,c,models[model]);fixed,groups=build_constraints(m,c,models[model])
    write_deck(tmp_path,m,c,f,fixed);run_ccx(tmp_path);cpu=parse_dat(tmp_path/'analysis.dat',len(m['points']),len(m['tets']))
    gpu,meta=solve_gpu(m,c,f,fixed)
    errs={}
    for field,relative,absolute in [('displacements',1e-6,1e-10),('stresses',1e-5,1.)]:
        err=np.linalg.norm(cpu[field]-gpu[field]);ref=np.linalg.norm(cpu[field]);errs[field]=float(err/max(ref,1e-30))
        assert err<=max(relative*ref,absolute*np.sqrt(cpu[field].size)),errs
    summary,reaction=assess(m,c,gpu,f,fixed,groups,a,info)
    assert summary['balance']['passed'],summary['balance']
    if kind=='force':assert abs(summary['maximum_von_mises_Pa']/1e7-1)<.01
    if kind=='beam':
        exact=.2**3/(3*210e9*(.02*.01**3/12));assert abs(abs(gpu['displacements'][:,2].min())/exact-1)<.03
    reused,again=solve_gpu(m,c,f*1.5,{k:v*1.5 for k,v in fixed.items()})
    assert again['factorization_reused']
    np.testing.assert_allclose(reused['displacements'],gpu['displacements']*1.5,rtol=1e-7,atol=1e-13)
    print(kind,json.dumps({'errors':errs,'balance':summary['balance'],'execution':meta},ensure_ascii=False))
    clear_cache()

def test_memory_budget_rejects():
    r=runtime()
    with pytest.raises(MemoryError):r.check_memory(r.budget+1)

def test_singular_matrix_is_not_regularized():
    from strength.cuda_runtime import CuDSS
    r=runtime();p=r.array(np.array([0,2,4],np.int32));c=r.array(np.array([0,1,0,1],np.int32));v=r.array(np.ones(4))
    s=CuDSS(r,p,c,v,2)
    try:
        s.analyze()
        with pytest.raises((ValueError,RuntimeError)):s.factor()
    finally:s.close();p.close();c.close();v.close()

def test_gpu_agent_auto_clarify_draft_reuse_and_cancel(client,models):
    import time,uuid
    from test_agent_api import project,send,wait
    from strength import service,store
    p=project(client,models)
    task,_=send(client,p,'使用 GPU，安装孔固定。')
    task=wait(client,task['id']);assert task['status']=='needs_input' and task['compute_backend']=='cudss_cuda'
    payload={'prompt':'先给方案：顶面向下 1000 N，比较 1500 N。','request_id':uuid.uuid4().hex}
    client.post('/api/v1/agent/tasks/'+task['id']+'/messages',json=payload)
    task=wait(client,task['id']);assert task['status']=='draft' and not task['job_ids']
    payload={'prompt':'开始计算','request_id':uuid.uuid4().hex}
    client.post('/api/v1/agent/tasks/'+task['id']+'/messages',json=payload);client.post('/api/v1/agent/tasks/'+task['id']+'/messages',json=payload)
    task=wait(client,task['id'],180);assert task['status']=='completed',task
    a,b=task['jobs'];assert a['summary']['execution']['backend']=='cudss_cuda';assert b['summary']['execution']['factorization_reused']
    assert abs(b['summary']['maximum_displacement_m']/a['summary']['maximum_displacement_m']-1.5)<1e-7
    service.THREADS[task['id']].join(15);assert task['id'] not in service.GPU_SLOTS and task['id'] not in service.RUNNING
    service.SLOT.acquire()
    try:
        t,_=send(client,p,'使用 GPU，安装孔固定，顶面向下 1000 N。')
        end=time.time()+10
        while time.time()<end and store.get('task',t['id'])['status']!='queued':time.sleep(.05)
        client.post('/api/v1/agent/tasks/'+t['id']+'/cancel')
    finally:service.SLOT.release()
    service.THREADS[t['id']].join(10);assert store.get('task',t['id'])['status']=='cancelled';assert t['id'] not in service.RUNNING

def test_unavailable_native_gpu_does_not_fallback(monkeypatch):
    from strength import backends
    monkeypatch.setattr(backends,'gpu_diagnostic',lambda:{'self_test_passed':False,'error':'test-unavailable'})
    with pytest.raises(ValueError,match='test-unavailable'):backends.resolve_backend('cudss_cuda')

def test_gpu_cache_invalidation(models,tmp_path):
    clear_cache();c=axial_case();build_mesh(DATA/'models'/'bar',c,tmp_path/'mesh.npz');m=dict(np.load(tmp_path/'mesh.npz'))
    f,_=assemble_loads(m,c,models['bar']);fixed,_=build_constraints(m,c,models['bar'])
    a,_=solve_gpu(m,c,f,fixed);c.material.young_modulus_Pa*=2
    b,meta=solve_gpu(m,c,f,fixed);assert not meta['factorization_reused']
    np.testing.assert_allclose(b['displacements'],a['displacements']/2,rtol=1e-7,atol=1e-13)
    m['points']=m['points']*1.01
    _,meta=solve_gpu(m,c,f,fixed);assert not meta['factorization_reused'];clear_cache()

def test_gpu_convergence_and_section_stress(models):
    from strength.store import uid,write_json
    from strength.solver import run_job
    from strength.fem import tet_shape,IP_BARY
    import h5py
    c=LoadCase(material=DEMO_MATERIAL,supports=[{'selection':selection('-x')}],loads=[{'kind':'force','selection':selection('+x'),'vector':[0,0,-1]}],mesh={'size_m':.009,'convergence':True})
    folder=DATA/'jobs'/uid();folder.mkdir();write_json(folder/'config.json',{'model_id':'beam','case':c.model_dump(),'compute_backend':'cudss_cuda'})
    s=run_job(folder);assert s['convergence']['displacement_change']<.02
    with h5py.File(folder/'fields.h5') as f:
        points=f['points'][:];t=f['tets'][:];stress=f['stresses'][:]
        n=np.array([tet_shape(q[1:])[0] for q in IP_BARY]);ip=np.einsum('qi,eic->eqc',n,points[t])
        mask=(ip[:,:,0]>.08)&(ip[:,:,0]<.12)&(abs(ip[:,:,2]-.005)>.002)
        exact=(.2-ip[:,:,0])*(ip[:,:,2]-.005)/(.02*.01**3/12)
        assert np.linalg.norm((stress[:,:,0]-exact)[mask])/np.linalg.norm(exact[mask])<.03
    assert len(s['convergence']['levels'])==3;clear_cache()

def test_cancel_running_gpu_worker(client,models):
    import time
    from test_agent_api import project,send
    from strength import service,store
    task,_=send(client,project(client,models),'使用 GPU，安装孔固定，顶面向下 1000 N。')
    end=time.time()+30
    while time.time()<end:
        t=store.get('task',task['id'])
        if t['status'] in ('meshing','solving'):break
        time.sleep(.02)
    assert t['status'] in ('meshing','solving'),t
    client.post('/api/v1/agent/tasks/'+task['id']+'/cancel');service.THREADS[task['id']].join(15)
    t=service.task_view(task['id']);assert t['status']=='cancelled';assert all(j['status']=='cancelled' for j in t['jobs'])
    assert task['id'] not in service.RUNNING and task['id'] not in service.GPU_SLOTS
