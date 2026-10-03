"""Contact field recovery and material-frame histories."""
from collections import defaultdict
import numpy as np
from scipy.spatial.transform import Rotation
from strength.fem import tet_shape
from .mechanics import FACE_CORNERS
from .fatigue import tensor,archard_increment
from .schemas import material_for


def face_keys(mesh):
    keys=[]
    for face,e in zip(mesh['faces'],mesh['owners']):
        tet=mesh['tets'][e];key=set(face[:3])
        local=next(i+1 for i,c in enumerate(FACE_CORNERS) if set(tet[list(c)])==key)
        keys.append((int(e)+1,local))
    return keys


def recover_contact(mesh,frame,maps,case,deformed=False):
    values=defaultdict(list)
    for e,face,p,t1,t2 in frame.get('contact_stresses',[]):values[(int(e),int(face))].append((p,t1,t2))
    averaged={key:np.mean(rows,axis=0) for key,rows in values.items()}
    keys=face_keys(mesh);result={};u=frame['displacements']
    for pair in case.contacts:
        records=maps[pair.name];pressures=[];gaps=[];relative=[];shear=[];vectors=[]
        for r in records:
            traction=averaged.get(keys[r['slave_face']],np.zeros(3))
            delta=r['slave_weights']@u[r['slave_nodes']]-r['master_weights']@u[r['master_nodes']]
            pressures.append(max(float(traction[0]),0.));gaps.append(r['gap_m'] if deformed else r['gap_m']+float(delta@r['normal']))
            relative.append(delta);shear.append(float(np.linalg.norm(traction[1:])))
            n=r['normal'];axis=np.array([0.,0.,1.]) if 1-abs(n[0])<1.5231e-6 else np.array([1.,0.,0.])
            t1=axis-n*(axis@n);t1/=np.linalg.norm(t1);t2=np.cross(n,t1)
            vectors.append(traction[1]*t1+traction[2]*t2)
        result[pair.name]={'pressure_Pa':np.array(pressures),'gap_m':np.array(gaps),'relative_m':np.array(relative),'shear_Pa':np.array(shear),
            'slave_face':np.array([r['slave_face'] for r in records],int),'master_face':np.array([r['master_face'] for r in records],int),
            'quadrature_id':np.array([r['quadrature_id'] for r in records],int),'position_m':np.array([r['position_m'] for r in records]),
            'area_m2':np.array([r['area_m2'] for r in records]),
            'shear_vector_Pa':np.asarray(vectors),
            'projection':'native contact quadrature -> slave-face mean -> thermal interface quadrature'}
    return result


def corotate_stresses(stress,mesh,case,index,frame=None):
    result=np.asarray(stress).copy()
    for motion in case.motions:
        mask=mesh['body_ids']==motion.body_id
        axis=np.asarray(motion.axis,float);axis/=np.linalg.norm(axis)
        R=Rotation.from_rotvec(axis*motion.angles_rad[index]).as_matrix()
        T=tensor(stress[mask]);local=np.einsum('ai,...ab,bj->...ij',R,T,R)
        result[mask]=np.stack([local[...,0,0],local[...,1,1],local[...,2,2],local[...,0,1],local[...,0,2],local[...,1,2]],axis=-1)
    for axle in (frame or {}).get('axles',{}).values():
        mask=mesh['body_ids']==axle['body_id'];R=Rotation.from_rotvec(axle['rotation_vector_rad']).as_matrix()
        local=np.einsum('ai,...ab,bj->...ij',R,tensor(stress[mask]),R)
        result[mask]=np.stack([local[...,0,0],local[...,1,1],local[...,2,2],local[...,0,1],local[...,0,2],local[...,1,2]],axis=-1)
    return result


