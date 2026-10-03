"""Auditable report statistics from native nodes, integration points and saved steps."""
import json
from functools import lru_cache
from pathlib import Path
import h5py
import numpy as np
from strength.fem import stress_invariants, tet_shape, IP_BARY


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else default


def selection(value):
    if not value:
        return '全部指定组件'
    body = '全模型' if value.get('body_id') is None else f"组件 {value['body_id']}"
    kind = value.get('kind')
    if kind == 'faces':
        ids = sorted(set(value.get('face_ids', [])))
        groups = []
        for item in ids:
            if groups and item == groups[-1][-1] + 1:
                groups[-1].append(item)
            else:
                groups.append([item])
        faces = ', '.join(str(g[0]) if len(g) == 1 else f'{g[0]}-{g[-1]}' for g in groups)
        return f'{body} / 面 {faces}（{len(ids)} 面）'
    if kind == 'named':
        return f"{body} / 命名选区 {value.get('name')}"
    if kind == 'direction':
        return f"{body} / 朝向 {value.get('direction')}"
    if kind == 'box':
        return f"{body} / 范围 {value.get('bounds_m')} m"
    return f'{body} / {kind}'


def native_statistics(source):
    """Global/component maxima are computed before display projection; indices are 1-based."""
    from forge.integrity import identity
    source=Path(source).resolve()
    return _native_statistics(str(source),identity(source/'fields.h5'))


@lru_cache(maxsize=8)
def _native_statistics(source,stamp):
    source = Path(source)
    result = {'frames': [], 'components': {}, 'hotspots': {}, 'contact_pairs': {}}
    with h5py.File(source / 'fields.h5') as f:
        points, tets, bodies = f['points'][:], f['tets'][:], f['body_ids'][:]
        groups = {int(b): np.flatnonzero(bodies == b) for b in np.unique(bodies)}
        nodes = {b: np.unique(tets[e]) for b, e in groups.items()}
        for b, e in groups.items():
            result['components'][str(b)] = {'body_id': b, 'nodes': len(nodes[b]), 'elements': len(e), 'temperature_history_C': []}

        def peak(key, value, frame, pos, body, **extra):
            if key not in result['hotspots'] or value > result['hotspots'][key]['value']:
                result['hotspots'][key] = {'value': float(value), 'frame': frame, 'time_s': float(f['times_s'][frame]),
                    'position_mm': (np.asarray(pos) * 1000).tolist(), 'body_id': int(body), **extra}

        for i, time in enumerate(f['times_s'][:]):
            row = {'time_s': float(time)}
            if 'temperature_C' in f:
                temp = f['temperature_C'][i]
                row.update(temperature_min_C=float(temp.min()), temperature_max_C=float(temp.max()))
                j = int(temp.argmax()); body = next(b for b, ids in nodes.items() if j in ids)
                peak('temperature', temp[j], i, points[j], body, node=j + 1)
                for b, ids in nodes.items():
                    c = result['components'][str(b)]; values = temp[ids]
                    c['temperature_history_C'].append(float(values.max()))
                    c['temperature_max_C'] = max(c.get('temperature_max_C', -np.inf), float(values.max()))
                    c['temperature_final_min_C'] = float(values.min()); c['temperature_final_max_C'] = float(values.max())
                for name, key in [('gradient_max_K_m', 'temperature_gradient_K_m'), ('heat_flux_max_W_m2', 'heat_flux_W_m2')]:
                    if key in f:
                        magnitude = np.linalg.norm(f[key][i], axis=-1)
                        row[name] = float(magnitude.max())
            if 'stresses' in f:
                vm = stress_invariants(f['stresses'][i])[0]
                row['stress_max_MPa'] = float(vm.max() / 1e6)
                e, q = np.unravel_index(vm.argmax(), vm.shape)
                pos = tet_shape(IP_BARY[q, 1:])[0] @ points[tets[e]]
                peak('stress', vm[e, q] / 1e6, i, pos, bodies[e], element=int(e) + 1, integration_point=int(q) + 1)
                disp = np.linalg.norm(f['displacements'][i], axis=-1)
                row['displacement_max_mm'] = float(disp.max() * 1000)
                j = int(disp.argmax()); body = next(b for b, ids in nodes.items() if j in ids)
                peak('displacement', disp[j] * 1000, i, points[j], body, node=j + 1)
                for b, elems in groups.items():
                    c = result['components'][str(b)]
                    c['stress_max_MPa'] = max(c.get('stress_max_MPa', 0), float(vm[elems].max() / 1e6))
                    c['displacement_max_mm'] = max(c.get('displacement_max_mm', 0), float(disp[nodes[b]].max() * 1000))
            native = f.get(f'native_contact/{i}/contact_stresses')
            if native is not None and len(native):
                row['native_pressure_max_MPa'] = float(native[:, 2].max() / 1e6)
            for name, pair in f.get(f'contact/{i}', {}).items():
                values = result['contact_pairs'].setdefault(name, [])
                pressure = pair['pressure_Pa'][:] if 'pressure_Pa' in pair else np.array([])
                gap = pair['gap_m'][:] if 'gap_m' in pair else np.array([])
                # Empty recovery maps have no sampled pressure/gap, not a measured zero.
                pressure = pressure[np.isfinite(pressure)]; gap = gap[np.isfinite(gap)]
                values.append({'time_s': float(time),
                    'sample_status': 'available' if len(pressure) else 'no_mapped_samples',
                    'projected_pressure_max_MPa': float(pressure.max() / 1e6) if len(pressure) else None,
                    'gap_min_um': float(gap.min() * 1e6) if len(gap) else None,
                    'gap_max_um': float(gap.max() * 1e6) if len(gap) else None})
            if 'wear_history' in f:
                row['wear_max_um'] = float(f['wear_history/depth_m'][i].max() * 1e6)
            elif 'wear' in f and i == len(f['times_s'])-1:
                depth = sum((p['depth_m'][:] for p in f['wear'].values()), np.zeros(len(points)))
                row['wear_final_only_um'] = float(depth.max() * 1e6)
            result['frames'].append(row)
    return result


def validation_rows(summary, audit, node):
    rows = []
    thermal = summary.get('thermal_balance')
    if thermal:
        rows.append(['热场能量平衡最大相对误差', f"{thermal['maximum_relative_error']:.6g}", '≤ 0.01', '通过' if thermal.get('passed') else '未通过'])
    mechanical = summary.get('mechanical_balance', {})
    states = mechanical.get('physical_steps') or mechanical.get('frames') or []
    for key, label in [('force_relative', '力平衡最大相对误差'), ('moment_relative', '力矩平衡最大相对误差')]:
        values = [v[key] for v in states if v.get(key) is not None]
        if values:
            rows.append([label, f'{max(values):.6g}', '≤ 0.01', '通过' if max(values) <= .01 else '未通过'])
    mesh_status=summary.get('convergence', {}).get('status', '未验证')
    mesh_status={'not_verified':'未验证','verified':'已验证','sensitive':'网格敏感','incomplete':'未完成'}.get(mesh_status,mesh_status)
    rows.append(['三档网格收敛', mesh_status, '独立网格比较', '不可由平衡通过替代'])
    validation = node.get('validation')
    if node.get('case',{}).get('analysis_type') in ('static','thermal_steady'):
        rows.append(['时间步减半比较','不适用','静态/稳态无物理时间轴','不构造瞬态验证'])
    else:
        rows.append(['时间步减半比较', '通过' if validation and validation.get('passed') else '本批次未绑定通过证据', '关键温升/位移/压力/间隙', '仅依据本批次证据'])
    return rows
