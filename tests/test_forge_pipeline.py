import json
import numpy as np
import pytest
from strength.config import DATA
from strength.store import write_json
from strength.geometry import build_mesh
from forge.schemas import AnalysisCase
from forge.thermal import HeatSystem,solve_history
from forge.worker import run
from test_forge_numerics import material


def selection(direction):return {'kind':'direction','direction':direction}


def test_unsaved_unbalanced_physical_step_marks_result_partial(models,tmp_path,monkeypatch):
    from forge import coupling
    geo=models['bar']
    case=AnalysisCase(name='未输出时间步的平衡校核',analysis_type='thermo_mechanical',material=material(),mesh={'size_m':.014},
        supports=[{'name':'固定','selection':selection('-x')}],thermal={'duration_s':2,'dt_s':1,'save_s':2})
    def physical_steps(system,*args,**kwargs):
        n=len(system.mesh['points']);e=len(system.mesh['tets'])
        frame={'displacements':np.zeros((n,3)),'forces':np.zeros((n,3)),'stresses':np.zeros((e,4,6)),'strains':np.zeros((e,4,6)),
               'balance':{'force_relative':0.,'moment_relative':0.,'passed':True}}
        audits=[{'time_s':t,'thermal':{'energy_balance_relative':0.},'mechanical':{'force_relative':.02 if t==1 else 0.,'moment_relative':0.,'passed':t!=1}} for t in (0,1,2)]
        return [0.,2.],[np.full(n,25.),np.full(n,25.)],[frame,frame],audits
    monkeypatch.setattr(coupling,'solve_coupled',physical_steps)
    folder=tmp_path/'unsaved-balance';folder.mkdir();write_json(folder/'config.json',{'case':case.model_dump(),'model_id':geo['id']})
    run(folder)
    summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    assert summary['frames']==2 and len(summary['mechanical_balance']['physical_steps'])==3
    assert not summary['mechanical_balance']['passed']
    assert json.loads((folder/'status.json').read_text(encoding='utf-8'))['status']=='partial'


def test_steady_and_transient_analytic(models,tmp_path):
    geo=models['bar'];m=material()
    case=AnalysisCase(name='线性导热',analysis_type='thermal_steady',material=m,mesh={'size_m':.01},
        thermal={'boundaries':[{'kind':'temperature','selection':selection('-x'),'value':20},{'kind':'temperature','selection':selection('+x'),'value':120}]})
    meshfile=tmp_path/'mesh.npz';build_mesh(DATA/'models'/geo['id'],case,meshfile);mesh=dict(np.load(meshfile));system=HeatSystem(mesh,case,geo)
    T,audit=system.solve(np.full(len(mesh['points']),25.),0)
    assert np.max(np.abs(T-(20+1000*mesh['points'][:,0])))<1e-7
    transient=case.model_copy(deep=True);transient.analysis_type='thermal_transient'
    transient.thermal.boundaries=[];transient.thermal.duration_s=10;transient.thermal.dt_s=2;transient.thermal.save_s=2
    from forge.schemas import ThermalBoundary
    transient.thermal.boundaries=[ThermalBoundary(kind='body_power',body_id=1,value=39.)]
    system=HeatSystem(mesh,transient,geo);times,frames,audits=solve_history(system,transient)
    assert np.max(np.abs(frames[-1]-35.))<1e-6  # 39 W / (0.078 kg * 500 J/kgK) * 10 s


def test_fatigue_complete_archive(models,tmp_path):
    geo=models['bar'];case=AnalysisCase(name='拉杆疲劳',analysis_type='fatigue',material=material(),mesh={'size_m':.01},
        supports=[{'name':'固定','selection':selection('-x')}],loads=[{'name':'拉力','kind':'force','selection':selection('+x'),'vector':[10000,0,0]}],
        history={'times_s':[0,1,2,3,4],'factors':{'拉力':[0,1,0,-1,0]},'periodic':True})
    folder=tmp_path/'fatigue';folder.mkdir();write_json(folder/'config.json',{'case':case.model_dump(),'model_id':geo['id']})
    run(folder)
    status=json.loads((folder/'status.json').read_text(encoding='utf-8'));assert status['status']=='completed'
    assert (folder/'fields.h5').exists() and (folder/'report.pdf').exists()
    summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    assert summary['mechanical_balance']['passed'] and len(summary['mechanical_balance']['load_bases'])==1
    import h5py
    with h5py.File(folder/'fields.h5') as f:
        assert f['stresses'].shape[0]==5
        assert f['fatigue/evaluated'][:].all()
        assert f.attrs['stress_order']=='xx,yy,zz,xy,xz,yz'


def test_phase_change_energy_crossing(models,tmp_path):
    from forge.schemas import PhaseChange
    geo=models['bar'];m=material();m.phase_change=PhaseChange(solidus_C=30,interval_K=10,latent_J_kg=10000)
    case=AnalysisCase(name='绝热相变能量基准',analysis_type='thermal_transient',material=m,mesh={'size_m':.014},
        thermal={'initial_C':25,'duration_s':20,'dt_s':20,'save_s':20,'boundaries':[{'kind':'body_power','body_id':1,'value':39.}]})
    path=tmp_path/'mesh.npz';build_mesh(DATA/'models'/geo['id'],case,path);mesh=dict(np.load(path));system=HeatSystem(mesh,case,geo)
    times,frames,audits=solve_history(system,case)
    # Energy = .078 kg * [500*(35-25) + 10000*(35-30)/10] = 780 J.
    assert np.max(np.abs(frames[-1]-35))<1e-5
    assert audits[-1]['storage_W']==pytest.approx(39,rel=1e-6)


def test_transient_slab_diffusion(models,tmp_path):
    geo=models['bar'];m=material();case=AnalysisCase(name='瞬态平板基准',analysis_type='thermal_transient',material=m,mesh={'size_m':.008},
        thermal={'initial_C':0,'duration_s':100,'dt_s':1,'save_s':100,'boundaries':[{'kind':'temperature','selection':selection('-x'),'value':100},{'kind':'temperature','selection':selection('+x'),'value':100}]})
    path=tmp_path/'mesh.npz';build_mesh(DATA/'models'/geo['id'],case,path);mesh=dict(np.load(path));system=HeatSystem(mesh,case,geo)
    _,T,_=solve_history(system,case);x=mesh['points'][:,0];a=40/(7800*500)
    exact=np.full(len(x),100.)
    for n in range(1,120,2):exact-=400/(n*np.pi)*np.sin(n*np.pi*x/.1)*np.exp(-a*(n*np.pi/.1)**2*100)
    assert np.max(np.abs(T[-1]-exact))/100<.02
