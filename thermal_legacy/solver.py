from thermal_legacy.runtime import *
import csv, time, zipfile, threading, uuid
import numpy as np
import trimesh, h5py
from itertools import combinations
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import splu
from skfem import MeshTet, Basis, ElementTetP1, BilinearForm, asm
from skfem.helpers import dot, grad
from thermal_legacy.geometry import project, build_mesh, connected_tet_components

SIGMA = 5.670374419e-8

try:
    import cupy as cp
    import cupyx.scipy.sparse as cps
    from cupyx.scipy.sparse.linalg import cg as cupy_cg
except Exception as error:
    cp = None
    cps = None
    cupy_cg = None
    _CUPY_IMPORT_ERROR = f'{type(error).__name__}: {error}'
else:
    _CUPY_IMPORT_ERROR = None


_CUDA_PREFLIGHT = {}
_CUDA_PREFLIGHT_LOCK = threading.Lock()


def _cuda_preflight(index):
    """Compile and execute real kernels once per device/process before meshing."""
    with _CUDA_PREFLIGHT_LOCK:
        if index not in _CUDA_PREFLIGHT:
            try:
                from thermal_legacy.cuda_runtime import prepare_nvrtc
                evidence = prepare_nvrtc()
                # A unique source prevents a previous disk cache from hiding a
                # broken NVRTC installation. Do not clear the user's CuPy cache.
                code = ('extern "C" __global__ void thermal_probe(double* x) {'
                        'if (threadIdx.x < 4) x[threadIdx.x] = 2.0 * threadIdx.x + 1.0; }'
                        '// ' + uuid.uuid4().hex)
                kernel = cp.RawKernel(code, 'thermal_probe')
                values = cp.empty(4, dtype=cp.float64)
                kernel((1,), (4,), (values,))
                cp.cuda.get_current_stream().synchronize()
                np.testing.assert_array_equal(cp.asnumpy(values), [1., 3., 5., 7.])
                # Exercise the sparse diagonal, preconditioner and CG path that
                # previously failed only after geometry and matrix assembly.
                matrix = diags([-np.ones(3), np.full(4, 3.), -np.ones(3)],
                               [-1, 0, 1], format='csr')
                rhs = np.arange(1., 5.)
                solution = _CudaFactor(matrix).solve(rhs)
                np.testing.assert_allclose(matrix @ solution, rhs, rtol=1e-8, atol=1e-10)
                _CUDA_PREFLIGHT[index] = dict(evidence, kernel_test='passed', sparse_test='passed')
            except Exception as error:
                _CUDA_PREFLIGHT[index] = dict(error=f'{type(error).__name__}: {error}')
        result = _CUDA_PREFLIGHT[index]
        if 'error' in result:
            raise RuntimeError('CUDA 内核编译/求解自检失败：' + result['error'])
        return dict(result)


def gpu_status(analysis_mode=None):
    """Return the selected numerical device and whether CUDA is usable."""
    if os.environ.get('THERMAL_CUDA_BACKEND')=='native_cudss':
        from forge.native_heat import gpu_status as native_status
        return native_status(analysis_mode)
    requested = os.environ.get('THERMAL_DEVICE', 'auto').strip().lower()
    if requested in ('cpu', 'host'):
        return dict(requested=requested, device='cpu', available=False, name=None, reason='forced CPU')
    if cp is None:
        reason = _CUPY_IMPORT_ERROR or 'CuPy is not installed'
        if requested in ('cuda', 'gpu'):
            raise RuntimeError(f'CUDA requested but unavailable: {reason}')
        return dict(requested=requested, device='cpu', available=False, name=None, reason=reason)
    try:
        count = int(cp.cuda.runtime.getDeviceCount())
        if count <= 0:
            raise RuntimeError('no CUDA devices')
        index = int(os.environ.get('THERMAL_CUDA_DEVICE', '0'))
        cp.cuda.Device(index).use()
        name = cp.cuda.runtime.getDeviceProperties(index)['name']
        if isinstance(name, bytes):
            name = name.decode(errors='replace')
        preflight = _cuda_preflight(index)
        if requested=='auto' and analysis_mode=='transient':
            return dict(requested=requested,device='cpu',available=True,name=name,index=index,
                        preflight=preflight,
                        reason='auto: transient cached CPU factorization; explicit cuda remains available')
        return dict(requested=requested, device='cuda', available=True, name=name, index=index,
                    preflight=preflight)
    except Exception as error:
        if requested in ('cuda', 'gpu'):
            raise RuntimeError(f'CUDA requested but unavailable: {error}') from error
        return dict(requested=requested, device='cpu', available=False, name=None, reason=str(error))


class _CudaFactor:
    """Keep one SPD matrix and Jacobi preconditioner resident for repeated solves."""
    def __new__(cls,A):
        if os.environ.get('THERMAL_CUDA_BACKEND')=='native_cudss':
            from forge.native_heat import NativeFactor
            return NativeFactor(A)
        return super().__new__(cls)
    def __init__(self,A):
        self.matrix=cps.csr_matrix(A)
        diagonal=self.matrix.diagonal()
        if not bool(cp.all(cp.isfinite(diagonal)&(diagonal>0))):
            raise ValueError('CUDA 热矩阵必须具有有限的正对角元')
        self.preconditioner=cps.diags(1/diagonal,format='csr')
        self.previous=None

    def solve(self,rhs):
        vector=cp.asarray(rhs)
        # Warm starts affect iterations only, never the physical initial field.
        solution,info=cupy_cg(self.matrix,vector,x0=self.previous,M=self.preconditioner,
                             rtol=1e-10,atol=0.0,maxiter=max(1000,self.matrix.shape[0]*2))
        residual=float(cp.linalg.norm(self.matrix@solution-vector))
        scale=max(float(cp.linalg.norm(vector)),1e-30)
        if int(info)!=0 or not np.isfinite(residual) or residual>max(1e-12,scale*1e-8):
            raise RuntimeError(f'CUDA sparse solve did not converge (info={int(info)}, relative residual={residual/scale:.3g})')
        self.previous=solution
        return cp.asnumpy(solution)


def _gpu_solver(A,rhs):
    return _CudaFactor(A).solve(rhs)

@BilinearForm
def capacity(u,v,w):return w.capacity*u*v
@BilinearForm
def conductivity(u,v,w):return w.conductivity*dot(grad(u),grad(v))

def _point_source_load(points, candidates, position, radius):
    """Spread a point input over nearby FEM nodes while preserving total power."""
    distances=np.linalg.norm(points[candidates]-np.asarray(position,dtype=float),axis=1)
    span=max(float(np.ptp(points,axis=0).max()),1e-9)
    spread=max(float(radius or 0),span/500)
    selected=distances<=3*spread
    if not np.any(selected): selected[np.argmin(distances)]=True
    weights=np.exp(-np.square(distances[selected]/spread)/2)
    weights/=weights.sum()
    load=np.zeros(len(points),dtype=float);load[np.asarray(candidates)[selected]]=weights
    return load


