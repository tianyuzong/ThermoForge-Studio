import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import thermal_legacy.cuda_runtime as cuda_runtime
import thermal_legacy.solver as solver
from thermal_legacy.compute_policy import worker_environment


def test_builtins_are_loaded_beside_actual_compiler_and_retained(tmp_path, monkeypatch):
    compiler = tmp_path / 'nvrtc64_120_0.dll'
    builtins = tmp_path / 'nvrtc-builtins64_126.dll'
    builtins.touch()
    calls = []
    handle = object()
    monkeypatch.setattr(cuda_runtime, '_BUILTINS_HANDLES', {})
    monkeypatch.setattr(cuda_runtime.ctypes, 'WinDLL',
                        lambda path: calls.append(path) or handle, raising=False)
    monkeypatch.setenv('CUDA_PATH', str(tmp_path / 'wrong-version'))
    for _ in range(2):
        evidence = cuda_runtime._windows_builtins(compiler, (12, 6))
        assert evidence['builtins_dll'] == str(builtins.resolve())
    assert calls == [str(builtins.resolve())]
    assert cuda_runtime._BUILTINS_HANDLES[calls[0]] is handle


def test_missing_matching_builtins_does_not_load_another_version(tmp_path, monkeypatch):
    (tmp_path / 'nvrtc-builtins64_125.dll').touch()
    def unexpected_load(path):
        pytest.fail('must not load a mismatched DLL')
    monkeypatch.setattr(cuda_runtime.ctypes, 'WinDLL', unexpected_load, raising=False)
    with pytest.raises(RuntimeError, match='nvrtc-builtins64_126.dll'):
        cuda_runtime._windows_builtins(tmp_path / 'nvrtc64_120_0.dll', (12, 6))


def test_compile_failure_falls_back_only_in_auto_mode(monkeypatch):
    runtime = SimpleNamespace(getDeviceCount=lambda: 1,
                              getDeviceProperties=lambda i: {'name': b'test GPU'})
    fake_cp = SimpleNamespace(cuda=SimpleNamespace(runtime=runtime,
        Device=lambda i: SimpleNamespace(use=lambda: None)))
    monkeypatch.setattr(solver, 'cp', fake_cp)
    def broken(index):
        raise RuntimeError('nvrtc-builtins64_126.dll')
    monkeypatch.setattr(solver, '_cuda_preflight', broken)
    monkeypatch.setenv('THERMAL_DEVICE', 'auto')
    status = solver.gpu_status('steady')
    assert status['device'] == 'cpu' and not status['available']
    assert 'nvrtc-builtins64_126.dll' in status['reason']
    monkeypatch.setenv('THERMAL_DEVICE', 'cuda')
    with pytest.raises(RuntimeError, match='nvrtc-builtins64_126.dll'):
        solver.gpu_status('steady')
    monkeypatch.setenv('THERMAL_DEVICE', 'cpu')
    assert solver.gpu_status()['reason'] == 'forced CPU'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows NVRTC/Gmsh regression')
def test_two_fresh_workers_compile_before_and_after_gmsh(tmp_path):
    if solver.cp is None:
        pytest.skip('CuPy not installed')
    try:
        count = solver.cp.cuda.runtime.getDeviceCount()
    except Exception:
        pytest.skip('CUDA device unavailable')
    if not count:
        pytest.skip('CUDA device unavailable')
    script = r'''
import os, uuid
import gmsh, numpy as np, solver
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve
before = os.environ['GMSH_ORDER'] == 'before'
if before:
    gmsh.initialize(); gmsh.finalize()
status = solver.gpu_status('steady')
assert status['device'] == 'cuda', status
assert status['preflight']['kernel_test'] == 'passed'
assert status['preflight']['sparse_test'] == 'passed'
if not before:
    gmsh.initialize(); gmsh.finalize()
# Always compile a new kernel AFTER geometry initialization, with an empty
# process-specific cache. A cached readiness flag alone cannot cover this.
cp = solver.cp
source = 'extern "C" __global__ void after_mesh(double* x) {x[0]=42.;} // ' + uuid.uuid4().hex
x = cp.empty(1, dtype=cp.float64)
cp.RawKernel(source, 'after_mesh')((1,), (1,), (x,))
assert x.get()[0] == 42.
n = 80
A = diags([-np.ones(n-1), np.linspace(3, 5, n), -np.ones(n-1)], [-1, 0, 1], format='csr')
factor = solver._CudaFactor(A)
for rhs in (np.arange(n, dtype=float), np.full(n, -3.), np.zeros(n)):
    np.testing.assert_allclose(factor.solve(rhs), spsolve(A, rhs), rtol=1e-8, atol=1e-8)
print('passed', os.environ['GMSH_ORDER'], status['preflight'])
'''
    processes = []
    try:
        for order in ('before', 'after'):
            env = worker_environment()
            env.update(THERMAL_DEVICE='cuda', CUPY_CACHE_DIR=str(tmp_path / order), GMSH_ORDER=order)
            processes.append(subprocess.Popen([sys.executable, '-X', 'utf8', '-c', script],
                cwd=Path(solver.__file__).parent, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        results = [p.communicate(timeout=60)[0] for p in processes]
        for p, output in zip(processes, results):
            assert p.returncode == 0, output
    finally:
        for p in processes:
            if p.poll() is None:
                p.kill(); p.wait()
