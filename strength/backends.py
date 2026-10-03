"""Host-controlled solver backends. The Agent cannot supply commands or paths."""
import csv, hashlib, os, shutil, subprocess, sys, json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from .config import CCX, CPU_THREADS, MAX_DOFS

_last_gpu_diagnostic = None

@lru_cache(maxsize=1)
def gpu_diagnostic():
    global _last_gpu_diagnostic
    result={'device_detected':bool(nvidia_devices()),'cuda_initialized':False,'cudss_loaded':False,'self_test_passed':False}
    try:
        p=subprocess.run([sys.executable,'-m','strength.cuda_runtime'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if not p.stdout.strip():raise RuntimeError(p.stderr[-2000:])
        result.update(json.loads(p.stdout.strip().splitlines()[-1]))
    except Exception as e:result['error']=str(e)
    _last_gpu_diagnostic = result
    return result

@dataclass(frozen=True)
class NativeCudaBackend:
    id: str='cudss_cuda'


@lru_cache(maxsize=1)
def nvidia_devices():
    executable=shutil.which('nvidia-smi')
    if not executable: return []
    try:
        p=subprocess.run([executable,'--query-gpu=name,memory.total,driver_version','--format=csv,noheader,nounits'],
            capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if p.returncode: return []
        return [{'name':r[0].strip(),'memory_MiB':int(r[1].strip()),'driver':r[2].strip()} for r in csv.reader(p.stdout.splitlines()) if len(r)==3]
    except (OSError,ValueError,subprocess.TimeoutExpired): return []


def capabilities():
    cuda=os.environ.get('STRENGTH_CCX_CUDA',''); devices=nvidia_devices()
    configured=bool(cuda and Path(cuda).is_file())
    diagnostic=_last_gpu_diagnostic or {'device_detected':bool(devices),'cuda_initialized':False,'cudss_loaded':False,
        'self_test_passed':False,'error':'GPU 尚未自检；CPU 模式无需 CUDA' if devices else '未检测到 NVIDIA GPU；本机使用 CPU'}
    return {'devices':devices,'cpu_threads':CPU_THREADS,'max_dofs':MAX_DOFS,'gpu_diagnostic':diagnostic,'backends':[
        {'id':'calculix_cpu','name':'CalculiX 2.23 · CPU','available':CCX.is_file(),'validated':True,
         'detail':'已通过本平台解析解和静力平衡回归验证'},
        {'id':'cudss_cuda','name':'原生 CUDA + cuDSS · FP64','available':diagnostic['self_test_passed'],'validated':diagnostic['self_test_passed'],
         'detail':'CUDA 初始化、cuDSS 加载和真实方程自检通过；性能以计算记录为准' if diagnostic['self_test_passed'] else diagnostic.get('error','GPU 自检未通过')},
        {'id':'calculix_cuda','name':'CalculiX + PaStiX · CUDA','available':configured and bool(devices),'validated':False,
         'detail':'外部 PaStiX/CUDA 构建接入；需要独立验证，未承诺加速比' if configured else '需配置专门编译的 CalculiX 2.23 + PaStiX/CUDA 可执行文件'}]}


@dataclass(frozen=True)
class CalculixBackend:
    id: str
    executable: Path
    device_request: str

    def environment(self):
        # PASTIX_GPU is implemented by CalculiX 2.23 src/pastix.c.
        return {**os.environ,'OMP_NUM_THREADS':str(CPU_THREADS),
            'CCX_NPROC_RESULTS':str(CPU_THREADS),
            'PASTIX_GPU':'1' if self.id=='calculix_cuda' else '0'}

    def metadata(self):
        return {'backend':self.id,'requested_device':self.device_request,'cpu_threads':CPU_THREADS,
            'executable_sha256':hashlib.sha256(self.executable.read_bytes()).hexdigest(),
            'gpu_execution_verified':False,
            'note':'CPU 求解' if self.id=='calculix_cpu' else '已请求 PaStiX GPU；是否实际卸载及其比例须查 solver.log / GPU 监测，未自动回退 CPU'}


def resolve_backend(name='calculix_cpu'):
    if name=='cudss_cuda':
        diagnostic=gpu_diagnostic()
        if not diagnostic['self_test_passed']:raise ValueError('GPU 自检未通过，未回退 CPU：'+diagnostic.get('error','设备不可用'))
        return NativeCudaBackend()
    if name=='calculix_cpu': path=CCX; device='CPU'
    elif name=='calculix_cuda':
        configured=os.environ.get('STRENGTH_CCX_CUDA')
        if not configured: raise ValueError('CUDA 求解后端未安装；请配置专用 PaStiX/CUDA 构建。未自动改为 CPU。')
        if not nvidia_devices(): raise ValueError('未检测到可用 NVIDIA 驱动与显卡')
        path=Path(configured).expanduser().resolve(); device='CUDA requested'
        if path==CCX.resolve(): raise ValueError('CUDA 后端必须使用独立构建，不能指向随附 CPU 可执行文件')
    else: raise ValueError('未知求解后端')
    if not path.is_file(): raise ValueError('求解器文件不存在，请运行安装程序或修正宿主配置')
    return CalculixBackend(name,path,device)
