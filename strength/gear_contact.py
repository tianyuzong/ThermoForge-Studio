"""Experimental small-slip unilateral gear contact with exact rigid bore kinematics.

Elastic C3D10 assembly, compliance solves and recovery run on CUDA/cuDSS.
The host solves a bounded complementary-energy QP for contact forces. Each
constraint is at a subtriangle quadrature point, with quadratic interpolation
on BOTH faces (not node-to-face contact, penalty springs or a bonded tie).
Normals/projections are frozen in the imported configuration. This is a static
teaching formulation; Hertz pressure / fatigue and mesh convergence are NOT
certified. Positive initial clearances are retained, never closed by moving CAD.
"""
import hashlib,json,time
import numpy as np
from scipy.sparse import coo_matrix
from scipy.spatial import cKDTree
from scipy.optimize import linprog
from scipy.linalg import solve
from .fem import tri_shape,build_constraints
from .materials import bodies,property_array
from .schemas import Support

_cached=None
Q=np.array([[1/6,1/6],[2/3,1/6],[1/6,2/3],[1/3,1/3]])
SPLIT=np.array([[0,3,5],[3,1,4],[5,4,2],[3,4,5]])

def clear_cache():
    global _cached
    if _cached is not None:_cached['system'].close();_cached=None

def project_quadratic(point,P,q):
    """Orthogonal projection to the interior of a curved quadratic triangle."""
    q=np.array(q,float)
    for _ in range(12):
        N,d=tri_shape(q);x=N@P;J=P.T@d
        step=np.linalg.solve(J.T@J,J.T@(x-point));q-=step
        if np.linalg.norm(step)<1e-10:break
    N,d=tri_shape(q);x=N@P;J=P.T@d
    if min(q)<-1e-7 or q.sum()>1+1e-7:return None
    n=np.cross(J[:,0],J[:,1]);n/=np.linalg.norm(n)
    if np.linalg.norm((x-point)-n*np.dot(x-point,n))>2e-9:return None
    return N,x,n,q

