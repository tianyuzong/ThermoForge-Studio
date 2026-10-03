"""Explicit CPU ideal-bond MPCs between independent C3D10 bodies.

Each accepted slave point uses the *volume* interpolation of the adjacent master
element. Tiny permitted offsets therefore reproduce affine fields (translations
and rotations), conserving force AND moment without moving the mesh. This is a
kinematic bonded extension, not unilateral contact, a bearing or a bolt model.
"""
import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from trimesh.triangles import closest_point
from .geometry import resolve_selection
from .fem import tet_shape
from .materials import bodies, selection_mask


def validate_graph(case,evidence):
    ids={c['id'] for c in evidence['components']};parents={i:set() for i in ids}
    for pair in case.bonded_connections:
        a,b=pair.slave.body_id,pair.master.body_id
        if a not in ids or b not in ids:raise ValueError('粘结连接引用未知组件')
        resolve_selection(pair.slave,evidence);resolve_selection(pair.master,evidence)
        parents[a].add(b)
    order=[];pending=set(ids)
    while pending:
        ready=sorted(i for i in pending if not (parents[i]&pending))
        if not ready:raise ValueError('粘结主从关系存在环；请按传力层级重新指定主从面')
        order+=ready;pending.difference_update(ready)
    return order


def interpolation(P,target):
    """Inverse quadratic map with bounded extrapolation and affine patch check."""
    try:
        q=np.linalg.solve((P[1:4]-P[0]).T,target-P[0])
        for _ in range(15):
            N,d=tet_shape(q);r=N@P-target
            if np.linalg.norm(r)<1e-12:break
            q-=np.linalg.solve(P.T@d,r)
        N,_=tet_shape(q);bary=np.r_[1-q.sum(),q]
        if np.linalg.norm(N@P-target)>1e-10 or bary.min()<-.2 or bary.max()>1.2 or np.abs(N).sum()>4:return None
        if abs(N.sum()-1)>1e-10:return None
        return N
    except np.linalg.LinAlgError:return None


