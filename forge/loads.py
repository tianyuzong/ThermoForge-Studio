"""Consistent forces and pure couples with explicit SI resultants."""
import numpy as np
from strength.fem import assemble_loads as original_loads,surface_weights
from strength.materials import selection_mask


def assemble_loads(mesh,case,evidence):
    force,audit=original_loads(mesh,case.model_copy(update={'loads':[l for l in case.loads if l.kind!='torque']}),evidence)
    for load in case.loads:
        if load.kind!='torque':continue
        mask=selection_mask(mesh,load.selection,evidence);nodes=np.unique(mesh['faces'][mask]);weights=np.zeros(len(mesh['points']))
        for faces,N,normal,norm,w in surface_weights(mesh,mask):np.add.at(weights,faces.ravel(),(norm[:,None]*w*N**2).ravel())
        r=mesh['points'][nodes]-load.center_m;B=np.zeros((6,len(nodes),3));B[:3]=np.eye(3)[:,None,:]
        for axis in range(3):B[3:,:,axis]=np.cross(r,np.eye(3)[axis]).T
        B=B.reshape(6,-1);weights=np.repeat(weights[nodes],3);target=np.r_[np.zeros(3),load.torque_Nm]
        matrix=(B*weights)@B.T
        if np.linalg.matrix_rank(matrix,tol=np.linalg.norm(matrix)*1e-12)<6:raise ValueError('扭矩选区不能形成完整力偶')
        nodal=weights*(B.T@np.linalg.solve(matrix,target));force[nodes]+=nodal.reshape(-1,3)
        actual=B@nodal
        if np.linalg.norm(actual-target)>max(np.linalg.norm(target),1.)*1e-9:raise ValueError('扭矩离散合力／力矩校验失败')
        audit.append({'name':load.name,'kind':'torque','force_N':actual[:3].tolist(),'moment_Nm':actual[3:].tolist(),'center_m':load.center_m})
    return force,audit
