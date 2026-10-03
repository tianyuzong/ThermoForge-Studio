"""Collect saved evidence without converting missing or failed runs into passes."""
import hashlib,json,math,sys,xml.etree.ElementTree as ET
import h5py,numpy as np
from datetime import datetime,timezone
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def suite(name):
    path=root/'validation'/name
    if not path.exists():return {'status':'missing'}
    tree=ET.parse(path);cases=tree.findall('.//testcase');skips=sum(c.find('skipped') is not None for c in cases)
    failures=sum(c.find('failure') is not None or c.find('error') is not None for c in cases)
    return dict(tests=len(cases),passed=len(cases)-skips-failures,skipped=skips,failures=failures,sha256=digest(path),file=str(path.relative_to(root)))
def study(study_id):
    folder=root/'data'/'studies'/study_id
    status=read(folder/'status.json') if (folder/'status.json').exists() else {'status':'missing'}
    result=dict(id=study_id,**status)
    if (folder/'summary.json').exists():
        s=read(folder/'summary.json')
        for key in ('mesh','frames','maximum_contact_pressure_Pa','maximum_von_mises_Pa','maximum_displacement_m','maximum_temperature_C','minimum_temperature_C','maximum_wear_depth_m','thermal_balance','convergence','fatigue','clearance_probes','solve_failure'):
            if key in s:result[key]=s[key]
        if 'mechanical_balance' in s:result['mechanical_balance_passed']=s['mechanical_balance']['passed']
        result['summary_sha256']=digest(folder/'summary.json')
    return result
def record(name):
    path=root/'validation'/(name+'.json')
    if not path.exists():return {'status':'missing'}
    saved=read(path);result=study(saved['id'])
    # Queued jobs have a database record but no worker output directory yet.
    if result['status']=='missing':result.update({k:saved[k] for k in ('status','detail') if k in saved})
    return result
def compare(rows,kind):
    if any(r.get('status')!='completed' for r in rows):return {'status':'incomplete','passed':False}
    comparisons=[]
    for a,b in zip(rows,rows[1:]):
        values={}
        if 'maximum_contact_pressure_Pa' in a and 'maximum_contact_pressure_Pa' in b:
            change=abs(a['maximum_contact_pressure_Pa']/b['maximum_contact_pressure_Pa']-1)
            values['pressure']={'relative_change':change,'limit':.05,'passed':change<=.05}
        x=a.get('fatigue',{}).get('hotspot',{}).get('life_cycles');y=b.get('fatigue',{}).get('hotspot',{}).get('life_cycles')
        if x and y:
            change=abs(math.log10(x/y));values['life']={'log10_change':change,'limit':.1,'passed':change<=.1}
        comparisons.append(values)
    passed=bool(comparisons) and all(row and all(v['passed'] for v in row.values()) for row in comparisons)
    return {'kind':kind,'status':'verified' if passed else 'sensitive','passed':passed,'comparisons':comparisons,'studies':[r['id'] for r in rows]}

def transient_comparison(coarse,fine):
    result=compare([coarse,fine],'step_halving')
    if result['status']=='incomplete':return result
    paths=[root/'data'/'studies'/r['id']/'fields.h5' for r in (coarse,fine)]
    with h5py.File(paths[0]) as a,h5py.File(paths[1]) as b:
        if not np.array_equal(a['points'][:],b['points'][:]) or not np.array_equal(a['tets'][:],b['tets'][:]):
            return {**result,'status':'mesh_mismatch','passed':False}
        ta=a['times_s'][:];tb=b['times_s'][:]
        indices=[int(np.argmin(abs(tb-t))) for t in ta]
        if not np.allclose(ta,tb[indices],atol=1e-9,rtol=0):
            return {**result,'status':'missing_matching_times','passed':False}
        T0=a['temperature_C'][0];scale=max(float(np.max(abs(b['temperature_C'][:]-T0))),1e-12)
        temperature=[];pressure=[];gaps=[]
        for i,j in enumerate(indices):
            temperature.append(float(np.max(abs(a['temperature_C'][i]-b['temperature_C'][j]))))
            peak=[]
            for f,k in ((a,i),(b,j)):
                values=f['native_contact'][str(k)]['contact_stresses'][:]
                peak.append(float(np.max(values[:,2])) if len(values) else 0.)
            pressure.append(abs(peak[0]-peak[1]))
            if 'contact' in a and 'contact' in b:
                for name,x in a['contact'][str(i)].items():
                    y=b['contact'][str(j)][name]
                    if np.array_equal(x['slave_face'][:],y['slave_face'][:]):
                        gaps.append(float(np.max(abs(x['gap_m'][:]-y['gap_m'][:]))))
        pscale=max(fine['maximum_contact_pressure_Pa'],1e-12)
        metrics={'temperature':{'maximum_difference_K':max(temperature),'normalization_temperature_rise_K':scale,'relative_change':max(temperature)/scale,'limit':.02,'passed':max(temperature)/scale<=.02},
                 'matched_time_pressure':{'maximum_difference_Pa':max(pressure),'normalization_peak_Pa':pscale,'relative_change':max(pressure)/pscale,'limit':.05,'passed':max(pressure)/pscale<=.05}}
        result.update(matching_times_s=ta.tolist(),same_nodes=True,field_comparisons=metrics,maximum_gap_difference_m=max(gaps,default=0.))
        result['passed']=result['passed'] and all(v['passed'] for v in metrics.values())
        result['status']='verified' if result['passed'] else 'sensitive'
    return result

