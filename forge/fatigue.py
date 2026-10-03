"""Fatigue postprocessing on unsmoothed material-point tensors (xx yy zz xy xz yz)."""
from collections import deque
import numpy as np
from strength.fem import stress_invariants


def turning_points(values):
    a=np.asarray(values,dtype=float)
    if a.ndim!=1 or len(a)<2 or not np.isfinite(a).all():raise ValueError('疲劳历史须为有限的一维序列')
    a=a[np.r_[True,np.diff(a)!=0]]
    if len(a)<3:return a
    return a[np.r_[True,np.diff(a)[:-1]*np.diff(a)[1:]<0,True]]


def rainflow(values,periodic=False):
    """Return (amplitude, mean, count); residual cycles have count 0.5.

    Periodic closed histories rotate at a global maximum, avoiding artificial
    half cycles at an arbitrary phase boundary. Counts cover one period.
    """
    a=turning_points(values)
    if periodic and len(a)>1:
        if not np.isclose(a[0],a[-1],rtol=1e-9,atol=1e-12):raise ValueError('周期应力历史未闭合')
        a=a[:-1];i=int(np.argmax(a));a=turning_points(np.r_[a[i:],a[:i],a[i]])
    stack=deque();out=[]
    for value in a:
        stack.append(float(value))
        while len(stack)>=3:
            older=abs(stack[-2]-stack[-3]);newer=abs(stack[-1]-stack[-2])
            if newer<older:break
            mean=(stack[-2]+stack[-3])/2
            if len(stack)==3:
                if older:out.append((older/2,mean,.5))
                stack.popleft()
            else:
                if older:out.append((older/2,mean,1.))
                last=stack.pop();stack.pop();stack.pop();stack.append(last)
    while len(stack)>1:
        x=stack.popleft();y=stack[0]
        if x!=y:out.append((abs(y-x)/2,(x+y)/2,.5))
    return out


def sn_life(amplitude,curve):
    if amplitude<=0 or (curve.endurance_limit_Pa is not None and amplitude<=curve.endurance_limit_Pa):return float('inf')
    n=np.array([p.x for p in curve.points]);s=np.array([p.y for p in curve.points])
    if amplitude>s[0]:raise ValueError('应力幅超过 S-N 曲线范围，不能外推到低周区')
    if amplitude<s[-1]:raise ValueError('应力幅低于曲线范围且没有覆盖该值的疲劳极限，不能外推寿命')
    return float(10**np.interp(np.log10(amplitude),np.log10(s[::-1]),np.log10(n[::-1])))


def goodman(amplitude,mean,ultimate):
    if ultimate is None or ultimate<=0:raise ValueError('Goodman 修正需要抗拉强度')
    mean=max(float(mean),0.)  # no uncalibrated benefit from compressive mean stress
    if mean>=ultimate:raise ValueError('平均拉应力达到抗拉强度，不能进行高周寿命计算')
    return float(amplitude/(1-mean/ultimate))


def signed_von_mises(stress):
    vm,p=stress_invariants(stress)
    dominant=np.where(np.abs(p[...,0])>np.abs(p[...,-1]),p[...,0],p[...,-1])
    return vm*np.where(dominant<0,-1.,1.)


def proportional(stress,tolerance=1e-5):
    a=np.asarray(stress).reshape(len(stress),-1)
    a=a-a.mean(0)
    singular=np.linalg.svd(a,compute_uv=False)
    return len(singular)<2 or singular[1]<=max(singular[0],1e-12)*tolerance


def sn_damage(stress,material,periodic=True):
    if material.sn_curve is None:raise ValueError('缺少 S-N 曲线')
    if material.sn_curve.reference_R!=-1:raise ValueError('Goodman 只接受 R=-1 基准曲线')
    if not proportional(stress):raise ValueError('非比例多轴载荷须使用周期 Findley 评估')
    vm,_=stress_invariants(stress)
    if material.yield_strength_Pa is None:raise ValueError('缺少屈服强度，无法确认高周弹性适用性')
    if np.max(vm)>material.yield_strength_Pa:raise ValueError('应力超过屈服强度，高周弹性模型不适用')
    history=signed_von_mises(stress);damage=0.;cycles=[]
    for amplitude,mean,count in rainflow(history,periodic):
        corrected=goodman(amplitude,mean,material.tensile_strength_Pa)
        life=sn_life(corrected,material.sn_curve)
        damage+=count/life
        cycles.append({'amplitude_Pa':amplitude,'mean_Pa':mean,'corrected_Pa':corrected,'count':count,'life_cycles':life if np.isfinite(life) else None})
    return damage,cycles


