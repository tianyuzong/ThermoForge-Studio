import numpy as np
import pytest
from strength.gear_contact import solve_contact_qp,project_quadratic

def test_compression_open_gap_and_torque():
    C=np.array([[2.,.2],[.2,3.]])*1e-8;A=np.array([[-.02],[-.02]])
    p,theta,gap,_,res=solve_contact_qp(C,A,np.array([1e-5,1e-4]),np.array([5.]))
    np.testing.assert_allclose(p,[250,0],rtol=1e-10,atol=1e-9)
    assert gap[0]==pytest.approx(0,abs=1e-12) and gap[1]>0 and theta[0]>0 and res<1e-10

def test_two_faces_compression_and_infeasible_sign():
    C=np.eye(2)*1e-8;A=np.array([[-.02],[-.02]])
    p,_,g,_,_=solve_contact_qp(C,A,np.array([1e-5,1e-5]),np.array([5.]))
    np.testing.assert_allclose(p,[125,125],rtol=1e-10);assert np.max(abs(g))<1e-12
    with pytest.raises(ValueError,match='无法'):solve_contact_qp(C,A,np.zeros(2),np.array([-5.]))

def test_quadratic_projection_preserves_geometry():
    P=np.array([[0,0,0],[1,0,0],[0,1,0],[.5,0,.03],[.5,.5,.03],[0,.5,.03]])
    from strength.fem import tri_shape
    N,d=tri_shape([.2,.3]);J=P.T@d;n=np.cross(J[:,0],J[:,1]);n/=np.linalg.norm(n)
    result=project_quadratic(N@P+n*.005,P,[1/3,1/3])
    assert result is not None
    np.testing.assert_allclose(result[1],N@P,atol=1e-10)
    assert project_quadratic(np.array([2.,2.,1.]),P,[1/3,1/3]) is None

def test_cudss_multiple_rhs():
    from strength.cuda_runtime import runtime,CuDSS
    r=runtime();p=r.array(np.array([0,2,4],np.int32));c=r.array(np.array([0,1,0,1],np.int32));v=r.array(np.array([4.,1.,1.,3.]))
    s=CuDSS(r,p,c,v,2)
    try:
        s.analyze();s.factor();rhs=np.array([[1.,2.,3.],[2.,-.5,1.]])
        np.testing.assert_allclose(s.solve_many(rhs),np.linalg.solve([[4.,1.],[1.,3.]],rhs),rtol=1e-11,atol=1e-12)
    finally:s.close();p.close();c.close();v.close()

def test_large_gear_context_keeps_bound_evidence():
    from strength.agent import planning_context
    ctx={'geometry':{'surfaces':[{'id':i,'area':i/100} for i in range(501)],'components':[{'id':1,'face_ids':list(range(501)),'named_selections':{'all':list(range(501)),'bore':[243]}}],'named_selections':{'all':list(range(501))}},
        'current_case':{'assembly_mode':'gear_contact','gear_contact':{'hubs':[{'bore':{'face_ids':[243]}}]}},'saved_cases':[]}
    small=planning_context(ctx)
    assert [s['id'] for s in small['geometry']['surfaces']]==[243]
    assert len(ctx['geometry']['surfaces'])==501
    assert 'summary_notice' in small['geometry']

def test_nonfinite_contact_system_is_rejected():
    with pytest.raises(ValueError,match='非有限'):
        solve_contact_qp(np.array([[np.nan]]),np.array([[-.02]]),np.array([0.]),np.array([5.]))
