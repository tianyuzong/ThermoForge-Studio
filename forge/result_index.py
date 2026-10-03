"""Hash-bound display metadata; immutable topology is transported only once.

Ranges and histories use native fields, never a repeated full-frame JSON projection.
Only display frame requests project integration-point values to surface nodes.
"""
import hashlib
import json
import os
import threading
from functools import lru_cache
from pathlib import Path
import h5py
import numpy as np
from strength.fem import stress_invariants, nodal_stress_for_display, tet_shape, IP_BARY
from strength.result_catalog import finite_json
from .results import CATALOG, nodal_scalar, display

VERSION = 4
_lock = threading.RLock()


def identity(path):
    stat = Path(path).stat()
    return (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=32)
def _hash(key):
    with Path(key[0]).open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    if identity(key[0]) != key:
        raise ValueError('结果文件在读取中变化，请等待该阶段保存完成')
    return value


@lru_cache(maxsize=3)
def _geometry(key):
    with h5py.File(key[0]) as archive:
        mesh = {k: archive[k][:] for k in ('points','tets','faces','surface_ids','owners','body_ids')}
    topology = display(mesh,{})
    topology.pop('fields'); topology.pop('field_catalog'); topology.pop('displacements')
    topology_id = hashlib.sha256(b''.join(mesh[k].tobytes() for k in ('points','faces','surface_ids','owners','body_ids'))).hexdigest()
    topology['topology_id'] = topology_id
    topology['body_ids'] = np.repeat(mesh['body_ids'][mesh['owners']],4).tolist()
    return mesh,topology,np.asarray(topology['node_ids'],int)-1


def surface_topology(folder):
    return _geometry(identity(Path(folder)/'fields.h5'))[1]


def _native(archive,index,mesh):
    """Native field arrays and locations, with missing data explicitly absent."""
    fields={}
    if 'temperature_C' in archive:
        fields['temperature']=(archive['temperature_C'][index],'node')
        for name,key in [('temperature_gradient','temperature_gradient_K_m'),('heat_flux','heat_flux_W_m2')]:
            if key in archive: fields[name]=(np.linalg.norm(archive[key][index],axis=-1),'integration_point')
    if 'stresses' in archive:
        fields['stress']=(stress_invariants(archive['stresses'][index])[0],'integration_point')
    if 'displacements' in archive:
        fields['displacement']=(np.linalg.norm(archive['displacements'][index],axis=-1),'node')
    if 'fatigue' in archive:
        for name,key in [('fatigue_life','life_cycles'),('fatigue_damage','damage'),('fatigue_accumulated_damage','accumulated_block_damage')]:
            if key in archive['fatigue']: fields[name]=(archive['fatigue/'+key][:],'integration_point')
    for name,key in [('contact_pressure','pressure_Pa'),('contact_gap','gap_m')]:
        samples=[p[key][:] for p in archive.get(f'contact/{index}',{}).values() if key in p]
        if samples: fields[name]=(np.concatenate(samples),'contact_face_projection')
    if 'wear_history' in archive:
        fields['wear_depth']=(archive['wear_history/depth_m'][index],'node')
    elif 'wear' in archive and index==len(archive['times_s'])-1:
        fields['wear_depth']=(sum((p['depth_m'][:] for p in archive['wear'].values()),np.zeros(len(mesh['points']))),'node_final_only')
    return fields


def _axes(archive,index):
    result={}
    if 'history_axes' in archive:
        axes=archive['history_axes']
        for key in ('cycle_count','wear_block_index','sample_index'):
            if key in axes: result[key]=float(axes[key][index])
        result['angles_rad']={v.attrs['name']:float(v['angle_rad'][index]) for v in axes.get('motion',{}).values()}
    return result


