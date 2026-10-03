"""Versioned raw archives and separate, explicitly projected display fields."""
import csv,json,hashlib
from pathlib import Path
import h5py,numpy as np
from strength.fem import stress_invariants,nodal_stress_for_display,tet_shape,IP_BARY
from strength.result_catalog import finite_json
from strength.store import write_json

CATALOG={
    'temperature':('温度','°C',1),'temperature_gradient':('温度梯度','K/m',1),
    'heat_flux':('热流密度','W/m²',1),'stress':('等效应力','MPa',1e-6),
    'displacement':('总位移','mm',1000),'fatigue_life':('疲劳寿命','次',1),
    'fatigue_damage':('疲劳损伤','',1),'fatigue_accumulated_damage':('已模拟磨损块累积损伤','',1),'contact_pressure':('接触压力','MPa',1e-6),
    'contact_gap':('接触间隙','mm',1000),'wear_depth':('磨损深度','μm',1e6)}


def nodal_scalar(mesh,values):
    values=np.asarray(values)
    if values.ndim==2:values=values.mean(axis=1)
    t=mesh['tets'];out=np.zeros(len(mesh['points']));weights=np.zeros(len(out))
    valid=np.isfinite(values)
    np.add.at(out,t[valid].ravel(),np.repeat(values[valid],10));np.add.at(weights,t[valid].ravel(),1)
    return np.divide(out,weights,out=np.full_like(out,np.nan),where=weights>0)


def display(mesh,fields,displacement=None):
    p=mesh['points'];faces=mesh['faces'];split=np.array([[0,3,5],[3,1,4],[5,4,2],[3,4,5]])
    tri=faces[:,split].reshape(-1,3);pp=p[tri];norm=np.cross(pp[:,1]-pp[:,0],pp[:,2]-pp[:,0])
    owners=np.repeat(mesh['owners'],4)
    flip=np.sum(norm*(pp.mean(1)-p[mesh['tets'][owners,:4]].mean(1)),axis=1)<0;tri[flip]=tri[flip][:,[0,2,1]]
    used=np.unique(tri);lut=np.full(len(p),-1,int);lut[used]=np.arange(len(used))
    return finite_json({'points':p[used].tolist(),'triangles':lut[tri].tolist(),'surface_ids':np.repeat(mesh['surface_ids'],4).tolist(),
        'node_ids':(used+1).tolist(),'displacements':(displacement[used] if displacement is not None else np.zeros((len(used),3))).tolist(),
        'fields':{k:np.asarray(v)[used].tolist() for k,v in fields.items()},
        'field_catalog':[{'key':k,'label':CATALOG[k][0],'unit':CATALOG[k][1],'factor':CATALOG[k][2]} for k in fields]})


