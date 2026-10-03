"""Independent heavy worker for immutable multi-physics studies."""
import json,sys,traceback,os,hashlib
from pathlib import Path
if __name__=='__main__' and len(sys.argv)>1:
    raw=json.loads((Path(sys.argv[1])/'config.json').read_text(encoding='utf-8'))
    os.environ['STRENGTH_CPU_THREADS']=str(raw['case'].get('resources',{}).get('cpu_threads',6))
from strength.config import CPU_THREADS
for variable in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[variable]=str(CPU_THREADS)
import numpy as np
from strength.config import DATA
from strength.geometry import build_mesh
from strength.fem import stress_invariants,tet_shape,IP_BARY
from .loads import assemble_loads
from strength.store import write_json
from strength.result_catalog import finite_json
from .schemas import AnalysisCase,material_for,coupling_mode
from .thermal import HeatSystem,solve_history
from .mechanics import solve_static,solve_cycle
from .fatigue import sn_damage,findley
from .interfaces import interface_map
from .contact import recover_contact,corotate_stresses,wear_history
from .results import write_archive,write_report,frame_display


def precompute_findley(mesh,case,stress_history,progress):
    from .fatigue import tensor,findley_parameters,sn_life
    histories=stress_history.transpose(1,2,0,3).reshape(-1,len(stress_history),6);bodies=np.repeat(mesh['body_ids'],4)
    cache={};best_parameter=0.;done=0
    for body in np.unique(bodies):
        if case.fatigue.body_ids and body not in case.fatigue.body_ids:continue
        m=material_for(case,body)
        if m.valid_temperature_C and not m.valid_temperature_C[0]<=case.thermal.initial_C<=m.valid_temperature_C[1]:continue
        if m.findley is None or m.yield_strength_Pa is None or case.analysis_type=='contact_fatigue' and m.findley.calibration!='rolling_contact':continue
        ids=np.flatnonzero(bodies==body);h=histories[ids]
        closed=np.linalg.norm(h[:,0]-h[:,-1],axis=1)<=np.maximum(np.linalg.norm(h,axis=2).max(1)*1e-3,1.)
        elastic=stress_invariants(h)[0].max(1)<=m.yield_strength_Pa
        ids=ids[closed&elastic];h=histories[ids]
        if not len(ids):continue
        T=tensor(h);diagonal=np.diagonal(T,axis1=2,axis2=3)
        bounds=np.maximum(0.,np.linalg.norm(T-T[:,:1],axis=(2,3)).max(1)+m.findley.k*(diagonal+np.abs(T).sum(3)-np.abs(diagonal)).max(axis=(1,2)))
        order=np.argsort(-bounds);ids=ids[order];bounds=bounds[order];del T,h,diagonal
        for start in range(0,len(ids),32):
            selected=ids[start:start+32];upper=bounds[start:start+32]
            skipped=(upper<=min(best_parameter,m.findley.curve.endurance_limit_Pa)) if m.findley.curve.endurance_limit_Pa is not None and best_parameter>0 else np.zeros(len(selected),bool)
            for idx,bound in zip(selected[skipped],upper[skipped]):cache[int(idx)]={'parameter_Pa':float(bound),'parameter_is_upper_bound':True,'normal':None,'damage_per_cycle':0.}
            active=selected[~skipped]
            if len(active):
                values,normals=findley_parameters(histories[active],m.findley.k,case.fatigue.plane_count)
                for idx,value,normal in zip(active,values,normals):
                    try:
                        n=sn_life(float(value),m.findley.curve);cache[int(idx)]={'parameter_Pa':float(value),'normal':normal.tolist() if value else None,'damage_per_cycle':0. if not np.isfinite(n) else 1/n}
                        best_parameter=max(best_parameter,float(value))
                    except ValueError as exc:cache[int(idx)]={'error':str(exc)}
            done+=len(selected)
            if start%256==0:progress('analyzing',f'临界平面批量评估：已处理 {done:,} 个有效材料点')
    return cache