def wear_history(mesh,case,frames,evidence,history_out=None):
    """Integrate relative *material velocities* at the CURRENT contact location.

    Differencing an old slave/master displacement pair gives false slip during
    rolling. Re-project each midpoint and separate prescribed rigid motion from
    elastic deformation before integrating the relative tangential velocity.
    """
    from .interfaces import interface_map
    out={};times=case.history.times_s
    for pair in case.contacts:
        if not pair.wear:continue
        material=material_for(case,pair.slave.body_id)
        if material.hardness_Pa is None or material.wear_coefficient is None:raise ValueError('磨损需要接触材料硬度及经标定的磨损系数')
        depth=np.zeros(len(mesh['points']));distance=np.zeros_like(depth);offset=np.zeros_like(mesh['points'])
        history=[depth.copy()]
        for i,(a,b) in enumerate(zip(frames,frames[1:])):
            dt=times[i+1]-times[i];mid,velocity=midpoint_kinematics(mesh,case,a['displacements'],b['displacements'],i,dt,a,b)
            records=interface_map(mesh,pair,evidence,mid,allow_empty=True)
            maps={pair.name:records};subcase=case.model_copy(update={'contacts':[pair]})
            aa=recover_contact(mesh,a,maps,subcase,True)[pair.name];bb=recover_contact(mesh,b,maps,subcase,True)[pair.name]
            step=np.zeros_like(depth);slip=np.zeros_like(depth);weights=np.zeros_like(depth);normal_sum=np.zeros_like(offset)
            for j,r in enumerate(records):
                relative=r['slave_weights']@velocity[r['slave_nodes']]-r['master_weights']@velocity[r['master_nodes']]
                tangential=relative-r['normal']*(relative@r['normal'])
                pressure=(aa['pressure_Pa'][j]+bb['pressure_Pa'][j])/2
                motion=tangential*dt
                sliding=irreversible_slip(motion,aa['shear_vector_Pa'][j],bb['shear_vector_Pa'][j],aa['pressure_Pa'][j],bb['pressure_Pa'][j],pair)
                increment=archard_increment(pressure,sliding,material.hardness_Pa,material.wear_coefficient)
                ids=r['slave_nodes'];w=r['slave_weights']**2*r['area_m2']
                np.add.at(step,ids,w*increment);np.add.at(slip,ids,w*sliding);np.add.at(weights,ids,w)
                np.add.at(normal_sum,ids,w[:,None]*r['normal'])
            active=weights>0;step[active]/=weights[active];slip[active]/=weights[active]
            norms=np.linalg.norm(normal_sum,axis=1);normal_sum[norms>0]/=norms[norms>0,None]
            depth+=step;distance+=slip;offset+=step[:,None]*normal_sum
            if history_out is not None:history.append(depth.copy())
        depth*=case.cycles_per_wear_block;offset*=case.cycles_per_wear_block;distance*=case.cycles_per_wear_block
        out[pair.name]={'depth_m':depth,'sliding_distance_m':distance,'surface_offset_m':offset,'cycles':case.cycles_per_wear_block}
        if history_out is not None:history_out[pair.name]=np.asarray(history)*case.cycles_per_wear_block
    return out


def irreversible_slip(relative,tau_a,tau_b,pressure_a,pressure_b,pair):
    """Exclude reversible penalty-spring displacement from Archard sliding."""
    magnitude=float(np.linalg.norm(relative))
    if magnitude<1e-16 or max(pressure_a,pressure_b)<=0:return 0.
    if pair.friction<=0:return magnitude
    active=max(np.linalg.norm(tau_a)/max(pair.friction*pressure_a,1e-30),np.linalg.norm(tau_b)/max(pair.friction*pressure_b,1e-30))
    if active<1-1e-4:return 0.
    irreversible=relative-(np.asarray(tau_b)-tau_a)/pair.tangential_stiffness_Pa_m
    # A resolved increment must not report reverse sliding on elastic unloading.
    if irreversible@relative<=0:return 0.
    return float(np.linalg.norm(irreversible))


def midpoint_kinematics(mesh,case,ua,ub,index,dt,frame_a=None,frame_b=None):
    mid=(ua+ub)/2;velocity=(ub-ua)/dt;p=mesh['points']
    for motion in case.motions:
        ids=np.unique(mesh['tets'][mesh['body_ids']==motion.body_id]);x=p[ids];center=np.asarray(motion.center_m)
        axis=np.asarray(motion.axis,float);axis/=np.linalg.norm(axis)
        angle0,angle1=motion.angles_rad[index:index+2]
        t0,t1=(np.asarray(motion.translations_m[index:index+2]) if motion.translations_m else np.zeros((2,3)))
        def rigid(angle,t):return (x-center)@Rotation.from_rotvec(axis*angle).as_matrix().T+center+t-x
        ra,rb=rigid(angle0,t0),rigid(angle1,t1);rm=rigid((angle0+angle1)/2,(t0+t1)/2)
        mid[ids]=rm+(ua[ids]-ra+ub[ids]-rb)/2
        spatial=x+rm-center-(t0+t1)/2
        velocity[ids]=np.cross(axis*(angle1-angle0)/dt,spatial)+(t1-t0)/dt+(ub[ids]-rb-ua[ids]+ra)/dt
    for name,a in (frame_a or {}).get('axles',{}).items():
        b=(frame_b or {}).get('axles',{}).get(name)
        if b is None:raise ValueError('轴运动历史不完整，不能计算实际滑移')
        ids=np.unique(mesh['tets'][mesh['body_ids']==a['body_id']]);x=p[ids];center=np.asarray(a['center_m'])
        r0,r1=np.asarray(a['rotation_vector_rad']),np.asarray(b['rotation_vector_rad'])
        t0,t1=np.asarray(a['translation_m']),np.asarray(b['translation_m'])
        def rigid(rv,t):return (x-center)@Rotation.from_rotvec(rv).as_matrix().T+center+t-x
        ra,rb=rigid(r0,t0),rigid(r1,t1);rm=rigid((r0+r1)/2,(t0+t1)/2)
        mid[ids]=rm+(ua[ids]-ra+ub[ids]-rb)/2
        spatial=x+rm-center-(t0+t1)/2
        velocity[ids]=np.cross((r1-r0)/dt,spatial)+(t1-t0)/dt+(ub[ids]-rb-ua[ids]+ra)/dt
    return mid,velocity
