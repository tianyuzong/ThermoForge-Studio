"""Static scientific figures, with the same P2 surface and projections as the viewer."""
from functools import lru_cache
from pathlib import Path
import h5py
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import Normalize
from matplotlib.font_manager import fontManager, FontProperties
from strength.fem import nodal_stress_for_display, stress_invariants
from forge.results import nodal_scalar, CATALOG

FONT = Path('C:/Windows/Fonts/msyh.ttc')
if FONT.exists():
    fontManager.addfont(str(FONT))
    plt.rcParams['font.family'] = FontProperties(fname=str(FONT)).get_name()
plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 9, 'axes.spines.top': False,
    'axes.spines.right': False, 'axes.labelcolor': '#263b46', 'text.color': '#263b46',
    'savefig.facecolor': 'white', 'axes.titlesize': 10, 'figure.dpi': 110})
COLORS = ['#137f8c', '#dc734d', '#5874b8', '#879143', '#986db5', '#b85d72']


class Figures:
    def __init__(self, source, destination, stats, case, summary):
        self.source, self.dest = Path(source), Path(destination)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.stats, self.case, self.summary = stats, case, summary
        with h5py.File(self.source / 'fields.h5') as f:
            self.mesh = {k: f[k][:] for k in ('points', 'tets', 'faces', 'surface_ids', 'owners', 'body_ids')}
            self.times = f['times_s'][:]
        self.tri = self.mesh['faces'][:, np.array([[0, 3, 5], [3, 1, 4], [5, 4, 2], [3, 4, 5]])].reshape(-1, 3)
        self.bodies = np.repeat(self.mesh['body_ids'][self.mesh['owners']], 4)
        self.surfaces = np.repeat(self.mesh['surface_ids'], 4)
        # Orthographic camera, right-handed projection; coordinates are never rescaled independently.
        a, e = np.deg2rad([-60, 24])
        self.rotation = np.array([[-np.sin(a), np.cos(a), 0],
            [-np.sin(e)*np.cos(a), -np.sin(e)*np.sin(a), np.cos(e)],
            [np.cos(e)*np.cos(a), np.cos(e)*np.sin(a), np.sin(e)]])
        self.records = []
        self.aliases = {}

    def body_label(self,body):
        return ('组件 '+str(body)+' · '+self.aliases[str(body)]) if str(body) in self.aliases else '组件 '+str(body)

    def save(self, fig, name, caption, **extra):
        path = self.dest / (name + '.png')
        fig.savefig(path, dpi=210, bbox_inches='tight', pad_inches=.12)
        plt.close(fig)
        result = {'path': path, 'caption': caption, **extra}
        self.records.append(result)
        return result

    @lru_cache(maxsize=6)
    def field(self, key, index):
        with h5py.File(self.source / 'fields.h5') as f:
            if key == 'temperature':
                return f['temperature_C'][index]
            if key in ('temperature_gradient', 'heat_flux'):
                source = {'temperature_gradient': 'temperature_gradient_K_m', 'heat_flux': 'heat_flux_W_m2'}[key]
                return nodal_scalar(self.mesh, np.linalg.norm(f[source][index], axis=-1))
            if key == 'stress':
                return stress_invariants(nodal_stress_for_display(self.mesh, f['stresses'][index]))[0]
            if key == 'displacement':
                return np.linalg.norm(f['displacements'][index], axis=-1)
            if key in ('contact_pressure', 'contact_gap'):
                total = np.zeros(len(self.mesh['points'])); count = np.zeros_like(total)
                for pair in f.get(f'contact/{index}', {}).values():
                    for face, value in zip(pair['slave_face'][:], pair['pressure_Pa' if key == 'contact_pressure' else 'gap_m'][:]):
                        ids = self.mesh['faces'][face]
                        np.add.at(total, ids, value); np.add.at(count, ids, 1)
                return np.divide(total, count, out=np.full_like(total, np.nan), where=count > 0)
            if key == 'wear_depth':
                if 'wear_history' in f:
                    return f['wear_history/depth_m'][index]
                if 'wear' in f and index == len(self.times)-1:
                    return sum((p['depth_m'][:] for p in f['wear'].values()), np.zeros(len(self.mesh['points'])))
            if key.startswith('fatigue_'):
                name = {'fatigue_damage': 'damage', 'fatigue_life': 'life_cycles'}[key]
                return nodal_scalar(self.mesh, f['fatigue/' + name][:])
        raise KeyError(key)

    def projection(self, points):
        return np.asarray(points) @ self.rotation.T

    def surface(self, ax, values=None, norm=None, body_ids=None, deformation=0, index=-1, markers=None, mesh=False):
        points = self.mesh['points'].copy() * 1000
        if deformation:
            with h5py.File(self.source / 'fields.h5') as f:
                if 'displacements' in f:
                    points += f['displacements'][index] * 1000 * deformation
        mask = np.isin(self.bodies, body_ids) if body_ids is not None else np.ones(len(self.tri), bool)
        tri = self.tri[mask]; projection = self.projection(points); polys = projection[tri]
        depth = polys[:, :, 2].mean(1); order = np.argsort(depth)
        if values is None:
            color = plt.get_cmap('tab20')((self.bodies[mask] % 20) / 20)
        else:
            colorvalues = np.nanmean(np.asarray(values)[tri], axis=1) if np.isfinite(values).all() else np.array([
                np.mean(v[np.isfinite(v)]) if np.isfinite(v).any() else np.nan for v in np.asarray(values)[tri]])
            color = plt.get_cmap('turbo')(norm(colorvalues))
            color[~np.isfinite(colorvalues)] = [.78, .82, .83, 1]
        collection = PolyCollection(polys[order, :, :2], facecolors=color[order],
            edgecolors='#33505c' if mesh else 'none', linewidths=.12 if mesh else 0, rasterized=True)
        ax.add_collection(collection)
        used = np.unique(tri); bounds = projection[used, :2]
        lo, hi = bounds.min(0), bounds.max(0); span = np.maximum(hi-lo, 1e-6)
        ax.set_xlim(lo[0]-.09*span[0], hi[0]+.09*span[0]); ax.set_ylim(lo[1]-.09*span[1], hi[1]+.12*span[1])
        ax.set_aspect('equal'); ax.axis('off')
        for point, text, color in markers or []:
            xy = self.projection(np.asarray(point) * 1000)[:2]
            ax.plot(*xy, marker='o', color=color, markersize=4)
            ax.annotate(text, xy, xytext=(12, 15), textcoords='offset points', fontsize=8, color=color,
                arrowprops={'arrowstyle': '-', 'color': color}, bbox={'fc':'white', 'alpha':.9, 'ec':'none', 'pad':2})

    def cloud(self, key, views, title, caption, name, limits=None, deformation=0):
        factor = CATALOG[key][2]; label, unit = CATALOG[key][:2]
        all_values = [self.field(key, i)*factor for i, _, _ in views]
        finite = np.concatenate([v[np.isfinite(v)] for v in all_values])
        if not len(finite):
            return None
        lo, hi = limits if limits else (float(finite.min()), float(finite.max()))
        if hi <= lo: hi = lo + max(abs(lo)*.01, 1e-6)
        norm = Normalize(lo, hi)
        fig, axes = plt.subplots(1, len(views), figsize=(8.1, 3.6), squeeze=False)
        for ax, (i, bodies, subtitle), value in zip(axes[0], views, all_values):
            markers = []
            hot = self.stats['hotspots'].get(key)
            if hot and hot['frame'] == i and (bodies is None or hot['body_id'] in bodies) and not deformation:
                markers = [(np.asarray(hot['position_mm'])/1000, '原始极值位置', '#242424')]
            self.surface(ax, value, norm, bodies, deformation, i, markers)
            ax.set_title(subtitle, pad=8)
        fig.suptitle(title, fontsize=12, fontweight='bold', x=.06, ha='left')
        fig.subplots_adjust(left=.02, right=.88, bottom=.04, top=.83, wspace=.12)
        bar = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap='turbo'), cax=fig.add_axes([.91,.18,.018,.53]))
        bar.ax.set_title(unit, fontsize=9, pad=12); bar.ax.tick_params(labelsize=8)
        return self.save(fig, name, caption + ' 同组固定色标；灰色表示该处无对应场值。', field=key,
            frames=[i for i, _, _ in views], limits=[lo,hi], deformation_scale=deformation)

    def geometry(self):
        fig, axes = plt.subplots(1, 2, figsize=(8.1, 3.6))
        self.surface(axes[0]); axes[0].set_title('原始装配位置 / 组件着色')
        # Show the highest-temperature component when available, otherwise the raw stress hotspot body.
        hot = self.stats['hotspots'].get('temperature') or self.stats['hotspots'].get('stress')
        body = hot['body_id'] if hot else min(self.bodies)
        self.surface(axes[1], body_ids=[body], mesh=True)
        axes[1].set_title(self.body_label(body)+' / 二阶表面网格局部')
        fig.tight_layout()
        return self.save(fig, 'geometry', '左图为原始装配，组件编号对应后文独立组件图集；右图显示真实 T6 表面离散，绘图细分不改变求解网格。')

    def component_atlas(self):
        result=[];bodies=sorted(set(self.bodies))
        for start in range(0,len(bodies),12):
            items=bodies[start:start+12];rows=(len(items)+2)//3
            fig,axes=plt.subplots(rows,3,figsize=(8.1,rows*1.85),squeeze=False)
            for ax,body in zip(axes.ravel(),items):
                self.surface(ax,body_ids=[body]);ax.set_title(self.body_label(body),fontsize=10)
            for ax in axes.ravel()[len(items):]:ax.axis('off')
            fig.tight_layout()
            result.append(self.save(fig,f'components-{start//12+1}',
                '独立组件识别图集；每个小图分别适应窗口，缩放比例不同，不用于比较零件绝对尺寸。组件身份和几何未改变。'))
        return result

    def boundaries(self):
        fig, axes = plt.subplots(1, 2, figsize=(8.1, 3.6))
        kind = self.case['analysis_type']; thermal = kind.startswith('thermal') or kind == 'thermo_mechanical'
        groups = [('机械选区', self.case.get('supports', []) + self.case.get('loads', []) if not kind.startswith('thermal') else []),
                  ('热源与散热选区', self.case.get('thermal', {}).get('boundaries', []) if thermal else [])]
        for ax, (title, items) in zip(axes, groups):
            markers=[]
            for j, item in enumerate(items):
                sel=item.get('selection') or {}; mask=np.ones(len(self.tri),bool)
                if sel.get('face_ids'): mask &= np.isin(self.surfaces,sel['face_ids'])
                body=sel.get('body_id') or item.get('body_id')
                if body is not None: mask &= self.bodies==body
                if not mask.any():continue
                ids=np.unique(self.tri[mask]);center=self.mesh['points'][ids].mean(0)
                markers.append((center,f'{j+1}',COLORS[j%len(COLORS)]))
            self.surface(ax, markers=markers);ax.set_title(title if items else title+' / 本工况未启用')
        fig.tight_layout()
        return self.save(fig,'boundaries','编号按下方边界表顺序；标记位于真实选区或发热组件的节点几何中心，完整作用面见表及工况快照。')

    def history(self):
        frames=self.stats['frames'];times=[v['time_s'] for v in frames]
        if len(times)<2:return None
        metrics=[(k,label) for k,label in [('temperature_max_C','最高温度 / °C'),('stress_max_MPa','原始积分点应力峰值 / MPa'),
            ('displacement_max_mm','最大位移 / mm'),('native_pressure_max_MPa','原生接触压力峰值 / MPa')] if k in frames[0]]
        if not metrics:return None
        fig,axes=plt.subplots(len(metrics),1,figsize=(8.1,2.15*len(metrics)),squeeze=False)
        for ax,(key,label),color in zip(axes[:,0],metrics,COLORS):
            ax.plot(times,[v[key] for v in frames],'-o',ms=3,color=color);ax.set_ylabel(label);ax.grid(alpha=.2)
            ax.ticklabel_format(axis='y',style='plain',useOffset=False)
            if key=='temperature_max_C':
                ax.plot(times,[v['temperature_min_C'] for v in frames],'--',color='#5874b8',label='全模型最低温度');ax.legend(loc='best',fontsize=8)
        axes[-1,0].set_xlabel('实际保存时间 / s');fig.tight_layout()
        return self.save(fig,'history','各点为实际保存帧；连线仅连接样本，不代表额外求解时刻。应力及接触压力取原始结果，未使用平滑云图峰值。')

    def component_temperatures(self):
        rows=self.stats['components'];times=[v['time_s'] for v in self.stats['frames']]
        if len(times)<2 or not any(r['temperature_history_C'] for r in rows.values()):return None
        fig,ax=plt.subplots(figsize=(8.1,3.7))
        for i,(body,r) in enumerate(rows.items()):
            if r['temperature_history_C']:ax.plot(times,r['temperature_history_C'],label=f'组件 {body}',color=plt.get_cmap('tab10')(i%10),lw=1.5)
        ax.set(xlabel='实际保存时间 / s',ylabel='组件最高节点温度 / °C');ax.grid(alpha=.2)
        ax.legend(ncol=5,loc='upper center',bbox_to_anchor=(.5,1.25),fontsize=8,frameon=False);fig.tight_layout()
        return self.save(fig,'component-temperatures','按独立组件统计最高节点温度；这不是体积平均温度，也不把稳态解当作瞬态初始条件。')

    def clearance(self):
        probes=self.summary.get('clearance_probes',{})
        if not probes:return None
        fig,axes=plt.subplots(len(probes),1,figsize=(8.1,2.3*len(probes)),squeeze=False)
        for ax,(name,p) in zip(axes[:,0],probes.items()):
            t=p['times_s'];lo=np.asarray(p['minimum_gap_m'])*1e6;hi=np.asarray(p['maximum_gap_m'])*1e6
            ax.fill_between(t,lo,hi,color='#137f8c',alpha=.15,label='最小-最大范围')
            ax.plot(t,lo,'o-',ms=3,color='#137f8c',label='最小');ax.plot(t,hi,'--',color='#dc734d',label='最大')
            ax.plot(t,np.asarray(p['mean_gap_m'])*1e6,color='#5874b8',label='均值')
            ax.set_ylabel(name+' / μm');ax.ticklabel_format(axis='y',style='plain',useOffset=False);ax.grid(alpha=.2)
            ax.legend(ncol=4,fontsize=7,loc='best')
        axes[-1,0].set_xlabel('实际保存时间 / s');fig.tight_layout()
        return self.save(fig,'clearance','小位移法向几何探针。负间隙可能包含罚函数穿透或插值误差，不能直接据此认定实际干涉；请结合原生接触结果和收敛检查。')

    def contact_history(self):
        pairs=self.stats['contact_pairs'];states=self.summary.get('contact_history',[])
        if not pairs:return None
        fig,axes=plt.subplots(3,1,figsize=(8.1,7.0));area_recorded=False
        for i,(name,values) in enumerate(pairs.items()):
            axes[0].plot([v['time_s'] for v in values],[v['projected_pressure_max_MPa'] if v['projected_pressure_max_MPa'] is not None else np.nan for v in values],'-o',ms=3,label=name,color=COLORS[i%len(COLORS)])
            items=[(v['time_s'],v['pairs'][name]['area_m2']*1e6) for v in states if name in v.get('pairs',{}) and 'area_m2' in v['pairs'][name]]
            if items:axes[1].plot(*np.array(items).T,'-o',ms=3,label=name,color=COLORS[i%len(COLORS)]);area_recorded=True
            axes[2].plot([v['time_s'] for v in values],[v.get('gap_min_um') if v.get('gap_min_um') is not None else np.nan for v in values],'-o',ms=3,label=name+' 最小间隙',color=COLORS[i%len(COLORS)])
            axes[2].plot([v['time_s'] for v in values],[v.get('gap_max_um') if v.get('gap_max_um') is not None else np.nan for v in values],'--',lw=1,color=COLORS[i%len(COLORS)])
        axes[0].set_ylabel('从面投影压力最大值 / MPa');axes[1].set_ylabel('求解器接触面积 / mm²');axes[2].set_ylabel('最小/最大间隙 / μm');axes[2].set_xlabel('实际保存时间 / s')
        if not area_recorded:axes[1].text(.5,.5,'未记录原生接触面积',ha='center',transform=axes[1].transAxes)
        for ax in axes:
            ax.grid(alpha=.2)
            if ax.get_legend_handles_labels()[0]:ax.legend(fontsize=8)
        fig.tight_layout()
        return self.save(fig,'contact-history','依次为从面投影压力、求解器保存的接触面积和间隙范围。缺少有效映射的样本留空，没有以零补齐，也没有以压力阈值估算接触面积。')

    def wear_history(self):
        rows=[r for r in self.stats['frames'] if 'wear_max_um' in r]
        if len(rows)<2:return None
        fig,ax=plt.subplots(figsize=(8.1,3.2));ax.plot([r['time_s'] for r in rows],[r['wear_max_um'] for r in rows],'o-',color=COLORS[0])
        ax.set(xlabel='实际保存时间 / s',ylabel='最大累计磨损深度 / μm');ax.grid(alpha=.2);fig.tight_layout()
        return self.save(fig,'wear-history','逐帧累计 Archard 磨损；每个点来自 wear_history 原始数据。末值未重复填入历史帧。')

    def fatigue_curve(self,curve,key):
        fig,ax=plt.subplots(figsize=(8.1,3.2));ax.loglog([p['x'] for p in curve['points']],[p['y']/1e6 for p in curve['points']],'o-',color=COLORS[0])
        if curve.get('endurance_limit_Pa'):ax.axhline(curve['endurance_limit_Pa']/1e6,color=COLORS[1],ls='--',label='输入疲劳极限');ax.legend(fontsize=8)
        ax.set(xlabel='输入曲线寿命 / 次',ylabel='曲线应力参数 / MPa');ax.grid(alpha=.2,which='both');fig.tight_layout()
        return self.save(fig,'fatigue-curve-'+key,'材料卡原始标定点，寿命定义与适用条件见表；连线用于显示。')

    def load_history(self):
        history=self.case.get('history')
        if not history or not history.get('factors'):return None
        fig,ax=plt.subplots(figsize=(8.1,3.2))
        for name,values in history['factors'].items():ax.plot(history['times_s'],values,label=name)
        ax.set(xlabel='输入载荷时间 / s',ylabel='相对基准载荷的比例');ax.legend(fontsize=8);ax.grid(alpha=.2);fig.tight_layout()
        return self.save(fig,'load-history','用户保存的载荷比例历史，作用于边界表中的对应基准载荷；该图不是计算出的响应。')

    def convergence(self,audit):
        steps=[v for v in audit if isinstance(v,dict) and v.get('iterations') and 'time_s' in v
               and isinstance(v['iterations'],list) and isinstance(v['iterations'][-1],dict)
               and 'temperature_change_K' in v['iterations'][-1]]
        if not steps:return None
        cfg=self.case['coupling'];fig,axes=plt.subplots(1,2,figsize=(8.1,3.7))
        axes[0].plot([v['time_s'] for v in steps],[len(v['iterations']) for v in steps],'o-',color=COLORS[0]);axes[0].set(xlabel='物理时间 / s',ylabel='每步耦合迭代次数')
        last=steps[-1]['iterations']
        for key,limit,label,color in [('temperature_change_K',cfg['temperature_tolerance_K'],'温度变化',COLORS[0]),
            ('displacement_relative',cfg['displacement_relative_tolerance'],'位移变化',COLORS[1]),('heatflow_relative',cfg['heatflow_relative_tolerance'],'界面热流变化',COLORS[2])]:
            pairs=[(v['iteration'],max(v[key]/limit,1e-12)) for v in last if v.get(key) is not None]
            if pairs:axes[1].semilogy(*np.array(pairs).T,'o-',ms=3,label=label,color=color)
        axes[1].axhline(1,color='#777',ls='--');axes[1].set(xlabel=f"末物理步 {steps[-1]['time_s']:g} s / 迭代序号",ylabel='残差 / 设定阈值');axes[1].legend(fontsize=8)
        for ax in axes:ax.grid(alpha=.2)
        fig.tight_layout()
        return self.save(fig,'convergence','读取 audit.json 的真实迭代记录；归一化残差不超过 1 表示达到该项阈值。求解收敛不替代网格或时间步独立性检查。')