def planes(count):
    # Equal-area hemisphere; normals n and -n identify the same plane.
    i=np.arange(count);z=(i+.5)/count;phi=i*np.pi*(3-np.sqrt(5))
    return np.column_stack([np.sqrt(1-z*z)*np.cos(phi),np.sqrt(1-z*z)*np.sin(phi),z])


def enclosing_circle_radius(points):
    """Deterministic smallest enclosing circle of 2-D shear trajectories."""
    p=np.unique(np.asarray(points),axis=0);center=np.zeros(2);radius=-1.
    def outside(q):return np.linalg.norm(q-center)>radius+1e-9*max(radius,1.)
    for i,a in enumerate(p):
        if not outside(a):continue
        center=a.copy();radius=0.
        for j,b in enumerate(p[:i]):
            if not outside(b):continue
            center=(a+b)/2;radius=np.linalg.norm(a-b)/2
            for c in p[:j]:
                if not outside(c):continue
                M=2*np.array([b-a,c-a]);rhs=np.array([b@b-a@a,c@c-a@a])
                if abs(np.linalg.det(M))<1e-14*max(np.linalg.norm(M)**2,1e-30):
                    pairs=[(a,b),(a,c),(b,c)];u,v=max(pairs,key=lambda z:np.linalg.norm(z[0]-z[1]));center=(u+v)/2;radius=np.linalg.norm(u-v)/2
                else:center=np.linalg.solve(M,rhs);radius=np.linalg.norm(center-a)
    return max(float(radius),0.)


def tensor(stress):
    s=np.asarray(stress);T=np.zeros((*s.shape[:-1],3,3))
    T[...,0,0],T[...,1,1],T[...,2,2]=s[...,0],s[...,1],s[...,2]
    T[...,0,1]=T[...,1,0]=s[...,3];T[...,0,2]=T[...,2,0]=s[...,4];T[...,1,2]=T[...,2,1]=s[...,5]
    return T


def enclosing_circle_radii(paths):
    """Exact incremental smallest circles, vectorized over critical planes."""
    paths=np.asarray(paths,float);order=np.random.default_rng(731).permutation(paths.shape[1]);p=paths[:,order]
    centers=np.zeros((len(p),2));radii=np.full(len(p),-1.)
    def outside(ids,point):return np.linalg.norm(point-centers[ids],axis=1)>radii[ids]+1e-9*np.maximum(radii[ids],1.)
    for i in range(p.shape[1]):
        ids=np.flatnonzero(outside(np.arange(len(p)),p[:,i]))
        if not len(ids):continue
        centers[ids]=p[ids,i];radii[ids]=0.
        for j in range(i):
            jj=ids[outside(ids,p[ids,j])]
            if not len(jj):continue
            a=p[jj,i];b=p[jj,j];centers[jj]=(a+b)/2;radii[jj]=np.linalg.norm(a-b,axis=1)/2
            for k in range(j):
                kk=jj[outside(jj,p[jj,k])]
                if not len(kk):continue
                a=p[kk,i];b=p[kk,j];c=p[kk,k];d=b-a;e=c-a
                cross=d[:,0]*e[:,1]-d[:,1]*e[:,0];normal=np.abs(cross)>1e-14*np.maximum(np.sum(d*d+e*e,axis=1),1e-30)
                if normal.any():
                    dd=d[normal];ee=e[normal];aa=np.sum(dd*dd,1)/2;bb=np.sum(ee*ee,1)/2;cc=cross[normal]
                    center=a[normal]+np.column_stack([(aa*ee[:,1]-dd[:,1]*bb)/cc,(dd[:,0]*bb-aa*ee[:,0])/cc])
                    centers[kk[normal]]=center;radii[kk[normal]]=np.linalg.norm(center-a[normal],axis=1)
                if (~normal).any():
                    ia=kk[~normal];abc=np.stack([a[~normal],b[~normal],c[~normal]],axis=1)
                    left=abc[:,[0,0,1]];right=abc[:,[1,2,2]];dist=np.linalg.norm(left-right,axis=2);m=dist.argmax(1);row=np.arange(len(ia))
                    centers[ia]=(left[row,m]+right[row,m])/2;radii[ia]=dist[row,m]/2
    return np.maximum(radii,0.)


