import numpy as np
import pytest


@pytest.mark.skipif(__import__('os').environ.get('THERMOFORGE_TEST_GPU')!='1',reason='explicit GPU acceptance run')
def test_native_heat_preserves_cpu_temperature(monkeypatch):
    from scipy.sparse import csr_matrix
    from thermal_legacy.schemas import Simulation
    from thermal_legacy.solver import integrate
    monkeypatch.setenv('THERMAL_CUDA_BACKEND','native_cudss')
    cfg=Simulation(model_id='benchmark',duration_s=13,dt_s=4,save_s=5,heat_sources=[dict(name='heater',power_W=6,start_s=2,end_s=11,faces=[0])]).model_dump()
    M=csr_matrix([[2.,1.],[1.,2.]]);K=csr_matrix([[3.,-3.],[-3.,3.]]);Z=csr_matrix((2,2))
    monkeypatch.setenv('THERMAL_DEVICE','cpu');cpu=integrate(M,K,Z,np.zeros(2),[np.ones(2)*3],cfg)
    monkeypatch.setenv('THERMAL_DEVICE','cuda');gpu=integrate(M,K,Z,np.zeros(2),[np.ones(2)*3],cfg)
    assert np.max(np.abs(cpu[0]-gpu[0]))<1e-8
    assert np.array_equal(cpu[1],gpu[1])
