"""Keep NVRTC's matching builtins loaded across native geometry initialization."""
import ctypes
import sys
from pathlib import Path


_BUILTINS_HANDLES = {}


def _windows_builtins(compiler_path, version):
    # Resolve beside the compiler CuPy actually loaded, not a different CUDA_PATH.
    compiler = Path(compiler_path).resolve()
    major, minor = version
    builtins = compiler.with_name(f'nvrtc-builtins64_{major}{minor}.dll')
    if not builtins.is_file():
        raise RuntimeError(f'NVRTC {major}.{minor} 配套 DLL 不存在：{builtins}；'
                           '请修复同版本 CUDA 安装并重启服务')
    key = str(builtins)
    if key not in _BUILTINS_HANDLES:
        try:
            # NVRTC's lazy basename lookup can fail after Gmsh initialization on
            # Windows. An absolute load works; retaining the handle prevents
            # unloading before later, uncached sparse kernels are compiled.
            _BUILTINS_HANDLES[key] = ctypes.WinDLL(key)
        except OSError as error:
            raise RuntimeError(f'无法加载 NVRTC 配套 DLL：{builtins}：{error}') from error
    return dict(compiler_dll=str(compiler), builtins_dll=key)


def prepare_nvrtc():
    from cupy_backends.cuda.libs import nvrtc
    version = tuple(int(v) for v in nvrtc.getVersion())
    evidence = dict(nvrtc_version='.'.join(map(str, version)))
    if sys.platform == 'win32':
        # CuPy 14 uses this same loader, including its already-loaded library
        # detection. This also supports Toolkit, conda, and pip runtime layouts.
        from cuda.pathfinder import load_nvidia_dynamic_lib
        compiler = load_nvidia_dynamic_lib('nvrtc')
        evidence.update(_windows_builtins(compiler.abs_path, version))
    return evidence
