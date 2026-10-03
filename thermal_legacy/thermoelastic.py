"""Quasi-static, small-strain 3-D isotropic thermoelastic tetrahedra.

Engineering shear Voigt order: xx, yy, zz, xy, yz, xz.  Temperature is a
one-way load; no plasticity, creep, mechanical contact, or fluid mechanics.
"""
import numpy as np
from time import perf_counter
from scipy.sparse import coo_matrix, bmat, csc_matrix
from scipy.sparse.linalg import splu
from thermal_legacy.geometry import connected_tet_components, project


def map_supports(mesh, display, supports):
    import trimesh
    boundary=np.asarray(mesh['boundary_triangles'])
    tri=np.asarray(mesh['points'])[boundary]
    original=trimesh.Trimesh(vertices=display['points'],faces=display['faces'],process=False)
    _,distance,face_ids=project(original,tri.mean(axis=1))
    area=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    fixed=[];audit=[]
    source_tri=np.asarray(display['points'])[np.asarray(display['faces'])]
    source_area=np.linalg.norm(np.cross(source_tri[:,1]-source_tri[:,0],source_tri[:,2]-source_tri[:,0]),axis=1)/2
    for support in supports:
        mask=np.isin(face_ids,support['faces'])
        nodes=np.unique(boundary[mask])
        if not len(nodes): raise ValueError(f'固定支撑“{support["name"]}”未映射到网格，请细化网格或调整选区')
        axes=['xyz'.index(a) for a in support['axes']]
        fixed.extend((nodes[:,None]*3+axes).ravel().tolist())
        audit.append(dict(name=support['name'],axes=support['axes'],nodes=len(nodes),
            selected_area_m2=float(source_area[support['faces']].sum()),mapped_area_m2=float(area[mask].sum()),
            projection_max_m=float(distance[mask].max())))
    return np.unique(fixed).astype(int),audit


def element_operators(points,tets,materials,labels):
    xyz=points[tets]
    # Invert the local affine map; gradients are in global coordinates.
    edge=xyz[:,1:]-xyz[:,:1]
    det=np.linalg.det(edge)
    if np.any(np.abs(det)<1e-24): raise ValueError('结构网格含退化四面体')
    volume=np.abs(det)/6
    grad=np.zeros((len(tets),4,3))
    grad[:,1:]=np.linalg.inv(edge).transpose(0,2,1)
    grad[:,0]=-grad[:,1:].sum(axis=1)
    B=np.zeros((len(tets),6,12))
    for a in range(4):
        x,y,z=grad[:,a].T
        B[:,0,3*a]=x;B[:,1,3*a+1]=y;B[:,2,3*a+2]=z
        B[:,3,3*a]=y;B[:,3,3*a+1]=x
        B[:,4,3*a+1]=z;B[:,4,3*a+2]=y
        B[:,5,3*a]=z;B[:,5,3*a+2]=x
    E=np.array([materials[i]['young_modulus_Pa'] for i in labels],float)
    nu=np.array([materials[i]['poisson_ratio'] for i in labels],float)
    alpha=np.array([materials[i]['thermal_expansion_CTE_per_K'] for i in labels],float)
    if not np.all(np.isfinite(E)) or np.any(E<=0) or np.any((nu<=-1)|(nu>=.499)):
        raise ValueError('结构材料弹性参数无效')
    mu=E/(2*(1+nu));lam=E*nu/((1+nu)*(1-2*nu))
    D=np.zeros((len(tets),6,6));D[:,:3,:3]=lam[:,None,None]
    for a in range(3):D[:,a,a]+=2*mu;D[:,a+3,a+3]=mu
    return B,D,volume,alpha


def rigid_modes(points):
    r=points-points.mean(axis=0)
    r/=max(float(np.ptp(points,axis=0).max()),1e-12)
    R=np.zeros((len(points),3,6));R[:,:,:3]=np.eye(3)
    R[:,1,3]=-r[:,2];R[:,2,3]=r[:,1]
    R[:,0,4]=r[:,2];R[:,2,4]=-r[:,0]
    R[:,0,5]=-r[:,1];R[:,1,5]=r[:,0]
    return R.reshape(-1,6)


