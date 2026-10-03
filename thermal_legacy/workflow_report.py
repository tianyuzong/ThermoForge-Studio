"""Compare solved cases and export a report, without fabricating missing analyses."""
import csv, json, shutil, zipfile, re
import numpy as np
from thermal_legacy.runtime import JOBS,read_json,write_json


def input_summary(c,a):
    """Describe actual solved inputs, without large face-ID arrays."""
    def number(v):return '未提供' if v is None else f'{v:.6g}'
    def selection(item):
        box=item.get('surface_box')
        if box:
            return 'XYZ范围(mm) '+', '.join(f'{lo*1000:g}至{hi*1000:g}' for lo,hi in zip(box['min_m'],box['max_m']))
        if item.get('position_m') is not None:
            return '中心(m) '+str(item['position_m'])+'，半径(m) '+number(item.get('radius_m'))
        return f"表面三角面 {len(item.get('faces') or [])} 个，编号见配置包"
    m=c['base_material'];structural=c.get('structural') or {}
    lines=[f"材料参数：导热系数 {number(m.get('k'))} W/(m·K)，密度 {number(m.get('rho'))} kg/m³，比热 {number(m.get('cp'))} J/(kg·K)；弹性模量 {number(m.get('young_modulus_Pa'))} Pa，泊松比 {number(m.get('poisson_ratio'))}，线膨胀系数 {number(m.get('thermal_expansion_CTE_per_K'))} /K。",
        f"边界与初值：初始温度 {number(c.get('initial_C'))} °C，默认环境 {number(c.get('ambient_C'))} °C，默认换热系数 {number(c.get('default_h'))} W/(m²·K)，全局辐射 {'开启' if c.get('radiation_enabled') else '关闭'}。"+('使用父算例完整温度场，标量初温不覆盖它。' if c.get('initial_from_job') else ''),
        f"力学模式：{ {'free':'自由热弹性','off':'未启用','supported':'指定约束'}.get(structural.get('mode'),structural.get('mode','未启用'))}；无应力参考温度 {number(structural.get('reference_C'))} °C；约束组 {len(structural.get('supports') or [])}。"]
    sources=c.get('heat_sources') or []
    if not sources:lines.append('热源：无。')
    for source in sources:
        lines.append(f"热源 {source.get('name','')}：{number(source.get('power_W'))} W，开启窗口 {number(source.get('start_s'))}至{number(source.get('end_s'))} s；{selection(source)}。")
        for key,label in [('power_profile','功率时间曲线'),('thermostat','温控设置')]:
            if source.get(key):lines.append(label+'：'+json.dumps(source[key],ensure_ascii=False)+'。')
    cooling=c.get('cooling') or []
    if cooling:
        grouped={}
        for v in cooling:
            key=(v.get('h'),v.get('ambient_C'),bool(v.get('radiation')))
            grouped.setdefault(key,[]).append(selection(v))
        lines+=['| 局部换热作用对象 | h W/(m²·K)；环境 °C；辐射 |','|---|---|']
        lines += ['| '+'；'.join(objects)+f" | {number(h)}；{number(ambient)}；{'开启' if radiation else '关闭'} |" for (h,ambient,radiation),objects in grouped.items()]
    if c.get('ambient_profile'):
        lines.append('环境曲线（时间 s / 温度 °C）：'+'；'.join(f"{number(v.get('time_s'))} / {number(v.get('ambient_C'))}" for v in c['ambient_profile'])+'。全部转折点自动保存。')
    e=a.get('surface_evaluation') or {}
    if c.get('surface_evaluation'):
        lines.append('接触面评估：'+selection(c['surface_evaluation'])+'；实际积分面积 '+number(e.get('area_m2'))+' m²。')
    return lines