def frame_values(folder,index,fields=None):
    key=identity(Path(folder)/'fields.h5');mesh,topology,used=_geometry(key)
    requested=set(fields.split(',') if isinstance(fields,str) else fields) if fields else None
    if requested and requested-set(CATALOG): raise ValueError('未知结果场：'+', '.join(sorted(requested-set(CATALOG))))
    with h5py.File(key[0]) as archive:
        times=archive['times_s'][:]
        if index<0:index=len(times)-1
        if not 0<=index<len(times):raise ValueError('结果时间帧超出范围')
        values={};u=np.zeros((len(used),3))
        # Read only requested datasets. Stresses are averaged as tensors before invariants.
        for name in (requested or set(CATALOG)):
            value=None
            if name=='temperature' and 'temperature_C' in archive:value=archive['temperature_C'][index]
            elif name in ('temperature_gradient','heat_flux'):
                source={'temperature_gradient':'temperature_gradient_K_m','heat_flux':'heat_flux_W_m2'}[name]
                if source in archive:value=nodal_scalar(mesh,np.linalg.norm(archive[source][index],axis=-1))
            elif name=='stress' and 'stresses' in archive:value=stress_invariants(nodal_stress_for_display(mesh,archive['stresses'][index]))[0]
            elif name=='displacement' and 'displacements' in archive:value=np.linalg.norm(archive['displacements'][index],axis=-1)
            elif name.startswith('fatigue_') and 'fatigue' in archive:
                source={'fatigue_life':'life_cycles','fatigue_damage':'damage','fatigue_accumulated_damage':'accumulated_block_damage'}[name]
                if source in archive['fatigue']:value=nodal_scalar(mesh,archive['fatigue/'+source][:])
            elif name in ('contact_pressure','contact_gap') and f'contact/{index}' in archive:
                value=np.zeros(len(mesh['points']));count=np.zeros_like(value)
                source='pressure_Pa' if name=='contact_pressure' else 'gap_m'
                for pair in archive[f'contact/{index}'].values():
                    if source not in pair or 'slave_face' not in pair:continue
                    faces=mesh['faces'][pair['slave_face'][:]];scalar=pair[source][:]
                    np.add.at(value,faces.ravel(),np.repeat(scalar,faces.shape[1]));np.add.at(count,faces.ravel(),1)
                value=np.divide(value,count,out=np.full_like(value,np.nan),where=count>0)
            elif name=='wear_depth':
                if 'wear_history' in archive:value=archive['wear_history/depth_m'][index]
                elif 'wear' in archive and index==len(times)-1:value=sum((p['depth_m'][:] for p in archive['wear'].values()),np.zeros(len(mesh['points'])))
            if value is not None:values[name]=np.asarray(value)[used].tolist()
        if 'displacements' in archive:
            u=archive['displacements'][index][used]
            if 'mesh_offset_m' in archive:u=u+archive['mesh_offset_m'][index][used]
        return finite_json({'topology_id':topology['topology_id'],'frame_index':index,'time_s':float(times[index]),
            'axes':_axes(archive,index),'fields':values,'displacements':u.tolist(),
            'field_catalog':[{'key':k,'label':CATALOG[k][0],'unit':CATALOG[k][1],'factor':CATALOG[k][2]} for k in sorted(values)],
            'field_temporal_kind':{k:('cumulative_assessment' if k.startswith('fatigue_') else 'final_only' if k=='wear_depth' and 'wear_history' not in archive else 'saved_frame') for k in values}})


def _peak_location(name,values,location,archive,index,mesh):
    minimum=name in ('fatigue_life','contact_gap')
    finite=np.where(np.isfinite(values),values,np.inf if minimum else -np.inf)
    at=np.unravel_index(int(np.argmin(finite) if minimum else np.argmax(finite)),finite.shape);result={}
    if location.startswith('node'):
        node=at[0];elems=np.flatnonzero((mesh['tets']==node).any(axis=1));body=int(mesh['body_ids'][elems[0]]) if len(elems) else None
        result={'position_m':mesh['points'][node].tolist(),'node_id':int(node+1),'body_id':body}
    elif location=='integration_point':
        element=at[0];q=at[1] if len(at)>1 else 0
        pos=tet_shape(IP_BARY[q,1:])[0] @ mesh['points'][mesh['tets'][element]]
        result={'position_m':pos.tolist(),'element_id':int(element+1),'integration_point':int(q+1),'body_id':int(mesh['body_ids'][element])}
    elif location=='contact_face_projection':
        offset=at[0];source='pressure_Pa' if name=='contact_pressure' else 'gap_m'
        for pairname,pair in archive.get(f'contact/{index}',{}).items():
            if source not in pair:continue
            if offset<len(pair[source]):
                if 'slave_face' in pair:
                    face=int(pair['slave_face'][offset]);result={'position_m':mesh['points'][mesh['faces'][face]].mean(0).tolist(),'body_id':int(mesh['body_ids'][mesh['owners'][face]]),'contact_pair':pairname}
                break
            offset-=len(pair[source])
    return result