def prepare_bonds(mesh,case,evidence,prescribed,check_anchoring=True):
    order=validate_graph(case,evidence);rank={b:i for i,b in enumerate(order)}
    p=mesh['points'];t=mesh['tets'];body=bodies(mesh)
    scale=max(np.ptp(p,axis=0));chosen={};audit=[]
    subdivision=np.array([[0,3,5],[3,1,4],[5,4,2],[3,4,5]])
    locked={d//3 for d in prescribed}
    for pi,pair in enumerate(case.bonded_connections):
        sm=selection_mask(mesh,pair.slave,evidence);mm=selection_mask(mesh,pair.master,evidence)
        nodes=np.unique(mesh['faces'][sm]);faces=mesh['faces'][mm];owners=np.repeat(mesh['owners'][mm],4)
        if not len(nodes) or not len(faces):raise ValueError(pair.name+'：连接面未映射到网格')
        tri=p[faces[:,subdivision].reshape(-1,3)];centers=tri.mean(1)
        radius=np.linalg.norm(tri-centers[:,None,:],axis=2).max()
        tree=cKDTree(centers);candidates=tree.query_ball_point(p[nodes],radius+pair.max_gap_m)
        eligible=0;best_distance=[]
        for node,ci in zip(nodes,candidates):
            if not ci:continue
            ci=np.asarray(ci);cp=closest_point(tri[ci],np.broadcast_to(p[node],(len(ci),3)))
            dist=np.linalg.norm(cp-p[node],axis=1);idx=np.argsort(dist)
            for k in idx:
                gap=float(dist[k])
                if gap>pair.max_gap_m:break
                elem=int(owners[ci[k]]);weights=interpolation(p[t[elem]],p[node])
                if weights is None:continue
                if node in locked:raise ValueError(pair.name+'：从面节点同时存在固定自由度；请交换主从面或缩小连接选区')
                eligible+=1;best_distance.append(gap)
                # At a shared slave edge choose the closest eligible master;
                # one node must never be eliminated by two different MPCs.
                if node not in chosen or gap<chosen[node]['gap']:
                    chosen[int(node)]={'slave':int(node),'master':t[elem].copy(),'weights':weights,
                        'pair':pi,'gap':gap,'body':pair.slave.body_id,'master_body':pair.master.body_id}
                break
        if not eligible:raise ValueError(pair.name+'：指定容限内未找到可用连接；未跨间隙自动吸附')
        audit.append({'name':pair.name,'slave_body':pair.slave.body_id,'master_body':pair.master.body_id,
            'slave_face_ids':resolve_selection(pair.slave,evidence),'master_face_ids':resolve_selection(pair.master,evidence),
            'candidate_slave_nodes':len(nodes),'eligible_nodes':eligible,'max_gap_m':pair.max_gap_m,
            'maximum_mapped_distance_m':max(best_distance),'assumptions':pair.assumptions})
    links=sorted(chosen.values(),key=lambda a:(rank[a['body']],a['slave']))
    for i,row in enumerate(audit):
        row['tied_nodes']=sum(a['pair']==i for a in links)
        row['shared_nodes_assigned_to_closest_interface']=row['eligible_nodes']-row['tied_nodes']
        if not row['tied_nodes']:raise ValueError(row['name']+'：从面节点均已分配到其他更近的接口，连接没有独立映射')
    if check_anchoring:validate_anchoring(mesh,prescribed,links)
    return {'links':links,'interfaces':audit,'order':order}


def validate_anchoring(mesh,prescribed,links):
    """Every disconnected solid must be grounded through actual mapped MPCs."""
    p=mesh['points'];t=mesh['tets'];a=np.repeat(t[:,0],9);b=t[:,1:].ravel()
    graph=coo_matrix((np.ones(len(a)*2),(np.r_[a,b],np.r_[b,a])),shape=(len(p),len(p))).tocsr()
    count,labels=connected_components(graph,directed=False)
    nodes_by=[np.flatnonzero(labels==i) for i in range(count)]
    parent_labels={link['slave']:set(labels[link['master'][np.abs(link['weights'])>1e-12]]) for link in links}
    grounded=set()
    for _ in range(count+1):
        advanced=False
        for ci,nodes in enumerate(nodes_by):
            if ci in grounded:continue
            coords=p[nodes];scale=max(np.ptp(coords,axis=0));r=(coords-coords.mean(0))/scale
            rigid=np.zeros((len(nodes),3,6));rigid[:,:,:3]=np.eye(3)
            for axis in range(3):rigid[:,:,3+axis]=np.cross(np.eye(3)[axis],r)
            rows=[]
            for ni,node in enumerate(nodes):
                tied=node in parent_labels and parent_labels[node]<=grounded
                rows.extend(rigid[ni,k] for k in range(3) if node*3+k in prescribed or tied)
            if rows and np.linalg.matrix_rank(np.asarray(rows),tol=1e-8)==6:
                grounded.add(ci);advanced=True
        if not advanced:break
    if len(grounded)!=count:
        unresolved=sorted(set(int(bodies(mesh)[i]) for i,e in enumerate(t) if labels[e[0]] not in grounded))
        raise ValueError('粘结装配欠约束：以下组件未通过有效接口连接到完整支撑：'+','.join(map(str,unresolved)))


def equation_lines(bonding):
    lines=[]
    for link in bonding['links']:
        keep=np.abs(link['weights'])>1e-12
        for axis in range(1,4):
            terms=[(link['slave']+1,axis,1.)]+[(int(n)+1,axis,-float(w)) for n,w in zip(link['master'][keep],link['weights'][keep])]
            lines+=['*EQUATION',str(len(terms))]
            # CalculiX accepts at most four node/DOF/coefficient triplets per line.
            for start in range(0,len(terms),4):
                lines.append(','.join(f'{n},{a},{w:.12E}' for n,a,w in terms[start:start+4]))
    return lines


def recover_bonds(mesh,data,force,prescribed,bonding):
    r=data['forces']-force;projected=r.copy();p=mesh['points'];u=data['displacements']
    max_error=0.;transfers=[np.zeros(3) for _ in bonding['interfaces']]
    moments=[np.zeros(3) for _ in bonding['interfaces']]
    for link in reversed(bonding['links']):
        s=link['slave'];m=link['master'];w=link['weights'];f=projected[s].copy()
        transfers[link['pair']]+=f;moments[link['pair']]+=np.cross(p[s],f)
        np.add.at(projected,m,w[:,None]*f);projected[s]=0
        max_error=max(max_error,float(np.linalg.norm(u[s]-w@u[m])))
    data['reduced_residual']=projected
    reactions=np.zeros_like(force);dofs=np.array(sorted(prescribed),int);reactions.ravel()[dofs]=projected.ravel()[dofs]
    data['reactions']=reactions
    for i,row in enumerate(bonding['interfaces']):
        row.update(transfer_to_slave_N=transfers[i].tolist(),transfer_moment_about_origin_Nm=moments[i].tolist(),moment_origin_m=[0,0,0])
    relative=max_error/max(np.linalg.norm(u,axis=1).max(),1e-12)
    if relative>1e-5:raise ValueError(f'粘结位移兼容检查失败：相对误差 {relative:.3e}')
    return {'type':'linear_bonded_mpc','connected_components':len(np.unique(bodies(mesh))),
        'tied_nodes':len(bonding['links']),'equations':3*len(bonding['links']),
        'compatibility_relative':relative,'maximum_compatibility_error_m':max_error,
        'interfaces':bonding['interfaces'],'scope':'停机理想粘结；不允许分离、滑移或相对转动；容许微小间隙采用邻近主单元的仿射一致延拓，未移动节点；不代表真实轴承/螺栓/摩擦接触。'}
