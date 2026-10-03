"""Small native CUDA/NVRTC adapter. No shell execution or security-policy changes.

CuPy's Windows cp314 CUB extension is blocked by Smart App Control on the target
machine. This adapter uses the permitted NVIDIA libraries and the same CUDA
kernels directly. cuDSS remains the numerical solver; no CPU substitution.
"""
import ctypes as C
import hashlib
import os
from pathlib import Path
import sys
import numpy as np

P=C.c_void_p; I=C.c_int; L=C.c_int64; Z=C.c_size_t
_runtime=None

def host_available():
    class Memory(C.Structure):
        _fields_=[('length',C.c_ulong),('load',C.c_ulong)]+[(n,C.c_ulonglong) for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
    m=Memory();m.length=C.sizeof(m)
    C.windll.kernel32.GlobalMemoryStatusEx(C.byref(m))
    return m.available

class DeviceArray:
    def __init__(self,runtime,shape,dtype=np.float64,source=None,zero=False):
        self.runtime=runtime; self.shape=tuple(shape);self.dtype=np.dtype(dtype);self.ptr=P()
        self.nbytes=int(np.prod(shape))*self.dtype.itemsize
        runtime.check_memory(self.nbytes)
        runtime.call('cudaMalloc',C.byref(self.ptr),Z(max(self.nbytes,1)))
        runtime.allocated+=self.nbytes;runtime.peak=max(runtime.peak,runtime.allocated)
        if source is not None:self.set(source)
        elif zero:self.zero()
    def set(self,a):
        a=np.ascontiguousarray(a,dtype=self.dtype)
        if a.nbytes!=self.nbytes:raise ValueError('CUDA buffer shape mismatch')
        self.runtime.call('cudaMemcpy',self.ptr,P(a.ctypes.data),Z(a.nbytes),I(1))
    def get(self):
        a=np.empty(self.shape,dtype=self.dtype)
        self.runtime.call('cudaMemcpy',P(a.ctypes.data),self.ptr,Z(a.nbytes),I(2));return a
    def zero(self):self.runtime.call('cudaMemset',self.ptr,I(0),Z(self.nbytes))
    def close(self):
        if self.ptr.value:
            self.runtime.lib.cudaFree(self.ptr);self.ptr=P();self.runtime.allocated-=self.nbytes
    def __del__(self):
        if getattr(self,'ptr',None):self.close()

class CudaRuntime:
    def __init__(self):
        if os.name!='nt':raise RuntimeError('此 GPU 分发包要求 Windows x64')
        site=Path(sys.prefix)/'Lib'/'site-packages'/'nvidia'
        toolkit=Path(os.environ.get('CUDA_PATH',r'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6'))
        candidates=[site/'cu12'/'bin',site/'cublas'/'bin',toolkit/'bin']
        self.paths=[p for p in candidates if p.is_dir()]
        self.dll_dirs=[os.add_dll_directory(str(p)) for p in self.paths]
        self.lib=C.WinDLL(str(toolkit/'bin'/'cudart64_12.dll'))
        self.driver=C.WinDLL('nvcuda.dll')
        self.nvrtc=C.WinDLL(str(toolkit/'bin'/'nvrtc64_120_0.dll'))
        self.builtins=[C.WinDLL(str(p)) for p in (toolkit/'bin').glob('nvrtc-builtins64_*.dll')]
        self.allocated=0;self.peak=0
        self.lib.cudaGetErrorString.argtypes=[I];self.lib.cudaGetErrorString.restype=C.c_char_p
        self.call('cudaFree',P())
        free,total=self.memory();self.initial_free=free;self.budget=int(free*.8)
        self.cudss_path=site/'cu12'/'bin'/'cudss64_0.dll'
        self.functions={};self.module=None
    def call(self,name,*args):
        code=getattr(self.lib,name)(*args)
        if code:raise RuntimeError(f'{name}: {self.lib.cudaGetErrorString(code).decode(errors="replace")}')
    def sync(self):self.call('cudaDeviceSynchronize')
    def memory(self):
        a=Z();b=Z();self.call('cudaMemGetInfo',C.byref(a),C.byref(b));return a.value,b.value
    def check_memory(self,extra):
        if self.allocated+extra>self.budget or extra>self.memory()[0]*.8:
            raise MemoryError('GPU 显存预算不足（空闲显存的 80%）；未粗化网格或回退 CPU')
    def array(self,a):
        a=np.ascontiguousarray(a);return DeviceArray(self,a.shape,a.dtype,source=a)
    def zeros(self,shape,dtype=np.float64):return DeviceArray(self,shape,dtype,zero=True)
    def _nv(self,name,*args):
        code=getattr(self.nvrtc,name)(*args)
        if code:raise RuntimeError(f'{name} failed: {code}')
    def compile(self):
        if self.module:return
        source=Path(__file__).with_name('cuda_kernels.cu').read_bytes(); prog=P()
        self._nv('nvrtcCreateProgram',C.byref(prog),C.c_char_p(source),C.c_char_p(b'strength.cu'),I(0),P(),P())
        try:
            options=(C.c_char_p*2)(b'--gpu-architecture=compute_86',b'--std=c++14')
            code=self.nvrtc.nvrtcCompileProgram(prog,I(2),options)
            if code:
                n=Z();self._nv('nvrtcGetProgramLogSize',prog,C.byref(n));log=C.create_string_buffer(n.value)
                self._nv('nvrtcGetProgramLog',prog,log);raise RuntimeError(log.value.decode(errors='replace'))
            size=Z();self._nv('nvrtcGetPTXSize',prog,C.byref(size));ptx=C.create_string_buffer(size.value)
            self._nv('nvrtcGetPTX',prog,ptx);self.module=P()
            self._driver('cuModuleLoadData',C.byref(self.module),ptx)
            self.kernel_sha256=hashlib.sha256(source).hexdigest()
        finally:self.nvrtc.nvrtcDestroyProgram(C.byref(prog))
    def _driver(self,name,*args):
        code=getattr(self.driver,name)(*args)
        if code:raise RuntimeError(f'{name}: CUDA driver error {code}')
    def launch(self,name,n,*args):
        if n==0:return
        self.compile()
        if name not in self.functions:
            f=P();self._driver('cuModuleGetFunction',C.byref(f),self.module,C.c_char_p(name.encode()));self.functions[name]=f
        values=[a.ptr if isinstance(a,DeviceArray) else C.c_double(a) if isinstance(a,(float,np.floating)) else I(int(a)) for a in args]
        pointers=(P*len(values))(*[C.cast(C.byref(v),P) for v in values])
        self._driver('cuLaunchKernel',self.functions[name],I((n+127)//128),I(1),I(1),I(128),I(1),I(1),I(0),P(),pointers,P())

def runtime():
    global _runtime
    if _runtime is None:_runtime=CudaRuntime()
    return _runtime

class CuDSS:
    """ABI for cuDSS 0.8; constants checked against installed 0.8 headers.
    NVIDIA cuDSS is provided AS IS without warranty; see THIRD_PARTY.md.
    """
    def __init__(self,r,ptr,col,values,n):
        self.r=r;self.lib=C.WinDLL(str(r.cudss_path));self.h=P();self.config=P();self.data=P();self.A=P();self.xm=P();self.bm=P()
        self.n=n;self.ptr=ptr;self.col=col;self.values=values
        signatures={
            'cudssCreate':[C.POINTER(P)],'cudssDestroy':[P],'cudssConfigCreate':[C.POINTER(P)],'cudssConfigDestroy':[P],
            'cudssDataCreate':[P,C.POINTER(P)],'cudssDataDestroy':[P,P],
            'cudssConfigSet':[P,I,P,Z],'cudssDataGet':[P,P,I,P,Z,C.POINTER(Z)],
            'cudssExecute':[P,I,P,P,P,P,P],'cudssMatrixDestroy':[P],
            'cudssMatrixCreateCsr':[C.POINTER(P),L,L,L,P,P,P,P,I,I,I,I,I,I],
            'cudssMatrixCreateDn':[C.POINTER(P),L,L,L,P,I,I],
            'cudssMatrixSetValues':[P,P],'cudssGetProperty':[I,C.POINTER(I)],
            'cudssSetThreadingLayer':[P,C.c_char_p]}
        for name,types in signatures.items():getattr(self.lib,name).argtypes=types;getattr(self.lib,name).restype=I
        self.call('cudssCreate',C.byref(self.h));self.call('cudssConfigCreate',C.byref(self.config));self.call('cudssDataCreate',self.h,C.byref(self.data))
        version=[]
        for k in range(3):v=I();self.call('cudssGetProperty',k,C.byref(v));version.append(v.value)
        if version[:2]!=[0,8]:raise RuntimeError(f'cuDSS ABI version mismatch: {version}')
        self.version='.'.join(map(str,version))
        self.set(11,I(0));self.set(15,I(0));self.set(9,C.c_double(0));self.set(5,I(3));self.set(6,C.c_double(1e-10))
        # Gmsh's OpenMP runtime conflicts with the optional cuDSS VCOMP layer in
        # this Windows process. Use default host analysis; numerical work is CUDA.
        # CUDA_R_32I=10, CUDA_R_64F=1; SPD=3, FULL=0, zero based.
        self.call('cudssMatrixCreateCsr',C.byref(self.A),n,n,values.shape[0],ptr.ptr,None,col.ptr,values.ptr,10,10,1,3,0,0)
        self.x=r.zeros((n,));self.b=r.zeros((n,))
        for obj,buf in ((self.xm,self.x),(self.bm,self.b)):self.call('cudssMatrixCreateDn',C.byref(obj),n,1,n,buf.ptr,1,0)
    def call(self,name,*args):
        code=getattr(self.lib,name)(*args)
        if code:raise RuntimeError(f'{name}: cuDSS error {code}；未切换 CPU')
    def set(self,k,v):self.call('cudssConfigSet',self.config,k,C.byref(v),C.sizeof(v))
    def get(self,k,v):
        size=Z();self.call('cudssDataGet',self.h,self.data,k,C.byref(v),C.sizeof(v),C.byref(size));return v
    def execute(self,phase):
        self.call('cudssExecute',self.h,phase,self.config,self.data,self.A,self.xm,self.bm);self.r.sync()
        info=self.get(0,I()).value
        if info:raise ValueError(f'cuDSS device status {info}：刚度非正定、奇异或数值异常；未修改约束')
    def analyze(self):
        self.execute(3);m=list(self.get(13,(L*16)()));self.estimates=m
        self.r.check_memory(m[1])
        if m[3]>host_available()*.8:raise MemoryError('cuDSS 估算主机内存不足')
    def factor(self):self.execute(4)
    def solve(self):
        status=self.lib.cudssExecute(self.h,1008,self.config,self.data,self.A,self.xm,self.bm);self.r.sync()
        self.ir_status=int(status)
        if status not in (0,7):raise RuntimeError(f'cuDSS solve error {status}')
        info=self.get(0,I()).value
        if info:raise ValueError(f'cuDSS device status {info}')
        # Status 7 preserves the solution. It is accepted only if the separate
        # element-integration equilibrium check in gpu_solver passes 1e-8.
        return self.x
    def solve_many(self,rhs):
        """Reuse the numerical factorization for a bounded block of column RHSs."""
        rhs=np.asarray(rhs,dtype=np.float64)
        if rhs.ndim!=2 or rhs.shape[0]!=self.n:raise ValueError('cuDSS RHS shape mismatch')
        # DeviceArray stores C-order; transposed columns are contiguous in memory.
        b=self.r.array(rhs.T.copy());x=self.r.zeros(rhs.T.shape);bm=P();xm=P()
        try:
            for obj,buf in ((bm,b),(xm,x)):
                self.call('cudssMatrixCreateDn',C.byref(obj),self.n,rhs.shape[1],self.n,buf.ptr,1,0)
            status=self.lib.cudssExecute(self.h,1008,self.config,self.data,self.A,xm,bm);self.r.sync()
            if status not in (0,7):raise RuntimeError(f'cuDSS multi-RHS error {status}')
            if self.get(0,I()).value:raise ValueError('cuDSS multi-RHS device error')
            return x.get().T
        finally:
            for obj in (bm,xm):
                if obj.value:self.lib.cudssMatrixDestroy(obj)
            b.close();x.close()
    def close(self):
        for name,obj in (('cudssMatrixDestroy',self.A),('cudssMatrixDestroy',self.xm),('cudssMatrixDestroy',self.bm)):
            if obj.value:getattr(self.lib,name)(obj);obj.value=None
        if self.data.value:self.lib.cudssDataDestroy(self.h,self.data);self.data.value=None
        if self.config.value:self.lib.cudssConfigDestroy(self.config);self.config.value=None
        if self.h.value:self.lib.cudssDestroy(self.h);self.h.value=None
        if hasattr(self,'x'):self.x.close();self.b.close()
    def __del__(self):
        if hasattr(self,'h'):self.close()

def self_test():
    r=runtime();p=r.array(np.array([0,2,4],np.int32));c=r.array(np.array([0,1,0,1],np.int32));v=r.array(np.array([4.,1.,1.,3.]))
    s=CuDSS(r,p,c,v,2)
    try:
        s.b.set(np.array([1.,2.]));s.analyze();s.factor();x=s.solve().get()
        np.testing.assert_allclose(x,[1/11,7/11],rtol=1e-12,atol=1e-14)
        return {'passed':True,'backend':'cudss_cuda','cudss':s.version,'array_adapter':'native_cuda','solution':x.tolist(),'free_bytes':r.memory()[0],'kernel_trace_verified':False}
    finally:s.close();p.close();c.close();v.close()

def diagnostic():
    result={'cuda_initialized':False,'cudss_loaded':False,'kernels_compiled':False,'self_test_passed':False}
    try:
        r=runtime();result['cuda_initialized']=True
        library=C.WinDLL(str(r.cudss_path));result['cudss_loaded']=True
        r.compile();result['kernels_compiled']=True
        result['details']=self_test();result['self_test_passed']=True
    except Exception as e:result['error']=str(e)
    return result

if __name__=='__main__':
    import json
    result=diagnostic();print(json.dumps(result,ensure_ascii=False));sys.exit(0 if result['self_test_passed'] else 1)
