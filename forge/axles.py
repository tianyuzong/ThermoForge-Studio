"""CalculiX rigid bore supports, including a freely rotating torque-driven shaft."""
import numpy as np
from strength.materials import selection_mask


def references(mesh,case):
    n=len(mesh['points'])
    return [(a,n+2*i+1,n+2*i+2,'xyz'.index(a.axis)) for i,a in enumerate(case.axles)]


def deck(mesh,case,evidence):
    lines=[];owned=set()
    for i,(a,ref,rot,axis) in enumerate(references(mesh,case)):
        nodes=np.unique(mesh['faces'][selection_mask(mesh,a.selection,evidence)])+1
        if owned.intersection(nodes):raise ValueError('刚性轴孔选区相互重叠')
        owned.update(nodes)
        lines += ['*NODE,NSET=AXLES_NODES',f'{ref},'+','.join(map(str,a.center_m)),f'{rot},0,0,0',f'*NSET,NSET=HUB{i}']
        lines += [','.join(map(str,nodes[j:j+16])) for j in range(0,len(nodes),16)]
        lines += [f'*RIGID BODY,NSET=HUB{i},REF NODE={ref},ROT NODE={rot}']
    return lines


def prescribed(mesh,case,index):
    result={}
    for a,ref,rot,axis in references(mesh,case):
        result.update({3*(ref-1)+j:0. for j in range(3)})
        for j in range(3):
            if j!=axis:result[3*(rot-1)+j]=0.
            elif a.angles_rad is not None:result[3*(rot-1)+j]=a.angles_rad[index or 0]
    return result


def annotate(mesh,case,frame):
    frame['axles']={}
    for a,ref,rot,axis in references(mesh,case):
        u=frame.get('axle_displacements',{});rf=frame.get('axle_forces',{})
        if ref not in u or rot not in u:raise ValueError('缺少刚性轴孔参考节点结果')
        frame['axles'][a.name]={'body_id':a.body_id,'center_m':a.center_m,'rotation_vector_rad':u[rot],
            'translation_m':u[ref],'force_N':rf[ref],'moment_Nm':rf[rot],
            'axis':axis,'torque_Nm':a.torque_Nm,'driven':a.angles_rad is not None}


def balance_terms(frame,origin):
    force=np.zeros(3);moment=np.zeros(3);scale=0.;moment_scale=0.
    for a in frame.get('axles',{}).values():
        f=np.asarray(a['force_N']);m=np.asarray(a['moment_Nm']);external=np.zeros(3);external[a['axis']]=a['torque_Nm']
        reaction=m.copy()
        if not a['driven']:reaction[a['axis']]=0.
        force+=f;moment+=np.cross(np.asarray(a['center_m'])+a['translation_m']-origin,f)+reaction+external
        scale+=np.linalg.norm(f);moment_scale+=np.linalg.norm(reaction)+np.linalg.norm(external)
    return force,moment,scale,moment_scale
