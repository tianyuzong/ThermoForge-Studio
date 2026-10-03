"""Surface recession with harmonic volume-mesh displacement, preserving node IDs."""
import numpy as np
from scipy.sparse import coo_matrix,diags
from scipy.sparse.linalg import spsolve
from strength.fem import tet_shape,tetra_quadrature


def recede(mesh,wear):
    offset=sum((w['surface_offset_m'] for w in wear.values()),np.zeros_like(mesh['points']))
    if not np.any(offset):return mesh
    size=float(mesh['size_m'])
    if np.linalg.norm(offset,axis=1).max()>size*.05:
        raise ValueError('单磨损块退蚀超过网格尺度 5%；请减小每块循环数，不能跳过表面更新')
    boundary=np.unique(mesh['faces']);free=np.setdiff1d(np.arange(len(offset)),boundary)
    edges=np.concatenate([mesh['tets'][:,[i,j]] for i in range(10) for j in range(i)])
    edges=np.unique(np.sort(edges,axis=1),axis=0);weights=1/np.maximum(np.linalg.norm(mesh['points'][edges[:,0]]-mesh['points'][edges[:,1]],axis=1),1e-15)
    A=coo_matrix((np.r_[weights,weights],(np.r_[edges[:,0],edges[:,1]],np.r_[edges[:,1],edges[:,0]])),shape=(len(offset),len(offset))).tocsr()
    L=diags(np.asarray(A.sum(1)).ravel())-A
    if len(free):offset[free]=spsolve(L[free][:,free].tocsc(),-L[free][:,boundary]@offset[boundary])
    result={**mesh,'points':mesh['points']+offset};P=result['points'][mesh['tets']]
    for q,_ in tetra_quadrature(3):
        _,d=tet_shape(q)
        if np.any(np.linalg.det(np.einsum('eic,ir->ecr',P,d))<=0):raise ValueError('磨损更新导致非正单元 Jacobian；需重新划分网格')
    return result