def hertz_convergence(rows,dimension):
    rows=sorted(rows,key=lambda x:x['mesh']['nodes'])
    if len(rows)<3:return {'status':'incomplete','passed':False}
    changes=[{'pressure_relative_change':abs(a['actual_peak_Pa']/b['actual_peak_Pa']-1),'contact_size_relative_change':abs(a[dimension]/b[dimension]-1)} for a,b in zip(rows,rows[1:])]
    passed=all(v['passed'] for v in rows) and all(max(v.values())<=.05 for v in changes)
    return {'status':'verified' if passed else 'sensitive','passed':passed,'limit':.05,'changes':changes}
result={'generated_at':datetime.now(timezone.utc).isoformat(),'source_versions':read(root/'SOURCE_VERSIONS.json'),
    'test_suites':{k:suite(v) for k,v in {'structural_and_forge_cpu':'structure-and-forge-final.xml','legacy_thermal_cpu':'thermal-legacy.xml','gpu':'gpu-regression.xml','gpu_additional':'gpu-additional.xml','fatigue_domain_extension':'fatigue-domains.xml','wear_block_extension':'wear-blocks.xml','verification_provenance':'verification-provenance.xml','native_restart':'native-restart.xml','physical_step_balance':'physical-step-balance.xml','final_coupled_balance':'final-coupled-balance.xml'}.items()},
    'motor_static_regression':read(root/'validation'/'motor-static-reproduction.json')}
passed_cases=set();skipped_cases={};failed_cases=set()
for entry in result['test_suites'].values():
    if 'file' not in entry:continue
    for c in ET.parse(root/entry['file']).findall('.//testcase'):
        identity=c.get('classname','')+'::'+c.get('name','')
        if c.find('failure') is not None or c.find('error') is not None:failed_cases.add(identity)
        elif c.find('skipped') is not None:skipped_cases[identity]=c.find('skipped').get('message','')
        else:passed_cases.add(identity)
result['software_summary']={'unique_passed':len(passed_cases),'unique_failed':len(failed_cases),'unexecuted':{k:v for k,v in skipped_cases.items() if k not in passed_cases}}
result['hertz_point']=[dict(path=p.relative_to(root).as_posix(),**read(p)) for size in (45,36,30) for p in [root/'validation'/f'hertz-{size}-k2.0e+14-g400'/'benchmark.json'] if p.exists()]
result['hertz_line']=[dict(path=p.relative_to(root).as_posix(),**read(p)) for p in sorted((root/'validation').glob('hertz-line-*-K1e+15/benchmark.json'))]
result['hertz_point_convergence']=hertz_convergence(result['hertz_point'],'actual_radius_m')
result['hertz_line_convergence']=hertz_convergence(result['hertz_line'],'actual_half_width_m')
gap_steps=['thermal-gap-run','thermal-gap-step-half','thermal-gap-step-quarter','thermal-gap-step-eighth']
for label in ('sixteenth','thirtysecond','sixtyfourth'):
    name='thermal-gap-step-'+label
    if (root/'validation'/(name+'.json')).exists():gap_steps.append(name)
