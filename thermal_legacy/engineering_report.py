"""Engineering report built exclusively from persisted inputs and solved fields."""
from pathlib import Path
import json
import threading
import numpy as np
from thermal_legacy.material_catalog import CATEGORIES

REPORT_VERSION = 6
REPORT_LOCK = threading.RLock()  # Matplotlib and shared output files are not thread safe.
WORDS = dict(exceeded='超出限值',within_limits='在已设限值内',not_assessed='未评估',
             review_required='需复核',within_configured_limits='在已设限值内')


def number(value, scale=1):
    return '未提供' if value is None else f'{float(value)*scale:.6g}'


def position(value):
    return '未定位' if value is None else '('+', '.join(f'{v*1000:.3f}' for v in value)+') mm'


def figure_assets(job,cfg,audit):
    """No synthetic fields, contour clipping, smoothing or time extrapolation."""
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt, colors, cm, font_manager
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    font=Path('C:/Windows/Fonts/simhei.ttf')
    if font.exists():font_manager.fontManager.addfont(str(font))
    style={'font.family':'SimHei' if font.exists() else 'DejaVu Sans','axes.unicode_minus':False,
           'font.size':10,'axes.spines.top':False,'axes.spines.right':False}
    dest=job/'report-assets';dest.mkdir(exist_ok=True)
    with np.load(job/'mesh.npz') as mesh:
        p=mesh['points']*1000;faces=mesh['boundary_triangles'];tets=mesh['tets']
    T=np.load(job/'temperatures.npy',mmap_mode='r')
    times=np.asarray([s['time_s'] for s in audit['stats']]);nframes=len(T)
    if len(times)!=nframes:raise ValueError('报告帧数与时间数据不一致')
    result={}
    with plt.rc_context(style):
        def save(fig,name):
            try:fig.savefig(dest/(name+'.png'),dpi=160,facecolor='white',bbox_inches='tight',pad_inches=.15)
            finally:plt.close(fig)
            return 'report-assets/'+name+'.png'

        def cloud(values,name,title,unit,lo=None,hi=None):
            lo=float(values.min()) if lo is None else float(lo);hi=float(values.max()) if hi is None else float(hi)
            if hi<=lo:hi=lo+max(abs(lo)*1e-6,1e-6)
            norm=colors.Normalize(lo,hi);fig=plt.figure(figsize=(7.4,3.5))
            fc=cm.turbo(norm(values[faces].mean(axis=1)))
            for i,azim in enumerate((-60,120)):
                ax=fig.add_subplot(1,2,i+1,projection='3d')
                ax.add_collection3d(Poly3DCollection(p[faces],facecolors=fc,edgecolors='none',rasterized=True))
                for axis,dimension in zip(('x','y','z'),range(3)):
                    getattr(ax,'set_'+axis+'lim')(p[:,dimension].min(),p[:,dimension].max())
                    getattr(ax,'set_'+axis+'label')(axis.upper()+' (mm)',fontsize=8,labelpad=0)
                ax.set_box_aspect(np.maximum(np.ptp(p,axis=0),1e-6));ax.view_init(elev=18,azim=azim)
                ax.tick_params(labelsize=7,pad=0);ax.set_title('视角 '+str(i+1),fontsize=9)
            fig.suptitle(title,fontsize=12,y=.98)
            fig.subplots_adjust(left=.02,right=.88,bottom=.06,top=.87,wspace=.04)
            fig.colorbar(cm.ScalarMappable(norm=norm,cmap='turbo'),cax=fig.add_axes([.91,.18,.018,.60]),label=unit)
            return save(fig,name)

        thermal_frame=int(np.unravel_index(np.abs(T-cfg['initial_C']).argmax(),T.shape)[0])
        result['temperature']=dict(time_s=float(times[thermal_frame]),cloud=cloud(T[thermal_frame],'temperature',
            f'温度分布 · t = {times[thermal_frame]:g} s','°C',T.min(),T.max()))
        fig,ax=plt.subplots(figsize=(7.4,2.5),layout='constrained')
        for key,label,color in [('maximum_C','最高','#cf4a32'),('average_C','体积平均','#225ea8'),('minimum_C','最低','#319b75')]:
            ax.plot(times,[s[key] for s in audit['stats']],label=label,color=color,lw=1.6)
        for key,label in [('minimum_C','最低限值'),('maximum_C','最高限值')]:
            limit=(cfg.get('design_limits') or {}).get(key)
            if limit is not None:ax.axhline(limit,ls='--',lw=.8,label=label,color='#777777')
        ax.set(xlabel='时间 (s)',ylabel='温度 (°C)');ax.grid(alpha=.2);ax.legend(fontsize=8,ncol=3)
        result['temperature']['curve']=save(fig,'temperature-history')
        if audit.get('structural'):
            u=np.memmap(job/'displacement.bin',dtype='<f4',mode='r',shape=(nframes,len(p),3))
            stress=np.memmap(job/'stress.bin',dtype='<f4',mode='r',shape=(nframes,len(tets),6))
            for name,key,unit,scale in [('displacement','maximum_displacement_m','mm',1000),('stress','maximum_von_mises_Pa','MPa',1e-6)]:
                history=np.asarray([s[key] for s in audit['stats']])*scale;f=int(history.argmax())
                if name=='displacement':values=np.linalg.norm(u[f],axis=1)*scale
                else:
                    s=stress[f].astype(float)
                    vm=np.sqrt(.5*((s[:,0]-s[:,1])**2+(s[:,1]-s[:,2])**2+(s[:,2]-s[:,0])**2)+3*np.sum(s[:,3:]**2,axis=1))
                    values=np.zeros(len(p));np.maximum.at(values,tets.ravel(),np.repeat(vm*scale,4))
                title='热弹性位移' if name=='displacement' else 'von Mises 等效应力'
                result[name]=dict(time_s=float(times[f]),cloud=cloud(values,name,f'{title} · t = {times[f]:g} s',unit,0,history.max()))
                fig,ax=plt.subplots(figsize=(7.4,2.5),layout='constrained');ax.plot(times,history,color='#4b53ac',lw=1.8)
                ax.set(xlabel='时间 (s)',ylabel=title+'峰值 ('+unit+')');ax.grid(alpha=.2)
                result[name]['curve']=save(fig,name+'-history')
            del u,stress
    (dest/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def build_report(job,cfg,metadata,audit):
    stats=audit.get('stats',[]);structural=audit.get('structural');assessment=audit.get('assessment') or {}
    figures=figure_assets(job,cfg,audit) if (job/'mesh.npz').exists() and (job/'temperatures.npy').exists() and stats else {}
    lines=['# Thermal Studio · 加工前热评估报告','',f'算例：{cfg["name"]}',f'模型：{metadata.get("name",cfg["model_id"])}',
        f'计算记录：{job.name} / 报告版本 {REPORT_VERSION}','',
        '本报告以数字模型预测指定工况下的温度分布、热变形和热应力，用于加工前识别过热、低温、装配偏移及材料强度风险，支持结构/材料选择和样机验证。目标是规避热失效、缩短研发周期、降低样机成本，并为产品在高低温工况下稳定可靠运行提供依据；收益尚未量化，需要与实际项目数据对照。','',
        '## 计算结论',f'综合状态：{WORDS.get(assessment.get("status"),"尚未评估")}',
        '结论仅覆盖本次材料、边界、支撑和已设限值，不等于全温区长期可靠性保证。']
    if stats:
        lines += [f'实际覆盖：0–{stats[-1]["time_s"]:g} s；全程温度 {min(s["minimum_C"] for s in stats):.3f}–{max(s["maximum_C"] for s in stats):.3f} °C；末帧体积平均 {stats[-1]["average_C"]:.3f} °C。',
            f'环境设定：{cfg["ambient_C"]:g} °C；初始温度：{cfg["initial_C"]:g} °C。环境设定值与工件实际达到的温度分开记录。']
    if structural:
        lines += [f'最大热弹性位移：{structural["maximum_displacement_m"]*1000:.6g} mm；最大等效应力：{structural["maximum_von_mises_Pa"]/1e6:.6g} MPa。',
            f'主应力范围：{min(s["minimum_principal_Pa"] for s in structural["stats"])/1e6:.6g}–{max(s["maximum_principal_Pa"] for s in structural["stats"])/1e6:.6g} MPa。']
    else:lines += ['热变形/热应力：未启用三维热弹性求解。已有自由膨胀近似不能作为结构强度结论。']
    if structural and not structural.get('small_strain_valid',True):
        lines += ['模型适用性：最大应变超过本模型1%的验证范围，位移/应力结果需非线性模型复核，不可据此作最终强度判定。']
    for warning in audit.get('warnings',[]):
        if '下冲' in warning:lines += ['数值质量待复核：'+warning]
    exceeded=[c for c in assessment.get('checks',[]) if c['status']=='exceeded']
    for c in exceeded:lines += [f'- 超限：{c["name"]}，{number(c.get("value"))} {c.get("unit","")}，限值 {number(c.get("limit"))}；t={number(c.get("time_s"))} s；位置 {position(c.get("position_m"))}。']
    if not exceeded:lines += ['未检出已配置检查项的数值超限；未评估项及模型适用性仍需逐项核对。']
    lines += ['', '## 高低温工况覆盖','本报告对应一个独立算例。未运行的相反温区、稳态热浸、循环载荷及长期老化均未验证；不同高低温算例应分别保留报告，不能由单一工况外推。',
        '', '## 工程用途','- 设计筛查：利用热点、冷点和位移峰值确定需要复核的部位。',
        '- 打样验证：在预测峰值位置与参考位置布置测温/位移测点，对照温升、冷却和变形曲线。',
        '- 研发管理：记录材料、工况、网格、限值和结果，后续比较修改前后性能及实际打样次数。']
    lines += ['','<!-- pagebreak -->','## 几何、材料与边界条件',
        '几何包围尺寸：'+' × '.join(f'{d*1000:.5g}' for d in metadata.get('dimensions_m',[]))+' mm；坐标及峰值位置均相对导入模型坐标系。',
        f'分析类型：{"稳态" if cfg.get("analysis_mode")=="steady" else "瞬态"}；时长 {cfg["duration_s"]:g} s；计算步长 {cfg["dt_s"]:g} s；保存间隔 {cfg["save_s"]:g} s；网格尺寸 {cfg["mesh_size_m"]*1000:g} mm。',
        f'全局对流 h={cfg["default_h"]:g} W/(m²·K)，环境 {cfg["ambient_C"]:g} °C；辐射 {"开启" if cfg.get("radiation_enabled") else "关闭"}，发射率 {cfg.get("emissivity",.8):g}，辐射环境 {cfg.get("radiation_ambient_C",25):g} °C。',
        f'空气间隙导热 {"开启" if cfg.get("air_gap_enabled",True) else "关闭"}，k={cfg.get("air_gap_k_W_mK",.026):g} W/(m·K)，最大间隙 {cfg.get("air_gap_max_m",.05)*1000:g} mm；面积接触热阻 {cfg.get("contact_resistance_m2K_W",0):g} m²·K/W。',
        f'热源面参与默认对流：{"是" if cfg.get("heat_convection") else "否"}。']
    mats=[('基础实体',cfg['base_material'])]+[(f'材料区域 {r["name"]}',r['material']) for r in cfg.get('regions',[])]+[(f'组件 {r["component_id"]+1}',r['material']) for r in cfg.get('component_materials',[])]
    lines += ['', '| 作用对象 | 材料 | 类别 |','|---|---|---|']
    for scope,m in mats:lines += [f'| {scope} | {m["name"]} | {CATEGORIES.get(m.get("category","other"),"其他")} |']
    for r in cfg.get('regions',[]):lines += [f'区域 {r["name"]} 范围：{position(r["min_m"])} 至 {position(r["max_m"])}。']
    lines+=['','热源：'+('无热源，环境换热驱动。' if not cfg.get('heat_sources') else '')]
    for heat in cfg.get('heat_sources',[]):
        lines += [f'- {heat["name"]}：{heat["power_W"]:g} W，{heat["start_s"]:g}–{heat["end_s"]:g} s；类型 {heat.get("source_type")} / {heat.get("placement")}，{len(heat.get("faces",[]))} 个选中三角面；位置 {position(heat.get("position_m"))}，半径 {heat.get("radius_m",.001)*1000:g} mm。']
        if heat.get('thermostat'):lines += ['温控参数：'+json.dumps(heat['thermostat'],ensure_ascii=False)]
        if heat.get('power_profile'):lines += ['功率曲线 (s,W)：'+', '.join(f'({p["time_s"]:g},{p["power_W"]:g})' for p in heat['power_profile'])]
    for c in cfg.get('cooling',[]):lines += [f'- 散热区 {c["name"]}：{len(c["faces"])} 个面；h={c["h"]:g}，环境 {c["ambient_C"]:g} °C；辐射 {c.get("radiation",False)}，发射率 {c.get("emissivity",.85):g}。']
    if audit.get('components'):
        lines+=['','| 体网格组件 | 体积 (mm³) | 全程最低/最高 (°C) | 末帧体积平均 (°C) |','|---|---|---|---|']
        for c in audit['components']:lines += [f'| {c["component_id"]+1} | {number(c.get("volume_m3"),1e9)} | {number(c.get("minimum_C"))} / {number(c.get("maximum_C"))} | {number(c.get("final_average_C"))} |']
    if structural:
        lines += [f'结构：{"自由状态" if structural["mode"]=="free" else "固定支撑"}；无应力参考温度 {structural["reference_C"]:g} °C。']
        for s in structural.get('supports',[]):lines += [f'- 支撑 {s["name"]}：固定 {s["axes"]} 方向；{s["nodes"]} 个映射节点，映射面积 {s["mapped_area_m2"]:.6g} m²。']
    lines+=['完整选中面编号、组件覆盖及功率/温控数据保存在随报告导出的 config.json。']
    for key,title in [('temperature','温度分布及变化'),('displacement','热变形结果'),('stress','热应力结果')]:
        lines += ['','<!-- pagebreak -->','## '+title]
        fig=figures.get(key)
        if fig:
            lines += [f'代表帧：{fig["time_s"]:g} s。温度选取偏离初始温度最大的保存帧；位移/应力选取对应峰值帧。',
                f'![{title}云图]({fig["cloud"]})',f'![{title}时间曲线]({fig["curve"]})']
            if key=='temperature':lines += ['温标采用全程范围；两视角均为体网格外表面，不能显示遮挡的内部热点。最高/最低统计包含内部节点，平均值按实体体积加权。']
            elif key=='displacement':lines += ['图中几何为未变形形状，以实际位移模长着色（mm）；没有夸大几何位移。结果由三维热弹性方程求解。']
            else:lines += ['应力云图将各节点相邻单元的最大等效应力映射到表面后着色；峰值来自原始体单元。主应力判据使用拉/压强度，von Mises 云图仅用于显示，不能代替玻璃/陶瓷抗裂评估。']
            lines += ['曲线连接已保存结果帧，峰值时刻精度受保存间隔限制。'+('稳态模式曲线仅连接初始与稳态解，不表示真实瞬态过程。' if cfg.get('analysis_mode')=='steady' else '')]
        else:lines += ['未生成该结果场。'+('需通过 Agent 启用热弹性分析并设置材料弹性参数、参考温度及支撑状态，再计算。' if key!='temperature' else '缺少已保存温度数据。')]
    lines += ['','<!-- pagebreak -->','## 热失效风险与限值校核',assessment.get('note','未配置设计限值。'),
        '| 检查项 | 计算值 | 限值 | 结果 |','|---|---|---|---|']
    for c in assessment.get('checks',[]):
        scale=1e-6 if c.get('unit')=='Pa' else 1000 if c.get('unit')=='m' else 1
        unit='MPa' if c.get('unit')=='Pa' else 'mm' if c.get('unit')=='m' else c.get('unit','')
        lines += [f'| {c["name"]} | {number(c.get("value"),scale)} {unit} | {number(c.get("limit"),scale)} {unit} | {WORDS.get(c["status"],c["status"])} |']
    lines += ['', '强度限值采用所选强度除以安全系数 '+number((cfg.get('design_limits') or {}).get('strength_safety_factor',1.5))+'。未给出的限值不会自动判为合格。',
        '', '| 峰值/校核对象 | 时刻 (s) | 位置 XYZ |','|---|---|---|']
    for c in assessment.get('checks',[]):
        if c.get('position_m') is not None:lines += [f'| {c["name"]} | {number(c.get("time_s"))} | {position(c["position_m"])} |']
    if structural:
        f=max(range(len(structural['stats'])),key=lambda i:structural['stats'][i]['maximum_von_mises_Pa'])
        lines += [f'| 全局等效应力峰值 | {number(stats[f]["time_s"])} | {position(structural["stats"][f].get("stress_peak_position_m"))} |']
    for c in assessment.get('checks',[]):
        if c.get('reason'):lines += [f'- {c["name"]}：{c["reason"]}']
    lines += ['','<!-- pagebreak -->','## 材料物性与数据依据',
        '参数为本次算例实际输入。参考预设不能替代具体牌号数据；材料使用温区、物性有效温区和玻璃化转变温度分别记录。Tg未作为潜热熔化模型使用。']
    for scope,m in mats:
        lines += ['',f'## {scope}：{m["name"]}',
            '| 热学参数 | 输入值 | 力学参数 | 输入值 |','|---|---|---|---|',
            f'| 导热系数 W/(m·K) | {number(m["k"])} | 弹性模量 GPa | {number(m.get("young_modulus_Pa"),1e-9)} |',
            f'| 密度 kg/m³ | {number(m["rho"])} | 泊松比 | {number(m.get("poisson_ratio"))} |',
            f'| 比热容 J/(kg·K) | {number(m["cp"])} | 屈服强度 MPa | {number(m.get("yield_strength_Pa"),1e-6)} |',
            f'| 线膨胀系数 1/K | {number(m.get("thermal_expansion_CTE_per_K",0))} | 抗拉强度 MPa | {number(m.get("tensile_strength_Pa"),1e-6)} |',
            f'| 物性参考温度 °C | {number(m.get("reference_temperature_C"))} | 抗压强度 MPa | {number(m.get("compressive_strength_Pa"),1e-6)} |',
            f'物性有效温区：{number(m.get("valid_min_C"))} 至 {number(m.get("valid_max_C"))} °C；使用温区：{number(m.get("service_min_C"))} 至 {number(m.get("service_max_C"))} °C；Tg：{number(m.get("glass_transition_C"))} °C。',
            f'强度判据：{dict(von_mises="von Mises / 屈服",principal="最大主拉/主压应力",none="不评估").get(m.get("strength_criterion","von_mises"))}。',
            '物性说明：'+(m.get('property_notes') or '未提供牌号、测试条件及物性有效温区，请实测核对。'),
            '数据来源：'+(m.get('data_source') or '用户输入/历史预设，尚未提供出处。')]
        if m.get('phase_change'):lines += ['相变输入：'+json.dumps(m['phase_change'],ensure_ascii=False)]
    energy=audit.get('energy',{});gap=audit.get('air_gap',{})
    lines += ['','<!-- pagebreak -->','## 数值质量与验证计划',
        f'网格：{audit.get("nodes",0)} 个节点，{audit.get("tetrahedra",0)} 个四面体；最低形状质量 {number(audit.get("minimum_quality"))}。',
        f'计算设备：{audit.get("device",{}).get("device","cpu")}。']
    if cfg.get('analysis_mode')=='steady':lines += [f'稳态平衡残差：{number(energy.get("steady_residual"))} W。']
    else:lines += [f'热源输入能量：{number(energy.get("input_energy_J"))} J；边界净流出能量：{number(energy.get("convective_energy_J"))} J（负值表示环境向工件供热）；能量平衡误差：{number(energy.get("energy_balance_error_J"))} J。']
    lines += [f'空气间隙有效耦合：{gap.get("pairs",0)} 对；总热导 {number(gap.get("conductance_W_K",0))} W/K。']
    if structural:lines += [f'最大相对力平衡残差：{number(structural.get("maximum_relative_equilibrium_residual"))}；最大应变尺度：{number(structural.get("maximum_strain"))}；小应变检查：{"在1%范围内" if structural.get("small_strain_valid") else "超出1%，需非线性复核"}。']
    for w in audit.get('warnings',[]):lines += ['- '+w]
    lines += ['', '## 模型边界','- 当前为单向耦合、准静态、小变形、各向同性常物性热弹性。没有计算塑性、蠕变、疲劳、粘弹性、老化寿命或机械接触。',
        '- 共节点视为粘结；不连通实体之间不传递机械力。固定尖角可能出现随网格加密升高的应力集中。',
        '- 玻璃/陶瓷的主应力筛查不包含缺陷统计或裂纹扩展；高分子需要温度/时间/湿度依赖的材料数据。',
        '', '## 后续验证与设计决策','- 对热点、约束边缘及薄壁位置加密网格，减小时间步长与保存间隔，比较温度、位移和应力峰值的收敛。',
        '- 在报告给出的峰值坐标和实体参考点布置热电偶或位移测点，校准材料物性、换热与真实安装约束。',
        '- 若超限，优先复核热源功率/接触、散热条件、约束和材料适用范围，再比较结构或材料修改方案。',
        '- 完成独立高温、低温、热浸和循环工况；需要寿命结论时，增加材料专用失效模型及试验依据。',
        '- 记录优化前后峰值、样机次数、费用与周期，才能量化降本和研发效率提升。','',
        '交付内容：本 PDF、可读报告、config.json、audit.json、assessment.json、history.csv，以及 HDF5/XDMF 三维字段。']
    if audit.get('surface_evaluation'):
        lines += surface_report(job,cfg,audit)
    return '\n'.join(lines)+'\n'


def surface_report(job,cfg,audit):
    e=audit['surface_evaluation'];rows=e['rows'];last=rows[-1]
    lines=['','<!-- pagebreak -->','## 模块接触面专项评价',
        f'实际积分面积 {e["area_m2"]*1e6:.5f} mm²；平均温度按接触面面积加权；温差为同一接触区的最高减最低温度。',
        '平面度代理量：对变形后的接触面按面积权重拟合平面，取法向残差峰谷差，去除整体平移与倾斜。该量不是整个工件的最大位移，也不包含初始加工误差。',
        '| 接触面末帧指标 | 计算结果 |','|---|---|',
        f'| 最高 / 平均 / 最低温度 | {last["maximum_C"]:.4f} / {last["average_C"]:.4f} / {last["minimum_C"]:.4f} °C |',
        f'| 温度均匀性 ΔT | {last["delta_C"]:.4f} K |',
        f'| 平面度代理量 | {number(last.get("flatness_m"),1000)} mm |']
    if cfg.get('analysis_mode')=='steady':
        lines += [f'| 散热热阻（铝侧接触面至环境） | {number(last["thermal_resistance_K_W"])} K/W |',
            '稳态结果不提供实际到达稳态的时间；时间轴终点仅用于存放稳态场。']
    else:
        crossing=e.get('first_limit_crossing')
        lines += [f'温度限值：{number(e.get("temperature_limit_C"))} °C；'+
            (f'首次达到限值的保存帧区间 {crossing["bracket_s"]} s，线性插值估计 {number(crossing["linear_estimate_s"])} s。' if crossing else '在本次已保存帧中未达到限值，不向时窗外推算。')]
    if audit.get('continuation'):lines += ['初始场继承：'+audit['continuation']['parent_job']+'；使用完全相同网格的末帧节点温度。']
    if cfg.get('ambient_profile'):lines += ['环境温度曲线 (s,°C)：'+', '.join(f'({p["time_s"]:g},{p["ambient_C"]:g})' for p in cfg['ambient_profile'])]
    if len(rows)>2:
        import matplotlib.pyplot as plt
        with plt.rc_context({'font.family':'SimHei','axes.unicode_minus':False,'font.size':10}):
            fig,ax=plt.subplots(2,1,figsize=(7.4,4.9),layout='constrained')
            times=[r['time_s'] for r in rows]
            for key,label in [('maximum_C','接触面最高'),('average_C','接触面平均'),('minimum_C','接触面最低')]:
                ax[0].plot(times,[r[key] for r in rows],label=label)
            if e.get('temperature_limit_C') is not None:ax[0].axhline(e['temperature_limit_C'],ls='--',c='red',label='温度限值')
            ax[0].set_ylabel('温度 (°C)');ax[0].legend(fontsize=8);ax[0].grid(alpha=.2)
            if last.get('flatness_m') is not None:
                ax[1].plot(times,[r['flatness_m']*1000 for r in rows],label='拟合平面残差峰谷差')
                if e.get('flatness_limit_m') is not None:ax[1].axhline(e['flatness_limit_m']*1000,ls='--',c='red',label='翘曲限值')
                ax[1].set_ylabel('翘曲 (mm)')
            else:
                ax[1].plot(times,[r['delta_C'] for r in rows],label='接触区温差');ax[1].set_ylabel('温差 (K)')
            ax[1].set_xlabel('时间 (s)');ax[1].legend(fontsize=8);ax[1].grid(alpha=.2)
            path=job/'report-assets'/'contact-history.png'
            fig.savefig(path,dpi=160);plt.close(fig)
        lines += ['![接触面温度及翘曲](report-assets/contact-history.png)']
    return lines


def write_engineering_report(job,cfg,metadata,audit,renderer):
    with REPORT_LOCK:
        text=build_report(job,cfg,metadata,audit)
        # Atomic PDF replacement: a failed renderer must not serve a partial file.
        tmp=job/'report.pending.pdf';renderer(tmp,text);tmp.replace(job/'report.pdf')
        (job/'report.md').write_text(text,encoding='utf-8')
        (job/'report-version.txt').write_text(str(REPORT_VERSION),encoding='utf-8')


def ensure_current_report(job):
    """Upgrade presentation of saved results; never change a simulation input."""
    with REPORT_LOCK:
        version=job/'report-version.txt'
        if version.exists() and version.read_text(encoding='utf-8')==str(REPORT_VERSION):return
        from thermal_legacy.solver import write_report, MODELS
        import zipfile
        cfg=json.loads((job/'config.json').read_text(encoding='utf-8'))
        audit=json.loads((job/'audit.json').read_text(encoding='utf-8'))
        write_report(job,cfg,MODELS/cfg['model_id'],audit,audit.get('energy',{}))
        archive=job/'result.zip'
        if archive.exists():
            temp=job/'result.pending.zip'
            with zipfile.ZipFile(archive) as old,zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as new:
                for info in old.infolist():
                    if info.filename in ('report.pdf','report.md') or info.filename.startswith('report-assets/'):continue
                    # Stream large solved fields rather than duplicating them in memory.
                    import shutil
                    with old.open(info) as source,new.open(info.filename,'w') as target:shutil.copyfileobj(source,target)
                for name in ('report.pdf','report.md'):new.write(job/name,name)
                for asset in (job/'report-assets').glob('*'):new.write(asset,'report-assets/'+asset.name)
            temp.replace(archive)