def _boundary_component_ids(tets, component_id, boundary_faces):
    """Map each boundary triangle to its owning tetrahedron component."""
    # A boundary face occurs once in the tetrahedral face list.  Sorting the
    # three node IDs makes this independent of Gmsh face orientation.
    tet_faces=np.concatenate((
        tets[:, [1, 2, 3]], tets[:, [0, 2, 3]],
        tets[:, [0, 1, 3]], tets[:, [0, 1, 2]],
    ), axis=0)
    tet_faces.sort(axis=1)
    tet_faces=np.ascontiguousarray(tet_faces,dtype=np.int64)
    owners=np.tile(np.asarray(component_id, dtype=np.int64), 4)
    dtype=np.dtype([('a','<i8'),('b','<i8'),('c','<i8')])
    keys=tet_faces.view(dtype).reshape(-1)
    ordered=np.argsort(keys, order=('a','b','c'), kind='mergesort')
    sorted_keys=keys[ordered]
    bfaces=np.ascontiguousarray(np.sort(np.asarray(boundary_faces, dtype=np.int64), axis=1))
    bkeys=bfaces.view(dtype).reshape(-1)
    positions=np.searchsorted(sorted_keys, bkeys)
    valid=(positions<len(sorted_keys))
    valid &= sorted_keys[np.minimum(positions, len(sorted_keys)-1)]==bkeys
    result=np.full(len(bfaces), -1, dtype=np.int64)
    result[valid]=owners[ordered[positions[valid]]]
    return result


def _air_gap_coupling(points, boundary_faces, boundary_components, enabled, k_air, max_gap):
    """Build reduced-order conductive coupling across nearby air gaps.

    Each boundary triangle chooses its nearest triangle on every other solid
    component.  A pair contributes k_air * min(area) / distance, distributed
    uniformly over its six vertices.  This conserves energy exactly and keeps
    the usual FEM matrix symmetric positive semidefinite.
    """
    n=len(points)
    empty=coo_matrix((n,n)).tocsr()
    if not enabled or not len(boundary_faces):
        return empty, dict(enabled=bool(enabled), pairs=0, area_m2=0.,
                           max_gap_m=float(max_gap), conductance_W_K=0.)
    components=np.asarray(boundary_components, dtype=np.int64)
    valid=components>=0
    if valid.sum()<2:
        return empty, dict(enabled=True, pairs=0, area_m2=0., max_gap_m=float(max_gap), conductance_W_K=0.)
    faces=np.asarray(boundary_faces, dtype=np.int64)[valid]
    components=components[valid]
    tri=points[faces]
    centers=tri.mean(axis=1)
    areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    unique=np.unique(components)
    rows=[];cols=[];data=[];seen=set();pair_count=0;coupled_area=0.;total_g=0.
    for left,right in combinations(unique.tolist(),2):
        ia=np.flatnonzero(components==left);ib=np.flatnonzero(components==right)
        if not len(ia) or not len(ib): continue
        tree=cKDTree(centers[ib]);dist,nearest=tree.query(centers[ia],k=1)
        tree_rev=cKDTree(centers[ia]);dist_rev,nearest_rev=tree_rev.query(centers[ib],k=1)
        candidates=[(float(d),int(i),int(ib[j])) for i,d,j in zip(ia,dist,nearest) if d<=max_gap]
        candidates += [(float(d),int(ia[j]),int(i)) for i,d,j in zip(ib,dist_rev,nearest_rev) if d<=max_gap]
        for distance,fi,fj in candidates:
            key=(min(fi,fj),max(fi,fj))
            if key in seen: continue
            seen.add(key)
            distance=max(distance,1e-9)
            conductance=float(k_air)*min(float(areas[fi]),float(areas[fj]))/distance
            if conductance<=0: continue
            wa=np.full(3,1/3);wb=np.full(3,1/3)
            for ai,na in enumerate(faces[fi]):
                for aj,nb in enumerate(faces[fi]):
                    rows.append(int(na));cols.append(int(nb));data.append(conductance*wa[ai]*wa[aj])
                for aj,nb in enumerate(faces[fj]):
                    rows.append(int(na));cols.append(int(nb));data.append(-conductance*wa[ai]*wb[aj])
            for ai,na in enumerate(faces[fj]):
                for aj,nb in enumerate(faces[fi]):
                    rows.append(int(na));cols.append(int(nb));data.append(-conductance*wb[ai]*wa[aj])
                for aj,nb in enumerate(faces[fj]):
                    rows.append(int(na));cols.append(int(nb));data.append(conductance*wb[ai]*wb[aj])
            pair_count+=1;coupled_area+=min(float(areas[fi]),float(areas[fj]));total_g+=conductance
    matrix=coo_matrix((data,(rows,cols)),shape=(n,n)).tocsr() if data else empty
    return matrix, dict(enabled=True,pairs=pair_count,area_m2=coupled_area,
                        max_gap_m=float(max_gap),conductance_W_K=total_g,
                        k_air_W_mK=float(k_air))

