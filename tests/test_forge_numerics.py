import numpy as np
import pytest
from forge.schemas import SNCurve,Material,AnalysisCase
from forge.fatigue import rainflow,sn_life,goodman,sn_damage,findley,enclosing_circle_radius,archard_increment
from forge.thermal import HeatSystem


def material():
    return Material(name='验证材料',density_kg_m3=7800,young_modulus_Pa=200e9,poisson_ratio=.3,
        yield_strength_Pa=500e6,tensile_strength_Pa=800e6,conductivity_W_mK=40,specific_heat_J_kgK=500,
        thermal_expansion_per_K=12e-6,source='软件解析验证假设',sn_curve={'points':[{'x':1e3,'y':400e6},{'x':1e7,'y':100e6}],
        'endurance_limit_Pa':100e6,'source':'合成曲线，非实物材料','demonstration':True})


def test_rainflow_phase_and_miner():
    for values in ([0,1,0,-1,0],[1,0,-1,0,1],[-1,0,1,0,-1]):
        cycles=rainflow(values,periodic=True)
        assert sum(x[2] for x in cycles)==1
        assert all(x[:2]==(1.,0.) for x in cycles)
    assert sum(c[0]*2*c[2] for c in rainflow([0,3,1,2,0]))==4
    m=material();assert sn_life(200e6,m.sn_curve)==pytest.approx(1e5)
    assert goodman(100e6,400e6,800e6)==200e6
    stress=np.zeros((5,6));stress[:,0]=np.array([0,1,0,-1,0])*200e6
    damage,cycles=sn_damage(stress,m)
    assert damage==pytest.approx(1e-5)


def test_enclosing_circle_and_archard():
    assert enclosing_circle_radius([[1,0],[-1,0],[0,1],[0,-1]])==pytest.approx(1.)
    assert enclosing_circle_radius([[0,0],[2,0],[1,0]])==pytest.approx(1.)
    assert archard_increment(1e8,.1,1e9,1e-5)==pytest.approx(1e-7)
    assert archard_increment(1e8,0,1e9,1e-5)==0


def tetra_mesh():
    from strength.fem import EDGES
    corners=np.array([[0.,0,0],[1,0,0],[0,1,0],[0,0,1]])
    points=np.vstack([corners,[(corners[a]+corners[b])/2 for a,b in EDGES]])
    return {'points':points,'tets':np.arange(10).reshape(1,10),'body_ids':np.array([1]),
        'faces':np.array([[0,1,2,4,5,6],[0,3,1,7,8,4],[1,3,2,8,9,5],[2,3,0,9,7,6]]),
        'surface_ids':np.arange(1,5),'owners':np.zeros(4,int),'face_body_ids':np.ones(4,int)}


def test_quadratic_heat_patch_and_capacity():
    mesh=tetra_mesh();case=AnalysisCase(name='解析',analysis_type='thermal_transient',material=material())
    system=HeatSystem(mesh,case,{})
    assert system.M.sum()==pytest.approx(7800*500/6)
    assert np.max(np.abs(system.K@np.ones(10)))<1e-12
    x=mesh['points'];T=20+3*x[:,0]-2*x[:,1]+7*x[:,2]
    gradient,flux=system.gradients(T)
    assert np.allclose(gradient,[3,-2,7])
    assert np.allclose(flux,[-120,80,-280])
    T,audit=system.solve(np.full(10,25.),time=1,dt=1)
    assert np.allclose(T,25.)


def test_findley_uniaxial_reference():
    m=material();m=Material.model_validate({**m.model_dump(),'findley':{'k':.2,'curve':m.sn_curve.model_dump(),'calibration':'bulk'}})
    s=np.zeros((5,6));s[:,0]=np.array([0,1,0,-1,0])*200e6
    result=findley(s,m,1000)
    exact=200e6*(.2+np.sqrt(1+.2**2))/2
    assert result['parameter_Pa']==pytest.approx(exact,rel=.005)


def test_multiaxial_circle_batch_matches_independent_scalar():
    from forge.fatigue import enclosing_circle_radii
    rng=np.random.default_rng(123);paths=rng.normal(size=(30,33,2))*1e8
    expected=np.array([enclosing_circle_radius(p) for p in paths])
    assert np.allclose(enclosing_circle_radii(paths),expected,rtol=1e-8)
    angle=np.linspace(0,2*np.pi,60);circle=np.stack([np.cos(angle),np.sin(angle)],axis=-1)
    assert enclosing_circle_radii(circle[None])[0]==pytest.approx(1.,rel=1e-8)
def test_batched_findley_matches_independent_point_evaluation():
    from forge.fatigue import findley_parameters
    m=material();m=Material.model_validate({**m.model_dump(),'findley':{'k':.2,'curve':m.sn_curve.model_dump(),'calibration':'bulk'}})
    h=np.random.default_rng(71).normal(size=(11,17,6))*12e6;h[:,-1]=h[:,0];h[0]=h[0,0]
    values,normals=findley_parameters(h,.2,162)
    expected=[findley(s,m,162)['parameter_Pa'] for s in h]
    assert np.allclose(values,expected,rtol=1e-9)

