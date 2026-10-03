import numpy as np
import pytest
from skfem import MeshTet, Basis, ElementTetP1, ElementVector, asm
from skfem.models.elasticity import linear_elasticity, lame_parameters
from thermal_legacy.thermoelastic import solve_thermoelastic, element_operators, assess_design


def cube(n=3, offset=0):
    m=MeshTet.init_tensor(np.linspace(0,.02,n),np.linspace(0,.01,n),np.linspace(0,.01,n))
    p=m.p.T.copy();p[:,0]+=offset;t=m.t.T
    return dict(points=p,tets=t,boundary_triangles=m.facets[:,m.boundary_facets()].T,
                tet_volumes=np.abs(np.linalg.det(p[t[:,1:]]-p[t[:,:1]]))/6)


MAT=dict(name='test steel',young_modulus_Pa=200e9,poisson_ratio=.3,thermal_expansion_CTE_per_K=12e-6,yield_strength_Pa=250e6)


def run(mesh,delta=50,mode='free',fixed=()):
    T=np.full((2,len(mesh['points'])),20.);T[1]+=delta
    return solve_thermoelastic(mesh,T,[MAT],np.zeros(len(mesh['tets']),int),dict(mode=mode,reference_C=20),fixed)


@pytest.mark.parametrize('delta',[50,-60])
def test_free_uniform_expansion_and_contraction(delta):
    mesh=cube();r=run(mesh,delta)
    expected=(mesh['points']-mesh['points'].mean(axis=0))*MAT['thermal_expansion_CTE_per_K']*delta
    np.testing.assert_allclose(r['displacement'][1],expected,rtol=2e-6,atol=1e-12)
    assert np.abs(r['stress']).max()<.02


@pytest.mark.parametrize('delta',[50,-60])
def test_fully_restrained_hydrostatic_stress(delta):
    mesh=cube();r=run(mesh,delta,'constrained',np.arange(len(mesh['points'])*3))
    expected=-MAT['young_modulus_Pa']*MAT['thermal_expansion_CTE_per_K']*delta/(1-2*MAT['poisson_ratio'])
    np.testing.assert_allclose(r['stress'][1,:,:3],expected,rtol=1e-6)
    assert np.max(r['von_mises'])<1e-4
    assert np.max(np.abs(r['displacement']))==0


def test_bar_axial_restraint_matches_exact_solution():
    mesh=cube();p=mesh['points']
    fixed=np.concatenate([np.flatnonzero(np.isclose(p[:,0],0)|np.isclose(p[:,0],.02))*3,
                          np.flatnonzero(np.isclose(p[:,1],0))*3+1,np.flatnonzero(np.isclose(p[:,2],0))*3+2])
    r=run(mesh,50,'constrained',fixed)
    np.testing.assert_allclose(r['stress'][1,:,0],-120e6,rtol=1e-6)
    assert np.abs(r['stress'][1,:,1:]).max()<.05
    expected=p[:,1]*12e-6*50*1.3
    np.testing.assert_allclose(r['displacement'][1,:,1],expected,rtol=1e-6,atol=1e-12)


def test_stiffness_matches_independent_skfem_assembly():
    mesh=cube();p=mesh['points'].copy();p[:,0]+=.3*p[:,1];p[:,2]+=.2*p[:,0]
    t=mesh['tets'];B,D,v,_=element_operators(p,t,[MAT],np.zeros(len(t),int))
    K=np.zeros((len(p)*3,)*2)
    for tet,b,d,vol in zip(t,B,D,v):
        dofs=(tet[:,None]*3+np.arange(3)).ravel();K[np.ix_(dofs,dofs)]+=b.T@d@b*vol
    basis=Basis(MeshTet(p.T,t.T),ElementVector(ElementTetP1()))
    expected=asm(linear_elasticity(*lame_parameters(200e9,.3)),basis).toarray()
    np.testing.assert_allclose(K,expected,rtol=1e-12,atol=1e-5)


def test_disconnected_bodies_each_have_independent_free_modes():
    a=cube();b=cube(offset=.05);n=len(a['points'])
    mesh=dict(points=np.vstack([a['points'],b['points']]),tets=np.vstack([a['tets'],b['tets']+n]))
    r=run(mesh)
    assert r['summary']['components']==2
    assert np.abs(r['stress']).max()<.02
    assert np.linalg.norm(r['displacement'][1,:n].mean(axis=0))<1e-12
    assert np.linalg.norm(r['displacement'][1,n:].mean(axis=0))<1e-12