def assemble(mesh,cfg,display):
    points,tets=mesh['points'],mesh['tets'];centers=points[tets].mean(1)
    materials=[cfg['base_material']]+[r['material'] for r in cfg['regions']]+[r['material'] for r in cfg.get('component_materials',[])]
    labels=np.zeros(len(tets),dtype=np.int32)
    for i,r in enumerate(cfg['regions'],1):
        inside=np.all((centers>=np.asarray(r['min_m']))&(centers<=np.asarray(r['max_m'])),axis=1);labels[inside]=i
    component_id=mesh.get('component_id')
    if component_id is None:
        component_id=connected_tet_components(tets,points)
    if component_id is not None:
        for offset,assignment in enumerate(cfg.get('component_materials',[]),len(cfg['regions'])+1):
            labels[np.asarray(component_id)==assignment['component_id']]=offset
    vol=mesh['tet_volumes']
    k_values=np.array([m['k'] for m in materials],dtype=float)[labels]
    contact_r=float(cfg.get('contact_resistance_m2K_W',0) or 0)
    if contact_r and len(materials)>1:
        # The conforming mesh shares interface nodes. Smear an equivalent
        # interface layer over region cells to represent finite resistance.
        characteristic=np.maximum((6*vol/np.pi)**(1/3),1e-12)
        k_values[labels>0]=1/(1/k_values[labels>0]+contact_r/characteristic[labels>0])
    props=np.array([[m['rho']*m['cp'],m['k'],m['rho']] for m in materials])[labels]
    props[:,1]=k_values
    basis=Basis(MeshTet(points.T,tets.T),ElementTetP1())
    M=asm(capacity,basis,capacity=props[:,0,None]).tocsr()
    K=asm(conductivity,basis,conductivity=props[:,1,None]).tocsr()
    bf=mesh['boundary_triangles'];tri=points[bf]
    boundary_components=_boundary_component_ids(mesh['tets'],component_id,bf)
    air_gap_matrix,air_gap_audit=_air_gap_coupling(
        points,bf,boundary_components,
        bool(cfg.get('air_gap_enabled',True)),
        float(cfg.get('air_gap_k_W_mK',.026)),
        float(cfg.get('air_gap_max_m',.05)),
    )
    K=K+air_gap_matrix
    area=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    bary=np.array([[2/3,1/6,1/6],[1/6,2/3,1/6],[1/6,1/6,2/3]])
    queries=np.einsum('qn,fnc->fqc',bary,tri).reshape(-1,3)
    original=trimesh.Trimesh(vertices=display['points'],faces=display['faces'],process=False)
    _,dist,ids=project(original,queries)
    ids=ids.reshape(-1,3);exterior=display['is_outer'][ids]
    loads=[];heat_audits=[];is_heat=np.zeros(ids.shape,dtype=bool)
    display_tri=np.asarray(display['points'])[np.asarray(display['faces'])]
    display_centers=display_tri.mean(axis=1)
    boundary_nodes=np.unique(bf.ravel())
    for heat in cfg['heat_sources']:
        source_type=heat.get('source_type','surface');placement=heat.get('placement','surface')
        if source_type=='point' or placement=='embedded':
            if heat.get('position_m') is not None: position=np.asarray(heat['position_m'],dtype=float)
            elif heat.get('faces'): position=display_centers[np.asarray(heat['faces'],dtype=int)].mean(axis=0)
            else: raise ValueError(f'热源“{heat["name"]}”没有有效位置。')
            candidates=np.arange(len(points)) if placement=='embedded' else boundary_nodes
            F=_point_source_load(points,candidates,position,heat.get('radius_m',.001))*float(heat['power_W']);loads.append(F)
            selected=np.zeros(len(display['faces']),dtype=bool)
            selected[heat['faces']]=True
            if not heat.get('faces'):
                selected[int(np.argmin(np.linalg.norm(display_centers-position,axis=1)))]=True
            is_heat|=selected[ids]
            heat_audits.append(dict(name=heat['name'],source_type=source_type,placement=placement,power_W=float(F.sum()),position_m=position.tolist(),radius_m=float(heat.get('radius_m',.001))))
            continue
        selected=np.zeros(len(display['faces']),dtype=bool);selected[heat['faces']]=True
        from thermal_legacy.scenario_cases import clipped_quadrature,box_inside
        box=heat.get('surface_box')
        hot=selected[ids]&box_inside(queries.reshape(-1,3,3),box);is_heat|=hot;hotarea=float((area[:,None]/3*hot).sum())
        if box:
            candidates=np.arange(len(tri))
            parent,qb,qw=clipped_quadrature(tri[candidates],box);hotarea=float(qw.sum())
        if hotarea<=0:raise ValueError(f'热源“{heat["name"]}”的选区小于当前网格分辨率，请扩大选区或细化网格。')
        if box:
            F=np.bincount(bf[candidates[parent]].ravel(),weights=(qb*qw[:,None]*heat['power_W']/hotarea).ravel(),minlength=len(points))
        else:
            local=np.einsum('fq,qi->fi',area[:,None]/3*hot*heat['power_W']/hotarea,bary)
            F=np.bincount(bf.ravel(),weights=local.ravel(),minlength=len(points))
        loads.append(F)
        stltri=display['points'][display['faces'][heat['faces']]]
        originalarea=float(np.linalg.norm(np.cross(stltri[:,1]-stltri[:,0],stltri[:,2]-stltri[:,0]),axis=1).sum()/2)
        if box:originalarea=float(clipped_quadrature(stltri,box)[2].sum())
        heat_audits.append(dict(name=heat['name'],source_type=source_type,placement=placement,power_W=float(F.sum()),selected_area_m2=originalarea,mapped_area_m2=hotarea))
    h=np.where(exterior,cfg['default_h'],0.).astype(float)
    ambient=np.full(ids.shape,cfg['ambient_C'])
    radiation=np.full(ids.shape,bool(cfg.get('radiation_enabled',False)),dtype=bool)
    radiation_ambient=np.full(ids.shape,float(cfg.get('radiation_ambient_C',cfg['ambient_C'])))
    emissivity=np.full(ids.shape,float(cfg.get('emissivity',.8)))
    if not cfg['heat_convection']:h[is_heat]=0
    for cool in cfg['cooling']:
        selected=np.zeros(len(display['faces']),dtype=bool);selected[cool['faces']]=True
        from thermal_legacy.scenario_cases import box_inside
        mask=selected[ids]&box_inside(queries.reshape(-1,3,3),cool.get('surface_box'));h[mask]=cool['h'];ambient[mask]=cool['ambient_C']
        radiation[mask]=bool(cool.get('radiation',False));radiation_ambient[mask]=cool['ambient_C'];emissivity[mask]=cool.get('emissivity',.85)
    weights=area[:,None]/3*h
    local=np.einsum('fq,qi,qj->fij',weights,bary,bary)
    rows=np.broadcast_to(bf[:,:,None],local.shape);cols=np.broadcast_to(bf[:,None,:],local.shape)
    C=coo_matrix((local.ravel(),(rows.ravel(),cols.ravel())),shape=M.shape).tocsr()
    ambientlocal=np.einsum('fq,qi->fi',weights*(ambient-cfg['initial_C']),bary)
    G=np.bincount(bf.ravel(),weights=ambientlocal.ravel(),minlength=len(points))
    rad_weights=area[:,None]/3*radiation
    rad_area=np.bincount(bf.ravel(),weights=rad_weights.ravel(),minlength=len(points))
    rad_ambient_sum=np.bincount(bf.ravel(),weights=(rad_weights*radiation_ambient).ravel(),minlength=len(points))
    rad_eps_sum=np.bincount(bf.ravel(),weights=(rad_weights*emissivity).ravel(),minlength=len(points))
    rad_ambient=np.divide(rad_ambient_sum,rad_area,out=np.full(len(points),float(cfg['ambient_C']),dtype=float),where=rad_area>0)
    rad_eps=np.divide(rad_eps_sum,rad_area,out=np.zeros(len(points)),where=rad_area>0)
    phase_entries=[]
    for i,m in enumerate(materials):
        phase=m.get('phase_change')
        if not phase: continue
        extra=np.zeros(len(points),dtype=float)
        cell_extra=vol[labels==i]*m['rho']*phase['latent_J_kg']/phase['mushy_C']
        np.add.at(extra,tets[labels==i].ravel(),np.repeat(cell_extra/4,4))
        phase_entries.append(dict(extra=extra,low=phase['melting_C'],high=phase['melting_C']+phase['mushy_C']))
    bmesh=trimesh.Trimesh(vertices=points,faces=bf,process=False)
    closest,vd,vi=project(bmesh,display['points'])
    vb=trimesh.triangles.points_to_barycentric(points[bf[vi]],closest)
    vol=mesh['tet_volumes']
    audit=dict(heat_sources=heat_audits,boundary_projection_max_m=float(dist.max()),display_projection_max_m=float(vd.max()),
        volume_m3=float(vol.sum()),mass_kg=float(vol@props[:,2]),heat_capacity_J_K=float(M.sum()),contact_resistance_m2K_W=contact_r,
        constant_field_conduction_residual=float(np.abs(K@np.ones(len(points))).max()),
        air_gap=air_gap_audit,
        materials=[dict(**m,volume_m3=float(vol[labels==i].sum()),cells=int((labels==i).sum())) for i,m in enumerate(materials)])
    return M,K,C,G,loads,labels,bf[vi],vb,audit,rad_area,rad_ambient,rad_eps,phase_entries

def _power_at(source, t):
    profile=source.get('power_profile') or []
    if not profile: return float(source['power_W'])
    if t<=profile[0]['time_s']: return float(profile[0]['power_W'])
    for left,right in zip(profile[:-1],profile[1:]):
        if t<=right['time_s']:
            span=right['time_s']-left['time_s']
            return float(left['power_W']+(right['power_W']-left['power_W'])*(t-left['time_s'])/span) if span else float(right['power_W'])
    return float(profile[-1]['power_W'])


def _radiation_terms(theta, cfg, rad_area, rad_ambient, rad_eps):
    if rad_area is None or not np.any(rad_area): return None, None, np.zeros_like(theta)
    tk=np.maximum(theta+cfg['initial_C']+273.15,1e-6)
    ak=np.maximum(rad_ambient+273.15,1e-6)
    h=rad_eps*SIGMA*(tk+ak)*(tk*tk+ak*ak)
    coeff=rad_area*h
    return diags(coeff), coeff*(rad_ambient-cfg['initial_C']), coeff*(theta-(rad_ambient-cfg['initial_C']))


