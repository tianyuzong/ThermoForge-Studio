import json
import numpy as np
import pytest
from forge.mechanics import stage_reusable,save_stage_provenance
from forge.reprocess import validate_source
from forge.schemas import AnalysisCase,Material
from forge.fatigue import findley
from forge.worker import evaluate_fatigue
from forge.contact import midpoint_kinematics
from test_forge_numerics import material,tetra_mesh


def test_completed_stage_requires_identical_input_and_intact_raw_file(tmp_path):
    deck='*HEADING\nverified specimen\n'
    (tmp_path/'analysis.inp').write_text(deck,encoding='ascii')
    (tmp_path/'analysis.dat').write_text('raw result')
    (tmp_path/'solver.log').write_text('CalculiX Version 2.23\nJob finished')
    assert stage_reusable(tmp_path,deck)
    save_stage_provenance(tmp_path,deck,False)
    assert stage_reusable(tmp_path,deck)
    assert not stage_reusable(tmp_path,deck+'changed load')
    (tmp_path/'analysis.dat').write_text('damaged result')
    assert not stage_reusable(tmp_path,deck)


def test_native_nonconvergence_is_distinct_from_input_or_installation_error(monkeypatch,tmp_path):
    from forge import mechanics
    from forge.errors import NonlinearConvergenceError
    def fail(*args,**kwargs):raise RuntimeError('CalculiX 求解失败：*ERROR: too many cutbacks')
    monkeypatch.setattr(mechanics,'run_ccx',fail)
    with pytest.raises(NonlinearConvergenceError):mechanics.run_native(tmp_path)
    def invalid(*args,**kwargs):raise RuntimeError('不支持的 CalculiX 版本')
    monkeypatch.setattr(mechanics,'run_ccx',invalid)
    with pytest.raises(RuntimeError) as caught:mechanics.run_native(tmp_path)
    assert not isinstance(caught.value,NonlinearConvergenceError)


def test_coupled_cutback_replays_only_committed_physical_steps(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from forge import coupling
    from forge.errors import NonlinearConvergenceError
    case=AnalysisCase(name='步长回退',analysis_type='thermo_mechanical',material=material(),thermal={'duration_s':1,'dt_s':1,'save_s':1})
    system=SimpleNamespace(n=1,mesh={'points':np.zeros((1,3))},interface_records={})
    frame={k:np.zeros((1,3)) for k in ('displacements','forces')};frame.update(stresses=np.zeros((1,4,6)),strains=np.zeros((1,4,6)))
    monkeypatch.setattr(coupling,'solve_static',lambda *a,**k:frame)
    monkeypatch.setattr(coupling,'recover_contact',lambda *a,**k:{})
    attempts=[]
    def step(system,case,evidence,folder,previous,time,dt,progress,committed):
        attempts.append((time,dt,len(committed),float(previous[0])))
        if dt>.5:raise NonlinearConvergenceError('实际非线性求解未收敛')
        return previous+dt,frame,{'thermal':{},'iterations':[],'contact':{}}
    monkeypatch.setattr(coupling,'coupled_step',step)
    times,temps,structures,audits=coupling.solve_coupled(system,case,{},tmp_path)
    assert attempts==[(1.,1.,0,25.),(.5,.5,0,25.),(1.,.5,1,25.5)]
    assert times==[0.,1.] and temps[-1][0]==26.
    assert audits[1]['cutbacks']==1


def test_reprocessing_cannot_change_physics():
    original=AnalysisCase(name='原场',analysis_type='static',material=material())
    changed=original.model_copy(deep=True);changed.material.sn_curve.points[0].y*=.9
    validate_source(changed,{'case':original.model_dump()})
    changed.material.young_modulus_Pa*=2
    with pytest.raises(ValueError,match='物性'):validate_source(changed,{'case':original.model_dump()})


def test_endurance_hotspot_uses_cycle_parameter_and_constant_stress_has_no_cycles():
    case=AnalysisCase(name='零损伤热点',analysis_type='fatigue',material=material(),history={'times_s':[0,1,2],'factors':{},'periodic':True})
    history=np.zeros((3,1,4,6));history[1,0,:,0]=[1e6,2e6,6e6,3e6]
    result=evaluate_fatigue(tetra_mesh(),case,history)
    assert result['hotspot']['integration_point']==3
    assert result['hotspot']['life_status']=='below_defined_endurance_limit'
    m=case.material.model_copy(deep=True)
    m=Material.model_validate({**m.model_dump(),'findley':{'k':.2,'curve':m.sn_curve.model_dump(),'calibration':'bulk'}})
    assert findley(np.tile([300e6,0,0,0,0,0],(3,1)),m)['damage_per_cycle']==0


def test_actual_free_axle_midpoint_rolling_velocity():
    from scipy.spatial.transform import Rotation
    R=.002;a=.07;center=np.array([0.,0.,R]);x=np.array([[0.,0,0],[R,0,R],[0,R,R],[0,0,2*R]])
    mesh={'points':x,'tets':np.arange(4).reshape(1,4),'body_ids':np.array([1])}
    case=AnalysisCase(name='自由轴实际运动',analysis_type='static',material=material())
    def state(theta):
        u=(x-center)@Rotation.from_rotvec([0,theta,0]).as_matrix().T+center+[R*theta,0,0]-x
        frame={'axles':{'axis':{'body_id':1,'center_m':center.tolist(),'rotation_vector_rad':[0,theta,0],'translation_m':[R*theta,0,0]}}}
        return u,frame
    ua,fa=state(-a);ub,fb=state(a)
    mid,velocity=midpoint_kinematics(mesh,case,ua,ub,0,2,fa,fb)
    assert np.linalg.norm(mid[0])<1e-15 and np.linalg.norm(velocity[0])<1e-15
