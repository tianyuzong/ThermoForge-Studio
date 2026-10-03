"""Native CPU patch tests, component checks and lifecycle failure coverage."""
import time
import os,shutil
import numpy as np
import pytest
from strength import store,service,agent
from strength.geometry import build_mesh
from strength.fem import assemble_loads,build_constraints
from strength.solver import write_deck,run_ccx,parse_dat,assess
from strength.postprocess import fields_for,integration_geometry,compare_results
from strength.config import DATA
from test_physics import axial_case
from test_components import assembly
from test_agent_api import project

@pytest.mark.parametrize('mode',['uniaxial','shear','hydrostatic'])
def test_native_affine_patch_and_chinese_path(models,tmp_path,mode):
    c=axial_case();folder=tmp_path/'中文路径_静力验证';folder.mkdir()
    build_mesh(DATA/'models'/'bar',c,folder/'mesh.npz');mesh=dict(np.load(folder/'mesh.npz'))
    p=mesh['points'];E=c.material.young_modulus_Pa;nu=c.material.poisson_ratio;G=E/(2*(1+nu))
    expected_s=np.zeros(6);expected_e=np.zeros(6)
    if mode=='uniaxial':expected_s[0]=1e7;expected_e[:3]=[1e7/E,-nu*1e7/E,-nu*1e7/E]
    elif mode=='shear':expected_s[3]=1e7;expected_e[3]=1e7/(2*G)
    else:expected_s[:3]=1e7;expected_e[:3]=1e7*(1-2*nu)/E
    xx,yy,zz,xy,xz,yz=expected_e;eps=np.array([[xx,xy,xz],[xy,yy,yz],[xz,yz,zz]])
    u=p@eps;boundary=np.unique(mesh['faces']);fixed={int(n*3+a):float(u[n,a]) for n in boundary for a in range(3)}
    write_deck(folder,mesh,c,np.zeros_like(p),fixed);meta=run_ccx(folder)
    assert meta['backend']=='calculix_cpu' and meta['cpu_threads']==6
    data=parse_dat(folder/'analysis.dat',len(p),len(mesh['tets']))
    np.testing.assert_allclose(data['stresses'],np.broadcast_to(expected_s,data['stresses'].shape),rtol=1e-5,atol=10)
    np.testing.assert_allclose(data['strains'],np.broadcast_to(expected_e,data['strains'].shape),rtol=1e-5,atol=1e-10)
    np.testing.assert_allclose(data['displacements'],u,rtol=1e-5,atol=1e-12)
    raw,_=fields_for(mesh,c,data);_,w=integration_geometry(mesh)
    exact=.5*(expected_s[:3]@expected_e[:3]+2*expected_s[3:]@expected_e[3:])*.1*.01*.01
    assert float((raw['energy_density']*w).sum())==pytest.approx(exact,rel=.01)

def test_cpu_component_strength_uses_controlling_material(assembly,tmp_path):
    geo,c,mesh=assembly;f,a=assemble_loads(mesh,c,geo);fixed,groups=build_constraints(mesh,c,geo)
    write_deck(tmp_path,mesh,c,f,fixed);run_ccx(tmp_path)
    data=parse_dat(tmp_path/'analysis.dat',len(mesh['points']),len(mesh['tets']))
    s,_=assess(mesh,c,data,f,fixed,groups,a,{'nodes':len(mesh['points'])})
    one,two=s['components'];assert s['balance']['passed']
    assert two['maximum_displacement_m']/one['maximum_displacement_m']==pytest.approx(2,rel=1e-5)
    assert one['minimum_safety_factor']/two['minimum_safety_factor']==pytest.approx(2,rel=1e-5)
    assert s['checks']['strength']['critical_body_id']==two['body_id']
    assert s['checks']['strength']['value_Pa']/s['checks']['strength']['limit_Pa']==pytest.approx(c.target_safety_factor/s['minimum_safety_factor'])

def test_cpu_resource_rejection_retains_case(client,models):
    pid=project(client,models);c=axial_case();c.mesh.size_m=1e-6
    before=c.model_dump();t=client.post(f'/api/v1/projects/{pid}/jobs',json={'case':before,'request_id':store.uid()}).json()
    service.THREADS[t['id']].join(30);t=service.task_view(t['id'])
    assert t['status']=='failed' and len(t['jobs'])==1
    expected=service.bind_case(c,models['bracket']).model_dump()
    assert '资源预检' in t['jobs'][0]['detail'] and t['jobs'][0]['case']==expected

def test_cancel_running_cpu_worker(client,models):
    pid=project(client,models);c=axial_case();c.mesh.size_m=.004
    t=client.post(f'/api/v1/projects/{pid}/jobs',json={'case':c.model_dump(),'request_id':store.uid()}).json()
    deadline=time.time()+30;process=None
    while time.time()<deadline:
        process=service.RUNNING.get(t['id'])
        if process is not None:break
        time.sleep(.02)
    assert process is not None
    client.post('/api/v1/agent/tasks/'+t['id']+'/cancel');service.THREADS[t['id']].join(15)
    assert process.poll() is not None
    result=service.task_view(t['id']);assert result['status']=='cancelled'
    assert all(j['status']=='cancelled' for j in result['jobs']) and t['id'] not in service.RUNNING

def test_curve_groups_do_not_merge_different_other_loads():
    q={'name':'刚度','kind':'stiffness','field':'displacement_x','load_name':'轴力'}
    def job(id,force,gravity):
        return {'id':id,'name':id,'case':{'loads':[{'name':'轴力','kind':'force','vector':[force,0,0]}, {'name':'重力','kind':'gravity','vector':[0,0,gravity]}],'result_requests':[q]},
          'summary':{'geometry_sha256':'same','result_queries':[{'status':'ok','load_N':force,'displacement_m':force/1000}]}}
    assert len(compare_results([job('a',1000,-10),job('b',1500,-10)])['load_displacement_curves'])==1
    assert not compare_results([job('a',1000,-10),job('b',1500,-20)])['load_displacement_curves']

def test_real_cli_timeout_cleans_process(tmp_path,monkeypatch):
    if not (os.environ.get('STRENGTH_CODEX_COMMAND') or shutil.which('codex')):pytest.skip('Codex not installed')
    monkeypatch.setenv('STRENGTH_CODEX_TIMEOUT_S','0.001')
    original=agent.subprocess.Popen;launched=[]
    def track(args,**kwargs):
        p=original(args,**kwargs)
        if 'exec' in args:launched.append(p)
        return p
    monkeypatch.setattr(agent.subprocess,'Popen',track)
    with pytest.raises(TimeoutError,match='超时'):agent.cli_plan({'conversation':[]},tmp_path/'cli-timeout',lambda:False)
    assert launched and all(p.poll() is not None for p in launched)