def _source_power(source, t0, t1, theta, cfg, state):
    if t1<=source['start_s'] or t0>=source['end_s']: return 0.
    mid=(t0+t1)/2
    power=_power_at(source,mid)
    control=source.get('thermostat')
    if control:
        average=float(theta.mean()+cfg['initial_C'])
        if state.get(id(source),True) and average>=control['target_C']:
            state[id(source)]=False
        elif not state.get(id(source),True) and average<=control['target_C']-control['hysteresis_C']:
            state[id(source)]=True
        power=control['max_power_W'] if state.get(id(source),True) else control.get('min_power_W',0.)
    return float(power)


def integrate(M,K,C,G,loads,cfg,callback=lambda *x:None,rad_area=None,rad_ambient=None,rad_eps=None,phase_entries=None,initial_temperature=None):
    device=gpu_status('transient')
    use_gpu=device['device']=='cuda'
    end=cfg['duration_s'];dt=cfg['dt_s'];save=cfg['save_s']
    profile=cfg.get('ambient_profile') or []
    output=np.unique(np.r_[np.arange(0,end,save),end,[p['time_s'] for p in profile]])
    events=np.r_[0,end,np.arange(0,end,dt),output]
    for heat in cfg['heat_sources']:events=np.r_[events,heat['start_s'],heat['end_s']]
    grid=np.unique(np.round(events[(events>=0)&(events<=end)],10))
    theta=np.zeros(M.shape[0]);mc=np.asarray(M.sum(axis=0)).ravel();cc=np.asarray(C.sum(axis=0)).ravel()
    if initial_temperature is not None:
        theta=np.asarray(initial_temperature,dtype=float)-cfg['initial_C']
        if theta.shape!=(M.shape[0],) or not np.isfinite(theta).all():raise ValueError('续算初始温度场与网格不一致')
    def environment_load(t):
        delta=float(np.interp(t,[p['time_s'] for p in profile],[p['ambient_C'] for p in profile]))-cfg['ambient_C'] if profile else 0.
        return G+cc*delta
    phase_entries=phase_entries or []
    saved=[];stats=[];integrated_in=0.;integrated_loss=0.;max_residual=0.;factor=None;last_key=None;control_state={}
    def stored_energy(values):
        energy=float(mc@values)
        for entry in phase_entries:
            energy+=float(np.sum(entry['extra']*np.clip(values+cfg['initial_C']-entry['low'],0,entry['high']-entry['low'])))
        return energy
    def snapshot(t):
        energy=stored_energy(theta);saved.append(theta.astype(np.float32))
        stats.append(dict(time_s=float(t),minimum_C=float(theta.min()+cfg['initial_C']),maximum_C=float(theta.max()+cfg['initial_C']),
            average_C=float(energy/mc.sum()+cfg['initial_C']),stored_energy_J=energy,convective_loss_W=float(cc@theta-environment_load(t).sum())))
    initial_energy=stored_energy(theta)
    snapshot(0);save_index=1
    for i,(t0,t1) in enumerate(zip(grid[:-1],grid[1:])):
        step=round(float(t1-t0),10)
        if step<=0:continue
        phase_extra=sum((entry['extra']*((theta+cfg['initial_C']>=entry['low'])&(theta+cfg['initial_C']<=entry['high'])) for entry in phase_entries),np.zeros_like(theta))
        Meff=M+diags(phase_extra) if np.any(phase_extra) else M
        R,Gr,rad_loss=_radiation_terms(theta,cfg,rad_area,rad_ambient,rad_eps)
        key=(step, bool(np.any(phase_extra)), bool(R is not None))
        if R is not None or np.any(phase_extra) or factor is None or key!=last_key:
            A=Meff/step+K+C+(R if R is not None else 0)
            factor=_CudaFactor(A) if use_gpu else splu(A.tocsc());last_key=key
        boundary_G=environment_load(t1);F=boundary_G.copy();pin=0.
        for load,source in zip(loads,cfg['heat_sources']):
            power=_source_power(source,t0,t1,theta,cfg,control_state)
            if power and source['power_W']>0:
                F+=load*(power/source['power_W']);pin+=power
        before=stored_energy(theta)
        rhs=Meff@theta/step+F+(Gr if Gr is not None else 0)
        theta=factor.solve(rhs)
        if not np.isfinite(theta).all():raise ValueError('温度求解出现非有限值，请检查物性和网格。')
        rad_loss_new=float((np.asarray(R.diagonal())*(theta-(rad_ambient-cfg['initial_C']))).sum()) if R is not None else 0.
        loss=float(cc@theta-boundary_G.sum())+rad_loss_new
        integrated_in+=pin*step;integrated_loss+=loss*step
        stored=stored_energy(theta);max_residual=max(max_residual,abs(stored-before-step*(pin-loss)))
        if save_index<len(output) and abs(t1-output[save_index])<1e-7:snapshot(t1);save_index+=1
        if i%10==0:callback(float(t1/end),f'正在计算 {t1:.0f} / {end:.0f} s')
    balance=initial_energy+integrated_in-integrated_loss-stored_energy(theta)
    if abs(balance)>max(1.,abs(integrated_in)+abs(integrated_loss))*1e-6:raise ValueError('离散能量平衡检查未通过')
    return np.array(saved),output,stats,dict(initial_stored_energy_J=initial_energy,input_energy_J=integrated_in,convective_energy_J=integrated_loss,energy_balance_error_J=balance,maximum_step_residual_J=max_residual)


def steady(M,K,C,G,loads,cfg,callback=lambda *x:None,rad_area=None,rad_ambient=None,rad_eps=None):
    device=gpu_status('steady')
    use_gpu=device['device']=='cuda'
    theta=np.zeros(M.shape[0]);mc=np.asarray(M.sum(axis=0)).ravel();F=G.copy()
    for load,source in zip(loads,cfg['heat_sources']):
        power=_power_at(source,cfg['duration_s']) if source['start_s']<=cfg['duration_s']<=source['end_s'] else 0.
        if source['power_W']>0:F+=load*(power/source['power_W'])
    for iteration in range(80):
        R,Gr,_=_radiation_terms(theta,cfg,rad_area,rad_ambient,rad_eps)
        matrix=K+C+(R if R is not None else 0)
        rhs=F+(Gr if Gr is not None else 0)
        next_theta=_gpu_solver(matrix,rhs) if use_gpu else splu(matrix.tocsc()).solve(rhs)
        callback((iteration+1)/80,f'稳态迭代 {iteration+1} / 80')
        if R is None:theta=next_theta;break
        if np.max(np.abs(next_theta-theta))<1e-7:theta=next_theta;break
        theta=.65*theta+.35*next_theta
    final_R,final_Gr,_=_radiation_terms(theta,cfg,rad_area,rad_ambient,rad_eps)
    residual=float(np.max(np.abs((K+C+(final_R if final_R is not None else 0))@theta-F-(final_Gr if final_Gr is not None else 0))))
    stats=[dict(time_s=0.,minimum_C=float(cfg['initial_C']),maximum_C=float(cfg['initial_C']),average_C=float(cfg['initial_C']),stored_energy_J=0.,convective_loss_W=0.)]
    final=theta+cfg['initial_C'];stats.append(dict(time_s=float(cfg['duration_s']),minimum_C=float(final.min()),maximum_C=float(final.max()),average_C=float((mc@theta)/mc.sum()+cfg['initial_C']),stored_energy_J=float(mc@theta),convective_loss_W=float((C@theta-G).sum())))
    return np.asarray([np.zeros_like(theta),theta]),np.asarray([0.,cfg['duration_s']]),stats,dict(input_energy_J=float(sum(_power_at(s,cfg['duration_s']) for s in cfg['heat_sources'])),convective_energy_J=0.,energy_balance_error_J=residual,maximum_step_residual_J=residual,steady_residual=residual)