def solve_thermoelastic(mesh,temperatures,materials,labels,settings,fixed_dofs=(),callback=None,stage_callback=None):
    started=perf_counter()
    points=np.asarray(mesh['points'],float);tets=np.asarray(mesh['tets'],int)
    T=np.asarray(temperatures,float);ndof=len(points)*3;ne=len(tets)
    if ndof>450000 or T.shape[0]*ne*6>180000000:
        raise ValueError('热应力结果规模过大，请减少保存帧数或适当增大网格尺寸')
    if T.shape[1]!=len(points) or not np.all(np.isfinite(T)):raise ValueError('结构温度场与网格不匹配')
    if stage_callback:stage_callback(f'组装热弹性刚度矩阵（{ne:,}个单元）')
    B,D,volume,alpha=element_operators(points,tets,materials,labels)
    dofs=(tets[:,:,None]*3+np.arange(3)).reshape(ne,12)
    ke=np.einsum('eai,eab,ebj,e->eij',B,D,B,volume,optimize=True)
    K=coo_matrix((ke.ravel(),(np.repeat(dofs,12,axis=1).ravel(),np.tile(dofs,(1,12)).ravel())),shape=(ndof,ndof)).tocsc()
    del ke
    fixed=np.unique(np.asarray(fixed_dofs,int))
    if np.any((fixed<0)|(fixed>=ndof)):raise ValueError('固定支撑自由度无效')
    if settings['mode']=='free' and len(fixed):raise ValueError('自由分析不能含固定支撑')
    if settings['mode']=='constrained' and not len(fixed):raise ValueError('受约束分析缺少有效固定节点')
    free=np.setdiff1d(np.arange(ndof),fixed)
    components=connected_tet_components(tets,points)
    gauges=[];gauge_dofs=[]
    for component in np.unique(components):
        nodes=np.unique(tets[components==component]);cdof=(nodes[:,None]*3+np.arange(3)).ravel()
        R=rigid_modes(points[nodes]);held=np.isin(cdof,fixed)
        if settings['mode']=='constrained':
            rank=np.linalg.matrix_rank(R[held]) if np.any(held) else 0
            if rank<6:raise ValueError(f'结构体 {int(component)+1} 支撑不足，仍有 {6-rank} 个刚体自由度；请补充固定面/方向或选择自由分析')
        else:
            # Six independent scalar gauges span rigid motion exactly. Pin
            # them for the sparse solve, then recover the same orthogonal
            # gauge as a dense KKT system without its dense border/fill-in.
            from scipy.linalg import qr
            Q=np.linalg.qr(R)[0]
            _,_,pivots=qr(R.T,pivoting=True,mode='economic')
            gauge_dofs.extend(cdof[pivots[:6]])
            gauges.append((cdof,Q))
    if gauge_dofs:free=np.setdiff1d(free,gauge_dofs)
    scale=max(float(np.abs(K.diagonal()).max()),1.)
    A=K[free][:,free]/scale
    assembled=perf_counter()
    if stage_callback:stage_callback(f'分解热弹性矩阵（{len(free):,}个自由度）')
    factor=splu(A) if len(free) else None
    factored=perf_counter()
    if stage_callback:stage_callback(f'逐帧求解热位移与应力（{len(T)}帧）')
    displacements=[];stresses=[];mises=[];rows_out=[]
    max_residual=0.;max_strain=0.
    for f,temperature in enumerate(T):
        delta=temperature[tets].mean(axis=1)-settings['reference_C']
        eth=np.zeros((ne,6));eth[:,:3]=(alpha*delta)[:,None]
        sth=np.einsum('eab,eb->ea',D,eth)
        fe=np.einsum('eai,ea,e->ei',B,sth,volume)
        F=np.bincount(dofs.ravel(),weights=fe.ravel(),minlength=ndof)
        u=np.zeros(ndof)
        if len(free):
            rhs=F[free]/scale
            u[free]=factor.solve(rhs)
        for cdof,Q in gauges:u[cdof]-=Q@(Q.T@u[cdof])
        residual=K@u-F
        relative=float(np.linalg.norm(residual if gauges else residual[free])/max(np.linalg.norm(F),1e-12))
        if relative>1e-6 or not np.all(np.isfinite(u)):
            raise ValueError(f'热应力求解未通过平衡残差检查：{relative:.3g}')
        max_residual=max(max_residual,relative)
        strain=np.einsum('eai,ei->ea',B,u[dofs])
        stress=np.einsum('eab,eb->ea',D,strain-eth)
        vm=np.sqrt(.5*((stress[:,0]-stress[:,1])**2+(stress[:,1]-stress[:,2])**2+(stress[:,2]-stress[:,0])**2)+3*np.sum(stress[:,3:]**2,axis=1))
        tensor=np.zeros((ne,3,3))
        tensor[:,0,0]=stress[:,0];tensor[:,1,1]=stress[:,1];tensor[:,2,2]=stress[:,2]
        tensor[:,0,1]=tensor[:,1,0]=stress[:,3];tensor[:,1,2]=tensor[:,2,1]=stress[:,4];tensor[:,0,2]=tensor[:,2,0]=stress[:,5]
        principal=np.linalg.eigvalsh(tensor)
        # Within a P1 cell, thermal hydrostatic stress varies with temperature.
        # Include its extrema, rather than reporting centroid-only principals.
        hydro=D[:,:3,:3].sum(axis=2)[:,0]*alpha
        offset=(temperature[tets]-temperature[tets].mean(axis=1)[:,None])*hydro[:,None]
        magnitude=np.linalg.norm(u.reshape(-1,3),axis=1)
        max_strain=max(max_strain,float(np.abs(strain).max()),float(np.abs(alpha[:,None]*(temperature[tets]-settings['reference_C'])).max()))
        peak_cell=int(vm.argmax());peak_node=int(magnitude.argmax())
        rows_out.append(dict(maximum_von_mises_Pa=float(vm.max()),maximum_principal_Pa=float((principal[:,-1]-offset.min(axis=1)).max()),
            minimum_principal_Pa=float((principal[:,0]-offset.max(axis=1)).min()),maximum_displacement_m=float(magnitude.max()),
            stress_peak_position_m=points[tets[peak_cell]].mean(axis=0).tolist(),displacement_peak_position_m=points[peak_node].tolist(),
            reaction_force_N=residual.reshape(-1,3).sum(axis=0).tolist(),relative_equilibrium_residual=relative))
        displacements.append(u.reshape(-1,3));stresses.append(stress);mises.append(vm)
        if callback:callback((f+1)/len(T))
    return dict(displacement=np.asarray(displacements,dtype='<f4'),stress=np.asarray(stresses,dtype='<f4'),
        von_mises=np.asarray(mises,dtype='<f4'),stats=rows_out,summary=dict(mode=settings['mode'],reference_C=settings['reference_C'],
        method='3D P1 isotropic linear thermoelasticity; one-way quasi-static coupling',components=len(np.unique(components)),
        fixed_dofs=len(fixed),timings_s=dict(assembly=assembled-started,factorization=factored-assembled,frames=perf_counter()-factored),
        maximum_relative_equilibrium_residual=max_residual,maximum_strain=max_strain,
        small_strain_valid=max_strain<=.01,stress_components=['xx','yy','zz','xy','yz','xz'],
        assumptions=['small strain; constant isotropic elastic properties','no plasticity, creep, fatigue or mechanical contact',
                     'shared mesh nodes are mechanically bonded; disconnected solids are separate bodies',
                     'stress is cell-based; sharp fixed edges may produce mesh-dependent stress concentrations']))