def test_inadequate_support_is_rejected():
    with pytest.raises(ValueError,match='支撑不足'):run(cube(),mode='constrained',fixed=[0,1,2])


def test_heterogeneous_free_expansion_same_cte():
    mesh=cube();labels=(mesh['points'][mesh['tets']].mean(axis=1)[:,0]>.01).astype(int)
    r=solve_thermoelastic(mesh,np.full((1,len(mesh['points'])),70),[MAT,{**MAT,'young_modulus_Pa':70e9,'poisson_ratio':.25}],labels,dict(mode='free',reference_C=20))
    assert np.abs(r['stress']).max()<.03


def test_risk_checks_do_not_claim_safe_when_limits_missing():
    mesh=cube();r=run(mesh);T=np.full((2,len(mesh['points'])),20.);T[1]=70
    a=assess_design({},mesh,T,[0,100],r,[{**MAT,'yield_strength_Pa':None}],np.zeros(len(mesh['tets']),int))
    assert a['status']=='review_required'
    a=assess_design({'design_limits':dict(minimum_C=0,maximum_C=60,maximum_displacement_m=.001)},mesh,T,[0,100],r,[MAT],np.zeros(len(mesh['tets']),int))
    assert a['status']=='exceeded' and a['checks'][1]['time_s']==100


def test_large_thermal_strain_flagged():
    assert not run(cube(),delta=1500)['summary']['small_strain_valid']


def test_structural_progress_distinguishes_assembly_factorization_and_frames():
    mesh=cube();stages=[];frames=[]
    r=solve_thermoelastic(mesh,np.full((2,len(mesh['points'])),70),[MAT],np.zeros(len(mesh['tets']),int),
        dict(mode='free',reference_C=20),callback=frames.append,stage_callback=stages.append)
    assert len(stages)==3 and '组装' in stages[0] and '分解' in stages[1] and '逐帧' in stages[2]
    assert frames==[.5,1]
    assert all(t>=0 for t in r['summary']['timings_s'].values())


def test_free_nonuniform_gauges_match_dense_pseudoinverse():
    mesh=cube();p=mesh['points'];t=mesh['tets'];labels=np.zeros(len(t),int)
    temp=20+10*np.sin(p[:,0]*100)+30*p[:,2]
    result=solve_thermoelastic(mesh,temp[None,:],[MAT],labels,dict(mode='free',reference_C=20))
    B,D,v,alpha=element_operators(p,t,[MAT],labels)
    K=np.zeros((len(p)*3,)*2);F=np.zeros(len(p)*3)
    for i,tet in enumerate(t):
        dofs=(tet[:,None]*3+np.arange(3)).ravel()
        eth=np.r_[np.ones(3)*alpha[i]*(temp[tet].mean()-20),np.zeros(3)]
        K[np.ix_(dofs,dofs)]+=B[i].T@D[i]@B[i]*v[i]
        F[dofs]+=B[i].T@D[i]@eth*v[i]
    expected=np.linalg.lstsq(K,F,rcond=1e-12)[0].reshape(-1,3)
    np.testing.assert_allclose(result['displacement'][0],expected,atol=1e-11,rtol=1e-5)


def test_nonuniform_temperature_displacement_converges_to_plane_strain_solution():
    # Plane strain bar: uy=uz=0, ux=0 at both ends, T-Tref=A*sin(pi*x/L).
    # Equilibrium gives ux'=alpha*(1+nu)/(1-nu)*(T-Tmean).
    errors=[];L=.02;A=50;beta=12e-6*1.3/.7
    for n in (4,7,13):
        mesh=cube(n);p=mesh['points'];x=p[:,0]
        fixed=np.r_[np.arange(len(p))*3+1,np.arange(len(p))*3+2,
                    np.flatnonzero(np.isclose(x,0)|np.isclose(x,L))*3]
        T=20+A*np.sin(np.pi*x/L)
        r=solve_thermoelastic(mesh,T[None,:],[MAT],np.zeros(len(mesh['tets']),int),dict(mode='constrained',reference_C=20),fixed)
        exact=beta*A*(L/np.pi*(1-np.cos(np.pi*x/L))-2*x/np.pi)
        errors.append(float(np.max(np.abs(r['displacement'][0,:,0]-exact))))
    assert errors[1]<errors[0]*.4 and errors[2]<errors[1]*.4
    # Absolute error below 0.5% of the characteristic thermal extension.
    assert errors[-1]<.005*beta*A*L