def evaluate_fatigue(mesh,case,stress_history,progress=lambda *a:None):
    shape=(len(mesh['tets']),4);life=np.full(shape,np.nan);damage=np.full(shape,np.nan);evaluated=np.zeros(shape,bool);failures={};best=None
    target=case.fatigue.target_cycles
    cached=precompute_findley(mesh,case,stress_history,progress) if (case.fatigue.method=='findley' or case.analysis_type=='contact_fatigue') and case.history.periodic else {}
    for e,body in enumerate(mesh['body_ids']):
        if case.fatigue.body_ids and body not in case.fatigue.body_ids:continue
        material=material_for(case,body)
        for ip in range(4):
            try:
                if material.valid_temperature_C and not material.valid_temperature_C[0]<=case.thermal.initial_C<=material.valid_temperature_C[1]:raise ValueError('疲劳评估温度超出材料参数适用范围')
                history=stress_history[:,e,ip];upper_bound_only=False
                if case.fatigue.method=='findley' or case.analysis_type=='contact_fatigue':
                    if not case.history.periodic:raise ValueError('Findley 首版仅支持完整周期历史，不接受非周期随机谱')
                    if np.linalg.norm(history[0]-history[-1])>max(np.linalg.norm(history,axis=1).max()*1e-3,1.):raise ValueError('材料点周期尚未闭合；需先获得稳定完整应力周期')
                    r=cached.get(e*4+ip)
                    if r is None:r=findley(history,material,case.fatigue.plane_count,case.analysis_type=='contact_fatigue',None if best is None else best['fatigue_parameter_Pa'])
                    if 'error' in r:raise ValueError(r['error'])
                    upper_bound_only=r.get('parameter_is_upper_bound',False)
                    per_cycle=r['damage_per_cycle'];normal=r['normal'];parameter=r['parameter_Pa']
                    cycles=None
                else:
                    per_cycle,cycles=sn_damage(history,material,case.history.periodic);normal=None
                    per_cycle/=case.history.cycles_per_history
                    parameter=max((c['corrected_Pa'] for c in cycles),default=0.)
                d=per_cycle*target;N=1/per_cycle if per_cycle>0 else float('inf')
                life[e,ip]=N;damage[e,ip]=d;evaluated[e,ip]=True
                if not upper_bound_only and (best is None or (d,parameter)>(best['damage'],best['fatigue_parameter_Pa'])):
                    weights,_=tet_shape(IP_BARY[ip,1:]);best={'body_id':int(body),'element':e+1,'integration_point':ip+1,'position_m':(weights@mesh['points'][mesh['tets'][e]]).tolist(),
                        'life_cycles':float(N) if np.isfinite(N) else None,'life_status':'finite' if np.isfinite(N) else 'below_defined_endurance_limit',
                        'damage':float(d),'fatigue_parameter_Pa':float(parameter),'history_repetition_damage':float(per_cycle*case.history.cycles_per_history*case.history.repetitions),'critical_plane_normal':normal,'stress_cycles':cycles,
                        'life_definition':(material.findley.curve if case.fatigue.method=='findley' or case.analysis_type=='contact_fatigue' else material.sn_curve).life_definition}
            except ValueError as exc:failures[str(exc)]=failures.get(str(exc),0)+1
    selected=np.isin(mesh['body_ids'],case.fatigue.body_ids) if case.fatigue.body_ids else np.ones(len(mesh['tets']),bool)
    return {'life_cycles':life,'damage':damage,'evaluated':evaluated,'hotspot':best,'failures':failures,
        'life_definition':'按材料曲线定义；位置为萌生危险区','status':'evaluated' if selected.any() and evaluated[selected].all() else 'partial' if evaluated.any() else 'not_evaluated'}


