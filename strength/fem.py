"""C3D10 interpolation and consistent nodal loads, independent of display meshes."""
import numpy as np
from numpy.polynomial.legendre import leggauss
from .geometry import resolve_selection
from .materials import bodies,property_array,selection_mask

EDGES=((0,1),(1,2),(2,0),(0,3),(1,3),(2,3))
DL=np.array([[-1,-1,-1],[1,0,0],[0,1,0],[0,0,1]],float)

def tet_shape(q):
    L=np.r_[1-np.sum(q),q]; N=np.empty(10); d=np.empty((10,3))
    N[:4]=L*(2*L-1); d[:4]=(4*L-1)[:,None]*DL
    for k,(a,b) in enumerate(EDGES,4): N[k]=4*L[a]*L[b]; d[k]=4*(L[b]*DL[a]+L[a]*DL[b])
    return N,d

def tri_shape(q):
    r,s=q; L=np.array([1-r-s,r,s]); dl=np.array([[-1,-1],[1,0],[0,1]])
    N=np.empty(6); d=np.empty((6,2)); N[:3]=L*(2*L-1); d[:3]=(4*L-1)[:,None]*dl
    for k,(a,b) in enumerate(((0,1),(1,2),(2,0)),3): N[k]=4*L[a]*L[b]; d[k]=4*(L[b]*dl[a]+L[a]*dl[b])
    return N,d

def triangle_quadrature(n=4):
    x,w=leggauss(n); x=(x+1)/2; w=w/2
    return [(np.array([r,(1-r)*s]),wr*ws*(1-r)) for r,wr in zip(x,w) for s,ws in zip(x,w)]

def tetra_quadrature(n=4):
    x,w=leggauss(n); x=(x+1)/2; w=w/2
    return [(np.array([r,(1-r)*s,(1-r)*(1-s)*t]),wr*ws*wt*(1-r)**2*(1-s)) for r,wr in zip(x,w) for s,ws in zip(x,w) for t,wt in zip(x,w)]

def surface_weights(mesh,mask):
    faces=mesh['faces'][mask]; P=mesh['points'][faces]
    owner=mesh['points'][mesh['tets'][mesh['owners'][mask],:4]].mean(axis=1)
    outward=np.cross(P[:,1]-P[:,0],P[:,2]-P[:,0]); outward*=np.where(np.sum(outward*(P[:,:3].mean(1)-owner),axis=1)<0,-1,1)[:,None]
    for q,w in triangle_quadrature():
        N,d=tri_shape(q); dx=np.einsum('eic,ij->ecj',P,d)
        normal=np.cross(dx[:,:,0],dx[:,:,1]); norm=np.linalg.norm(normal,axis=1)
        if np.any(norm<=0): raise ValueError('退化表面网格')
        normal*=np.where(np.sum(normal*outward,axis=1)<0,-1,1)[:,None]
        yield faces,N,normal,norm,w

def assemble_loads(mesh,case,evidence):
    points=mesh['points']; force=np.zeros_like(points); audit=[]
    for load in case.loads:
        F=np.zeros_like(points)
        origin=points.mean(0); expected_force=np.zeros(3); expected_moment=np.zeros(3)
        if load.kind=='gravity':
            selected=np.ones(len(mesh['tets']),bool) if load.body_id is None else bodies(mesh)==load.body_id
            tets=mesh['tets'][selected]; P=points[tets]; accel=np.asarray(load.vector)
            density=property_array(mesh,case,'density_kg_m3')[selected]
            for q,w in tetra_quadrature():
                N,d=tet_shape(q); J=np.einsum('eic,ij->ecj',P,d); det=np.linalg.det(J)
                if np.any(det<=0): raise ValueError('载荷积分发现非正体 Jacobian')
                nodal=N[None,:,None]*det[:,None,None]*w*density[:,None,None]*accel
                np.add.at(F,tets.ravel(),nodal.reshape(-1,3))
                integrand=det[:,None]*w*density[:,None]*accel
                expected_force+=integrand.sum(0)
                expected_moment+=np.cross(np.einsum('i,eic->ec',N,P)-origin,integrand).sum(0)
            area=None
        else:
            mask=selection_mask(mesh,load.selection,evidence)
            weights=list(surface_weights(mesh,mask)); area=sum(float((norm*w).sum()) for _,_,_,norm,w in weights)
            if area<=0: raise ValueError('载荷面积为零')
            traction=np.asarray(load.vector)/area if load.kind=='force' else None
            for faces,N,normal,norm,w in weights:
                vec=norm[:,None]*traction if load.kind=='force' else -load.pressure_Pa*normal
                nodal=N[None,:,None]*vec[:,None,:]*w
                np.add.at(F,faces.ravel(),nodal.reshape(-1,3))
                expected_force+=(vec*w).sum(0)
                expected_moment+=np.cross(np.einsum('i,eic->ec',N,points[faces])-origin,vec*w).sum(0)
            if load.kind=='force' and not np.allclose(F.sum(0),load.vector,atol=1e-8,rtol=1e-10): raise ValueError('总力映射守恒失败')
        resultant=F.sum(0); moment=np.cross(points-origin,F).sum(0)
        fscale=max(np.linalg.norm(F,axis=1).sum(),1e-15)
        mscale=max(fscale*max(np.ptp(points,axis=0)),1e-18)
        ferr=float(np.linalg.norm(resultant-expected_force)/fscale)
        merr=float(np.linalg.norm(moment-expected_moment)/mscale)
        if max(ferr,merr)>1e-10: raise ValueError('载荷到节点的合力或合力矩映射校核失败')
        force+=F
        audit.append({'name':load.name,'kind':load.kind,'area_m2':area,'resultant_N':F.sum(0).tolist(),
            'moment_Nm':moment.tolist(),'moment_origin_m':origin.tolist(),
            'mapping_force_relative':ferr,'mapping_moment_relative':merr,'mapping_passed':True})
    return force,audit