def thermal_expansion_field(mesh, frames, labels, materials, reference_C):
    """Compute an unconstrained isotropic thermal displacement field.

    The thermal solver computes temperatures only.  For post-processing we
    approximate free expansion about the volume centroid using each node's
    volume-weighted CTE and temperature rise.  This is intentionally a
    kinematic result (no structural constraints or stress solve).
    """
    points = np.asarray(mesh['points'], dtype=float)
    tets = np.asarray(mesh['tets'], dtype=np.int64)
    volumes = np.asarray(mesh['tet_volumes'], dtype=float)
    alpha_tet = np.asarray([materials[int(label)].get('thermal_expansion_CTE_per_K', 0.)
                            for label in labels], dtype=float)
    node_weights = np.bincount(tets.ravel(), weights=np.repeat(volumes / 4., 4), minlength=len(points))
    node_alpha = np.bincount(tets.ravel(), weights=np.repeat(volumes * alpha_tet / 4., 4), minlength=len(points))
    node_alpha = np.divide(node_alpha, node_weights, out=np.zeros_like(node_alpha), where=node_weights > 0)
    centroid = np.average(points, axis=0, weights=node_weights) if np.any(node_weights) else points.mean(axis=0)
    radial = points - centroid
    delta = np.asarray(frames, dtype=float) - float(reference_C)
    displacement = delta[:, :, None] * node_alpha[None, :, None] * radial[None, :, :]
    magnitude = np.linalg.norm(displacement, axis=2)
    return displacement.astype('<f4'), magnitude, node_alpha, centroid

def make_slice(points,tets,frames,axis,value):
    eps=max(float(np.ptp(points,axis=0).max())*1e-10,1e-14)
    coord=points[:,axis]-value;selected=tets[(coord[tets].min(1)<=eps)&(coord[tets].max(1)>=-eps)]
    vertices=[];nodes=[];weights=[];faces=[];lookup={};other=[a for a in range(3) if a!=axis]
    for tet in selected:
        poly=[]
        for node in tet:
            if abs(coord[node])<=eps:
                key=(int(node),int(node))
                if key not in lookup:lookup[key]=len(vertices);vertices.append(points[node]);nodes.append(key);weights.append(0.)
                poly.append(lookup[key])
        for i,j in ((0,1),(0,2),(0,3),(1,2),(1,3),(2,3)):
            a,b=sorted((int(tet[i]),int(tet[j])))
            if (coord[a]<-eps and coord[b]>eps) or (coord[b]<-eps and coord[a]>eps):
                key=(a,b)
                if key not in lookup:
                    w=-coord[a]/(coord[b]-coord[a]);lookup[key]=len(vertices);vertices.append(points[a]*(1-w)+points[b]*w);nodes.append(key);weights.append(w)
                poly.append(lookup[key])
        poly=list(dict.fromkeys(poly))
        if len(poly)>=3:
            q=np.array([vertices[p] for p in poly]);q-=q.mean(0)
            poly=np.asarray(poly)[np.argsort(np.arctan2(q[:,other[1]],q[:,other[0]]))]
            for j in range(1,len(poly)-1):faces.append([poly[0],poly[j],poly[j+1]])
    if not faces:return np.empty((0,3)),np.empty((0,3),dtype=np.uint32),np.empty((len(frames),0))
    nodes=np.asarray(nodes);w=np.asarray(weights);faces=np.asarray(faces)
    _,unique=np.unique(np.sort(faces,axis=1),axis=0,return_index=True);faces=faces[np.sort(unique)]
    values=frames[:,nodes[:,0]]*(1-w)+frames[:,nodes[:,1]]*w
    return np.asarray(vertices),faces,values

def export_slice(job,axis,value):
    if axis not in (0,1,2) or not np.isfinite(value):raise ValueError('无效剖面')
    key=f'{axis}_{value:.9g}'.replace('-','n').replace('.','p')
    folder=job/'slices'/key;folder.mkdir(parents=True,exist_ok=True)
    if (folder/'manifest.json').exists():return read_json(folder/'manifest.json')
    mesh=np.load(job/'mesh.npz');frames=np.load(job/'temperatures.npy',mmap_mode='r')
    lo,hi=mesh['points'][:,axis].min(),mesh['points'][:,axis].max()
    if value<lo or value>hi:raise ValueError('剖面位置超出工件范围')
    pts,faces,values=make_slice(mesh['points'],mesh['tets'],frames,axis,value)
    pts.astype('<f4').tofile(folder/'points.bin');faces.astype('<u4').tofile(folder/'faces.bin');values.astype('<f4').tofile(folder/'values.bin')
    meta=dict(key=key,vertices=len(pts),triangles=len(faces),frames=len(frames),axis=axis,value=value)
    write_json(folder/'manifest.json',meta);return meta

