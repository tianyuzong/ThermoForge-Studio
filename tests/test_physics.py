import json
import numpy as np, h5py, pytest
from strength.schemas import LoadCase,DEMO_MATERIAL
from strength.config import DATA
from strength.solver import run_job
from strength.store import uid,write_json
from strength.geometry import build_mesh
from strength.fem import build_constraints,assemble_loads,IP_BARY,tet_shape

def selection(direction): return {'kind':'direction','direction':direction}
def axial_case(load=None):
    return LoadCase.model_validate({'material':DEMO_MATERIAL,'supports':[
        {'selection':selection('-x'),'components':['x']},{'selection':selection('-y'),'components':['y']},{'selection':selection('-z'),'components':['z']}],
        'loads':[load or {'kind':'force','selection':selection('+x'),'vector':[1000,0,0]}],'mesh':{'size_m':.005}})

def solve(model,case):
    folder=DATA/'jobs'/uid();folder.mkdir();write_json(folder/'config.json',{'model_id':model,'case':case.model_dump()})
    summary=run_job(folder)
    assert summary['balance']['passed'],summary['balance']
    assert max(summary['balance']['force_relative'],summary['balance']['moment_relative'])<1e-4
    return summary,folder

def test_bar_exact_and_artifacts(models):
    s,p=solve('bar',axial_case())
    with h5py.File(p/'fields.h5') as f:
        u=f['displacements'][:]; stress=f['stresses'][:]
        assert abs(u[:,0].max()/(1000*.1/(1e-4*210e9))-1)<.01
        np.testing.assert_allclose(stress[:,:,0],1e7,rtol=.01)
    assert abs(s['maximum_von_mises_Pa']/1e7-1)<.01
    assert s['checks']['displacement']['status']=='not_evaluated'
    for name in ('report.pdf','metrics.csv','result.zip','result.vtu','analysis.inp','fields.h5'): assert (p/name).stat().st_size>50

def test_pressure_on_constrained_face_reaction_correction(models):
    s,_=solve('bar',axial_case({'kind':'pressure','selection':selection('-x'),'pressure_Pa':1e7}))
    assert s['maximum_von_mises_Pa']<1
    np.testing.assert_allclose(s['balance']['reaction_N'],[-1000,0,0],atol=1e-3)

def test_gravity_bar_matches_analytic_solution(models):
    s,p=solve('bar',axial_case({'kind':'gravity','vector':[9.80665,0,0]}))
    exact=7850*9.80665*.1**2/(2*210e9)
    with h5py.File(p/'fields.h5') as f: assert abs(f['displacements'][:,0].max()/exact-1)<.01
    np.testing.assert_allclose(s['balance']['reaction_N'][0],-7850*9.80665*1e-5,rtol=1e-4)

def test_prescribed_displacement(models):
    c=axial_case(); c.loads=[];c.supports.append(type(c.supports[0]).model_validate({'selection':selection('+x'),'components':['x'],'displacement_m':[1e-5,0,0]}))
    s,p=solve('bar',c)
    assert abs(s['maximum_von_mises_Pa']/(210e9*1e-5/.1)-1)<.01

def test_cantilever_displacement_and_section_stress(models):
    c=LoadCase.model_validate({'material':DEMO_MATERIAL,'supports':[{'selection':selection('-x')}],
        'loads':[{'kind':'force','selection':selection('+x'),'vector':[0,0,-1]}],'mesh':{'size_m':.009,'convergence':True}})
    s,p=solve('beam',c); exact=.2**3/(3*210e9*(.02*.01**3/12))
    assert s['convergence']['displacement_change']<.02
    with h5py.File(p/'fields.h5') as f:
        u=f['displacements'][:]; points=f['points'][:]; t=f['tets'][:]; stresses=f['stresses'][:]
        assert abs(abs(u[:,2].min())/exact-1)<.03
        N=np.array([tet_shape(q[1:])[0] for q in IP_BARY]); ip=np.einsum('qi,eic->eqc',N,points[t])
        mask=(ip[:,:,0]>.08)&(ip[:,:,0]<.12)&(abs(ip[:,:,2]-.005)>.002)
        expected=-(.2-ip[:,:,0])*(ip[:,:,2]-.005)/(.02*.01**3/12)
        # Top surface is in tension under a downward end load on this cantilever.
        expected=-expected
        assert np.sqrt(np.mean((stresses[:,:,0][mask]-expected[mask])**2))/np.sqrt(np.mean(expected[mask]**2))<.03

def test_underconstraint_and_conflict_are_rejected(models,tmp_path):
    c=axial_case();build_mesh(DATA/'models'/'bar',c,tmp_path/'mesh.npz')
    mesh=dict(np.load(tmp_path/'mesh.npz'));c.supports=c.supports[:1]
    with pytest.raises(ValueError,match='欠约束'): build_constraints(mesh,c,models['bar'])
    c=axial_case();s=c.supports[0].model_copy(deep=True);s.displacement_m=[1e-5,0,0];c.supports.append(s)
    with pytest.raises(ValueError,match='冲突'): build_constraints(mesh,c,models['bar'])

def test_three_level_convergence_and_missing_yield(models):
    c=axial_case();c.material.yield_strength_Pa=None;c.mesh.convergence=True
    s,_=solve('bar',c)
    assert s['convergence']['status']=='converged'
    assert len(s['convergence']['levels'])==3
    exact_energy=1000**2*.1/(2*1e-4*210e9)
    for level in s['convergence']['levels']:
        assert level['strain_energy_J']==pytest.approx(exact_energy,rel=.01)
        assert level['displacement_peak']['position_m'][0]==pytest.approx(.1,abs=1e-10)
    assert s['checks']['strength']['status']=='not_evaluated'
    assert s['minimum_safety_factor'] is None