def accumulate_fatigue_block(mesh,case,previous,current,block):
    """Retain material-point Miner damage while geometry changes between blocks.

    A per-period life belongs to the latest geometry; it cannot be presented as
    a prediction of future wear. An invalid earlier block remains invalid.
    """
    result=dict(current)
    prior=np.zeros_like(current['damage']) if previous is None else previous['accumulated_block_damage']
    total=prior+current['damage']/case.fatigue.target_cycles*case.cycles_per_wear_block
    result['accumulated_block_damage']=total
    result['last_block_hotspot']=current['hotspot']
    result['accumulated_cycles']=(block+1)*case.cycles_per_wear_block
    result['life_basis']='last_completed_wear_block_held_fixed' if case.wear_blocks>1 else 'single_complete_period'
    result['blocks']=([] if previous is None else previous['blocks'])+[{'block':block+1,'cycles':case.cycles_per_wear_block,'status':current['status'],'hotspot':current['hotspot'],'failures':dict(current['failures'])}]
    result['failures']=({f"第 {item['block']} 磨损块：{reason}":count for item in result['blocks'] for reason,count in item['failures'].items()} if case.wear_blocks>1 else dict(current['failures']))
    if previous is not None and previous['status']!='evaluated':result['status']='partial' if np.isfinite(total).any() else 'not_evaluated'
    if np.isfinite(total).any():
        e,ip=np.unravel_index(np.nanargmax(total),total.shape);weights,_=tet_shape(IP_BARY[ip,1:])
        result['accumulated_hotspot']={'body_id':int(mesh['body_ids'][e]),'element':int(e+1),'integration_point':int(ip+1),'position_m':(weights@mesh['points'][mesh['tets'][e]]).tolist(),'damage':float(total[e,ip])}
    else:result['accumulated_hotspot']=None
    return result


