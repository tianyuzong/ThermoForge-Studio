import numpy as np
import pytest
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve
import thermal_legacy.solver as solver


def test_cuda_repeated_rhs_and_zero_rhs_match_direct_solution(monkeypatch):
    if solver.cp is None:pytest.skip('CUDA not installed')
    monkeypatch.setenv('THERMAL_DEVICE','auto')
    if solver.gpu_status()['device']!='cuda':pytest.skip('CUDA unavailable')
    assert solver.gpu_status('transient')['device']=='cpu'
    assert solver.gpu_status('transient')['available']
    monkeypatch.setenv('THERMAL_DEVICE','cuda')
    assert solver.gpu_status('transient')['device']=='cuda'
    n=80
    A=diags([-np.ones(n-1),np.linspace(3,5,n),-np.ones(n-1)],[-1,0,1],format='csr')
    factor=solver._CudaFactor(A)
    for rhs in (np.arange(n,dtype=float),np.full(n,-3.),np.zeros(n),np.ones(n)):
        actual=factor.solve(rhs)
        np.testing.assert_allclose(actual,spsolve(A,rhs),rtol=1e-8,atol=1e-8)
        assert np.linalg.norm(A@actual-rhs)<max(1,np.linalg.norm(rhs))*1e-8
