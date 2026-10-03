from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thermal_legacy.runtime import *
import numpy as np
from scipy.sparse import csr_matrix
from thermal_legacy.schemas import Simulation
from thermal_legacy.solver import integrate, make_slice, _boundary_component_ids, _air_gap_coupling

def test_uniform_adiabatic_heating_and_nondivisible_time_grid():
    cfg=Simulation(model_id='test',duration_s=13,dt_s=4,save_s=5,heat_sources=[dict(name='heater',power_W=6,start_s=2,end_s=11,faces=[0])]).model_dump()
    M=csr_matrix([[2.,1.],[1.,2.]]);K=csr_matrix([[3.,-3.],[-3.,3.]]);Z=csr_matrix((2,2))
    frames,times,stats,energy=integrate(M,K,Z,np.zeros(2),[np.ones(2)*3],cfg)
    expected=np.clip(times-2,0,9)
    np.testing.assert_allclose(frames,np.column_stack([expected,expected]),atol=1e-6)
    assert times.tolist()==[0,5,10,13]
    assert abs(energy['input_energy_J']-54)<1e-9
    assert abs(energy['energy_balance_error_J'])<1e-9

def test_equal_temperature_and_ambient_stays_constant():
    cfg=Simulation(model_id='test',initial_C=31,ambient_C=31,duration_s=10,dt_s=2,save_s=5).model_dump()
    I=csr_matrix(np.eye(2));K=csr_matrix([[1.,-1.],[-1.,1.]])
    frames,*_=integrate(I,K,I,np.zeros(2),[],cfg)
    np.testing.assert_array_equal(frames,np.zeros_like(frames))

def test_cut_preserves_a_linear_temperature_field():
    pts=np.array([[0.,0,0],[1.,0,0],[0.,1,0],[0.,0,1]])
    temp=(25+pts@np.array([2,3,5]))[None,:]
    p,f,t=make_slice(pts,np.array([[0,1,2,3]]),temp,0,.25)
    assert len(f)==1
    np.testing.assert_allclose(t[0],25+p@np.array([2,3,5]))

def test_rejects_nonfinite_and_oversized_runs():
    import pytest
    for update in [dict(initial_C=float('nan')),dict(dt_s=.000001),dict(save_s=.001),dict(regions=[dict(min_m=[0,0,0],max_m=[0,1,1],material=dict(name='x',k=1,rho=1,cp=1))])]:
        with pytest.raises(ValueError):Simulation(model_id='test',**update)


def test_air_gap_coupling_connects_nearby_components_and_conserves_constant_field():
    points=np.array([[0.,0,0],[0,1,0],[0,0,1],[1,0,0],
                     [.2,0,0],[.2,1,0],[.2,0,1],[1.2,0,0]])
    tets=np.array([[0,1,2,3],[4,5,6,7]])
    boundary=np.array([[0,1,2],[4,5,6]])
    components=_boundary_component_ids(tets,np.array([0,1]),boundary)
    assert components.tolist()==[0,1]
    coupling,audit=_air_gap_coupling(points,boundary,components,True,.026,.5)
    assert audit['pairs']==1
    assert audit['conductance_W_K']>0
    np.testing.assert_allclose(coupling@np.ones(len(points)),0,atol=1e-14)
    assert coupling[0,4]<0
