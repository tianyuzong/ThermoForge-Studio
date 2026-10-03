"""Single source of truth for scalar fields. All numerical values use SI units."""
import numpy as np
from .fem import stress_invariants

FIELDS = {}

def register(key, label, group, unit='', display_unit='', factor=1., location='integration_point', note=''):
    FIELDS[key] = dict(key=key,label=label,group=group,unit=unit,display_unit=display_unit or unit,
        factor=factor,location=location,note=note,
        storage={'hdf5':('display_' if location=='node' else 'ip_')+key,'vtu_point':key,'csv':'field-statistics.csv'},
        applicability='三维实体、各向同性、线弹性、小变形静力；应力强度统计使用原始积分点')

for key,label in [('displacement','总位移'),('displacement_x','X 位移'),('displacement_y','Y 位移'),('displacement_z','Z 位移')]:
    register(key,label,'位移','m','mm',1000.,'node')
for key,label in [('stress','米塞斯等效应力'),('principal_max','最大主应力'),('principal_mid','中间主应力'),
    ('principal_min','最小主应力'),('maximum_shear','最大剪应力'),('stress_intensity','应力强度'),('hydrostatic','平均正应力')]:
    register(key,label,'应力','Pa','MPa',1e-6)
for axis in ('xx','yy','zz','xy','xz','yz'):
    register('stress_'+axis,axis.upper()+' 应力','应力','Pa','MPa',1e-6)
    register('strain_'+axis,axis.upper()+' 张量弹性应变','弹性应变','','%',100.)
for axis in ('xy','xz','yz'):
    register('engineering_shear_'+axis,axis.upper()+' 工程剪应变','弹性应变','','%',100.,note='gamma = 2 epsilon')
for key,label in [('strain','最大绝对法向应变（旧字段）'),('equivalent_elastic_strain','等效弹性应变'),
    ('principal_strain_max','最大主弹性应变'),('principal_strain_mid','中间主弹性应变'),
    ('principal_strain_min','最小主弹性应变'),('maximum_shear_strain','最大工程剪应变')]:
    register(key,label,'弹性应变','','%',100.)
register('triaxiality','应力三轴度','应力',note='平均正应力 / 米塞斯等效应力；分母近零时无定义')
register('energy_density','弹性应变能密度','能量','J/m³')
for key,label in [('safety_factor','所选判据安全系数'),('safety_factor_von_mises','米塞斯安全系数'),
    ('safety_factor_tresca','特雷斯卡安全系数'),('stress_ratio','应力比'),('safety_margin','安全裕度（相对目标）')]:
    register(key,label,'强度',note='缺少屈服强度时未评估；零应力安全系数无定义')

METRICS = [
    {'key':'strain_energy_J','label':'总弹性应变能','unit':'J','location':'volume_integral','storage':'summary.json / fields.h5 attribute / metrics.csv','definition':'0.5 sigma:epsilon；张量剪切项计两次'},
    {'key':'reaction_N','label':'支撑反力','unit':'N','components':['x','y','z'],'location':'constrained_dof','storage':'fields.h5:reaction / summary.supports','definition':'约束自由度去重；支持指定组件或面节点'},
    {'key':'moment_Nm','label':'支撑反力矩','unit':'N*m','components':['x','y','z'],'location':'reference_point','storage':'summary.supports / result-queries.json','definition':'sum((x-origin) cross reaction)，同时保存 moment_origin_m'},
    {'key':'stiffness_N_m','label':'指定载荷位移对等效刚度','unit':'N/m','location':'load_probe_pair','storage':'result-queries.json','definition':'载荷投影 / 位移投影；含该工况全部载荷影响；近零时无定义'},
    {'key':'compliance_m_N','label':'柔度','unit':'m/N','location':'load_probe_pair','storage':'result-queries.json','definition':'位移投影 / 载荷投影；近零时无定义'},
    {'key':'relative_displacement_m','label':'两点相对位移','unit':'m','components':['x','y','z'],'location':'two_points','storage':'result-queries.json','definition':'终点位移减去起点位移；C3D10 插值'},
    {'key':'deformed_position_m','label':'变形后坐标','unit':'m','components':['x','y','z'],'location':'probe','storage':'result-queries.json','definition':'原始坐标加真实位移（放大系数为一）'},
]

def finite_json(value):
    if isinstance(value,dict): return {str(k):finite_json(v) for k,v in value.items()}
    if isinstance(value,np.ndarray) and value.ndim==0:return finite_json(value.item())
    if isinstance(value,(list,tuple,np.ndarray)): return [finite_json(v) for v in value]
    if isinstance(value,(float,np.floating)): return float(value) if np.isfinite(value) else None
    if isinstance(value,np.integer): return int(value)
    if isinstance(value,np.bool_): return bool(value)
    return value

def derive(stress, strain, young, poisson, strength, criterion='von_mises', target=1.5):
    """strain contains tensor shear, ordered xx yy zz xy xz yz."""
    s,e=np.asarray(stress),np.asarray(strain)
    vm,sp=stress_invariants(s); _,ep=stress_invariants(e)
    intensity=sp[...,-1]-sp[...,0]
    result={'stress':vm,'principal_max':sp[...,-1],'principal_mid':sp[...,1],'principal_min':sp[...,0],
        'maximum_shear':intensity/2,'stress_intensity':intensity,'hydrostatic':s[...,:3].mean(-1),
        'strain':np.abs(e[...,:3]).max(-1),'principal_strain_max':ep[...,-1],
        'principal_strain_mid':ep[...,1],'principal_strain_min':ep[...,0],
        'maximum_shear_strain':ep[...,-1]-ep[...,0],
        'equivalent_elastic_strain':np.sqrt(((ep[...,2]-ep[...,1])**2+(ep[...,1]-ep[...,0])**2+(ep[...,0]-ep[...,2])**2)/2)/(1+np.asarray(poisson)),
        'energy_density':.5*((s[...,:3]*e[...,:3]).sum(-1)+2*(s[...,3:]*e[...,3:]).sum(-1))}
    for i,axis in enumerate(('xx','yy','zz','xy','xz','yz')):
        result['stress_'+axis]=s[...,i];result['strain_'+axis]=e[...,i]
        if i>=3:result['engineering_shear_'+axis]=2*e[...,i]
    result['triaxiality']=np.divide(result['hydrostatic'],vm,out=np.full_like(vm,np.nan),where=vm>1e-12)
    sy=np.broadcast_to(np.asarray(strength),vm.shape)
    for key,measure in [('safety_factor_von_mises',vm),('safety_factor_tresca',intensity)]:
        result[key]=np.divide(sy,measure,out=np.full_like(vm,np.nan),where=(measure>1e-12)&np.isfinite(sy))
    chosen=vm if criterion=='von_mises' else intensity
    result['safety_factor']=result['safety_factor_'+criterion]
    result['stress_ratio']=np.divide(chosen,sy,out=np.full_like(vm,np.nan),where=np.isfinite(sy)&(sy>0))
    result['safety_margin']=result['safety_factor']/target-1
    return result

def displacement_fields(u):
    return {'displacement':np.linalg.norm(u,axis=-1),**{'displacement_'+a:u[...,i] for i,a in enumerate('xyz')}}
