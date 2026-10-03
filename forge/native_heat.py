"""Native cuDSS solve adapter for the preserved P1 thermal equations."""
from functools import lru_cache
import os,numpy as np


@lru_cache(maxsize=1)
def preflight():
    from strength.cuda_runtime import self_test
    return self_test()


def gpu_status(mode=None):
    requested=os.environ.get('THERMAL_DEVICE','auto').lower()
    if requested=='cpu':return {'requested':requested,'device':'cpu','available':False,'name':None,'reason':'forced CPU'}
    try:
        evidence=preflight()
        from strength.backends import nvidia_devices
        devices=nvidia_devices();name=devices[0]['name'] if devices else 'CUDA device 0'
        selected='cpu' if requested=='auto' and mode=='transient' else 'cuda'
        return {'requested':requested,'device':selected,'available':True,'name':name,'index':0,'adapter':'native_cudss','preflight':evidence}
    except Exception as exc:
        if requested in ('cuda','gpu'):raise RuntimeError('已请求 CUDA，但原生热求解后端不可用：'+str(exc)) from exc
        return {'requested':requested,'device':'cpu','available':False,'name':None,'reason':str(exc)}


class NativeFactor:
    def __init__(self,A):
        from strength.cuda_runtime import runtime,CuDSS
        self.A=A.tocsr(copy=True);self.A.sum_duplicates();self.A.sort_indices();self.buffers=[];self.solver=None
        if self.A.nnz>=2**31:raise MemoryError('CUDA 热方程超过 32 位索引限制')
        try:
            r=runtime()
            for v in (self.A.indptr.astype(np.int32),self.A.indices.astype(np.int32),self.A.data.astype(np.float64)):self.buffers.append(r.array(v))
            self.solver=CuDSS(r,*self.buffers,self.A.shape[0]);self.solver.analyze();self.solver.factor()
        except BaseException:self.close();raise
    def solve(self,rhs):
        self.solver.b.set(rhs);x=self.solver.solve().get()
        error=np.linalg.norm(self.A@x-rhs)/max(np.linalg.norm(rhs),1e-12)
        if not np.isfinite(x).all() or error>1e-8:raise RuntimeError(f'CUDA 热方程残差超限：{error:g}；未切换 CPU')
        return x
    def close(self):
        if self.solver:self.solver.close();self.solver=None
        for v in self.buffers:v.close()
        self.buffers=[]
    def __del__(self):self.close()