def export_archive(job,mesh,frames,times,labels,displacement=None,structural=None):
    dest=job/'export';dest.mkdir(exist_ok=True)
    hf=h5py.File(dest/'thermal-fields.h5','w')
    hf.create_dataset('points',data=mesh['points'],compression='gzip',shuffle=True)
    hf.create_dataset('tetra',data=mesh['tets'].astype(np.int32),compression='gzip',shuffle=True)
    hf.create_dataset('material',data=labels,compression='gzip',shuffle=True)
    if displacement is not None:
        for i, values in enumerate(np.asarray(displacement)):
            hf.create_dataset(f'displacement/{i}', data=values, compression='gzip', shuffle=True)
    xml=['<?xml version="1.0"?>','<Xdmf Version="3.0"><Domain><Grid Name="Temperature" GridType="Collection" CollectionType="Temporal">']
    n=len(mesh['points']);ne=len(mesh['tets'])
    for i,t in enumerate(times):
        hf.create_dataset(f'temperature/{i}',data=frames[i],compression='gzip',shuffle=True)
        disp_attr = f'<Attribute Name="Displacement_m" AttributeType="Vector" Center="Node"><DataItem Dimensions="{n} 3" NumberType="Float" Precision="4" Format="HDF">thermal-fields.h5:/displacement/{i}</DataItem></Attribute>' if displacement is not None else ''
        if structural is not None:
            hf.create_dataset(f'von_mises/{i}',data=structural['von_mises'][i],compression='gzip')
            hf.create_dataset(f'stress/{i}',data=structural['stress'][i],compression='gzip')
            disp_attr+=f'<Attribute Name="Von_Mises_Pa" AttributeType="Scalar" Center="Cell"><DataItem Dimensions="{ne}" NumberType="Float" Precision="4" Format="HDF">thermal-fields.h5:/von_mises/{i}</DataItem></Attribute>'
            # Export six components individually so Voigt ordering is explicit.
            for axis,key in enumerate(('xx','yy','zz','xy','yz','xz')):
                hf.create_dataset(f'stress_{key}/{i}',data=structural['stress'][i,:,axis],compression='gzip')
                disp_attr+=f'<Attribute Name="Stress_{key}_Pa" AttributeType="Scalar" Center="Cell"><DataItem Dimensions="{ne}" NumberType="Float" Precision="4" Format="HDF">thermal-fields.h5:/stress_{key}/{i}</DataItem></Attribute>'
        xml.append(f'<Grid GridType="Uniform"><Time Value="{t:g}"/><Topology TopologyType="Tetrahedron" NumberOfElements="{ne}"><DataItem Dimensions="{ne} 4" NumberType="Int" Precision="4" Format="HDF">thermal-fields.h5:/tetra</DataItem></Topology><Geometry GeometryType="XYZ"><DataItem Dimensions="{n} 3" NumberType="Float" Precision="8" Format="HDF">thermal-fields.h5:/points</DataItem></Geometry><Attribute Name="Temperature_C" AttributeType="Scalar" Center="Node"><DataItem Dimensions="{n}" NumberType="Float" Precision="4" Format="HDF">thermal-fields.h5:/temperature/{i}</DataItem></Attribute>{disp_attr}<Attribute Name="Material_ID" AttributeType="Scalar" Center="Cell"><DataItem Dimensions="{ne}" NumberType="Int" Precision="4" Format="HDF">thermal-fields.h5:/material</DataItem></Attribute></Grid>')
    hf.close();xml.append('</Grid></Domain></Xdmf>');(dest/'temperature.xdmf').write_text('\n'.join(xml),encoding='utf-8')
    with zipfile.ZipFile(job/'result.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for name in ('thermal-fields.h5','temperature.xdmf'):z.write(dest/name,name)
        for name in ('config.json','audit.json','assessment.json','history.csv','report.md','report.pdf'):
            if (job/name).exists(): z.write(job/name,name)
        for asset in (job/'report-assets').glob('*'):z.write(asset,'report-assets/'+asset.name)

def solve_job(job):
    cfg=read_json(job/'config.json');folder=MODELS/cfg['model_id'];display=dict(np.load(folder/'display.npz'))
    device=gpu_status(cfg.get('analysis_mode','transient'))
    progress(job,'running',5,'准备几何与材料分区')
    initial_temperature=None
    if cfg.get('initial_from_job'):
        from thermal_legacy.runtime import JOBS
        parent=JOBS/cfg['initial_from_job']
        parent_cfg=read_json(parent/'config.json')
        if read_json(parent/'status.json').get('phase')!='completed':raise ValueError('续算来源必须是已完成算例')
        for key in ('model_id','mesh_size_m','base_material','regions','component_materials'):
            if cfg.get(key)!=parent_cfg.get(key):raise ValueError('续算要求相同几何、网格和材料：'+key)
        mesh=dict(np.load(parent/'mesh.npz'))
        initial_temperature=np.load(parent/'temperatures.npy')[-1].copy()
    else:mesh=build_mesh(folder,cfg,job)
    if 'component_id' not in mesh:
        mesh=dict(mesh);mesh['component_id']=connected_tet_components(mesh['tets'],mesh['points'])
    np.savez_compressed(job/'mesh.npz',**mesh)
    if len(mesh['points'])*(cfg['duration_s']/cfg['save_s']+2)>25000000:raise ValueError('结果规模过大，请增大保存间隔或网格尺寸。')
    progress(job,'running',35,'组装多材料导热、热容量和表面边界')
    M,K,C,G,loads,labels,display_nodes,display_bary,audit,rad_area,rad_ambient,rad_eps,phase_entries=assemble(mesh,cfg,display)
    progress(job,'running',55,'开始'+('稳态' if cfg.get('analysis_mode')=='steady' else '瞬态')+'温度计算（'+device['device'].upper()+'）')
    callback=lambda fraction,msg:progress(job,'running',55+int(30*fraction),msg)
    if cfg.get('analysis_mode')=='steady':
        theta,times,stats,energy=steady(M,K,C,G,loads,cfg,callback,rad_area,rad_ambient,rad_eps)
    else:
        theta,times,stats,energy=integrate(M,K,C,G,loads,cfg,callback,rad_area,rad_ambient,rad_eps,phase_entries,initial_temperature)
    if initial_temperature is not None:
        audit['continuation']=dict(parent_job=cfg['initial_from_job'],method='identical mesh; saved final nodal temperatures',
            initial_minimum_C=float(initial_temperature.min()),initial_maximum_C=float(initial_temperature.max()))
    # Report the actual volume average, distinct from the heat-capacity-weighted
    # average used internally for the energy balance in a heterogeneous solid.
    volume_weights=np.bincount(mesh['tets'].ravel(),weights=np.repeat(mesh['tet_volumes']/4,4),minlength=len(mesh['points']))
    for row,values in zip(stats,theta):row['average_C']=float(volume_weights@values/volume_weights.sum()+cfg['initial_C'])
    frames=theta+np.float32(cfg['initial_C']);np.save(job/'temperatures.npy',frames)
    materials_for_expansion=[cfg['base_material']]+[r['material'] for r in cfg['regions']]+[r['material'] for r in cfg.get('component_materials',[])]
    displacement, displacement_magnitude, node_cte, expansion_centroid = thermal_expansion_field(
        mesh, frames, labels, materials_for_expansion, cfg['initial_C'])
    structural=None
    if cfg.get('structural'):
        from thermal_legacy.thermoelastic import map_supports,solve_thermoelastic
        fixed,support_audit=map_supports(mesh,display,cfg['structural'].get('supports',[])) if cfg['structural'].get('supports') else ([],[])
        progress(job,'running',85,'计算三维热弹性位移与应力（CPU）')
        structural=solve_thermoelastic(mesh,frames,materials_for_expansion,labels,cfg['structural'],fixed,
            lambda p:progress(job,'running',85+int(2*p),'计算热应力 '+str(round(p*100))+'%'),
            stage_callback=lambda detail:progress(job,'running',85,detail))
        structural['summary']['supports']=support_audit
        displacement=structural['displacement'];displacement_magnitude=np.linalg.norm(displacement,axis=2)
        structural['stress'].tofile(job/'stress.bin')
        # Browser surface stress uses the maximum incident cell value before
        # barycentric projection; raw cell stresses remain in the 3-D export.
        node_vm=np.zeros((len(frames),len(mesh['points'])),dtype='<f4')
        for f,vm in enumerate(structural['von_mises']):
            np.maximum.at(node_vm[f],mesh['tets'].ravel(),np.repeat(vm,4))
        np.einsum('fvi,vi->fv',node_vm[:,display_nodes],display_bary).astype('<f4').tofile(job/'surface-von-mises.bin')
        for row,srow in zip(stats,structural['stats']):
            for key in ('maximum_von_mises_Pa','maximum_principal_Pa','minimum_principal_Pa','relative_equilibrium_residual'):
                row[key]=srow[key]
        audit['structural']={**structural['summary'],'stats':structural['stats'],
            'maximum_von_mises_Pa':float(structural['von_mises'].max()),'maximum_displacement_m':float(displacement_magnitude.max())}
    displacement.tofile(job/'displacement.bin')
    np.einsum('fvic,vi->fvc',displacement[:,display_nodes],display_bary).astype('<f4').tofile(job/'surface-displacement.bin')
    from thermal_legacy.thermoelastic import assess_design
    assessment=assess_design(cfg,mesh,frames,times,structural,materials_for_expansion,labels)
    from thermal_legacy.scenario_cases import surface_metrics
    evaluation=surface_metrics(mesh,frames,displacement if structural else None,times,cfg,display)
    if evaluation:
        audit['surface_evaluation']=evaluation
        for title,key,limit_key,unit in [('接触面最高温度','maximum_C','temperature_limit_C','°C'),('接触面拟合平面翘曲','maximum_flatness_m','flatness_limit_m','m')]:
            val,limit=evaluation[key],evaluation[limit_key]
            status='not_assessed' if val is None or limit is None else 'exceeded' if val>limit else 'within_limits'
            assessment['checks'].append(dict(name=title,value=val,limit=limit,unit=unit,status=status))
            if status=='exceeded':assessment['status']='exceeded'
    write_json(job/'assessment.json',assessment)
    audit['assessment']=assessment
    for row, mag in zip(stats, displacement_magnitude):
        row['maximum_displacement_m'] = float(np.max(mag))
        row['average_displacement_m'] = float(np.mean(mag))
    surface=np.einsum('fvi,vi->fv',frames[:,display_nodes],display_bary).astype('<f4');surface.tofile(job/'surface.bin')
    component_rows=[]
    material_rows=audit.get('materials',[])
    for component in sorted(np.unique(mesh['component_id']).tolist()):
        cells=np.asarray(mesh['component_id'])==component;node_ids=np.unique(mesh['tets'][cells]);values=frames[:,node_ids]
        cell_labels=labels[cells]
        dominant_label=int(np.bincount(cell_labels).argmax()) if len(cell_labels) else 0
        material=material_rows[dominant_label] if dominant_label<len(material_rows) else {}
        component_points=mesh['points'][mesh['tets'][cells].ravel()]
        length_scale=float(np.max(np.ptp(component_points,axis=0))) if len(component_points) else 0
        diffusivity=float(material['k']/(material['rho']*material['cp'])) if material.get('rho') and material.get('cp') else 0
        component_weights=np.bincount(mesh['tets'][cells].ravel(),weights=np.repeat(mesh['tet_volumes'][cells]/4,4),minlength=len(mesh['points']))
        mean_by_frame=frames@component_weights/component_weights.sum()
        peak_rise=float(np.max(mean_by_frame)-cfg['initial_C'])
        half_time=None
        if peak_rise>1e-9:
            reached=np.flatnonzero(mean_by_frame>=cfg['initial_C']+peak_rise*.5)
            if len(reached): half_time=float(times[reached[0]])
        component_rows.append(dict(component_id=int(component),cells=int(cells.sum()),nodes=int(len(node_ids)),volume_m3=float(mesh['tet_volumes'][cells].sum()),
            material_name=material.get('name','未指定'),conductivity_k=float(material.get('k',0)),density_rho=float(material.get('rho',0)),specific_heat_cp=float(material.get('cp',0)),
            thermal_diffusivity_m2_s=diffusivity,diffusion_length_m=length_scale,diffusion_time_estimate_s=float(length_scale**2/diffusivity) if diffusivity>0 else None,
            minimum_C=float(values.min()),maximum_C=float(values.max()),final_minimum_C=float(values[-1].min()),final_maximum_C=float(values[-1].max()),final_average_C=float(mean_by_frame[-1]),
            peak_average_rise_C=peak_rise,time_to_half_peak_s=half_time,
            final_maximum_displacement_m=float(displacement_magnitude[-1, node_ids].max()) if len(node_ids) else 0.0,
            peak_maximum_displacement_m=float(displacement_magnitude[:, node_ids].max()) if len(node_ids) else 0.0))
    audit.update(energy=energy,nodes=len(mesh['points']),tetrahedra=len(mesh['tets']),minimum_quality=float(mesh.get('minimum_quality',0)),stats=stats,components=component_rows,device=device,
        thermal_expansion=dict(enabled=bool(np.any(node_cte > 0)), reference_C=float(cfg['initial_C']), centroid_m=expansion_centroid.tolist(),
            cte_min_per_K=float(node_cte.min()) if len(node_cte) else 0., cte_max_per_K=float(node_cte.max()) if len(node_cte) else 0.,
            maximum_displacement_m=float(displacement_magnitude.max()), final_maximum_displacement_m=float(displacement_magnitude[-1].max())),
        material_interface='CAD-conforming box fragments' if read_json(folder/'metadata.json')['kind']=='step' else 'element-centroid assignment; refine mesh at material interfaces',
        assumptions=['constant material properties outside phase-change intervals','smeared interface resistance when configured','prescribed convection coefficient plus optional surface radiation','air gaps use reduced-order conduction k_air*A/gap; no airflow/CFD solve','thermal expansion is reported as unconstrained isotropic free expansion; no stress or mechanical constraint solve'])
    warnings=[]
    if structural:
        audit['thermal_expansion'].update(method='thermoelastic',reference_C=cfg['structural']['reference_C'])
        audit['assumptions'][-1]='3D quasi-static small-strain thermoelastic FEM; see structural assumptions'
        if not structural['summary']['small_strain_valid']:warnings.append('热应变或总应变超过1%，超出本线弹性小变形模型的验证范围，需使用非线性结构模型复核。')
        if any(m.get('thermal_expansion_CTE_per_K',0)==0 for m in materials_for_expansion):warnings.append('部分材料线膨胀系数为0，请核对是否为真实材料参数。')
        for support in structural['summary']['supports']:
            if abs(support['mapped_area_m2']/support['selected_area_m2']-1)>.05:
                warnings.append('固定支撑“'+support['name']+'”网格映射面积偏差超过5%，请细化网格复核约束范围。')
    lower_bound=min([cfg['initial_C'],cfg['ambient_C']]+[c['ambient_C'] for c in cfg['cooling']]+[p['ambient_C'] for p in cfg.get('ambient_profile',[])]+([float(initial_temperature.min())] if initial_temperature is not None else []))
    undershoot=lower_bound-float(frames.min())
    if undershoot>1e-4:warnings.append(f'最低温度比所有初始/环境温度低 {undershoot:.4f}°C，属于离散数值下冲；请调整网格与时间步长复核。结果未做截断修饰。')
    if float(mesh.get('minimum_quality',1))<.05:warnings.append('局部存在形状质量较低的网格单元，请通过网格加密比较确认热点精度。')
    metadata=read_json(folder/'metadata.json')
    if len(metadata.get('components',[]))>1:
        gap=audit.get('air_gap',{})
        if gap.get('enabled') and gap.get('pairs',0):
            warnings.append(f'模型包含 {len(metadata["components"])} 个几何组件；已启用空气间隙导热，{int(gap["pairs"])} 对相邻表面通过空气传热（k={float(gap.get("k_air_W_mK",.026)):.4g} W/(m·K)）。')
        else:
            warnings.append(f'模型包含 {len(metadata["components"])} 个几何组件；组件间没有可用的空气间隙耦合，只有实体接触并共享网格节点的组件才会直接传热。')
    thermal_component_count=len(np.unique(mesh['component_id']))
    if thermal_component_count>1 and audit.get('heat_sources'):
        if audit.get('air_gap',{}).get('enabled') and audit.get('air_gap',{}).get('pairs',0):
            warnings.append(f'当前四面体热网格包含 {thermal_component_count} 个互不连通的体组件；固体内部导热之外，已通过空气间隙耦合传递热量。')
        else:
            warnings.append(f'当前四面体热网格包含 {thermal_component_count} 个互不连通的体组件；热源只会在其所在组件内扩散，其他组件需要实体接触或有效空气间隙耦合后才会升温。')
    for heat in cfg.get('heat_sources',[]):
        if float(heat.get('end_s',cfg['duration_s'])) < float(cfg['duration_s']):
            warnings.append(f'热源“{heat["name"]}”在 {float(heat["end_s"]):g} s 结束，但仿真持续到 {float(cfg["duration_s"]):g} s；末帧可能已冷却回环境温度，请查看最高温度帧。')
    for h in audit['heat_sources']:
        selected_area=h.get('selected_area_m2')
        relative=abs(h['mapped_area_m2']/selected_area-1) if selected_area else 0
        if relative>.05:warnings.append(f'热源“{h["name"]}”的网格面积与选区面积差 {relative:.1%}；总功率已保持，建议细化热源附近网格。')
    audit['warnings']=warnings
    write_json(job/'audit.json',audit)
    with (job/'history.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=stats[0].keys());writer.writeheader();writer.writerows(stats)
    manifest=dict(analysis_mode=cfg.get('analysis_mode','transient'),times_s=times.tolist(),vertices=surface.shape[1],frames=len(times),minimum_C=float(frames.min()),maximum_C=float(frames.max()),
        displacement_vertices=int(displacement.shape[1]), displacement_frames=int(displacement.shape[0]),
        maximum_displacement_m=float(displacement_magnitude.max()), final_maximum_displacement_m=float(displacement_magnitude[-1].max()),
        thermal_expansion=dict(reference_C=float(cfg['initial_C']), centroid_m=expansion_centroid.tolist(),
            max_displacement_m=float(displacement_magnitude.max()), cte_min_per_K=float(node_cte.min()), cte_max_per_K=float(node_cte.max())),
        structural=audit.get('structural'),assessment=assessment,stats=stats,summary=audit)
    write_json(job/'result.json',manifest)
    write_report(job,cfg,folder,audit,energy)
    progress(job,'running',88,'生成内部剖面和完整结果文件')
    bounds=np.array(read_json(folder/'metadata.json')['bounds_m']);export_slice(job,0,float(bounds[:,0].mean()))
    export_archive(job,mesh,frames,times,labels,displacement,structural)
    progress(job,'completed',100,'计算完成',finished_at=time.time())

def write_report(job,cfg,folder,audit,energy):
    from thermal_legacy.engineering_report import write_engineering_report
    write_engineering_report(job,cfg,read_json(folder/'metadata.json'),audit,write_report_pdf)


def write_report_pdf(path, text):
    """Render the Markdown report as a readable PDF with headings and tables."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from xml.sax.saxutils import escape
        import re
        font_name='Helvetica'
        # Prefer standalone TTF files.  Some ReportLab/PDF viewers only
        # expose the first 128 glyphs when a TTC collection is embedded.
        for candidate in (r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\simsunb.ttf', r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simsun.ttc'):
            if os.path.exists(candidate):
                try:
                    kwargs={'subfontIndex':0} if candidate.lower().endswith('.ttc') else {}
                    pdfmetrics.registerFont(TTFont('ThermalCJK', candidate, **kwargs))
                    font_name='ThermalCJK'
                    break
                except Exception:
                    pass
        styles=getSampleStyleSheet()
        body=ParagraphStyle('ThermalBody', parent=styles['BodyText'], fontName=font_name, fontSize=8.5, leading=12, spaceAfter=4,wordWrap='CJK')
        heading=ParagraphStyle('ThermalHeading', parent=body, fontSize=13, leading=17, spaceBefore=8, spaceAfter=5)
        subheading=ParagraphStyle('ThermalSubheading', parent=body, fontSize=10, leading=13, spaceBefore=7, spaceAfter=3, keepWithNext=True)
        bullet=ParagraphStyle('ThermalBullet', parent=body, leftIndent=10, firstLineIndent=-7)
        def inline(value):
            value=value.replace('²','^2').replace('³','^3')
            value=escape(value.replace('`',''))
            return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', value)
        story=[]; lines=text.splitlines(); i=0
        while i<len(lines):
            raw=lines[i].strip()
            # Paragraph styles already supply spacing. Blank-line spacers can
            # overflow to an otherwise empty page just before a page break.
            if not raw: i+=1; continue
            if raw=='<!-- pagebreak -->':story.append(PageBreak());i+=1;continue
            picture=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',raw)
            if picture:
                asset=(path.parent/picture.group(2)).resolve()
                if not asset.is_relative_to((path.parent/'report-assets').resolve()) or not asset.is_file():
                    raise ValueError('报告图像不存在或路径无效：'+picture.group(2))
                img=Image(str(asset));img.drawHeight*=180*mm/img.drawWidth;img.drawWidth=180*mm
                story.append(img);i+=1;continue
            if raw.startswith('|'):
                rows=[]
                while i<len(lines) and lines[i].strip().startswith('|'):
                    cells=[c.strip() for c in lines[i].strip().strip('|').split('|')]
                    if not all(set(c)<=set('-: ') for c in cells): rows.append(cells)
                    i+=1
                if rows:
                    width=max(len(r) for r in rows); rows=[r+['']*(width-len(r)) for r in rows]
                    table=Table([[Paragraph(inline(c),body) for c in r] for r in rows], colWidths=[180*mm/width]*width, repeatRows=1, hAlign='LEFT')
                    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9eef3')),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#aab4bf')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)])); story.append(table); story.append(Spacer(1,5))
                continue
            if raw.startswith('## '): story.append(Paragraph(inline(raw[3:]),subheading))
            elif raw.startswith('# '): story.append(Paragraph(inline(raw[2:]),heading))
            elif raw.startswith('- '): story.append(Paragraph('- '+inline(raw[2:]),bullet))
            else: story.append(Paragraph(inline(raw),body))
            i+=1
        doc=SimpleDocTemplate(str(path),pagesize=A4,rightMargin=15*mm,leftMargin=15*mm,topMargin=14*mm,bottomMargin=14*mm,title='Thermal Studio 仿真报告')
        def footer(canvas,doc):
            canvas.setFont(font_name,8);canvas.setFillColor(colors.HexColor('#667085'))
            canvas.drawString(15*mm,8*mm,'Thermal Studio | 工程筛查 · 需实测验证')
            canvas.drawRightString(195*mm,8*mm,str(doc.page))
        doc.build(story,onFirstPage=footer,onLaterPages=footer)
        return
    except Exception as error:
        raise RuntimeError("PDF 报告生成失败："+str(error)) from error


def _write_basic_pdf(path, text):
    """Minimal ASCII PDF fallback used only when ReportLab is unavailable."""
    lines=[]
    for raw in text.splitlines():
        line=''.join(ch if 32<=ord(ch)<127 else '?' for ch in raw)
        lines.extend(line[i:i+100] for i in range(0,max(1,len(line)),100))
    stream=['BT','/F1 9 Tf','38 805 Td']
    for line in lines:
        escaped=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        stream.append(f'({escaped}) Tj 0 -13 Td')
    stream.append('ET')
    content='\n'.join(stream).encode('ascii')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(content)).encode('ascii')+b' >>\nstream\n'+content+b'\nendstream']
    pdf=bytearray(b'%PDF-1.4\n');offsets=[0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(pdf));pdf.extend(f'{index} 0 obj\n'.encode('ascii'));pdf.extend(obj);pdf.extend(b'\nendobj\n')
    xref=len(pdf);pdf.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode('ascii'))
    for offset in offsets[1:]:pdf.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    pdf.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode('ascii'))
    path.write_bytes(pdf)