def write_archive(folder,mesh,times,temperatures,structures,gradients,fluxes,fatigue=None,contacts=None,wear=None,probes=None,case=None,wear_frames=None):
    def series(archive,name,values,extract=lambda value:value):
        # Journal-backed sequences are deliberately never coerced to one ndarray.
        first=np.asarray(extract(values[0]));dataset=archive.create_dataset(name,shape=(len(values),*first.shape),dtype=first.dtype,compression='gzip')
        dataset[0]=first
        for index in range(1,len(values)):dataset[index]=extract(values[index])
    with h5py.File(folder/'fields.h5','w') as f:
        f.attrs.update(result_schema_version=4,units='m,N,Pa,s,C',stress_order='xx,yy,zz,xy,xz,yz',stress_location='integration_point',strain_shear='tensor')
        for key in ('points','tets','faces','surface_ids','owners','body_ids'):f.create_dataset(key,data=mesh[key],compression='gzip')
        f.create_dataset('times_s',data=times)
        if case and case.history:
            count=len(case.history.times_s);axis=f.create_group('history_axes')
            axis.attrs.update(history_repetitions=case.history.repetitions,cycles_per_history=case.history.cycles_per_history,stabilization_cycles=case.history.stabilization_cycles)
            axis.create_dataset('sample_index',data=np.arange(len(times))%count)
            if case.analysis_type in ('contact_cycle','contact_fatigue'):
                phase=(np.asarray(case.history.times_s)-case.history.times_s[0])/(case.history.times_s[-1]-case.history.times_s[0])
                blocks=np.arange(len(times))//count;axis.create_dataset('wear_block_index',data=blocks)
                axis.create_dataset('cycle_count',data=blocks*case.cycles_per_wear_block+phase[np.arange(len(times))%count])
                axis.attrs['cycles_represented_per_wear_block']=case.cycles_per_wear_block
            motion=axis.create_group('motion')
            for i,m in enumerate(case.motions):
                g=motion.create_group(str(i));g.attrs.update(name=m.name,body_id=m.body_id,frame='prescribed rigid rotation, radians')
                g.create_dataset('angle_rad',data=np.asarray(m.angles_rad)[np.arange(len(times))%count])
        if temperatures:
            series(f,'temperature_C',temperatures)
            series(f,'temperature_gradient_K_m',gradients)
            series(f,'heat_flux_W_m2',fluxes)
        if structures:
            for key in ('displacements','stresses','strains','forces'):series(f,key,structures,lambda state:state[key])
            if any('mesh_offset_m' in s for s in structures):series(f,'mesh_offset_m',structures,lambda state:state.get('mesh_offset_m',np.zeros_like(mesh['points'])))
            raw=f.create_group('native_contact')
            raw.attrs['columns']='slave_element,local_face,normal,tangent_1,tangent_2'
            raw.attrs['location']='CalculiX contact integration points; repeated element/face rows retained in solver order'
            for i,s in enumerate(structures):
                g=raw.create_group(str(i))
                g.attrs['contact_totals_json']=json.dumps(finite_json(s.get('contact_totals',{})))
                g.attrs['axles_json']=json.dumps(finite_json(s.get('axles',{})))
                for key in ('contact_stresses','contact_displacements'):
                    g.create_dataset(key,data=np.asarray(s.get(key,[])).reshape(-1,5),compression='gzip')
        if fatigue:
            g=f.create_group('fatigue')
            for key in ('life_cycles','damage','evaluated'):g.create_dataset(key,data=fatigue[key],compression='gzip')
            if 'accumulated_block_damage' in fatigue:g.create_dataset('accumulated_block_damage',data=fatigue['accumulated_block_damage'],compression='gzip')
            g.attrs['location']='integration_point';g.attrs['life_definition']=fatigue['life_definition']
        if contacts:
            g=f.create_group('contact')
            for i,state in enumerate(contacts):
                frame=g.create_group(str(i))
                for name,values in state.items():
                    pair=frame.create_group(name)
                    for key,value in values.items():
                        if isinstance(value,np.ndarray):pair.create_dataset(key,data=value,compression='gzip')
                        elif isinstance(value,str):pair.attrs[key]=value
        if wear:
            g=f.create_group('wear')
            for name,values in wear.items():
                pair=g.create_group(name)
                for key,value in values.items():pair.create_dataset(key,data=value)
        if wear_frames:
            g=f.create_group('wear_history');series(g,'depth_m',wear_frames)
            g.attrs['meaning']='Accumulated Archard depth; within each representative cycle scaled by cycles_per_wear_block, geometry updated only between blocks.'
        if probes:
            group=f.create_group('clearance_probes')
            for name,values in probes.items():
                g=group.create_group(name)
                for key,value in values.items():
                    if isinstance(value,str):g.attrs[key]=value
                    else:g.create_dataset(key,data=value)
    finalize_archive(folder)


def finalize_archive(folder):
    with (folder/'fields.h5').open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    with h5py.File(folder/'fields.h5') as f:version=int(f.attrs.get('result_schema_version',3))
    write_json(folder/'raw-fields.sha256.json',{'file':'fields.h5','sha256':checksum,'schema_version':version})
    # Cache native ranges and histories once, before a completed result is exposed.
    result_index(folder)


def frame_display(folder,index):
    """Compatibility endpoint: topology and fields use the same immutable archive."""
    return {**surface_topology(folder), **frame_values(folder,index)}


def surface_topology(folder):
    from .result_index import surface_topology as read
    return read(folder)


def frame_values(folder,index,fields=None):
    from .result_index import frame_values as read
    return read(folder,index,fields)


def result_index(folder):
    from .result_index import result_index as read
    return read(folder)


def write_report(folder,case,summary,source=None):
    # Deterministic text report; scientific values come exclusively from summary.
    text=['# '+case.name,'','ThermoForge Studio · 本地有限元计算记录','',f'分析类型：{case.analysis_type}',
        '','## 参数来源与假设','',*['- '+x for x in case.assumptions],'- 基础材料：'+case.material.source,
        '','## 计算摘要','', '```json',json.dumps(finite_json(summary),ensure_ascii=False,indent=2),'```']
    (folder/'report.md').write_text('\n'.join(text),encoding='utf-8')
    with (folder/'metrics.csv').open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.writer(out);writer.writerow(['metric','value'])
        for key,value in summary.items():
            if isinstance(value,(str,int,float,bool)):writer.writerow([key,value])
    from .report import report_pdf
    report_pdf(folder,case,summary,source=source)
