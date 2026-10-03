"""Offline raster figures from actual FE surfaces and contact quadrature data."""
from pathlib import Path
from functools import lru_cache

import h5py
import numpy as np
from PIL import Image, ImageFont
from .report_canvas import FigureCanvas, FigureDraw, RENDER_SCALE, wrap_text
from .stress_regions import region_result
from .report_locale import report_text

PALETTE = ['#197e91', '#daa34b', '#7769a3', '#619775', '#be6f62']
INK = '#000000'
MUTED = INK
W, H = 1600, 1080


@lru_cache(maxsize=32)
def font(size=28, bold=False):
    # Figure labels are Chinese; Latin-only fonts silently render missing glyphs.
    paths = (['C:/Windows/Fonts/msyhbd.ttc',
              '/usr/share/fonts/truetype/noto/NotoSansSC-Bold.ttf',
              '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'] if bold else
             ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simsun.ttc',
              '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
              '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'])
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, max(38, round(size * 1.4)) * RENDER_SCALE)
    raise RuntimeError('报告图表缺少中文字体，请安装微软雅黑、宋体或 Noto Sans CJK。')


def color(value):
    v = np.clip(np.asarray(value), 0, 1)
    stops = np.array([[22, 80, 125], [35, 158, 170], [134, 204, 167], [251, 206, 83], [202, 47, 44]])
    i = np.minimum((v * 4).astype(int), 3)
    return np.rint(stops[i] + (stops[i + 1] - stops[i]) * (v * 4 - i)[..., None]).astype(np.uint8)


