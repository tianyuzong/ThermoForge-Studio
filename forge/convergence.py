"""Three-grid evidence using unchanged physical cases and explicit resource limits."""
import json,math,hashlib
from strength.store import write_json


def compare(summaries,case=None):
    comparisons=[];passed=True;missing=[];invalid=[]
    kinds={s.get('analysis_type') for s in summaries}
    kind=case.analysis_type if case is not None else next(iter(kinds)) if len(kinds)==1 else None
    thermal=kind in ('thermal_steady','thermal_transient','thermo_mechanical')
    mechanical=kind in ('static','fatigue','contact_cycle','contact_fatigue','thermo_mechanical')
    if len(summaries)!=3:missing.append('需要三档完整网格结果')
    if not (thermal or mechanical) or kinds!={kind}:missing.append('分析类型缺失或不一致')
    required=[]
    if thermal:required.append('maximum_temperature_rise_K')
    if mechanical:required += ['maximum_von_mises_Pa','maximum_displacement_m']
    if kind in ('contact_cycle','contact_fatigue') or case is not None and case.contacts or any('maximum_contact_pressure_Pa' in s for s in summaries):required.append('maximum_contact_pressure_Pa')
    sizes=[s.get('mesh',{}).get('size_m') for s in summaries]
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in sizes):missing.append('缺少有效网格尺寸')
    elif any(a<=b for a,b in zip(sizes,sizes[1:])):missing.append('必须依次提供三档逐级加密的网格结果')
    counts=[s.get('mesh',{}).get('elements') for s in summaries]
    if any(not isinstance(v,int) or v<=0 for v in counts):missing.append('缺少实际单元数量')
    elif any(a>=b for a,b in zip(counts,counts[1:])):missing.append('实际单元数量未随网格加密增加，不能作为三档证据')
    for i,s in enumerate(summaries,1):
        if not s.get('mesh'):missing.append(f'第 {i} 档缺少网格信息')
        if s.get('solve_failure'):invalid.append(f'第 {i} 档求解未完成')
        for key in required:
            if not isinstance(s.get(key),(int,float)) or isinstance(s.get(key),bool) or not math.isfinite(s[key]) or s[key]<0:missing.append(f'第 {i} 档缺少有效指标 {key}')
        for needed,key in ((thermal,'thermal_balance'),(mechanical,'mechanical_balance')):
            if needed and not isinstance(s.get(key,{}).get('passed'),bool):missing.append(f'第 {i} 档缺少 {key} 检查')
            elif needed and s[key]['passed'] is not True:invalid.append(f'第 {i} 档 {key} 未通过')
        if kind in ('fatigue','contact_fatigue'):
            f=s.get('fatigue',{})
            if f.get('status')!='evaluated' or not f.get('hotspot'):missing.append(f'第 {i} 档疲劳评估不完整')
    if missing or invalid:
        return {'status':'insufficient_evidence' if missing else 'invalid','passed':False,'comparisons':[],
                'meshes':[s.get('mesh') for s in summaries],'missing':missing,'invalid':invalid,
                'note':'缺失指标、未完成计算或未通过平衡检查不能标记为网格验证通过。'}
    for a,b in zip(summaries,summaries[1:]):
        row={}
        for key,tolerance in [('maximum_contact_pressure_Pa',.05),('maximum_von_mises_Pa',.05),('maximum_displacement_m',.01),('maximum_temperature_rise_K',.02)]:
            if key not in required:continue
            change=abs(a[key]-b[key])/max(abs(b[key]),1e-12);row[key]={'relative_change':change,'tolerance':tolerance,'passed':change<=tolerance};passed &= change<=tolerance
        fa=a.get('fatigue',{}).get('hotspot');fb=b.get('fatigue',{}).get('hotspot')
        if fa and fb:
            x,y=fa.get('life_cycles'),fb.get('life_cycles')
            if isinstance(x,(int,float)) and isinstance(y,(int,float)) and math.isfinite(x) and math.isfinite(y) and x>0 and y>0:
                delta=abs(math.log10(x/y));ok=delta<=.1
            else:delta=None;ok=fa.get('life_status')==fb.get('life_status')=='below_defined_endurance_limit'
            row['fatigue_life']={'log10_change':delta,'tolerance':.1,'passed':ok};passed &=ok
        comparisons.append(row)
    return {'status':'verified' if passed else 'sensitive','passed':bool(passed),'comparisons':comparisons,
        'meshes':[s['mesh'] for s in summaries],'note':'三档全局指标。尖角／约束峰值和局部寿命仍需审查其位置，不能以显示平滑掩盖不收敛。'}


def run_refinement(folder,config,case,coarse,runner,progress):
    summaries=[coarse]
    def incomplete(level,detail):
        result={'status':'incomplete','passed':False,'failed_level':level+1,'detail':detail,'meshes':[s.get('mesh') for s in summaries]}
        write_json(folder/'convergence.json',result);return result
    for level,factor in enumerate((1.5,2.25),1):
        progress('meshing',f'网格收敛：第 {level+1}/3 档，尺寸缩小至 1/{factor:g}')
        fine=case.model_copy(deep=True);fine.mesh.convergence=False
        fine.mesh.size_m=coarse['mesh']['size_m']/factor
        if fine.mesh.local_size_m:fine.mesh.local_size_m/=factor
        for region in fine.mesh.regions:region.size_m/=factor
        sub=folder/f'grid{level}';sub.mkdir(exist_ok=True)
        snapshot={k:v for k,v in config.items() if k!='sha256'};snapshot.update(case=fine.model_dump(),parent_snapshot_sha256=config.get('sha256'))
        snapshot['sha256']=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
        write_json(sub/'config.json',snapshot)
        try:runner(sub)
        except Exception as exc:
            return incomplete(level,str(exc))
        status=json.loads((sub/'status.json').read_text(encoding='utf-8')) if (sub/'status.json').exists() else {}
        if status.get('status')!='completed':
            return incomplete(level,'细网格研究未完整成功：'+status.get('status','缺少状态'))
        summaries.append(json.loads((sub/'summary.json').read_text(encoding='utf-8')))
    result=compare(summaries,case);write_json(folder/'convergence.json',result);return result