def result_index(folder):
    folder=Path(folder);key=identity(folder/'fields.h5');checksum=_hash(key)
    return _index(key,checksum)


@lru_cache(maxsize=12)
def _index(key,checksum):
    with _lock:
        path=Path(key[0]).parent/'result-index.json'
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
            payload_hash=hashlib.sha256(json.dumps({k:v for k,v in saved.items() if k!='index_sha256'},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            if saved.get('schema_version')==VERSION and saved.get('raw_sha256')==checksum and saved.get('index_sha256')==payload_hash:return saved
        except (OSError,ValueError):pass
        mesh,topology,used=_geometry(key);histories={};ranges={};hotspots={};missing={}
        with h5py.File(key[0]) as archive:
            times=archive['times_s'][:].tolist()
            for i,time in enumerate(times):
                for name,(values,location) in _native(archive,i,mesh).items():
                    finite=values[np.isfinite(values)];h=histories.setdefault(name,{'field':name,'label':CATALOG[name][0],'unit':CATALOG[name][1],'factor':CATALOG[name][2],'basis':location,'times_s':[],'minimum':[],'maximum':[],'mean':[]})
                    h['times_s'].append(time)
                    for label,fn in [('minimum',np.min),('maximum',np.max),('mean',np.mean)]:h[label].append(float(fn(finite)) if len(finite) else None)
                    missing[name]=max(missing.get(name,0),int(values.size-len(finite)))
                    if not len(finite):continue
                    lo,hi=float(finite.min()),float(finite.max());old=ranges.setdefault(name,[lo,hi]);old[0]=min(old[0],lo);old[1]=max(old[1],hi)
                    minimum=name in ('fatigue_life','contact_gap');critical=lo if minimum else hi
                    if name not in hotspots or (critical<hotspots[name]['value'] if minimum else critical>hotspots[name]['value']):
                        hotspots[name]={'value':critical,'extremum':'minimum' if minimum else 'maximum','frame_index':i,'time_s':time,'basis':location,**_peak_location(name,values,location,archive,i,mesh)}
            temporal={name:('cumulative_assessment' if name.startswith('fatigue_') else 'final_only' if name=='wear_depth' and 'wear_history' not in archive else 'saved_frame') for name in histories}
        for name,pair in ranges.items():
            if name not in ('temperature','contact_gap','fatigue_life'):pair[0]=min(0,pair[0])
            if pair[1]<=pair[0]:pair[1]=pair[0]+max(abs(pair[0])*.01,1e-12)
        result=finite_json({'schema_version':VERSION,'raw_sha256':checksum,'topology_id':topology['topology_id'],'ranges':ranges,'times_s':times,
            'field_catalog':[{'key':k,'label':CATALOG[k][0],'unit':CATALOG[k][1],'factor':CATALOG[k][2]} for k in sorted(histories)],
            'histories':list(histories.values()),'hotspots':hotspots,'maximum_missing_count':missing,'field_temporal_kind':temporal,
            'range_basis':'native nodal/integration-point fields; contact uses stored face projection; display smoothing may lower peaks'})
        result['index_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        pending=path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.pending')
        try:
            pending.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8');pending.replace(path)
        except OSError:
            # Read-only imported archives can still be inspected using the in-memory index.
            if pending.exists():pending.unlink()
        return result
