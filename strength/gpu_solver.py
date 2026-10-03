"""Native Windows CUDA C3D10 assembly + cuDSS FP64 solve + CUDA recovery."""
import hashlib, json, sys, time
from contextlib import contextmanager
from pathlib import Path
import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, kron
from .cuda_runtime import runtime, CuDSS, host_available
from .fem import IP_BARY, tet_shape
from .materials import property_array

_cached=None
CHUNK=16384

def clear_cache():
    global _cached
    if _cached is not None:_cached.close();_cached=None
    # Contact caches exist only after the optional contact module was loaded.
    # Ordinary static solves must not import its optimization dependencies.
    contact = sys.modules.get(f'{__package__}.gear_contact')
    if contact is not None:contact.clear_cache()

class GpuSystem:
    def __init__(self,mesh,case,prescribed,progress):
        self.r=runtime();self.mesh=mesh;self.solver=None;self.buffers=[];self.times={};self.progress=progress
        self.r.peak=self.r.allocated
        self.ndof=mesh['points'].size
        self.fixed=np.array(sorted(prescribed),np.int32)
        mask=np.ones(self.ndof,bool);mask[self.fixed]=False;self.free=np.flatnonzero(mask).astype(np.int32)
        self.E=property_array(mesh,case,'young_modulus_Pa');self.nu=property_array(mesh,case,'poisson_ratio')
        self.lam=self.E*self.nu/((1+self.nu)*(1-2*self.nu));self.mu=self.E/(2*(1+self.nu))
        self.ready=False
    def own(self,b):self.buffers.append(b);return b
    @contextmanager
    def phase(self,key,label):
        self.progress('solving',label);self.r.sync();t=time.perf_counter()
        try:yield
        finally:self.r.sync();self.times[key]=self.times.get(key,0)+time.perf_counter()-t
    def chunks(self):
        t=self.mesh['tets']
        for start in range(0,len(t),CHUNK):
            stop=min(start+CHUNK,len(t));n=stop-start
            td=self.r.array(t[start:stop].astype(np.int64));g=self.r.zeros((n,4,10,3));w=self.r.zeros((n,4));bad=self.r.zeros((1,),np.int32)
            try:
                self.r.launch('gradients',n*4,self.points,td,self.deriv,g,w,bad,n)
                if bad.get()[0]:raise ValueError('GPU 积分发现非正 Jacobian')
                yield start,stop,td,g,w
            finally:
                for b in (td,g,w,bad):b.close()
    def build(self):
        with self.phase('structure','构建 C3D10 稀疏连接结构'):
            t=self.mesh['tets'].astype(np.int32);nn=len(self.mesh['points'])
            estimate=len(t)*100*12+self.ndof*4096
            if estimate>host_available()*.8:raise MemoryError('稀疏连接结构预计超过可用主机内存预算')
            adjacency=coo_matrix((np.ones(t.size*10,bool),(np.repeat(t,10,axis=1).ravel(),np.tile(t,(1,10)).ravel())),shape=(nn,nn)).tocsr()
            structure=kron(adjacency,csr_matrix(np.ones((3,3),bool)),format='csr')[self.free][:,self.free]
            structure.sort_indices()
            if structure.nnz>=2**31:raise MemoryError('当前 GPU CSR 索引上限为 2^31−1')
        with self.phase('transfer','传输连接结构与几何到 GPU'):
            self.points=self.own(self.r.array(self.mesh['points']))
            self.lam_d=self.own(self.r.array(self.lam));self.mu_d=self.own(self.r.array(self.mu))
            self.deriv=self.own(self.r.array(np.array([tet_shape(q[1:])[1] for q in IP_BARY])))
            mapping=np.full(self.ndof,-1,np.int32);mapping[self.free]=np.arange(len(self.free),dtype=np.int32)
            self.mapping=self.own(self.r.array(mapping));self.free_d=self.own(self.r.array(self.free))
            self.ptr=self.own(self.r.array(structure.indptr.astype(np.int32)));self.col=self.own(self.r.array(structure.indices.astype(np.int32)))
            self.values=self.own(self.r.zeros((structure.nnz,)))
        del structure,adjacency
        if not len(self.free):self.ready=True;return
        with self.phase('assembly','GPU 双精度组装刚度矩阵'):
            for start,stop,t,g,w in self.chunks():
                self.r.launch('assemble',(stop-start)*900,t,g,w,self.mapping,self.ptr,self.col,self.values,self.lam_d,self.mu_d,start,stop-start)
        self.solver=CuDSS(self.r,self.ptr,self.col,self.values,len(self.free))
        with self.phase('analysis','cuDSS 重排序与符号分析'):self.solver.analyze()
        with self.phase('factorization','GPU cuDSS 双精度分解'):self.solver.factor()
        self.ready=True
    def recover(self,u,fields=True):
        internal=self.r.zeros((self.ndof,));stress=np.empty((len(self.mesh['tets']),4,6)) if fields else None;strain=np.empty_like(stress) if fields else None
        try:
            for start,stop,t,g,w in self.chunks():
                s=self.r.zeros((stop-start,4,6));ep=self.r.zeros((stop-start,4,6))
                try:
                    self.r.launch('recover',(stop-start)*4,t,g,u,w,s,ep,internal,self.lam_d,self.mu_d,start,stop-start)
                    if fields:stress[start:stop]=s.get();strain[start:stop]=ep.get()
                finally:s.close();ep.close()
            return internal,stress,strain
        except BaseException:internal.close();raise
    def solve(self,force,prescribed,reused=False):
        u0=np.zeros(self.ndof);u0[self.fixed]=[prescribed[int(i)] for i in self.fixed]
        with self.phase('rhs_transfer','构建载荷与给定位移右端项'):
            u=self.r.array(u0);f=self.r.array(force.ravel())
            if np.any(u0):internal,_,_=self.recover(u,False)
            else:internal=self.r.zeros((self.ndof,))
        try:
            if len(self.free):
                self.r.launch('gather_rhs',len(self.free),self.free_d,f,internal,self.solver.b,len(self.free))
                with self.phase('solve','GPU cuDSS 回代与迭代修正'):
                    x=self.solver.solve();self.r.launch('scatter',len(self.free),self.free_d,x,u,len(self.free))
            internal.close()
            with self.phase('recovery','GPU 恢复积分点应力、应变与节点内力'):
                internal,stress,strain=self.recover(u)
                internal_cpu=internal.get();displacements=u.get().reshape(-1,3)
            scale=max(np.linalg.norm(force.ravel()[self.free]),np.linalg.norm(internal_cpu[self.fixed]),1e-12)
            residual=float(np.linalg.norm((internal_cpu-force.ravel())[self.free])/scale)
            if residual>1e-8:raise ValueError(f'GPU 自由度平衡残差 {residual:g} 超过 1e-8')
            reactions=np.zeros(self.ndof);reactions[self.fixed]=(internal_cpu-force.ravel())[self.fixed]
            data={'displacements':displacements,'forces':internal_cpu.reshape(-1,3),'reactions':reactions.reshape(-1,3),'stresses':stress,'strains':strain}
            from .backends import nvidia_devices
            execution={'backend':'cudss_cuda','requested_device':'GPU','executed_device':'CUDA','array_adapter':'native_cuda',
                'precision':'float64','cudss_version':self.solver.version if self.solver else '0.8.0','devices':nvidia_devices(),
                'kernel_sha256':self.r.kernel_sha256,'cudss_sha256':hashlib.sha256(self.r.cudss_path.read_bytes()).hexdigest(),
                'factorization_reused':reused,'gpu_execution_verified':True,'kernel_trace_verified':False,
                'cudss_ir_status':self.solver.ir_status if self.solver else 0,
                'verification_basis':'CUDA kernels and cuDSS GPU mode completed; independent force residual checked; trace evidence is separate',
                'timings_s':dict(self.times),'compute_s':sum(self.times.values()),'relative_residual':residual,
                'buffer_peak_bytes':self.r.peak,'memory_budget_bytes':self.r.budget,
                'factor_memory_estimates_bytes':self.solver.estimates if self.solver else [],
                'note':'原生 Windows CUDA / cuDSS · FP64；GPU 组装、分解、回代和结果恢复；CPU 符号分析'}
            return data,execution
        finally:u.close();f.close();internal.close()
    def close(self):
        if self.solver:self.solver.close();self.solver=None
        for b in self.buffers:b.close()
        self.buffers=[]

def solve_gpu(mesh,case,force,prescribed,progress=lambda *a:None):
    global _cached
    started=time.perf_counter()
    h=hashlib.sha256()
    for a in (mesh['points'],mesh['tets'],np.array(sorted(prescribed),np.int64)):h.update(np.ascontiguousarray(a).tobytes())
    for name in ('young_modulus_Pa','poisson_ratio'):h.update(property_array(mesh,case,name).tobytes())
    key=h.hexdigest()
    reused=_cached is not None and _cached.key==key and _cached.ready
    try:
        if not reused:
            clear_cache();_cached=GpuSystem(mesh,case,prescribed,progress);_cached.key=key;_cached.build()
        else:_cached.times={};_cached.progress=progress
        data,execution=_cached.solve(force,prescribed,reused)
        execution['compute_s']=time.perf_counter()-started
        return data,execution
    except BaseException:clear_cache();raise