def findley(stress,material,plane_count=162,contact=False,skip_below=None):
    if material.findley is None:raise ValueError('缺少 Findley 标定参数')
    if contact and material.findley.calibration!='rolling_contact':raise ValueError('点蚀评估需要滚动接触标定数据')
    vm,_=stress_invariants(stress)
    if material.yield_strength_Pa is None or np.max(vm)>material.yield_strength_Pa:raise ValueError('无法确认接触／多轴应力处于弹性范围')
    if np.max(np.abs(np.asarray(stress)-stress[0]))<=1e-12*max(float(np.max(np.abs(stress))),1.):
        return {'parameter_Pa':0.,'normal':None,'life_cycles':None,'damage_per_cycle':0.}
    T=tensor(stress);best=(-float('inf'),None);normals=planes(plane_count)
    if skip_below is not None and material.findley.curve.endurance_limit_Pa is not None:
        # Every plane's shear circle fits inside the radius about its first
        # traction. Frobenius and Gershgorin bounds conservatively bound it.
        shear_bound=np.linalg.norm(T-T[0],axis=(1,2)).max()
        normal_bound=np.max(np.diagonal(T,axis1=1,axis2=2)+np.sum(np.abs(T),axis=2)-np.abs(np.diagonal(T,axis1=1,axis2=2)))
        bound=max(0.,float(shear_bound+material.findley.k*normal_bound))
        if bound<=min(skip_below,material.findley.curve.endurance_limit_Pa):
            return {'parameter_Pa':bound,'parameter_is_upper_bound':True,'normal':None,'life_cycles':None,'damage_per_cycle':0.}
    if proportional(stress):
        # A proportional tensor path projects to a straight shear segment on
        # every plane. Its enclosing-circle radius is half the segment length.
        centered=np.asarray(stress)-np.mean(stress,axis=0)
        u,s,_=np.linalg.svd(centered,full_matrices=False)
        a,b=int(np.argmin(u[:,0])),int(np.argmax(u[:,0]))
        traction=np.einsum('tij,pj->tpi',T,normals)
        normal=np.einsum('tpi,pi->tp',traction,normals)
        delta=traction[a]-traction[b]-(normal[a]-normal[b])[:,None]*normals
        values=np.maximum(0.,np.linalg.norm(delta,axis=1)/2+material.findley.k*normal.max(axis=0))
        i=int(np.argmax(values));best=(float(values[i]),normals[i])
    if best[1] is None:
        axes=np.where((np.abs(normals[:,2])<.9)[:,None],[0,0,1],[1,0,0]);u=np.cross(normals,axes);u/=np.linalg.norm(u,axis=1)[:,None];v=np.cross(normals,u)
        traction=np.einsum('tij,pj->pti',T,normals);normal=np.einsum('pti,pi->pt',traction,normals)
        shear=np.stack([np.einsum('pti,pi->pt',traction,u),np.einsum('pti,pi->pt',traction,v)],axis=-1)
        values=np.maximum(0.,enclosing_circle_radii(shear)+material.findley.k*normal.max(axis=1))
        i=int(values.argmax());best=(float(values[i]),normals[i])
    life=sn_life(best[0],material.findley.curve)
    return {'parameter_Pa':best[0],'normal':best[1].tolist(),'life_cycles':None if not np.isfinite(life) else life,'damage_per_cycle':0. if not np.isfinite(life) else 1/life}


def findley_parameters(histories,k,plane_count=162):
    """Exact same discrete planes/circles, batched over independent material points."""
    stress=np.asarray(histories);T=tensor(stress);normals=planes(plane_count)
    axes=np.where((np.abs(normals[:,2])<.9)[:,None],[0,0,1],[1,0,0]);u=np.cross(normals,axes);u/=np.linalg.norm(u,axis=1)[:,None];v=np.cross(normals,u)
    traction=np.einsum('btij,pj->bpti',T,normals);normal=np.einsum('bpti,pi->bpt',traction,normals)
    shear=np.stack([np.einsum('bpti,pi->bpt',traction,u),np.einsum('bpti,pi->bpt',traction,v)],axis=-1)
    radius=enclosing_circle_radii(shear.reshape(-1,stress.shape[1],2)).reshape(len(stress),plane_count)
    values=np.maximum(0.,radius+k*normal.max(axis=2));which=values.argmax(1)
    parameters=values[np.arange(len(stress)),which]
    constant=np.max(np.abs(stress-stress[:,:1]),axis=(1,2))<=1e-12*np.maximum(np.max(np.abs(stress),axis=(1,2)),1.)
    parameters[constant]=0
    return parameters,normals[which]


def archard_increment(pressure_Pa,sliding_distance_m,hardness_Pa,coefficient):
    p=np.asarray(pressure_Pa);s=np.asarray(sliding_distance_m)
    if hardness_Pa is None or hardness_Pa<=0 or coefficient is None or coefficient<0:raise ValueError('磨损计算需要硬度和非负磨损系数')
    if np.any(p<0) or np.any(s<0):raise ValueError('接触压力和滑移距离必须非负')
    return coefficient*p*s/hardness_Pa
