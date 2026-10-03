"""Signed stress extraction on explicitly selected surface-adjacent elements.

This is a region query, not stress linearization or a gear fatigue criterion.
No surface extrapolation is used for the quantitative extrema.
"""
import math

import numpy as np

from .fem import IP_BARY, tet_shape
from .schemas import StressRegion


MEASURE_LABELS = {'principal_max': '最大主应力', 'normal': '指定方向正应力'}
REGION_NOTE = ('选面相邻体单元的四个原始积分点参与统计；拉应力为正，压应力为负。'
               '图面颜色取相邻单元四点算术平均，未外推至齿面。'
               '此项未分离膜应力与弯曲分量，不能替代齿根弯曲强度或疲劳校核。')


def stress_measure(stresses, measure, direction=None):
    """Voigt order: xx, yy, zz, xy, xz, yz. Shear stress is not doubled."""
    s = np.asarray(stresses, dtype=float)
    if s.shape[-1] != 6 or not np.isfinite(s).all():
        raise ValueError('局部应力原始张量无效')
    if measure == 'normal':
        if direction is None or len(direction) != 3 or not np.isfinite(direction).all():
            raise ValueError('正应力方向无效')
        norm = math.hypot(*direction)
        if not math.isfinite(norm) or norm < 1e-12: raise ValueError('正应力方向不能为零或超出数值范围')
        x, y, z = np.asarray(direction, float) / norm
        return s @ np.array([x*x, y*y, z*z, 2*x*y, 2*x*z, 2*y*z])
    if measure != 'principal_max': raise ValueError('未知局部应力指标')
    tensor = np.empty(s.shape[:-1] + (3, 3))
    tensor[..., 0, 0], tensor[..., 1, 1], tensor[..., 2, 2] = s[..., 0], s[..., 1], s[..., 2]
    tensor[..., 0, 1] = tensor[..., 1, 0] = s[..., 3]
    tensor[..., 0, 2] = tensor[..., 2, 0] = s[..., 4]
    tensor[..., 1, 2] = tensor[..., 2, 1] = s[..., 5]
    return np.linalg.eigvalsh(tensor)[..., -1]


def region_result(fields, definition):
    spec = StressRegion.model_validate(definition)
    face_ids = sorted(set(spec.selection.face_ids))
    if spec.selection.kind != 'faces' or not face_ids:
        raise ValueError('局部应力选区尚未绑定真实面 ID')
    surfaces, owners = fields['surface_ids'][:], fields['owners'][:]
    body = fields['body_ids'][:] if 'body_ids' in fields else np.ones(len(fields['tets']), int)
    mask = np.isin(surfaces, face_ids)
    if set(surfaces[mask].tolist()) != set(face_ids):
        raise ValueError('局部应力选区没有完整映射到当前网格')
    if not np.all(body[owners[mask]] == spec.selection.body_id):
        raise ValueError('局部应力选区跨越了其他组件')
    elements = np.unique(owners[mask])
    values = stress_measure(fields['stresses'][elements], spec.measure, spec.direction)
    if values.shape != (len(elements), 4): raise ValueError('局部应力需要 C3D10 四积分点结果')

    def extreme(maximum):
        local, ip = np.unravel_index((np.argmax if maximum else np.argmin)(values), values.shape)
        element = int(elements[local])
        n, _ = tet_shape(IP_BARY[ip, 1:])
        # h5py fancy indices must be sorted; preserve C3D10 local node order.
        nodes = fields['tets'][element]
        unique, inverse = np.unique(nodes, return_inverse=True)
        point = n @ fields['points'][unique][inverse]
        return {'value_Pa': float(values[local, ip]), 'element': element + 1,
                'integration_point': int(ip) + 1, 'position_m': point.tolist()}

    normal = (np.asarray(spec.direction) / math.hypot(*spec.direction)).tolist() if spec.direction else None
    evidence = {'name': spec.name, 'body_id': spec.selection.body_id, 'face_ids': face_ids,
                'measure': spec.measure, 'label': MEASURE_LABELS[spec.measure], 'direction': normal,
                'element_count': len(elements), 'integration_point_count': int(values.size),
                'maximum': extreme(True), 'minimum': extreme(False), 'note': REGION_NOTE,
                'strength_status': 'not_evaluated'}
    # NaN identifies unselected context, never a fictitious zero stress.
    face_values = np.full(len(surfaces), np.nan)
    face_values[mask] = values.mean(1)[np.searchsorted(elements, owners[mask])]
    return evidence, face_values