def camera(direction):
    z = np.asarray(direction, float); z /= np.linalg.norm(z)
    up = np.array([0., 0., 1.]) if abs(z[2]) < .96 else np.array([0., 1., 0.])
    x = np.cross(up, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


class SurfaceFigures:
    def __init__(self, folder, display, summary):
        self.folder = Path(folder)
        self.directory = self.folder / 'report-figures'
        self.directory.mkdir(exist_ok=True)
        self.p = np.asarray(display['points'])
        self.t = np.asarray(display['triangles'], int)
        self.u = np.asarray(display['displacements'])
        self.values = {k: np.asarray(v,dtype=float) for k, v in display['fields'].items()}
        self.summary = summary
        with h5py.File(self.folder / 'fields.h5') as f:
            body = f['body_ids'][:] if 'body_ids' in f else np.ones(len(f['tets']), int)
            self.face_bodies = np.repeat(body[f['owners'][:]], 4)
            self.surface_ids = np.repeat(f['surface_ids'][:], 4)
        assert len(self.face_bodies) == len(self.t)
        self.body_colors = {int(b): PALETTE[i % len(PALETTE)] for i, b in enumerate(np.unique(self.face_bodies))}
        if summary.get('contact'):
            _, vec = np.linalg.eigh(np.cov(self.p[:, :2].T))
            x = vec[:, -1]
            if x[0] < 0: x = -x
            self.view = np.array([[x[0], -x[1], 0], [x[1], x[0], 0], [0, 0, 1.]])
        else:
            self.view = camera([1.4, -2., 1.5])

    def surface(self, filename, title, field=None, unit='', multiplier=1., mesh=False,
                center=None, radius=None, view=None, body=None, marks=(), highlights=None,
                deform=0., maximum=None, annotate_bodies=False, triangle_values=None):
        title = report_text(title)
        basis = self.view if view is None else view
        points = self.p + self.u * deform
        origin = self.p.mean(0) if center is None else np.asarray(center)
        xyz = (points - origin) @ basis
        xy = xyz[:, :2]
        if radius is None:
            lo, hi = xy.min(0), xy.max(0)
            middle = (lo + hi) / 2
            span = hi - lo
        else:
            middle = np.zeros(2); span = np.ones(2) * radius * 2
        out = FigureCanvas((W, H)); od = FigureDraw(out)
        title = wrap_text(od, title, font(35, bold=True), W - 150)
        header = 42 + len(title.splitlines()) * 62
        od.text((75, 20), title, fill=INK, font=font(35, bold=True))
        pw, ph = W - 150, H - header - 140
        scale = min((pw - 130) / max(span[0], 1e-10), (ph - 120) / max(span[1], 1e-10))
        def screen(pos):
            a = (np.asarray(pos) - origin) @ basis
            return np.array([pw / 2 + (a[..., 0] - middle[0]) * scale,
                             ph / 2 - (a[..., 1] - middle[1]) * scale]).T
        pts = screen(points)
        plot = FigureCanvas((pw, ph), '#f6f9fb')
        draw = FigureDraw(plot)
        draw.line((0, ph - 1, pw, ph - 1), fill='#dfe9ee', width=2)
        polygon = pts[self.t]
        mask = (polygon[:, :, 0].max(1) >= 0) & (polygon[:, :, 0].min(1) < pw)
        mask &= (polygon[:, :, 1].max(1) >= 0) & (polygon[:, :, 1].min(1) < ph)
        if body is not None: mask &= self.face_bodies == body
        ids = np.flatnonzero(mask)
        ids = ids[np.argsort(xyz[self.t[ids], 2].mean(1), kind='stable')]
        scalar = field is not None or triangle_values is not None
        if triangle_values is not None:
            v = np.asarray(triangle_values) * multiplier
            if v.shape != (len(self.t),) or not np.isfinite(v).any():
                raise ValueError('局部应力图缺少有效表面结果')
            lo, hi = min(0., float(np.nanmin(v))), max(0., float(np.nanmax(v)))
            hi = max(hi, lo + 1e-15)
            rgb = color((np.nan_to_num(v, nan=lo) - lo) / (hi - lo))
            rgb[~np.isfinite(v)] = [217, 227, 231]
        elif field:
            v = self.values[field] * multiplier
            # This is the display scale, not a replacement of raw IP peaks.
            lo = min(0.,float(np.nanmin(v))) if np.isfinite(v).any() else 0.
            hi = (float(np.nanmax(v)) if np.isfinite(v).any() else 1.) if maximum is None else float(maximum)
            hi = max(hi, lo + 1e-15)
            normalized = (v[self.t].mean(1) - lo) / (hi - lo)
            rgb = color(np.nan_to_num(1 - normalized if field == 'safety_factor' else normalized,nan=0.))
            rgb[~np.isfinite(normalized)] = [217,227,231]
        for i in ids:
            shade = tuple(rgb[i]) if scalar else self.body_colors[int(self.face_bodies[i])]
            if highlights is not None:
                shade = '#1783a0' if self.surface_ids[i] in highlights else '#d9e3e7'
            draw.polygon([tuple(a) for a in polygon[i]], fill=shade,
                         outline='#456575' if mesh else None, width=1)
        occupied = []
        def callout(point, label):
            x, y = screen(point)
            if not (-10 <= x <= pw + 10 and -10 <= y <= ph + 10): return
            label = wrap_text(draw, report_text(label), font(25), min(620, pw * .48))
            box = draw.textbbox((0, 0), label, font=font(25))
            width, height = box[2] - box[0] + 24, box[3] - box[1] + 24
            candidates = [(x+30,y-height-30),(x+30,y+30),(x-width-30,y-height-30),(x-width-30,y+30)]
            candidates += [(20,yy) for yy in range(20,max(21,int(ph-height)),int(height+20))]
            for tx, ty in candidates:
                tx = min(max(tx, 12), pw-width-12); ty = min(max(ty, 12), ph-height-12)
                rect = (tx,ty,tx+width,ty+height)
                if not any(rect[0]<o[2]+8 and rect[2]>o[0]-8 and rect[1]<o[3]+8 and rect[3]>o[1]-8 for o in occupied): break
            occupied.append(rect)
            draw.ellipse((x-12,y-12,x+12,y+12),outline='#151f2e',width=3)
            draw.line((x,y,tx+width/2,ty+height/2),fill=INK,width=2)
            draw.rounded_rectangle(rect,8,fill='white',outline='#adbec7',width=1)
            draw.text((tx+12-box[0],ty+12-box[1]),label,fill=INK,font=font(25))
        for point, label in marks: callout(point, label)
        if annotate_bodies and len(self.body_colors)<=8:
            for bid in self.body_colors:
                nodes = np.unique(self.t[self.face_bodies == bid])
                callout(self.p[nodes].mean(0), f'组件 {bid}')
        # Global axes projected with the exact same camera as the mesh.
        base = np.array([100., ph - 110.])
        draw.rounded_rectangle((12, ph-220, 220, ph-16), 9, fill='white', outline='#dfe9ee', width=1)
        for i, (label, tint) in enumerate(zip('XYZ', ['#c7514d', '#438f64', '#4374ad'])):
            tip = base + np.array([basis[i, 0], -basis[i, 1]]) * 90
            draw.line((*base, *tip), fill=tint, width=4)
            draw.text(tuple(tip + [0, -12]), label, font=font(20), fill=INK, anchor='mm')
        length_m = span.max() / 5
        power = 10 ** np.floor(np.log10(max(length_m * 1000, 1e-12)))
        length_mm = max(1., np.floor(length_m * 1000 / power)) * power
        pixels = length_mm / 1000 * scale
        x, y = pw - 75 - pixels, ph - 40
        scale_label = f'{length_mm:g} mm'
        label_width = draw.textlength(scale_label, font=font(21))
        half_width = max(pixels, label_width) / 2 + 16
        midpoint = x + pixels / 2
        draw.rounded_rectangle((midpoint-half_width, y-56, midpoint+half_width, y+17),
                               8, fill='white', outline='#dfe9ee', width=1)
        draw.line((x, y, x + pixels, y), fill=INK, width=3)
        for xx in (x, x + pixels): draw.line((xx, y - 7, xx, y + 7), fill=INK, width=2)
        draw.text((midpoint, y - 25), scale_label, fill=INK, font=font(21), anchor='mm')
        out.paste(plot, (75, header))
        if scalar:
            ticks = np.linspace(0, 1, 900)
            gradient = np.tile(color(1 - ticks if field == 'safety_factor' else ticks)[None, :, :], (20, 1, 1))
            out.paste(Image.fromarray(gradient), (350, H - 85))
            for tick in np.linspace(0, 1, 5):
                od.text((350 + tick * 900, H - 36), f'{lo + tick * (hi - lo):.4g}',
                        fill=INK, font=font(23), anchor='mm')
            od.text((1370, H - 56), unit, fill=INK, font=font(25), anchor='mm')
        elif highlights is not None:
            for i, (label, tint) in enumerate([('约束接口', '#1783a0'), ('模型背景', '#d9e3e7')]):
                x = 370 + i * 470
                od.rectangle((x, H - 72, x + 26, H - 46), fill=tint)
                od.text((x + 40, H - 74), label, fill=INK, font=font(23))
        elif len(self.body_colors)>5:
            od.text((360,H-74),f'共 {len(self.body_colors)} 个组件 / 完整组件对应表见报告正文',fill=INK,font=font(23))
        else:
            for i, (bid, tint) in enumerate(self.body_colors.items()):
                x = 360 + i * 230
                od.rectangle((x, H - 72, x + 26, H - 46), fill=tint)
                od.text((x + 40, H - 74), f'组件 {bid}', fill=INK, font=font(23))
        target = self.directory / filename
        out.save(target, optimize=True)
        return target

    def contact_samples(self, filename, contact, pair_id):
        records = contact['records']
        ids = np.array([i for i, r in enumerate(records) if r['pair_id'] == pair_id])
        p = np.array([records[i]['position_m'] for i in ids])
        n = np.mean([records[i]['normal'] for i in ids], axis=0)
        tangent = np.array([-n[1], n[0], 0.]); tangent /= np.linalg.norm(tangent)
        x = (p - p.mean(0)) @ tangent * 1000
        y = p[:, 2] * 1000
        pressures = np.asarray(contact['pressures_Pa'])[ids] / 1e6
        active = np.asarray(contact['normal_forces_N'])[ids] > 1e-7
        out = FigureCanvas((W, H)); d = FigureDraw(out)
        pair = contact['summary']['pairs'][pair_id]
        title=wrap_text(d, f'接触对：组件 {pair["slave_body"]} - 组件 {pair["master_body"]} / 积分采样压力', font(34, bold=True), W-160)
        d.text((80,22),title,font=font(34, bold=True),fill=INK)
        header=42+len(title.splitlines())*62
        box = (150, header+65, W - 150, H - 340)
        lx, hx = float(x.min()), float(x.max()); ly, hy = float(y.min()), float(y.max())
        dx, dy = max(hx - lx, .02), max(hy - ly, .02)
        lx -= dx * .08; hx += dx * .08; ly -= dy * .1; hy += dy * .1
        def pos(xx, yy): return (box[0] + (xx - lx) / (hx - lx) * (box[2] - box[0]), box[3] - (yy - ly) / (hy - ly) * (box[3] - box[1]))
        d.rectangle(box, fill='#f6f9fb', outline='#cbdbe3', width=2)
        for tick in np.linspace(0, 1, 6):
            xx = lx + tick * (hx - lx); yy = ly + tick * (hy - ly)
            px, py = pos(xx, yy)
            d.line((px, box[1], px, box[3]), fill='#e0e9ee')
            d.line((box[0], py, box[2], py), fill='#e0e9ee')
            d.text((px, box[3] + 25), f'{xx:.3g}', fill=MUTED, font=font(22), anchor='mm')
            d.text((box[0] - 16, py), f'{yy:.3g}', fill=MUTED, font=font(22), anchor='rm')
        hi = max(float(pressures.max()), 1e-12)
        rgb = color(pressures / hi)
        for i in np.argsort(pressures):
            px, py = pos(x[i], y[i]); radius = 10 if active[i] else 5
            d.ellipse((px - radius, py - radius, px + radius, py + radius),
                      fill=tuple(rgb[i]) if active[i] else '#c4cfd6', outline='white', width=1)
        d.text((W / 2, H - 250), '局部切向坐标 / mm（固定接触位置）', font=font(24), fill=INK, anchor='mm')
        d.text((80, header+8), '全局 Z 坐标 / mm', font=font(22), fill=MUTED)
        gradient = np.tile(color(np.linspace(0, 1, 700))[None, :, :], (20, 1, 1))
        out.paste(Image.fromarray(gradient), (420, H - 190))
        d.text((400, H - 180), '0', font=font(22), fill=INK, anchor='rm')
        d.text((1145, H - 180), f'{hi:.5g} MPa', font=font(22), fill=INK, anchor='lm')
        d.text((80, H - 115), wrap_text(d, '灰点：未接触候选；彩点：活跃接触采样。点的大小仅用于显示，不代表接触面积。', font(21), W-160), font=font(21), fill=MUTED)
        target = self.directory / filename; out.save(target, optimize=True)
        return target


def create_figures(folder, display, summary, evidence, contact=None):
    renderer = SurfaceFigures(folder, display, summary)
    figures = {}
    figures['model'] = renderer.surface('01-model.png', '装配模型与组件编号', annotate_bodies=True)
    figures['mesh'] = renderer.surface('02-mesh.png', '实际外表面网格 / 二次四面体单元表面细分（C3D10）', mesh=True)
    case = summary['configuration']['case']
    marks, ids = [], set()
    if contact:
        for hub in case['gear_contact']['hubs']:
            ids.update(hub['bore']['face_ids'])
            label = f'组件 {hub["body_id"]}：绕 Z 转动锁止' if hub['rotation_locked'] else f'组件 {hub["body_id"]}：绕 Z 自由，扭矩 {hub["torque_Nm"]:g} N·m'
            marks.append((hub['center_m'], label))
    else:
        for support in summary['supports']: ids.update(support['face_ids'])
        for i, load in enumerate(case['loads']):
            face_ids = (load.get('selection') or {}).get('face_ids', [])
            nodes = np.unique(renderer.t[np.isin(renderer.surface_ids, face_ids)])
            kind = {'force': '总力', 'pressure': '压力', 'gravity': '重力'}.get(load['kind'], load['kind'])
            if len(nodes): marks.append((renderer.p[nodes].mean(0), f'载荷 {i+1}：{kind}'))
    figures['boundary'] = renderer.surface('03-boundary.png', '边界条件与约束接口', highlights=ids, marks=marks)
    figures['displacement'] = renderer.surface('04-displacement.png', '总位移幅值 / 原始几何形状', 'displacement', 'mm', 1000,
                                               marks=[(summary['displacement_peak']['position_m'], '最大位移位置')])
    if 'elastic_displacement' in renderer.values:
        figures['elastic'] = renderer.surface('05-elastic.png', '弹性位移幅值 / 已扣除刚体转动', 'elastic_displacement', 'mm', 1000)
    figures['stress'] = renderer.surface('06-stress.png', '米塞斯等效应力 / 节点平滑显示场', 'stress', 'MPa', 1e-6,
                                         marks=[(summary['stress_peak']['position_m'], '原始积分点峰值位置')])
    figures['hotspots'] = []
    chosen=hotspot_components(evidence)
    figures['hotspot_body_ids']=[c['body_id'] for c in chosen]
    for comp in chosen:
        bid = comp['body_id']; point = np.array(comp['stress_peak']['position_m'])
        center = np.mean(comp['bounds_m'], axis=0); outward = point - center
        outward[2] = max(np.linalg.norm(outward[:2]) * .65, 1e-4)
        radius = max(.003, min(np.ptp(np.array(comp['bounds_m']), axis=0).max() * .12, .012))
        note = '未作齿根弯曲评估' if contact else '节点平滑显示'
        figures['hotspots'].append(renderer.surface(f'07-hotspot-{bid}.png', f'组件 {bid} / 局部应力热点（{note}）',
            'stress', 'MPa', 1e-6, center=point, radius=radius, view=camera(outward), body=bid, marks=[(point, f'积分点峰值：{comp["stress_peak"]["value_Pa"]/1e6:.4g} MPa')]))
    figures['stress_regions'] = []
    with h5py.File(Path(folder) / 'fields.h5') as fields:
        for i, spec in enumerate(case.get('stress_regions', [])):
            region, face_values = region_result(fields, spec)
            bid = region['body_id']; point = np.array(region['maximum']['position_m'])
            comp = next(c for c in evidence['components'] if c['body_id'] == bid)
            outward = point - np.mean(comp['bounds_m'], axis=0)
            outward[2] = max(np.linalg.norm(outward[:2]) * .65, 1e-4)
            nodes = np.unique(renderer.t[np.repeat(np.isfinite(face_values), 4)])
            radius = max(.003, np.ptp(renderer.p[nodes], axis=0).max() * .6)
            measure = '最大主应力' if region['measure'] == 'principal_max' else '指定方向正应力'
            figures['stress_regions'].append(renderer.surface(f'11-region-{i+1}.png',
                f'组件 {bid} / 选区 {i+1}：{measure}（未分离弯曲分量）',
                unit='MPa', multiplier=1e-6, triangle_values=np.repeat(face_values, 4),
                center=point, radius=radius, view=camera(outward), body=bid,
                marks=[(point, f'原始积分点最大值：{region["maximum"]["value_Pa"]/1e6:.4g} MPa')]))
    if 'safety_factor' in renderer.values:
        figures['safety'] = renderer.surface('08-safety.png', '屈服安全系数 / 节点平滑显示，色标上限为 10', 'safety_factor', '无量纲', maximum=10)
    if contact:
        figures['contact'] = renderer.surface('09-contact.png', '离散接触压力 / 表面加权显示', 'contact_pressure', 'MPa', 1e-6)
        figures['contact_pairs'] = [renderer.contact_samples(f'10-contact-pair-{i+1}.png', contact, i) for i in range(len(contact['summary']['pairs']))]
    return figures


def hotspot_components(evidence):
    # Keep all quantitative component rows, but bound the visual appendix.
    comps=evidence['components']
    return comps if len(comps)<=8 else sorted(comps,key=lambda c:c['maximum_von_mises_Pa'],reverse=True)[:8]


def comparison_figure(path, summaries):
    """Separate scales preserve stress/displacement meaning across cases."""
    panel_height = 95 + max(1,len(summaries)) * 58
    out = FigureCanvas((W, 110 + 3*panel_height)); d = FigureDraw(out)
    d.text((75, 20), '多工况比较 / 原始结果', fill=INK, font=font(35, bold=True))
    specs = [('等效应力 / MPa', lambda s: s['maximum_von_mises_Pa']/1e6),
             ('总位移 / mm', lambda s: s['maximum_displacement_m']*1000),
             ('屈服安全系数 / 无量纲', lambda s: s['minimum_safety_factor'])]
    for j, (title, value) in enumerate(specs):
        top = 105 + j * panel_height
        d.text((75, top), title, font=font(25, bold=True), fill=INK)
        vals = [value(s) for s in summaries]; hi = max([v for v in vals if v is not None] or [1])
        for i, v in enumerate(vals):
            y = top + 65 + i * 58
            d.text((160, y + 12), f'工况 {i+1}', font=font(21), fill=MUTED, anchor='rm')
            if v is not None:
                length = 1050 * v / max(hi, 1e-15)
                d.rectangle((180, y, 180 + length, y + 22), fill=PALETTE[i % len(PALETTE)])
                d.text((200 + length, y + 11), f'{v:.5g}', font=font(21), fill=INK, anchor='lm')
            else: d.text((180, y+11), '未评估', font=font(21), fill=MUTED, anchor='lm')
    out.save(path, optimize=True)
    return path