def build_constraints(mesh,case,evidence,check_components=True):
    prescribed={}; groups=[]
    for support in case.supports:
        ids=resolve_selection(support.selection,evidence)
        mask=selection_mask(mesh,support.selection,evidence); nodes=np.unique(mesh['faces'][mask])
        if not len(nodes): raise ValueError('约束未映射到网格')
        for axis in support.components:
            a='xyz'.index(axis); value=support.displacement_m[a]
            for n in nodes:
                dof=int(n)*3+a
                if dof in prescribed and abs(prescribed[dof]-value)>1e-12: raise ValueError('同一自由度存在冲突的位移约束')
                prescribed[dof]=value
        groups.append({'name':support.name,'nodes':nodes.tolist(),'components':support.components,
            'face_ids':ids,'area_m2':sum(float((norm*w).sum()) for _,_,_,norm,w in surface_weights(mesh,mask))})
    p=mesh['points']; center=p.mean(0); scale=max(np.ptp(p,axis=0)); r=(p-center)/scale
    rigid=np.zeros((len(p),3,6)); rigid[:,:,:3]=np.eye(3)
    for j in range(3): rigid[:,:,3+j]=np.cross(np.eye(3)[j],r)
    dofs=np.array(sorted(prescribed),int); rank=np.linalg.matrix_rank(rigid.reshape(-1,6)[dofs],tol=1e-8)
    if rank<6: raise ValueError(f'欠约束：6 个刚体运动中仅约束 {rank} 个；请补充真实支撑方向，不会自动添加固定点')
    # Coupled assemblies undergo the per-connected-component rigid-mode check
    # after their explicit MPC interface mapping has been constructed.
    if not check_components:return prescribed,groups
    # Ensure the volume mesh is connected, not just the CAD label.
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    t=mesh['tets']; a=np.repeat(t[:,0],9); b=t[:,1:].ravel()
    graph=coo_matrix((np.ones(len(a)*2),(np.r_[a,b],np.r_[b,a])),shape=(len(p),len(p))).tocsr()
    count,labels=connected_components(graph,directed=False)
    for component in range(count):
        nodes=np.flatnonzero(labels==component);cp=p[nodes];origin=cp.mean(0);size=max(np.ptp(cp,axis=0))
        rr=(cp-origin)/size;basis=np.zeros((len(cp),3,6));basis[:,:,:3]=np.eye(3)
        for j in range(3):basis[:,:,3+j]=np.cross(np.eye(3)[j],rr)
        local=[i*3+a for i,n in enumerate(nodes) for a in range(3) if int(n)*3+a in prescribed]
        rank=np.linalg.matrix_rank(basis.reshape(-1,6)[local],tol=1e-8) if local else 0
        if rank<6: raise ValueError(f'组件 {component+1} 欠约束：仅消除 {rank}/6 个刚体运动；独立组件必须分别设置真实支撑')
    return prescribed,groups

def stress_invariants(stress):
    s=np.asarray(stress)
    vm=np.sqrt(.5*((s[...,0]-s[...,1])**2+(s[...,1]-s[...,2])**2+(s[...,2]-s[...,0])**2)+3*(s[...,3:]**2).sum(-1))
    tensor=np.zeros((*s.shape[:-1],3,3)); tensor[...,0,0]=s[...,0]; tensor[...,1,1]=s[...,1]; tensor[...,2,2]=s[...,2]
    tensor[...,0,1]=tensor[...,1,0]=s[...,3]; tensor[...,0,2]=tensor[...,2,0]=s[...,4]; tensor[...,1,2]=tensor[...,2,1]=s[...,5]
    return vm,np.linalg.eigvalsh(tensor)

IP_BARY=np.array([[.5854101966249685,.1381966011250105,.1381966011250105,.1381966011250105],
    [.1381966011250105,.5854101966249685,.1381966011250105,.1381966011250105],
    [.1381966011250105,.1381966011250105,.5854101966249685,.1381966011250105],
    [.1381966011250105,.1381966011250105,.1381966011250105,.5854101966249685]])

def nodal_stress_for_display(mesh,stress):
    # Extrapolate 4 Gauss points to 10 element nodes then volume-weight average.
    bary=np.vstack([np.eye(4),np.array([(np.eye(4)[a]+np.eye(4)[b])/2 for a,b in EDGES])])
    interpolation=bary@np.linalg.inv(IP_BARY)
    values=np.einsum('ni,eic->enc',interpolation,stress)
    points=mesh['points']; t=mesh['tets']; P=points[t[:,:4]]
    vol=np.abs(np.linalg.det(np.stack([P[:,1]-P[:,0],P[:,2]-P[:,0],P[:,3]-P[:,0]],axis=2)))/6
    acc=np.zeros((len(points),6)); weights=np.zeros(len(points))
    np.add.at(acc,t.ravel(),(values*vol[:,None,None]).reshape(-1,6)); np.add.at(weights,t.ravel(),np.repeat(vol,10))
    return acc/weights[:,None]