def cycle_range(job,cfg,audit):
    t=np.load(job/'temperatures.npy',mmap_mode='r')
    with np.load(job/'mesh.npz') as mesh:p=mesh['points'];cells=mesh['tets']
    nf,ne=len(t),len(cells)
    if nf>120 or nf*nf*ne>2e9:
        return dict(status='not_completed',reason='同位置张量差比较超过当前后处理资源预算')
    s=np.memmap(job/'stress.bin',dtype='<f4',mode='r',shape=(nf,ne,6))
    best=(-1,0,0,0);strain_best=(-1,0,0,0,0)
    E=cfg['base_material'].get('young_modulus_Pa');nu=cfg['base_material'].get('poisson_ratio');alpha=cfg['base_material']['thermal_expansion_CTE_per_K']
    if not E or nu is None or cfg.get('regions') or cfg.get('component_materials'):
        return dict(status='not_completed',reason='此比较目前要求单一均匀线弹性材料')
    for start in range(0,ne,8192):
        stop=min(ne,start+8192);block=np.asarray(s[:,start:stop],dtype=float)
        for i in range(nf):
            for j in range(i):
                d=block[i]-block[j]
                vm=np.sqrt(.5*((d[:,0]-d[:,1])**2+(d[:,1]-d[:,2])**2+(d[:,2]-d[:,0])**2)+3*np.sum(d[:,3:]**2,axis=1))
                k=int(vm.argmax())
                if vm[k]>best[0]:best=(float(vm[k]),j,i,start+k)
        normal=block[:,:,:3]
        eps=((1+nu)*normal-nu*normal.sum(axis=2)[:,:,None])/E
        eps+=alpha*(t[:,cells[start:stop]].mean(axis=2)-cfg['structural']['reference_C'])[:,:,None]
        span=np.ptp(eps,axis=0);k,axis=np.unravel_index(span.argmax(),span.shape)
        if span[k,axis]>strain_best[0]:
            strain_best=(float(span[k,axis]),start+k,int(axis),int(eps[:,k,axis].argmin()),int(eps[:,k,axis].argmax()))
    times=[v['time_s'] for v in audit['stats']]
    return dict(status='completed',scope='same_cell_saved_frames',equivalent_stress_tensor_range_Pa=best[0],
        time_pair_s=[times[best[1]],times[best[2]]],stress_position_m=p[cells[best[3]]].mean(axis=0).tolist(),
        equivalent_deviatoric_elastic_strain_range=best[0]*2*(1+nu)/(3*E),
        maximum_total_normal_strain_range=strain_best[0],strain_axis='xyz'[strain_best[2]],
        strain_minimum_maximum_times_s=[times[strain_best[3]],times[strain_best[4]]],
        strain_position_m=p[cells[strain_best[1]]].mean(axis=0).tolist())


def generate(folder,workflow):
    from thermal_legacy.engineering_report import REPORT_LOCK
    with REPORT_LOCK:_generate(folder,workflow)


def report_identity(workflow):
    title=workflow['name'].removesuffix('（整体确认后执行）').removesuffix('计划')
    # The planning Agent may have included a stage notice among limitations.
    # Keep the original plan intact; completed reports use actual case statuses.
    limitations=[v for v in workflow.get('limitations',[]) if not v.startswith('本输出仅为待整体确认的执行计划')]
    return title,limitations


def report_inputs(c,a,workflow):
    """Reference a reported parent only after checking unchanged inputs."""
    parent_id=c.get('initial_from_job')
    parent=next((v for v in workflow['cases'] if v.get('job_id')==parent_id and v['status']=='completed'),None) if parent_id else None
    if parent:
        source=read_json(JOBS/parent_id/'config.json')
        varying={'name','analysis_mode','dt_s','duration_s','save_s','initial_from_job','cooling'}
        same=all(c.get(k)==source.get(k) for k in set(c)|set(source) if k not in varying)
        before=source.get('cooling') or [];after=c.get('cooling') or []
        if same and len(before)==len(after) and all(all(x.get(k)==y.get(k) for k in set(x)|set(y) if k!='h') for x,y in zip(before,after)):
            changes=[]
            for x,y in zip(before,after):
                if x.get('h')==y.get('h'):continue
                box=y.get('surface_box')
                if not box:return input_summary(c,a)
                bounds=', '.join(f'{lo*1000:g}至{hi*1000:g}' for lo,hi in zip(box['min_m'],box['max_m']))
                changes.append(f"XYZ范围(mm) {bounds}：h由{x['h']:g}改为{y['h']:g} W/(m²·K)")
            return [f"已逐字段核对：材料、热源、默认环境、网格、自由热弹性及接触面评估与“{parent['name']}”相同，详细值见该工况输入依据。",'局部换热变化：'+('；'.join(changes) if changes else '无')+'。续算温度场及本工况时间参数见本节执行记录。']
    return input_summary(c,a)


