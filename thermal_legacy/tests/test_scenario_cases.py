import numpy as np
import pytest
from scipy.sparse import csr_matrix
from thermal_legacy.scenario_cases import clipped_quadrature, surface_metrics
from thermal_legacy.solver import integrate, assemble
from thermal_legacy.schemas import Simulation
from test_agent_skill import blocks
from test_thermoelastic import cube
import thermal_legacy.agent as agent; import thermal_legacy.agent_skill as agent_skill
import json


@pytest.mark.parametrize('dtype',[np.float64,np.float32])
def test_rectangle_clipping_exact_area_and_linear_moment(dtype):
    tri=np.array([[[-.05,-.03,-.004],[.05,-.03,-.004],[.05,.03,-.004]],
                  [[-.05,-.03,-.004],[.05,.03,-.004],[-.05,.03,-.004]]])
    box=dict(min_m=[-.04,-.018,-.004],max_m=[.04,.018,-.004])
    tri=tri.astype(dtype).astype(float)
    p,b,w=clipped_quadrature(tri,box)
    xyz=np.einsum('qij,qi->qj',tri[p],b)
    assert w.sum()==pytest.approx(.08*.036)
    np.testing.assert_allclose(w@xyz/w.sum(),[0,0,-.004],atol=1e-9)
    assert (w@xyz[:,0]**2)/w.sum()==pytest.approx(.04**2/3)


def test_continuation_and_ambient_ramp_energy_conservation():
    cfg=Simulation(model_id='test',initial_C=20,ambient_C=20,duration_s=2,dt_s=1,save_s=1,
        environment_only=True,ambient_profile=[dict(time_s=0,ambient_C=20),dict(time_s=2,ambient_C=24)]).model_dump()
    M=csr_matrix([[2.]]);C=csr_matrix([[1.]]);Z=csr_matrix((1,1))
    f,t,s,e=integrate(M,Z,C,np.zeros(1),[],cfg,initial_temperature=np.array([30.]))
    # Backward Euler exact recurrence Cth*(Tnew-Told)/dt=hA*(Tamb_new-Tnew).
    np.testing.assert_allclose(f[:,0]+20,[30,82/3,(2*(82/3)+24)/3],rtol=1e-7)
    assert e['initial_stored_energy_J']==20
    assert abs(e['energy_balance_error_J'])<1e-12


def test_surface_metrics_area_mean_and_rigid_rotation_invariance():
    mesh=cube();p=mesh['points'];faces=mesh['boundary_triangles']
    display=dict(points=p,faces=faces)
    box=dict(min_m=[0,0,0],max_m=[.02,.01,0])
    cfg=dict(ambient_C=20,surface_evaluation=dict(name='test',faces=list(range(len(faces))),surface_box=box,reference_power_W=10))
    T=(20+100*p[:,0])[None,:]
    angle=.3;R=np.array([[np.cos(angle),0,np.sin(angle)],[0,1,0],[-np.sin(angle),0,np.cos(angle)]])
    U=(p@R.T-p+np.array([.1,.2,.3]))[None,:,:]
    r=surface_metrics(mesh,T,U,[0],cfg,display)
    assert r['area_m2']==pytest.approx(.0002)
    assert r['rows'][0]['average_C']==pytest.approx(21)
    assert r['rows'][0]['delta_C']==pytest.approx(2)
    assert r['rows'][0]['flatness_m']<1e-12


def test_agent_patch_controls_and_codex_roundtrip(blocks):
    cfg,_=blocks
    p=('取消纯环境工况。清空热源。新增面热源150W，选区盒(-25,-15,-5,5,-10,-10)mm。'
       '评估接触面选区盒(-25,-15,-5,5,-10,-10)mm。接触面温度限值100摄氏度。接触面翘曲限值0.05mm。热阻参考功率150W。'
       '基础材料设为铝，清除屈服强度。')
    r=agent.plan_request('blocks',p,cfg)
    assert r['ok'],r['questions']
    c=r['config'];assert len(c['heat_sources'])==1
    assert c['heat_sources'][0]['surface_box']['min_m']==[-.025,-.005,-.01]
    assert c['surface_evaluation']['reference_power_W']==150
    assert c['base_material']['yield_strength_Pa'] is None
    prepared=agent_skill.prepare(agent,'blocks',p,cfg)
    roundtrip,q=agent_skill.compile_review(agent,prepared,json.dumps(dict(patch=[],questions=[])))
    assert not q and roundtrip==c


def test_clipped_heat_load_integrates_exact_power():
    mesh=cube();p=mesh['points'];faces=mesh['boundary_triangles']
    display=dict(points=p,faces=faces,is_outer=np.ones(len(faces),bool))
    cfg=Simulation(model_id='test',air_gap_enabled=False,default_h=0,
        heat_sources=[dict(name='small patch',faces=list(range(len(faces))),power_W=150,
            surface_box=dict(min_m=[.004,.002,0],max_m=[.016,.008,0]))]).model_dump()
    data=assemble(mesh,cfg,display)
    assert data[4][0].sum()==pytest.approx(150)
    assert data[8]['heat_sources'][0]['mapped_area_m2']==pytest.approx(.012*.006)