def run(folder):
    folder=Path(folder);config=json.loads((folder/'config.json').read_text(encoding='utf-8'))
    if config.get('sha256'):
        checksum=hashlib.sha256(json.dumps({k:v for k,v in config.items() if k!='sha256'},sort_keys=True).encode()).hexdigest()
        if checksum!=config['sha256']:raise ValueError('工况快照内容与保存的 SHA-256 不一致；请另建工况提交修改')
    case=AnalysisCase.model_validate(config['case'])
    model=DATA/'models'/config['model_id'];geo=json.loads((model/'geometry.json').read_text(encoding='utf-8'))
    if case.geometry_sha256 and case.geometry_sha256!=geo['source_sha256']:raise ValueError('工况与几何版本不匹配')
    source=json.loads((model/'source.json').read_text(encoding='utf-8'))
    with (model/source['file']).open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=geo['source_sha256']:raise ValueError('CAD 文件在导入后被修改；请重新导入并绑定选区')
    if config.get('reprocess_from'):
        from .reprocess import run as reprocess
        return reprocess(folder,config,case,geo)
    def progress(status,detail):write_json(folder/'status.json',{'status':status,'detail':detail})
    progress('meshing','生成共享 C3D10 / P2 网格')
    mesh_info=build_mesh(model,case,folder/'mesh.npz',max_dofs=case.resources.max_dofs);mesh=dict(np.load(folder/'mesh.npz'))
    from .resources import admit
    resource_audit=admit(mesh_info,case,folder)
    times=[];temperatures=[];structures=[];gradients=[];fluxes=[];audits=[];basis_balances=[];fatigue=None;contacts=[];wear=None;failure=None;wear_frames=[];initial_state=None;initial_temperature=None
    kind=case.analysis_type
    if kind.startswith('thermal') or kind=='thermo_mechanical':
        system=HeatSystem(mesh,case,geo)
        from .initial_state import load_initial_temperature
        initial_temperature,initial_state=load_initial_temperature(mesh,case)
        write_json(folder/'initial-state.json',initial_state)
        if kind=='thermo_mechanical':
            from .coupling import solve_coupled,CouplingFailure
            try:times,temperatures,structures,audits=solve_coupled(system,case,geo,folder/'coupling',progress)
            except CouplingFailure as exc:
                if exc.partial is None:raise
                times,temperatures,structures,audits,failure=exc.partial
            contacts=[{} for _ in times]
            for a in audits:
                if 'saved_frame' in a:contacts[a['saved_frame']]=a.get('contact',{})
            if not case.coupling.transient:contacts=[audits[0].get('contact',{})]
        else:
            times,temperatures,audits=solve_history(system,case,progress,initial_temperature,journal_folder=folder/'thermal')
        from .checkpoints import derive_thermal_fields
        gradients,fluxes=derive_thermal_fields(folder,system,temperatures)
    elif kind in ('contact_cycle','contact_fatigue'):
        from .wear import recede
        current_mesh=mesh;wear={};period=case.history.times_s[-1]-case.history.times_s[0]
        for block in range(case.wear_blocks):
            progress('solving',f'接触周期／磨损块 {block+1}/{case.wear_blocks}')
            frames=solve_cycle(current_mesh,case,geo,folder/f'cycle{block:03d}',progress)
            progress('analyzing','计算材料点相对滑移与 Archard 磨损' if any(p.wear for p in case.contacts) else '恢复接触周期与间隙场')
            block_history={}
            previous_depth=sum((v['depth_m'] for v in wear.values()),np.zeros(len(mesh['points'])))
            block_wear=wear_history(current_mesh,case,frames,geo,block_history)
            wear_frames.extend(previous_depth+sum((v[i] for v in block_history.values()),np.zeros(len(mesh['points']))) for i in range(len(frames)))
            for name,values in block_wear.items():
                if name not in wear:wear[name]={k:np.zeros_like(v) if isinstance(v,np.ndarray) else 0 for k,v in values.items()}
                for key,value in values.items():wear[name][key]+=value
            for i,s in enumerate(frames):
                if i%5==0 or i+1==len(frames):progress('analyzing',f'恢复接触场：第 {i+1}/{len(frames)} 帧')
                maps={pair.name:interface_map(current_mesh,pair,geo,s['displacements'],allow_empty=True) for pair in case.contacts}
                contacts.append(recover_contact(current_mesh,s,maps,case,True))
                s['mesh_offset_m']=current_mesh['points']-mesh['points'];structures.append(s)
                times.append(case.history.times_s[i]+block*case.cycles_per_wear_block*period)
            if kind=='contact_fatigue':
                progress('analyzing','接触材料点完整应力周期与临界平面评估')
                stress=np.array([corotate_stresses(s['stresses'],current_mesh,case,i,s) for i,s in enumerate(frames)])
                block_fatigue=evaluate_fatigue(current_mesh,case,stress,progress)
                fatigue=accumulate_fatigue_block(current_mesh,case,fatigue,block_fatigue,block)
            # Store each completed block before another expensive nonlinear solve.
            np.savez_compressed(folder/f'block{block:03d}.npz',points=current_mesh['points'],stresses=frames[-1]['stresses'])
            if block+1<case.wear_blocks:current_mesh=recede(current_mesh,block_wear)
    elif kind=='fatigue':
        history=case.history
        if case.contacts:raise ValueError('含接触的循环必须使用接触疲劳工况，不能使用线性叠加')
        if any(any(abs(v)>0 for v in s.displacement_m) for s in case.supports):raise ValueError('单位载荷叠加暂不接受非零指定位移；请改为显式应力周期')
        basis=[]
        for i,load in enumerate(case.loads):
            subcase=case.model_copy(update={'loads':[load]});force,_=assemble_loads(mesh,subcase,geo)
            basis.append(solve_static(mesh,subcase,geo,folder/f'basis{i}',force=force))
            basis_balances.append({'load_name':load.name,**basis[-1]['balance']})
        if not basis:raise ValueError('疲劳分析需要非零载荷')
        for index,time in enumerate(history.times_s):
            frame={'time_s':time}
            for key in ('displacements','stresses','strains','forces'):
                frame[key]=sum(b[key]*history.factors.get(l.name,[1.]*len(history.times_s))[index] for b,l in zip(basis,case.loads))
            structures.append(frame)
        times=history.times_s;progress('analyzing','原始积分点应力循环计数与寿命评估')
        fatigue=evaluate_fatigue(mesh,case,np.array([s['stresses'] for s in structures]),progress)
    else:
        progress('solving','CalculiX 静力求解');structures=[solve_static(mesh,case,geo,folder/'static')];times=[0.]
        if case.contacts:
            maps={p.name:interface_map(mesh,p,geo,structures[0]['displacements'],allow_empty=True) for p in case.contacts}
            contacts=[recover_contact(mesh,structures[0],maps,case,True)]
    summary={'name':case.name,'analysis_type':kind,'frames':len(times),'times_s':times,'mesh':mesh_info,
        'resources':resource_audit,
        'stress_basis':'C3D10 原始积分点；显示场独立投影','convergence':{'status':'not_verified'},
        'assumptions':case.assumptions,'source_versions':json.loads((Path(__file__).parents[1]/'SOURCE_VERSIONS.json').read_text()),'compute_backend':'calculix_cpu + scipy_fp64'}
    if initial_state:summary['initial_state']=initial_state
    if kind=='thermo_mechanical':summary['coupling_mode']=coupling_mode(case)
    if failure:summary['solve_failure']=failure
    if temperatures:summary.update(minimum_temperature_C=float(min(np.min(T) for T in temperatures)),maximum_temperature_C=float(max(np.max(T) for T in temperatures)),maximum_temperature_rise_K=float(max(np.max(np.abs(T-initial_temperature)) for T in temperatures)),maximum_gradient_K_m=float(max(np.linalg.norm(g,axis=-1).max() for g in gradients)))
    thermal_audits=[a.get('thermal',a) for a in audits if not a.get('initial')]
    if thermal_audits:summary['thermal_balance']={'maximum_relative_error':max(a.get('energy_balance_relative',0) for a in thermal_audits),'passed':all(a.get('energy_balance_relative',0)<=.01 for a in thermal_audits)}
    if structures:summary.update(maximum_displacement_m=float(max(np.linalg.norm(s['displacements'],axis=1).max() for s in structures)),maximum_von_mises_Pa=float(max(stress_invariants(s['stresses'])[0].max() for s in structures)))
    if structures:summary['reused_native_frames']=sum(bool(s.get('stage_reused')) for s in structures)
    probes={}
    if structures and case.clearance_probes:
        from .probes import clearance_history
        probes=clearance_history(mesh,case,geo,structures,times)
        summary['clearance_probes']={name:{k:v for k,v in values.items() if not isinstance(v,np.ndarray)} for name,values in probes.items()}
    balances=[s['balance'] for s in structures if 'balance' in s]
    physical_balances=[{'time_s':a.get('time_s',times[0] if times else 0.),**a['mechanical']} for a in audits if a.get('mechanical')]
    all_balances=[*balances,*physical_balances,*basis_balances]
    if all_balances:summary['mechanical_balance']={'passed':all(v['passed'] for v in all_balances),'frames':balances,'physical_steps':physical_balances,'load_bases':basis_balances}
    if fatigue:summary['fatigue']={k:v for k,v in fatigue.items() if not isinstance(v,np.ndarray)}
    if contacts:
        summary['maximum_contact_pressure_Pa']=float(max((np.asarray(s['contact_stresses'])[:,2].max(initial=0) for s in structures if s.get('contact_stresses')),default=0))
        summary['contact_projection']='Pressure peak from native contact quadrature; UI and thermal feedback use explicit slave-face averaging.'
        summary['contact_history']=[{'time_s':t,'pairs':{case.contacts[int(i)].name:values for i,values in s.get('contact_totals',{}).items()}} for t,s in zip(times,structures)]
    if wear:summary['maximum_wear_depth_m']=float(max((v['depth_m'].max(initial=0) for v in wear.values()),default=0))
    progress('reporting','保存原始场、时间帧、校核记录和中文报告')
    write_archive(folder,mesh,times,temperatures,structures,gradients,fluxes,fatigue,contacts,wear,probes,case,wear_frames)
    write_json(folder/'summary.json',finite_json(summary));write_json(folder/'audit.json',finite_json(audits))
    write_json(folder/'display.json',frame_display(folder,-1));write_report(folder,case,summary)
    if case.mesh.convergence:
        from .convergence import run_refinement
        summary['convergence']=run_refinement(folder,config,case,summary.copy(),run,progress)
        write_json(folder/'summary.json',finite_json(summary));write_report(folder,case,summary)
    incomplete=failure or (fatigue and fatigue['status']!='evaluated') or (all_balances and not all(v['passed'] for v in all_balances)) or (case.mesh.convergence and not summary['convergence'].get('passed')) or not summary.get('thermal_balance',{'passed':True})['passed']
    progress('partial' if incomplete else 'completed','计算完成；未评估或平衡超限项已列入报告' if incomplete else '结果与报告已保存')


if __name__=='__main__':
    folder=Path(sys.argv[1])
    try:run(folder)
    except Exception as exc:
        traceback.print_exc();write_json(folder/'status.json',{'status':'failed','detail':str(exc)});sys.exit(1)