names=['motor-fatigue-derived','motor-clearance-derived','local-bearing-resumed','motor-thermo-contact-final','motor-contact-balanced','gear-tooth-engagement-final','rolling-grid-45','rolling-grid-36','rolling-step-129','thermal-gap-heating-cooling','thermal-gap-restart-regression',*gap_steps]
result['studies']={name:record(name) for name in names}
s=result['studies'];result['rolling_grid']=compare([s['rolling-grid-45'],s['rolling-grid-36'],s['local-bearing-resumed']],'three_local_meshes')
result['rolling_step']=compare([s['local-bearing-resumed'],s['rolling-step-129']],'step_halving')
result['coupling_step_checks']=[transient_comparison(s[a],s[b]) for a,b in zip(gap_steps,gap_steps[1:])]
result['coupling_step']=result['coupling_step_checks'][-1]
result['coupling_step']['refinement_note']='Use the finest consecutive pair; all coarser comparisons remain in coupling_step_checks.'
for name in ('clean-install','mcp-handshake','browser-check','browser-final','browser-structure','gear-engagement','packaged-case-schemas','thermal-opening-closure','thermal-native-restart-comparison','thermal-physical-balance','report-qa'):
    path=root/'validation'/(name+'.json');result[name]=read(path) if path.exists() else {'status':'missing'}
external={}
for key,label in [('rolling_grid','滚动接触三档局部网格的压力与寿命'),('rolling_step','滚动接触时间步减半的压力与寿命'),('coupling_step','热结构时间步减半的温度与接触压力')]:
    check=result[key]
    if check.get('passed'):
        for sid in check['studies']:external.setdefault(sid,[]).append({'key':key,'label':label,**check})
for key,label in [('thermal-opening-closure','热致闭合、冷却再分离及全过程平衡'),('gear-engagement','单齿节啮合的从动转角、持续接触及全局平衡（不含压力峰值与疲劳寿命）')]:
    if result[key].get('passed'):external.setdefault(result[key]['source_study'],[]).append({'key':key,'label':label,**result[key]})
for check in result['thermal-physical-balance'].get('studies',{}).values():
    if check.get('passed'):external.setdefault(check['source_study'],[]).append({'key':'mechanical_physical_steps','label':'全部已收敛物理时间步的力与力矩平衡',**check})
for sid,checks in external.items():
    folder=root/'data'/'studies'/sid
    evidence={'schema_version':1,'study_id':sid,'config_sha256':digest(folder/'config.json'),'summary_sha256':digest(folder/'summary.json'),
              'raw_fields_sha256':read(folder/'raw-fields.sha256.json')['sha256'],'checks':checks}
    (folder/'verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
result['limitations']=['疲劳和滚动接触曲线为软件演示数据，不是电机／轴承实测材料。','整机接触局部网格仅用于流程展示，不能把其尖角峰值作为已验证寿命。','齿轮单齿节与材料点整周是不同周期；保留旧完整一周失败试算，不能据单齿节通过宣称整周验证完成。','Hertz 三档收敛与每档解析误差分别列示；未完成或未达到阈值的场景不能标为通过。']
result['limitations'].append('磨损几何更新在周期接触研究中执行；瞬态热结构研究保留摩擦历史，但不同时演变磨损几何。')
path=root/'validation'/'acceptance.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# ThermoForge Studio 验证记录','',f"生成时间（UTC）：{result['generated_at']}",'','本记录由已保存的测试及求解输出生成。执行完成、解析误差、网格收敛和实物材料标定分别列示。','', '## 软件回归','', '| 测试组 | 通过 | 跳过 | 失败 | 原始记录 |','|---|---:|---:|---:|---|']
for name,v in result['test_suites'].items():lines.append(f"| {name} | {v.get('passed','—')} | {v.get('skipped','—')} | {v.get('failures','—')} | `{v.get('file','missing')}` |")
lines += ['',f"按测试身份去重：{result['software_summary']['unique_passed']} 项通过，{result['software_summary']['unique_failed']} 项失败，{len(result['software_summary']['unexecuted'])} 项未执行。"]
lines+=['','跳过项主要为显式选择执行的 GPU 测试；独立 GPU 记录列出实际硬件执行结果。附加单项与主回归不重复计数。','', '## 数值基准','',f"- 原电机静力同网格回归：{'通过' if result['motor_static_regression']['passed'] else '未通过'}；位移差异 {result['motor_static_regression']['relative_changes']['maximum_displacement_m']:.4%}，应力差异 {result['motor_static_regression']['relative_changes']['maximum_von_mises_Pa']:.4%}。",'- 解析导热、瞬态平板、相变能量、自由膨胀、完全约束热应力、异材串联杆、循环计数、均值修正、Miner、Findley、库仑滑动、Archard 及表面更新见对应 pytest 数值断言。','', '| Hertz 场景 | 节点数 | 压力误差 | 接触区尺寸误差 | 通过 |','|---|---:|---:|---:|---|']
for category in ('hertz_point','hertz_line'):
    for v in result[category]:lines.append(f"| {v['path'].split('/')[1]} | {v['mesh']['nodes']} | {v['pressure_error']:.3%} | {v.get('radius_error',v.get('width_error')):.3%} | {v['passed']} |")
