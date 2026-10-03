"""Exact-node thermal continuation shared by heat and coupled solvers."""
import hashlib,json
from pathlib import Path
import h5py,numpy as np
from .integrity import checked_raw_fields,file_digest


def load_initial_temperature(mesh,case,study_root=None):
    initial=np.full(len(mesh['points']),case.thermal.initial_C,dtype=float)
    provenance={'kind':'uniform','temperature_C':case.thermal.initial_C,'physical_start_time_s':0.}
    source=case.thermal.initial_from_study
    if source:
        if study_root is None:
            from strength.config import DATA
            study_root=DATA/'studies'
        folder=Path(study_root)/source
        config=json.loads((folder/'config.json').read_text(encoding='utf-8'))
        if config.get('sha256'):
            actual=hashlib.sha256(json.dumps({k:v for k,v in config.items() if k!='sha256'},sort_keys=True).encode()).hexdigest()
            if actual!=config['sha256']:raise ValueError('续算来源工况快照校验失败')
        source_case=config.get('case',{})
        if source_case.get('analysis_type') not in ('thermal_steady','thermal_transient','thermo_mechanical'):raise ValueError('温度续算来源不是热结果')
        if case.geometry_sha256 and source_case.get('geometry_sha256')!=case.geometry_sha256:raise ValueError('续算来源几何版本不匹配')
        status=folder/'status.json'
        if not status.is_file() or json.loads(status.read_text(encoding='utf-8')).get('status')!='completed':raise ValueError('温度续算来源尚未完整计算成功')
        checksum=checked_raw_fields(folder)
        with h5py.File(folder/'fields.h5') as f:
            for key in ('points','tets','body_ids','faces','surface_ids','owners'):
                if key not in f or key not in mesh or not np.array_equal(f[key][:],mesh[key]):raise ValueError(f'续算必须具有相同网格和节点、组件及选面身份（{key} 不匹配）')
            if 'temperature_C' not in f or not len(f['temperature_C']):raise ValueError('续算来源缺少温度场')
            if 'times_s' not in f or len(f['times_s'])!=len(f['temperature_C']):raise ValueError('续算来源温度帧与时间轴不匹配')
            initial=f['temperature_C'][-1]
            source_time=float(f['times_s'][-1])
        provenance={'kind':'saved_field','study_id':source,'source_time_s':source_time,'physical_start_time_s':0.,
                    'raw_fields_sha256':checksum,'source_config_sha256':file_digest(folder/'config.json'),'mapping':'identical_mesh_node_and_component_ids'}
    if initial.shape!=(len(mesh['points']),) or not np.isfinite(initial).all() or initial.min() < -273.15:raise ValueError('初始温度场形状或物理温度无效')
    from .schemas import material_for
    for body in np.unique(mesh.get('body_ids',[])):
        material=material_for(case,body)
        if material.valid_temperature_C:
            nodes=np.unique(mesh['tets'][mesh['body_ids']==body]);lo,hi=material.valid_temperature_C
            if initial[nodes].min()<lo or initial[nodes].max()>hi:raise ValueError(f'组件 {body} 初始温度超出材料参数适用范围')
    provenance.update(minimum_temperature_C=float(initial.min()),maximum_temperature_C=float(initial.max()))
    return initial,provenance
