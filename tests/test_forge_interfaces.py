import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from forge.schemas import AnalysisCase,Motion
from forge.examples import demonstration_material,make_contact_geometry
from forge.contact import midpoint_kinematics,wear_history,face_keys
from forge.wear import recede
from strength.geometry import inspect_model,build_mesh
from strength.store import write_json


def test_rigid_pure_rolling_material_velocity():
    R=.002;theta=.07;center=np.array([0.,0.,R]);x=np.array([[0.,0.,0.],[R,0,R],[0,R,R],[0,0,2*R]])
    mesh={'points':x,'tets':np.arange(4).reshape(1,4),'body_ids':np.array([1])}
    motion=Motion(name='纯滚动',body_id=1,selection={'kind':'faces','body_id':1,'face_ids':[1]},center_m=center.tolist(),axis=[0,1,0],angles_rad=[-theta,theta],translations_m=[[-R*theta,0,0],[R*theta,0,0]])
    # This unit test exercises kinematics, not a submit-ready analysis case.
    case=AnalysisCase(name='解析运动学',analysis_type='static',material=demonstration_material()).model_copy(update={'motions':[motion]})
    def displacement(a):return (x-center)@Rotation.from_rotvec([0,a,0]).as_matrix().T+center+[R*a,0,0]-x
    midpoint,v=midpoint_kinematics(mesh,case,displacement(-theta),displacement(theta),0,2)
    assert np.linalg.norm(midpoint[0])<1e-15
    assert np.linalg.norm(v[0])<1e-15


def test_archard_excludes_reversible_stick():
    from forge.contact import irreversible_slip
    from types import SimpleNamespace
    p=SimpleNamespace(friction=.2,tangential_stiffness_Pa_m=1e12)
    assert irreversible_slip(np.array([1e-7,0,0]),np.zeros(3),np.array([1e5,0,0]),1e6,1e6,p)==0
    assert irreversible_slip(np.array([1e-5,0,0]),np.array([2e5,0,0]),np.array([2e5,0,0]),1e6,1e6,p)==pytest.approx(1e-5)


def test_batched_projection_preserves_curved_quadratic_surface():
    from forge.interfaces import project_surface,project_surfaces,quadratic_shapes
    P=np.array([[0.,0,0],[1,0,0],[0,1,0],[.5,0,.1],[.5,.5,.2],[0,.5,.1]])
    q=np.random.default_rng(731).dirichlet([2,2,2],size=40)[:,1:];N,d=quadratic_shapes(q)
    points=N@P+np.array([.001,-.002,.01]);expected=[project_surface(P,x) for x in points]
    weights,targets,normals,valid=project_surfaces(np.repeat(P[None],len(points),axis=0),points)
    assert valid.all()
    assert np.allclose(weights,np.array([e[0] for e in expected]),atol=1e-10)
    assert np.allclose(targets,np.array([e[1] for e in expected]),atol=1e-10)


def test_archard_projection_and_surface_update(tmp_path):
    folder=tmp_path/'model';folder.mkdir();make_contact_geometry(folder/'source.step');write_json(folder/'source.json',{'file':'source.step','units':'m'})
    geo=inspect_model(folder);sel=lambda b,d:dict(kind='direction',body_id=b,direction=d)
    m=demonstration_material(True);m.wear_coefficient=1e-3;m.hardness_Pa=1e9
    case=AnalysisCase(name='滑动磨损',analysis_type='contact_cycle',material=m,mesh={'size_m':.008},
        loads=[dict(kind='force',name='p',selection=sel(1,'+z'),vector=[0,0,-1])],history={'times_s':[0,1,2],'factors':{'p':[1,1,1]}},
        contacts=[dict(name='p',slave=sel(1,'-z'),master=sel(2,'+z'),wear=True)],cycles_per_wear_block=10000)
    path=tmp_path/'m.npz';build_mesh(folder,case,path);mesh=dict(np.load(path));keys=face_keys(mesh)
    pressure=2e6;raw=[[*k,pressure,0,0] for k in keys];frames=[];nodes=np.unique(mesh['tets'][mesh['body_ids']==1]);distance=1e-5
    for shift in (0,distance,2*distance):
        u=np.zeros_like(mesh['points']);u[nodes,0]=shift;frames.append({'displacements':u,'contact_stresses':raw})
    history={};wear=wear_history(mesh,case,frames,geo,history);expected=m.wear_coefficient*pressure*2*distance/m.hardness_Pa*10000
    assert np.all(history['p'][0]==0)
    assert np.array_equal(history['p'][-1],wear['p']['depth_m'])
    assert history['p'][1].max()==pytest.approx(expected/2,rel=1e-9)
    assert wear['p']['depth_m'].max()==pytest.approx(expected,rel=1e-9)
    assert wear_history(mesh,case,[frames[0]]*3,geo)['p']['depth_m'].max()==0
    changed=recede(mesh,wear)
    assert np.array_equal(changed['tets'],mesh['tets'])
    assert np.max(changed['points'][:,2]-mesh['points'][:,2])==pytest.approx(expected,rel=1e-8)