def _generate(folder,workflow):
    from thermal_legacy.solver import write_report_pdf
    assets=folder/'report-assets';assets.mkdir(exist_ok=True)
    rows=[];sections=[];configs=[]
    def fmt(v,scale=1):return '未评估' if v is None else f'{v*scale:.6g}'
    for case in workflow['cases']:
        if case['status']!='completed':
            sections.extend(['## '+case['name'],'未完成：'+case.get('detail',case['status'])]);continue
        p=JOBS/case['job_id'];c=read_json(p/'config.json');a=read_json(p/'audit.json')
        e=a.get('surface_evaluation') or {};last=(e.get('rows') or [{}])[-1];st=a.get('structural') or {}
        stress_frames=[v for v in a.get('stats',[]) if v.get('maximum_von_mises_Pa') is not None]
        stress_peak=max(stress_frames,key=lambda v:v['maximum_von_mises_Pa']) if stress_frames else {}
        crossing=e.get('first_limit_crossing') or {}
        row=dict(case=case['name'],job_id=case['job_id'],reused=case.get('reused',False),mode=c['analysis_mode'],
            contact_maximum_C=e.get('maximum_C'),final_contact_average_C=last.get('average_C'),
            final_contact_delta_C=last.get('delta_C'),steady_R_K_W=last.get('thermal_resistance_K_W') if c['analysis_mode']=='steady' else None,
            maximum_contact_flatness_mm=(e['maximum_flatness_m']*1000 if e.get('maximum_flatness_m') is not None else None),
            maximum_stress_MPa=(st['maximum_von_mises_Pa']/1e6 if st.get('maximum_von_mises_Pa') is not None else None),
            maximum_stress_time_s=stress_peak.get('time_s'),
            crossing_s=crossing.get('linear_estimate_s'),
            crossing_bracket_start_s=(crossing.get('bracket_s') or [None,None])[0],
            crossing_bracket_end_s=(crossing.get('bracket_s') or [None,None])[-1])
        rows.append(row)
        configs.append(c)
        sections+=['<!-- pagebreak -->','## '+case['name'],
            f"结果ID：{case['job_id']}。{'复用已完成结果' if case.get('reused') else '本任务执行'}。网格 {c['mesh_size_m']*1000:g} mm，计算步长 {c['dt_s']:g} s，保存间隔 {c['save_s']:g} s。",
            '材料：'+c['base_material']['name']+'。数据来源：'+str(c['base_material'].get('data_source') or '未提供')+'。',
            '| 指标 | 计算值 |','|---|---|',
            '| 接触面最高温度 °C | '+fmt(row['contact_maximum_C'])+' |',
            '| 末帧接触面面积平均温度 °C | '+fmt(row['final_contact_average_C'])+' |',
            '| 末帧接触面温差 K | '+fmt(row['final_contact_delta_C'])+' |',
            '| 稳态热阻 K/W | '+fmt(row['steady_R_K_W'])+' |',
            '| 保存帧最大接触面翘曲 mm | '+fmt(row['maximum_contact_flatness_mm'])+' |',
            '| 保存帧最大等效应力 MPa | '+fmt(row['maximum_stress_MPa'])+' |',
            '| 最大等效应力对应保存时刻 s | '+fmt(row['maximum_stress_time_s'])+('（稳态标签）' if c['analysis_mode']=='steady' else '')+' |',
            '| 首次达到设定温度限值的插值时刻 s | '+fmt(row['crossing_s'])+' |']
        if crossing:
            sections.append('首次达限区间：'+fmt(row['crossing_bracket_start_s'])+'至'+fmt(row['crossing_bracket_end_s'])+' s；使用相邻保存帧接触面最高温度作线性插值，不能视为精确越限时刻。')
        elif c['analysis_mode']!='steady' and e.get('temperature_limit_C') is not None:
            sections.append(f"已保存帧未达到{e['temperature_limit_C']:g} °C；不外推达限时刻。")
        if e.get('temperature_limit_C') is not None:
            sections.append(f"接触面温度目标 {e['temperature_limit_C']:g} °C："+('在限值内' if e['maximum_C']<=e['temperature_limit_C'] else '超出限值')+'。')
        if e.get('flatness_limit_m') is not None and e.get('maximum_flatness_m') is not None:
            sections.append(f"接触面翘曲目标 {e['flatness_limit_m']*1000:g} mm："+('在本次约束假设的限值内' if e['maximum_flatness_m']<=e['flatness_limit_m'] else '超出限值')+'。')
        for warning in a.get('warnings',[]):sections.append('- '+warning)
        verification=case.get('verification',{})
        energy=verification.get('energy',{})
        balance=energy.get('steady_residual') if c['analysis_mode']=='steady' else energy.get('energy_balance_error_J')
        unit='W' if c['analysis_mode']=='steady' else 'J'
        sections.append(f"结果校核：保存 {verification.get('saved_frames','未知')} 帧；环境转折点完整性：{'通过' if verification.get('all_profile_knots_saved') else '未验证'}；能量/功率平衡残差 {fmt(balance)} {unit}；最大相对力学平衡残差 {fmt(verification.get('maximum_relative_equilibrium_residual'))}。")
        if verification.get('parent_job'):sections.append('温度场来源：'+verification['parent_job']+'；节点及单元连接一致，保存初帧最大温差 '+fmt(verification.get('initial_field_max_difference_C'))+' °C。')
        for name in ('temperature','temperature-history','displacement','stress','contact-temperature','contact-flatness'):
            if c['analysis_mode']=='steady' and name in ('temperature-history','contact-temperature','contact-flatness'):
                continue  # Initial/steady endpoints are not a transient history.
            original=p/'report-assets'/(name+'.png')
            if original.is_file():
                target=assets/(case['key']+'-'+name+'.png');shutil.copy2(original,target)
                sections.append(f'![{case["name"]} {name}](report-assets/{target.name})')
        if c.get('ambient_profile') and st and (p/'stress.bin').exists():
            comparison=cycle_range(p,c,a);write_json(folder/(case['key']+'-cycle-range.json'),comparison)
            if comparison['status']=='completed':
                sections.append('<!-- pagebreak -->')
                sections.append('## 同位置循环范围及定义')
                sections.append('同位置循环比较仅覆盖已保存时刻；不计算疲劳寿命。'
                    f"最大同单元应力张量差等效值 {comparison['equivalent_stress_tensor_range_Pa']/1e6:.6g} MPa，时刻 {comparison['time_pair_s']} s。"
                    f"相应偏应变等效弹性范围 {comparison['equivalent_deviatoric_elastic_strain_range']*1e6:.6g} 微应变；最大同方向总正应变范围 {comparison['maximum_total_normal_strain_range']*1e6:.6g} 微应变，方向 {comparison['strain_axis']}。")
                coordinate=lambda key:'['+', '.join(f'{v*1000:.5g}' for v in comparison[key])+'] mm'
                sections.append('应力范围位置XYZ：'+coordinate('stress_position_m')+'；总正应变范围位置XYZ：'+coordinate('strain_position_m')+'，最小/最大总正应变对应时刻 '+str(comparison['strain_minimum_maximum_times_s'])+' s。两种指标分别在其最大范围所在单元报告。')
                sections.append('应力范围定义：同一单元、固定全局XYZ坐标系下，对任意两保存帧的应力张量相减，再求差张量的von Mises等效值。偏弹性应变范围由该应力差等效值乘2(1+ν)/(3E)得到，属于机械弹性范围。总正应变包含机械弹性正应变和α(T-Tref)，逐单元、逐固定XYZ方向取保存帧最大值减最小值。')
            else:sections.append('同位置循环范围未完成：'+comparison['reason'])
        sections+=['## 本工况输入依据']+report_inputs(c,a,workflow)
        dest=folder/'cases'/case['key'];dest.mkdir(parents=True,exist_ok=True)
        for name in ('config.json','audit.json','assessment.json','history.csv','report.pdf'):
            if (p/name).is_file():shutil.copy2(p/name,dest/name)
    title,limitations=report_identity(workflow)
    geometry=workflow['geometry'];topology=geometry.get('topology') or {}
    text=['# '+title,'多工况综合热仿真报告',
        '模型：'+workflow['geometry'].get('name','')+'。尺寸：'+' × '.join(f'{v*1000:.4g}' for v in workflow['geometry']['dimensions_m'])+' mm。',
        '源文件SHA256：'+str(workflow['geometry']['source_sha256']),
        f"几何校核：源单位{geometry.get('units','未提供')}，封闭表面{'通过' if topology.get('closed') else '未验证'}，边界开口{topology.get('boundary_edges','未知')}条，非流形边{topology.get('non_manifold_edges','未知')}条。",
        '本报告基于实际完成的有限元结果。温度、应力与翘曲峰值覆盖已保存帧；不代表连续时间峰值。稳态时长字段不表示达到稳态耗时。',
        '接触面翘曲采用面积加权变形平面拟合并去除平移、倾斜。全局位移不能代替接触面翘曲。安全系数、疲劳与装配可靠性仅在所需数据及模型完整时评估。',
        '## 工况执行记录','| 工况 | 状态 | 结果来源 |','|---|---|---|']
    status_labels={'completed':'已完成','failed':'未完成','skipped':'依赖未完成，已跳过'}
    text += ['| '+c['name']+' | '+status_labels.get(c['status'],c['status'])+' | '+('复用 '+c['job_id'] if c.get('reused') else c.get('job_id') or '未计算')+' |' for c in workflow['cases']]
    text+=['## 指标对比','| 工况 | 接触 Tmax °C | 稳态 R K/W | 翘曲 mm | 应力 MPa | 达限时刻 s |','|---|---|---|---|---|---|']
    text+=['| '+' | '.join([r['case'],fmt(r['contact_maximum_C']),fmt(r['steady_R_K_W']),fmt(r['maximum_contact_flatness_mm']),fmt(r['maximum_stress_MPa']),fmt(r['crossing_s'])])+' |' for r in rows]
    from thermal_legacy.workflows import signature
    comparisons=[]
    for i,left in enumerate(configs):
        for j,right in enumerate(configs[:i]):
            for parameter,label in [('mesh_size_m','网格尺寸'),('dt_s','计算步长')]:
                if left[parameter]==right[parameter]:continue
                stripped=dict(left);stripped[parameter]=right[parameter]
                if signature(stripped)!=signature(right):continue
                differences={key:abs(rows[i][key]-rows[j][key]) for key in ['contact_maximum_C','steady_R_K_W','maximum_contact_flatness_mm','maximum_stress_MPa','crossing_s'] if rows[i][key] is not None and rows[j][key] is not None}
                comparisons.append(dict(parameter=parameter,cases=[rows[j]['case'],rows[i]['case']],absolute_differences=differences))
                metric_labels={'contact_maximum_C':'接触面最高温度(°C)','steady_R_K_W':'稳态热阻(K/W)','maximum_contact_flatness_mm':'翘曲(mm)','maximum_stress_MPa':'等效应力(MPa)','crossing_s':'达限时间(s)'}
                text.append(label+'敏感性比较（其余配置相同）：'+rows[j]['case']+' / '+rows[i]['case']+'。指标绝对差：'+'；'.join(metric_labels[k]+' '+fmt(v) for k,v in differences.items())+'。')
    write_json(folder/'sensitivity.json',comparisons)
    text+=['## 限制与未完成项目']+['- '+v for v in limitations]+sections
    report='\n\n'.join(text).replace('−','-').replace('–','-').replace('—','-')
    # Keep Markdown table rows contiguous for the PDF renderer.
    report=re.sub(r'(?<=\|)\n\n(?=\|)','\n',report)
    (folder/'report.md').write_text(report,encoding='utf-8');write_report_pdf(folder/'report.pdf',report)
    write_json(folder/'comparison.json',rows)
    with (folder/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['case']);writer.writeheader();writer.writerows(rows)
    manifest=dict(workflow_id=workflow['id'],model=workflow['geometry'],jobs=[dict(case=c['name'],job_id=c.get('job_id'),
        result_url=f"/api/jobs/{c['job_id']}/files/result.zip" if c.get('job_id') else None) for c in workflow['cases']],
        note='此包包含报告与输入和审计；完整原始三维场保留于各算例result.zip，不重复拷贝大型数组')
    write_json(folder/'manifest.json',manifest)
    with zipfile.ZipFile(folder/'reports.zip','w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for path in folder.rglob('*'):
            if path.is_file() and path.name!='reports.zip' and not path.name.endswith('.tmp'):z.write(path,path.relative_to(folder))
