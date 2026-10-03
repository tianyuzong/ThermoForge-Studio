"""Deterministic report evidence from saved raw fields, never from model prose."""
import json
from pathlib import Path

import h5py
import numpy as np

from .fem import IP_BARY, tet_shape
from .stress_regions import region_result

REPORT_VERSION = '2.7'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def peak_record(values, elements, points, tets):
    local, ip = np.unravel_index(np.argmax(values[elements]), values[elements].shape)
    element = int(elements[local])
    shape, _ = tet_shape(IP_BARY[ip, 1:])
    return {'value_Pa': float(values[element, ip]), 'element': element + 1,
            'integration_point': int(ip) + 1,
            'position_m': (shape @ points[tets[element]]).tolist()}


def report_evidence(folder, summary):
    """Report-only derived statistics; do not alter immutable solver summaries."""
    case = summary['configuration']['case']
    hubs = {h['body_id']: h for h in (case.get('gear_contact') or {}).get('hubs', [])}
    with h5py.File(Path(folder) / 'fields.h5', 'r') as fields:
        p, t = fields['points'][:], fields['tets'][:]
        body = fields['body_ids'][:] if 'body_ids' in fields else np.ones(len(t), int)
        vm, u = fields['von_mises'][:], fields['displacements'][:]
        if not np.isfinite(vm).all() or not np.isfinite(u).all():
            raise ValueError('原始结果包含非有限值，不能生成工程报告')
        if not np.isclose(float(vm.max()), summary['maximum_von_mises_Pa'], rtol=1e-10, atol=1e-8):
            raise ValueError('摘要应力与原始积分点不一致，报告已停止')
        if not np.isclose(float(np.linalg.norm(u, axis=1).max()), summary['maximum_displacement_m'], rtol=1e-10, atol=1e-14):
            raise ValueError('摘要位移与原始场不一致，报告已停止')
        elastic = fields['elastic_displacements'][:] if 'elastic_displacements' in fields else u
        surface, owners = fields['surface_ids'][:], fields['owners'][:]
        principals = fields['principal'][:] if 'principal' in fields else None
        components = []
        for comp in summary['components']:
            bid = comp['body_id']
            elements = np.flatnonzero(body == bid)
            nodes = np.unique(t[elements])
            limits = [p[nodes].min(0), p[nodes].max(0)]
            item = dict(comp, bounds_m=np.asarray(limits).tolist(), nodes=len(nodes), elements=len(elements),
                        stress_peak=peak_record(vm, elements, p, t),
                        maximum_elastic_displacement_m=float(np.linalg.norm(elastic[nodes], axis=1).max()),
                        maximum_principal_Pa=float(principals[elements].max()) if principals is not None else None)
            node = nodes[np.linalg.norm(u[nodes], axis=1).argmax()]
            item['displacement_peak'] = {'node': int(node) + 1, 'position_m': p[node].tolist()}
            bore_ids = hubs.get(bid, {}).get('bore', {}).get('face_ids', [])
            adjacent = np.unique(owners[np.isin(surface, bore_ids)])
            adjacent = adjacent[body[adjacent] == bid]
            item['bore_adjacent_peak'] = peak_record(vm, adjacent, p, t) if len(adjacent) else None
            components.append(item)
        # Explicitly named metric: corner-tetrahedron mean ratio, not a claimed
        # Gmsh high-order element-quality score or a convergence certificate.
        q = p[t[:, :4]]
        volume6 = np.abs(np.linalg.det(np.stack([q[:, i] - q[:, 0] for i in (1, 2, 3)], axis=-1)))
        edges2 = sum(np.sum((q[:, i] - q[:, j])**2, axis=1) for i in range(4) for j in range(i))
        quality = 12 * np.power(volume6 / 2, 2 / 3) / np.maximum(edges2, 1e-30)
        regions = [region_result(fields, spec)[0] for spec in case.get('stress_regions', [])]
    all_evaluated = summary['checks']['strength']['status'] != 'not_evaluated'
    status = ('invalid' if not summary['balance']['passed'] else
              'exceeded' if summary['checks']['strength']['status'] == 'exceeded' else
              'conditional' if all_evaluated else 'not_evaluated')
    return {'report_version': REPORT_VERSION, 'job_id': summary['job_id'], 'components': components,
            'stress_regions': regions,
            'mesh_quality': {'metric': 'corner_tetrahedron_mean_ratio', 'mean': float(quality.mean()),
                             'minimum': float(quality.min()), 'p05': float(np.quantile(quality, .05)),
                             'below_screening_005': int(np.count_nonzero(quality < .05)),
                             'note': '仅衡量四个角节点组成的线性四面体形状；不代替曲边二次单元 Jacobian 或网格收敛。'},
            'assessment_status': status,
            'evaluations': {
                'static_yield': summary['checks']['strength']['status'],
                'displacement': summary['checks']['displacement']['status'],
                'contact_allowable': 'not_evaluated', 'tooth_root_bending': 'not_evaluated',
                'fatigue_or_pitting': 'not_evaluated', 'operating_interference': 'not_evaluated',
                'mesh_convergence': summary['convergence']['status']},
            'external_validation': {'reference_solver': 'Ansys Mechanical / MAPDL', 'status': 'not_verified',
                                    'reason': '当前作业未绑定经条件核对的 Ansys 参考结果。'},
            'root_bending_note': ('已按用户选区提取局部应力，但未分离截面膜应力与弯曲分量；局部应力图不是齿根弯曲强度校核。' if regions else
                                  '尚未定义齿根评估面、截面方向和弯曲应力提取方法；局部 von Mises 云图不是齿根弯曲应力校核。')}