def assess_design(cfg,mesh,temperatures,times,structural,materials,labels):
    limits=cfg.get('design_limits') or {};checks=[]
    T=np.asarray(temperatures);tets=mesh['tets'];points=mesh['points']
    for name,key,fn,op in [('最低温度','minimum_C',np.argmin,lambda a,b:a<b),('最高温度','maximum_C',np.argmax,lambda a,b:a>b)]:
        f,n=np.unravel_index(fn(T),T.shape);value=float(T[f,n]);limit=limits.get(key)
        checks.append(dict(name=name,value=value,limit=limit,unit='°C',status='not_assessed' if limit is None else 'exceeded' if op(value,limit) else 'within_limits',time_s=float(times[f]),position_m=points[n].tolist()))
    if structural:
        mag=np.linalg.norm(structural['displacement'],axis=2);f,n=np.unravel_index(mag.argmax(),mag.shape)
        limit=limits.get('maximum_displacement_m')
        checks.append(dict(name='最大热位移',value=float(mag[f,n]),limit=limit,unit='m',status='not_assessed' if limit is None else 'exceeded' if mag[f,n]>limit else 'within_limits',time_s=float(times[f]),position_m=points[n].tolist()))
        sf=limits.get('strength_safety_factor',1.5)
        for label in sorted(np.unique(labels)):
            ids=np.flatnonzero(labels==label);v=structural['von_mises'][:,ids];f,c=np.unravel_index(v.argmax(),v.shape)
            material=materials[label];criterion=material.get('strength_criterion','von_mises')
            if criterion=='von_mises':
                strength=material.get('yield_strength_Pa');limit=strength/sf if strength else None
                checks.append(dict(name=material['name']+' 屈服风险',value=float(v[f,c]),limit=limit,unit='Pa',
                    status='not_assessed' if limit is None else 'exceeded' if v[f,c]>limit else 'within_limits',
                    time_s=float(times[f]),position_m=points[tets[ids[c]]].mean(axis=0).tolist(),safety_factor=sf))
            elif criterion=='principal':
                # P1 total strain is constant; temperature-dependent hydrostatic
                # stress varies linearly within each tetrahedron. Check vertices.
                peaks=[(-1.,0,0,0),(-1.,0,0,0)]
                hydro=material['young_modulus_Pa']*material.get('thermal_expansion_CTE_per_K',0)/(1-2*material['poisson_ratio'])
                for frame,s in enumerate(structural['stress'][:,ids]):
                    tensor=np.zeros((len(ids),3,3))
                    tensor[:,0,0]=s[:,0];tensor[:,1,1]=s[:,1];tensor[:,2,2]=s[:,2]
                    tensor[:,0,1]=tensor[:,1,0]=s[:,3];tensor[:,1,2]=tensor[:,2,1]=s[:,4];tensor[:,0,2]=tensor[:,2,0]=s[:,5]
                    eig=np.linalg.eigvalsh(tensor);local=T[frame,tets[ids]]
                    offset=(local-local.mean(axis=1)[:,None])*hydro
                    for i,values in enumerate((np.maximum(0,eig[:,-1,None]-offset),np.maximum(0,-eig[:,0,None]+offset))):
                        cell,node=np.unravel_index(values.argmax(),values.shape)
                        if values[cell,node]>peaks[i][0]:peaks[i]=(float(values[cell,node]),frame,cell,node)
                for (value,f,c,n),key,title in zip(peaks,('tensile_strength_Pa','compressive_strength_Pa'),('主拉应力/抗裂','主压应力/抗压')):
                    strength=material.get(key);limit=strength/sf if strength else None
                    checks.append(dict(name=material['name']+' '+title,value=value,limit=limit,unit='Pa',
                        status='not_assessed' if limit is None else 'exceeded' if value>limit else 'within_limits',
                        time_s=float(times[f]),position_m=points[tets[ids[c],n]].tolist(),safety_factor=sf))
            else:checks.append(dict(name=material['name']+' 强度',status='not_assessed',reason='未启用材料强度判据'))
    else:
        checks.append(dict(name='热变形与热应力',status='not_assessed',reason='尚未启用热弹性求解'))
    for label in sorted(np.unique(labels)):
        material=materials[label];nodes=np.unique(tets[np.asarray(labels)==label]);local=T[:,nodes]
        for key,title,minimum,validity in [('service_min_C','最低使用温度',True,False),('service_max_C','最高使用温度',False,False),
                ('valid_min_C','物性温区下限',True,True),('valid_max_C','物性温区上限',False,True)]:
            limit=material.get(key)
            if limit is None:continue
            f,n=np.unravel_index((local.argmin() if minimum else local.argmax()),local.shape);value=float(local[f,n])
            outside=value<limit if minimum else value>limit
            checks.append(dict(name=material['name']+' '+title,value=value,limit=limit,unit='°C',time_s=float(times[f]),position_m=points[nodes[n]].tolist(),
                status=('review_required' if validity else 'exceeded') if outside else 'within_limits',
                reason='物性有效温区校核，不是产品失效温度' if validity else '厂商参考使用温度，仍需核对载荷和时间条件'))
        if material.get('category') in ('polymer','elastomer','composite'):
            checks.append(dict(name=material['name']+' 本构适用性',status='review_required',
                reason='当前为各向同性常物性模型；高分子粘弹性、蠕变、湿度及复合材料各向异性未求解。泊松比等示例假设须核实。'))
        tg=material.get('glass_transition_C')
        if tg is not None and float(local.max())>=tg:
            f,n=np.unravel_index(local.argmax(),local.shape)
            checks.append(dict(name=material['name']+' 玻璃化转变提示',value=float(local[f,n]),limit=tg,unit='°C',
                time_s=float(times[f]),position_m=points[nodes[n]].tolist(),status='review_required',
                reason='达到或高于Tg，应核对温度依赖物性；半结晶高分子可在Tg以上使用，不能仅据Tg判定失效。'))
    exceeded=any(c['status']=='exceeded' for c in checks)
    missing=any(c['status'] in ('not_assessed','review_required') for c in checks)
    invalid=bool(structural and not structural['summary']['small_strain_valid'])
    return dict(status='exceeded' if exceeded else 'review_required' if missing or invalid else 'within_configured_limits',
        checks=checks,model_validity='outside_small_strain' if invalid else 'requires_validation',
        note='仅评估已设置的限值；结果须经网格/时间步长收敛及样机测温验证，不代表疲劳寿命或产品可靠性认证。')