lines+=['','## 应用与收敛','', '| 工况 | 执行状态 | 帧数 |','|---|---|---:|']
for name,v in s.items():lines.append(f"| {name} | {v['status']} | {v.get('frames','—')} |")
for key,label in [('rolling_grid','滚动接触三档局部网格'),('rolling_step','滚动接触步长减半'),('coupling_step','瞬态接触耦合步长减半')]:lines.append(f"\n- {label}：{result[key]['status']}。比较值及阈值见 `validation/acceptance.json`。")
lines+=['','### 瞬态步长细化的全部记录','','| 相邻工况 | 相同时刻最大温差 K | 相同时刻压力差 / 细步长峰值 | 结论 |','|---|---:|---:|---|']
for (a,b),v in zip(zip(gap_steps,gap_steps[1:]),result['coupling_step_checks']):
    fields=v.get('field_comparisons',{});temperature=fields.get('temperature',{}).get('maximum_difference_K');pressure=fields.get('matched_time_pressure',{}).get('relative_change')
    lines.append(f"| {a} → {b} | {temperature:.6g} | {pressure:.3%} | {v['status']} |" if temperature is not None else f"| {a} → {b} | — | — | {v['status']} |")
lines+=['',f"- 热致闭合与冷却分离：{result['thermal-opening-closure'].get('passed','尚未完成')}。",f"- 原生重启动与完整历史重放：{result['thermal-native-restart-comparison'].get('passed','尚未完成')}。",f"- Hertz 点接触三档收敛：{result['hertz_point_convergence']['status']}；线接触三档收敛：{result['hertz_line_convergence']['status']}。"]
gear=result['gear-engagement']
if gear.get('passed'):
    lines+=['',f"- 齿轮18°单齿节：驱动 {gear['driver_sweep_deg']:.6f}°，从动 {gear['follower_sweep_deg']:.6f}°；最大传动偏差 {gear['maximum_transmission_deviation_relative_to_sweep']:.3%}（限值1%），接触连续性与全局平衡通过；不含局部压力峰值或疲劳寿命验收。"]
lines+=['','## 交付检查','','| 检查 | 已保存结果 |','|---|---|',
        f"| 全新环境安装及启动／停止 | {result['clean-install'].get('passed',False)} |",
        f"| 原结构浏览器流程 | {result['browser-structure'].get('passed',False)} |",
        f"| 多物理场浏览器流程 | {result['browser-final'].get('passed',False)}；{len(result['browser-final'].get('checks',[]))} 项 |",
        f"| MCP 类型化接口 | {result['mcp-handshake'].get('schema_success',False)}；{result['mcp-handshake'].get('tool_count',0)} 个工具 |",
        f"| 便携示例配置校验 | {result['packaged-case-schemas'].get('passed',False)}；{result['packaged-case-schemas'].get('count',0)} 个工况 |",
        f"| PDF 逐页视觉检查 | {result['report-qa'].get('passed',False)}；{len(result['report-qa'].get('reports',{}))} 份报告 |"]
lines+=['','## 适用边界','']+['- '+x for x in result['limitations']]
(root/'VALIDATION.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'report':'VALIDATION.md','rolling_grid':result['rolling_grid']['status'],'rolling_step':result['rolling_step']['status'],'coupling_step':result['coupling_step']['status']},ensure_ascii=False))