def contact_rows(mesh,cfg,progress=lambda *a:None):
    p=mesh['points'];faces=mesh['faces'];P=p[faces]
    owners=p[mesh['tets'][mesh['owners'],:4]].mean(1)
    N,d=tri_shape([1/3,1/3]);J=np.einsum('fic,ij->fcj',P,d)
    normal=np.cross(J[:,:,0],J[:,:,1]);normal/=np.linalg.norm(normal,axis=1)[:,None]
    normal*=np.where(np.sum(normal*(np.einsum('i,fic->fc',N,P)-owners),axis=1)<0,-1,1)[:,None]
    rows=[];cols=[];vals=[];gaps=[];records=[]
    for pair_id,pair in enumerate(cfg.pairs):
        progress('solving',f'搜索齿面接触 {pair.slave_body} → {pair.master_body}（保持原始齿隙）')
        si=np.flatnonzero((mesh['face_body_ids']==pair.slave_body)&(np.abs(normal[:,2])<.65))
        mi=np.flatnonzero((mesh['face_body_ids']==pair.master_body)&(np.abs(normal[:,2])<.65))
        if not len(si) or not len(mi):raise ValueError('接触组件无可用侧面；当前仅支持轴线平行 Z 的直齿轮')
        sub=P[mi][:,SPLIT].reshape(-1,3,3);tree=cKDTree(sub.mean(1));radius=np.max(np.linalg.norm(sub-sub.mean(1)[:,None,:],axis=2))
        # Conservative broad phase. No truncation of contacts to fit resources.
        for q in Q:
            Ns,ds=tri_shape(q);xs=np.einsum('i,fic->fc',Ns,P[si])
            distance,index=tree.query(xs,k=min(24,len(sub)))
            if index.ndim==1:index=index[:,None];distance=distance[:,None]
            for local in np.flatnonzero(distance[:,0]<cfg.search_distance_m+radius):
                sf=si[local];point=xs[local];best=None
                for mf in np.unique(mi[index[local]//4]):
                    if np.dot(normal[sf],normal[mf])>-.7:continue
                    projected=project_quadratic(point,P[mf],[1/3,1/3])
                    if projected is None:continue
                    Nm,xm,n,qm=projected
                    # n points from slave towards master; master reaction is +n.
                    if np.dot(n,normal[mf])>0:n=-n
                    delta=xm-point;gap=float(delta@n)
                    dist=float(np.linalg.norm(delta))
                    if gap < -2e-8 and dist<=cfg.search_distance_m:raise ValueError('初始二次接触面存在穿透；请检查几何/网格，未自动移动工件')
                    if gap < -2e-8:continue
                    if dist<=cfg.search_distance_m and (best is None or dist<best[0]):best=(dist,mf,Nm,xm,n,max(0.,gap),qm)
                if best is None:continue
                _,mf,Nm,xm,n,gap,qm=best
                js=P[sf].T@ds;area=float(np.linalg.norm(np.cross(js[:,0],js[:,1]))/8)
                row=len(gaps)
                for f,Nf,sign in ((sf,Ns,-1),(mf,Nm,1)):
                    for node,w in zip(faces[f],Nf):
                        for axis in range(3):rows.append(row);cols.append(int(node)*3+axis);vals.append(sign*w*n[axis])
                gaps.append(gap);records.append(dict(pair_id=pair_id,slave_face=int(sf),master_face=int(mf),
                    position_m=point.tolist(),master_position_m=xm.tolist(),normal=n.tolist(),area_m2=area,
                    slave_weights=Ns.tolist(),master_weights=Nm.tolist()))
                if len(gaps)>cfg.max_contact_points:raise MemoryError('接触积分点超过配置上限；未删除接触点或修改网格，请明确调整网格或接触点预算')
        if not any(r['pair_id']==pair_id for r in records):raise ValueError(f'接触对 {pair.slave_body}/{pair.master_body} 无候选积分点；请检查初始齿隙与接触搜索距离')
    B=coo_matrix((vals,(rows,cols)),shape=(len(gaps),p.size)).tocsr()
    # Each contact row must conserve global force and moment for arbitrary p.
    R=np.zeros((len(p),3,6));R[:,:,:3]=np.eye(3)
    for axis in range(3):R[:,:,axis+3]=np.cross(np.eye(3)[axis],p)
    rigid_error=np.max(np.abs(B@R.reshape(-1,6)),axis=0)
    if np.max(rigid_error[:3])>1e-10 or np.max(rigid_error[3:])>1e-9:raise ValueError('接触力映射未通过刚体虚功守恒检查')
    return B,np.array(gaps),records

def solve_contact_qp(C,A,g0,T):
    """min 1/2 p'C p + g0'p; A'p=-T, p>=0. No penalty/regularization."""
    if any(not np.isfinite(a).all() for a in (C,A,g0,T)):raise ValueError('接触方程包含非有限数值')
    # Normalize physical units before feasibility and active-set iterations.
    lscale=max(float(np.max(np.abs(A))),1e-12)
    fscale=max(float(np.max(np.abs(T)))/lscale,1.)
    dscale=max(float(np.max(np.diag(C)))*fscale,float(np.max(np.abs(g0))),1e-10)
    H=C*fscale/dscale;g=g0/dscale;E=A/lscale;b=-T/(fscale*lscale)
    initial=linprog(g,A_eq=E.T,b_eq=b,bounds=(0,None),method='highs')
    if not initial.success:raise ValueError('所选接触面无法以压缩接触力平衡轴端扭矩；请检查方向、接触对和间隙，未增加约束')
    x=initial.x;active=set(np.flatnonzero(x>1e-10).tolist());n=len(x);theta=np.zeros(A.shape[1])
    for iteration in range(max(200,n*5)):
        I=np.array(sorted(active));m=len(I)
        K=np.block([[H[np.ix_(I,I)],E[I]],[E[I].T,np.zeros((len(T),len(T)))]])
        try:z=solve(K,np.r_[-g[I],b],assume_a='sym')
        except np.linalg.LinAlgError as exc:raise ValueError('接触约束矩阵奇异，保留诊断；未加弱弹簧或数值正则项') from exc
        target=np.zeros(n);target[I]=z[:m];direction=target-x
        negative=I[direction[I]<-1e-12]
        if len(negative):
            fractions=-x[negative]/direction[negative];alpha=min(1.,float(fractions.min()))
        else:alpha=1.
        x+=alpha*direction;x[np.abs(x)<1e-12]=0
        if alpha<1-1e-12:
            drop=negative[np.argmin(fractions)];active.remove(int(drop));continue
        theta=z[m:];slack=H@x+g+E@theta
        inactive=np.array([j for j in range(n) if j not in active],int)
        if not len(inactive) or slack[inactive].min()>=-1e-9:
            p=x*fscale;angle=theta*dscale/lscale;gap=g0+C@p+A@angle
            if any(not np.isfinite(a).all() for a in (p,angle,gap)):raise ValueError('接触解包含非有限数值')
            eq=np.linalg.norm(A.T@p+T)/max(np.linalg.norm(T),1e-12)
            if eq>1e-8 or p.min()<-1e-7 or gap.min()<-1e-9 or np.max(np.abs(p*gap))>max(np.max(p),1)*1e-9:
                raise ValueError('接触互补或轴端扭矩校核失败')
            return p,angle,gap,iteration+1,eq
        active.add(int(inactive[np.argmin(slack[inactive])]))
    raise ValueError('接触主动集迭代未收敛，未修改载荷或间隙')

def hub_constraints(mesh,case,evidence):
    cfg=case.gear_contact
    supports=[Support(name=f'组件 {h.body_id} 刚性轴接口',selection=h.bore) for h in cfg.hubs]
    reference=case.model_copy(update={'supports':supports})
    fixed,groups=build_constraints(mesh,reference,evidence)
    rotations=[];freehubs=[]
    for h in cfg.hubs:
        if h.rotation_locked:continue
        r=np.zeros_like(mesh['points']);nodes=np.unique(mesh['tets'][bodies(mesh)==h.body_id])
        r[nodes]=np.cross([0.,0.,1.],mesh['points'][nodes]-h.center_m)
        rotations.append(r.ravel());freehubs.append(h)
    return fixed,groups,np.array(rotations).T,freehubs

def solve_gears(mesh,case,evidence,folder,progress):
    global _cached
    from .gpu_solver import GpuSystem,clear_cache as clear_all
    from .store import write_json
    start=time.perf_counter();cfg=case.gear_contact
    fixed,groups,R,freehubs=hub_constraints(mesh,case,evidence)
    h=hashlib.sha256()
    for a in (mesh['points'],mesh['tets'],np.array(sorted(fixed)),property_array(mesh,case,'young_modulus_Pa'),property_array(mesh,case,'poisson_ratio')):h.update(a.tobytes())
    geometry=cfg.model_dump()
    for hub in geometry['hubs']:hub['torque_Nm']=0
    geometry.pop('assumptions');h.update(json.dumps(geometry,sort_keys=True).encode());key=h.hexdigest()
    reused=_cached is not None and _cached['key']==key
    try:
        if not reused:
            clear_all();system=GpuSystem(mesh,case,fixed,progress)
            _cached=dict(key=key,system=system)
            with system.phase('contact_search','建立二次齿面接触积分点'):
                B,g0,records=contact_rows(mesh,cfg,progress);Bf=B[:,system.free];A=B@R
            if np.linalg.matrix_rank(A)<len(freehubs):raise ValueError('接触无法消除剩余轴旋转刚体模态')
            system.build();C=np.empty((len(g0),len(g0)))
            with system.phase('contact_compliance','GPU 多右端回代：凝聚齿面柔度'):
                for offset in range(0,len(g0),8):
                    stop=min(offset+8,len(g0));rhs=Bf[offset:stop].T.toarray()
                    X=system.solver.solve_many(rhs);C[:,offset:stop]=Bf@X
                    if offset%64==0:progress('solving',f'GPU 齿面柔度 {stop}/{len(g0)}')
            asym=float(np.linalg.norm(C-C.T)/max(np.linalg.norm(C),1e-30))
            if asym>1e-7:raise ValueError(f'GPU 接触柔度对称性检查失败 {asym:g}')
            C=(C+C.T)/2
            _cached.update(B=B,g0=g0,records=records,A=A,C=C,asym=asym)
        else:system=_cached['system'];system.times={};system.progress=progress
        B,g0,records,A,C=(_cached[k] for k in ('B','g0','records','A','C'))
        T=np.array([h.torque_Nm for h in freehubs])
        with system.phase('contact_active_set','求解压缩接触与各轴扭矩平衡'):
            pressure_force,angle,gap,iterations,eq=solve_contact_qp(C,A,g0,T)
        if np.max(np.abs(angle))>.01:raise ValueError('齿轮转角超过 0.01 rad，小滑移固定法向模型不适用；未继续给出强度结论')
        contact_force=np.asarray(B.T@pressure_force).reshape(-1,3)
        data,execution=system.solve(contact_force,fixed,reused)
        elastic=data['displacements'].copy();rigid=(R@angle).reshape(-1,3)
        data['displacements']+=rigid;data['elastic_displacements']=elastic
        # Independently check the final FE field against the condensed gaps.
        verified_gap=g0+B@data['displacements'].ravel()
        if np.max(np.abs(verified_gap-gap))>1e-9:raise ValueError('最终有限元位移与接触凝聚解不一致')
        # Limited small-slip search validity. Retain the original search distance.
        slip=[]
        for record in records:
            sf,mf=record['slave_face'],record['master_face'];n=np.array(record['normal'])
            delta=np.array(record['master_weights'])@data['displacements'][mesh['faces'][mf]]-np.array(record['slave_weights'])@data['displacements'][mesh['faces'][sf]]
            slip.append(float(np.linalg.norm(delta-n*(delta@n))))
        maxslip=max(slip)
        if maxslip>cfg.search_distance_m/2:raise ValueError('切向相对位移超出固定投影接触搜索有效范围；需要更新接触几何的非线性求解')
        shaft=[]
        for hub in cfg.hubs:
            nodes=np.unique(mesh['tets'][bodies(mesh)==hub.body_id]);reaction=data['reactions'][nodes]
            torque=np.cross(mesh['points'][nodes]-hub.center_m,reaction).sum(0)
            idx=next((i for i,h in enumerate(freehubs) if h.body_id==hub.body_id),None)
            shaft.append(dict(body_id=hub.body_id,center_m=hub.center_m,rotation_locked=hub.rotation_locked,input_torque_Nm=hub.torque_Nm,
                rotation_rad=float(angle[idx]) if idx is not None else 0.,reaction_N=reaction.sum(0).tolist(),shaft_torque_Nm=torque.tolist()))
        pairs=[];area=np.array([r['area_m2'] for r in records]);pressure=pressure_force/area
        for i,pair in enumerate(cfg.pairs):
            ids=np.array([j for j,r in enumerate(records) if r['pair_id']==i]);active=ids[pressure_force[ids]>1e-7]
            resultant=sum((pressure_force[j]*np.array(records[j]['normal']) for j in ids),start=np.zeros(3))
            pairs.append(dict(slave_body=pair.slave_body,master_body=pair.master_body,candidate_points=len(ids),active_points=len(active),
                master_resultant_N=resultant.tolist(),normal_force_N=float(pressure_force[ids].sum()),
                maximum_pressure_Pa=float(pressure[ids].max()),active_sample_area_m2=float(area[active].sum())))
        summary=dict(formulation='small-slip quadratic-surface quadrature unilateral contact / rigid Z shafts',experimental=True,
            assumptions=cfg.assumptions,shaft_results=shaft,pairs=pairs,iterations=iterations,contact_points=len(g0),
            minimum_initial_sample_gap_m=float(g0.min()),minimum_final_sample_gap_m=float(gap.min()),maximum_tangential_slip_m=maxslip,
            torque_relative_residual=eq,complementarity_max_Nm=float(np.max(np.abs(pressure_force*verified_gap))),
            compliance_symmetry_relative=_cached['asym'],maximum_contact_pressure_Pa=float(pressure.max()),
            maximum_elastic_displacement_m=float(np.linalg.norm(elastic,axis=1).max()),
            pressure_basis='离散子三角形积分点力 / 参考面积；未经接触网格收敛，不能作为 Hertz 接触疲劳许用值',
            passed=True)
        # Save full contact records so the surface search and balance are reproducible.
        write_json(folder/'contact.json',dict(summary=summary,records=records,initial_gaps_m=g0.tolist(),final_gaps_m=verified_gap.tolist(),
            normal_forces_N=pressure_force.tolist(),pressures_Pa=pressure.tolist()))
        data.update(contact_point_positions=np.array([r['position_m'] for r in records]),contact_gaps_m=verified_gap,
            contact_pressures_Pa=pressure,contact_forces_N=pressure_force)
        # Surface field is a positive weighted display average, never the strength basis.
        display_sum=np.zeros(len(mesh['points']));display_weight=np.zeros_like(display_sum)
        for i,record in enumerate(records):
            for face in (record['slave_face'],record['master_face']):
                np.add.at(display_sum,mesh['faces'][face],pressure[i]*area[i]);np.add.at(display_weight,mesh['faces'][face],area[i])
        data['contact_pressure_display']=np.divide(display_sum,display_weight,out=np.zeros_like(display_sum),where=display_weight>0)
        execution.update(contact_factorization_reused=reused,compute_s=time.perf_counter()-start,contact_host_qp=True,
            note=execution['note']+'；主机接触搜索与小型主动集；固定轴心，齿轮允许绕 Z 的小转角')
        audit=[dict(name='齿面相互作用（内力）',kind='gear_contact',resultant_N=contact_force.sum(0).tolist(),
            moment_Nm=np.cross(mesh['points'],contact_force).sum(0).tolist(),moment_origin_m=[0,0,0],mapping_passed=True)]
        return data,execution,contact_force,fixed,groups,audit,summary
    except BaseException:clear_cache();raise
