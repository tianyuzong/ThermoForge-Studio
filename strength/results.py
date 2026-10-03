import numpy as np, h5py
from .result_catalog import FIELDS,finite_json

# Linear sub-tetrahedra of C3D10 for visual slicing only; assessments use raw IPs.
SUBTETS=np.array([[0,4,6,7],[4,1,5,8],[6,5,2,9],[7,8,9,3],[4,6,7,8],[4,5,6,8],[6,7,8,9],[5,6,8,9]])
EDGES=[(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)]

def slice_field(folder,axis,position,field):
    if field not in FIELDS and field!='elastic_displacement': raise ValueError('此结果场不可作体内剖切（接触压力只定义在表面）')
    with h5py.File(folder/'fields.h5') as f:
        p=f['points'][:]; t=f['tets'][:]; key='display_'+field
        if key not in f: raise ValueError('未提供材料强度，无法显示安全系数')
        values=f[key][:]; displacement=f['displacements'][:]
    outp=[]; outv=[]; outu=[]; triangles=[]
    sub=t[:,SUBTETS].reshape(-1,4); pts=p[sub]; candidates=np.where((pts[:,:,axis].min(1)<=position)&(pts[:,:,axis].max(1)>=position))[0]
    for cell in sub[candidates]:
        q=p[cell]; d=q[:,axis]-position; found=[]
        for a,b in EDGES:
            if d[a]*d[b]>0 or abs(d[a]-d[b])<1e-16: continue
            alpha=d[a]/(d[a]-d[b]); point=q[a]+alpha*(q[b]-q[a])
            if not any(np.linalg.norm(point-v[0])<1e-12 for v in found): found.append((point,values[cell[a]]*(1-alpha)+values[cell[b]]*alpha,displacement[cell[a]]*(1-alpha)+displacement[cell[b]]*alpha))
        if len(found)<3: continue
        coord=np.array([v[0] for v in found]); center=coord.mean(0); other=[a for a in range(3) if a!=axis]; angle=np.arctan2(coord[:,other[1]]-center[other[1]],coord[:,other[0]]-center[other[0]])
        order=np.argsort(angle); start=len(outp)
        for i in order: outp.append(found[i][0].tolist()); outv.append(float(found[i][1])); outu.append(found[i][2].tolist())
        for i in range(1,len(found)-1): triangles.append([start,start+i,start+i+1])
    return finite_json({'points':outp,'triangles':triangles,'fields':{field:outv},'displacements':outu,'note':'二次单元线性细分切片，仅用于显示；强度评估使用原始积分点结果'})
