"""Conservative two-sided quadrature maps on independent quadratic surfaces."""
import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from strength.fem import tri_shape, triangle_quadrature
from strength.materials import selection_mask


def project_surface(P,point):
    q=np.linalg.lstsq((P[1:3]-P[0]).T,point-P[0],rcond=None)[0]
    for _ in range(12):
        N,d=tri_shape(q);J=P.T@d
        delta=np.linalg.lstsq(J,point-N@P,rcond=None)[0];q+=delta
        if np.linalg.norm(delta)<1e-10:break
    if min(q)<-1e-7 or sum(q)>1+1e-7:return None
    N,d=tri_shape(q);normal=np.cross(*(P.T@d).T);normal/=np.linalg.norm(normal)
    return N,N@P,normal


def quadratic_shapes(q):
    L=np.column_stack([1-q.sum(1),q]);dl=np.array([[-1.,-1.],[1,0],[0,1]])
    N=np.empty((len(q),6));d=np.empty((len(q),6,2));N[:,:3]=L*(2*L-1);d[:,:3]=(4*L-1)[:,:,None]*dl
    for k,(a,b) in enumerate(((0,1),(1,2),(2,0)),3):N[:,k]=4*L[:,a]*L[:,b];d[:,k]=4*(L[:,b,None]*dl[a]+L[:,a,None]*dl[b])
    return N,d


def project_surfaces(P,points):
    """Batch closest-point Newton solves on curved T6 faces, without flattening."""
    def least_squares(J,r):
        a=np.sum(J[:,:,0]**2,axis=1);b=np.sum(J[:,:,0]*J[:,:,1],axis=1);c=np.sum(J[:,:,1]**2,axis=1)
        u=np.einsum('ni,ni->n',J[:,:,0],r);v=np.einsum('ni,ni->n',J[:,:,1],r)
        det=a*c-b*b;valid=det>np.finfo(float).eps*32*np.maximum(a*c+b*b,1e-300);safe=np.where(valid,det,1.)
        return np.column_stack([(c*u-b*v)/safe,(a*v-b*u)/safe]),valid
    J=np.swapaxes(P[:,1:3]-P[:,0,None],1,2);q,valid=least_squares(J,points-P[:,0])
    for _ in range(12):
        N,d=quadratic_shapes(q);J=np.einsum('nic,nij->ncj',P,d);r=points-np.einsum('ni,nic->nc',N,P)
        delta,good=least_squares(J,r);valid &=good;q=np.clip(q+delta,-100,100)
        if np.max(np.linalg.norm(delta,axis=1),initial=0)<1e-10:break
    N,d=quadratic_shapes(q);J=np.einsum('nic,nij->ncj',P,d);normal=np.cross(J[:,:,0],J[:,:,1]);norm=np.linalg.norm(normal,axis=1)
    valid &= (q.min(1)>=-1e-7)&(q.sum(1)<=1+1e-7)&(norm>0)
    normal/=np.where(norm>0,norm,1)[:,None]
    return N,np.einsum('ni,nic->nc',N,P),normal,valid


def interface_map(mesh,pair,evidence,displacement=None,allow_empty=False):
    p=mesh['points'].copy()
    if displacement is not None:p+=displacement
    si=np.flatnonzero(selection_mask(mesh,pair.slave,evidence));mi=np.flatnonzero(selection_mask(mesh,pair.master,evidence))
    sf=mesh['faces'][si];mf=mesh['faces'][mi]
    if not len(sf) or not len(mf):raise ValueError(pair.name+' 接触面为空')
    tree=cKDTree(p[mf].mean(1));records=[]
    for qi,(q,w) in enumerate(triangle_quadrature(3)):
        N,d=tri_shape(q);x=np.einsum('i,eic->ec',N,p[sf]);J=np.einsum('eic,ij->ecj',p[sf],d)
        normals=np.cross(J[:,:,0],J[:,:,1]);areas=np.linalg.norm(normals,axis=1)*w
        _,candidates=tree.query(x,k=min(12,len(mf)))
        candidates=np.asarray(candidates).reshape(len(x),-1)
        count=candidates.shape[1];indices=candidates.ravel();query=np.repeat(x,count,axis=0)
        weights,target,normal,valid=project_surfaces(p[mf[indices]],query)
        centers=p[mesh['tets'][mesh['owners'][mi[indices]],:4]].mean(1)
        flip=np.einsum('ni,ni->n',normal,target-centers)<0;normal[flip]*=-1
        distances=np.linalg.norm(query-target,axis=1);distances[~valid|(distances>pair.search_distance_m)]=np.inf
        distances=distances.reshape(len(x),count);best=np.argmin(distances,axis=1)
        for j in np.flatnonzero(np.isfinite(distances[np.arange(len(x)),best])):
            index=j*count+best[j];k=indices[index];M=weights[index];nn=normal[index];gap=float((x[j]-target[index])@nn)-pair.initial_offset_m
            records.append({'slave_nodes':sf[j].copy(),'master_nodes':mf[k].copy(),'slave_weights':N.copy(),'master_weights':M.copy(),
                'area_m2':float(areas[j]),'gap_m':gap,'normal':nn.copy(),'position_m':x[j].copy(),'slave_face':int(si[j]),'master_face':int(mi[k]),'quadrature_id':qi})
    if not records and not allow_empty:raise ValueError(pair.name+' 搜索距离内没有有效界面映射')
    return records


def conductance_matrix(nnodes,records,conductances):
    rows=[];cols=[];data=[]
    for row,h in zip(records,conductances):
        indices=np.r_[row['slave_nodes'],row['master_nodes']]
        b=np.r_[row['slave_weights'],-row['master_weights']]
        matrix=h*row['area_m2']*np.outer(b,b)
        rows.extend(np.repeat(indices,len(indices)));cols.extend(np.tile(indices,len(indices)));data.extend(matrix.ravel())
    return coo_matrix((data,(rows,cols)),shape=(nnodes,nnodes)).tocsr()


def thermal_conductances(pair,records,pressure=None,temperature=None):
    pressure=np.zeros(len(records)) if pressure is None else np.asarray(pressure)
    temperature=np.full(len(records),25.) if temperature is None else np.asarray(temperature)
    levels=sorted({v.temperature_C for v in pair.conductance},key=lambda v:0 if v is None else v)
    groups=[[v for v in pair.conductance if v.temperature_C==level] for level in levels]
    h=[]
    for row,p,T in zip(records,pressure,temperature):
        gap=row['gap_m']
        if gap<=pair.minimum_gap_m or p>0:
            values=[np.interp(max(p,0),[v.pressure_Pa for v in g],[v.conductance_W_m2K for v in g]) for g in groups]
            h.append(float(values[0] if levels==[None] else np.interp(T,levels,values)))
        elif gap<=pair.maximum_gap_m:h.append(pair.gap_conductivity_W_mK/max(gap,pair.minimum_gap_m))
        else:h.append(0.)
    return np.array(h)
