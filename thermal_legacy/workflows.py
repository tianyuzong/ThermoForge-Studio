"""Durable dependency-aware workflow orchestration above the Agent/job API."""
import copy, hashlib, json, threading, time, uuid
from pathlib import Path
import numpy as np
from thermal_legacy.runtime import DATA,JOBS,MODELS,read_json,write_json
from thermal_legacy.schemas import Simulation
from thermal_legacy.workflow_agent import WorkflowRequest,Blueprint,plan
from thermal_legacy.host_geometry import model_evidence
from thermal_legacy import agent
from thermal_legacy.compute_policy import execution_policy

WORKFLOWS=DATA/'workflows'
LOCK=threading.RLock()
THREADS={}
ACTIVE={'planning','running','reporting'}


def signature(config):
    def canonical(value):
        if isinstance(value,float):
            rounded=round(value,14)
            return int(rounded) if rounded.is_integer() else rounded
        if isinstance(value,list):return [canonical(x) for x in value]
        if isinstance(value,dict):return {k:canonical(v) for k,v in value.items() if k!='name'}
        return value
    return hashlib.sha256(json.dumps(canonical(config),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def folder(wid):
    if len(wid)!=32 or any(c not in '0123456789abcdef' for c in wid):raise ValueError('任务不存在')
    path=WORKFLOWS/wid
    if not (path/'workflow.json').is_file():raise ValueError('任务不存在')
    return path


def get(wid,public=True):
    with LOCK:
        p=folder(wid);w=read_json(p/'workflow.json')
        if w['phase'] in ACTIVE and not (THREADS.get(wid) and THREADS[wid].is_alive()):
            w['phase']='interrupted';w['detail']='服务重启中断了任务；已完成算例保留，可继续执行';write_json(p/'workflow.json',w)
        if public:
            w=copy.deepcopy(w)
            for c in w.get('cases',[]):
                c.pop('config',None);c.pop('effective_config',None)
            w.pop('base_config',None)
        return w


def update(wid,**changes):
    with LOCK:
        p=folder(wid);w=read_json(p/'workflow.json');w.update(changes,updated_at=time.time());write_json(p/'workflow.json',w)
        return w


def activity(wid,actor,stage,text,status='completed',case_key=None,details=None):
    """Persist observable actions and returned summaries, never model reasoning."""
    with LOCK:
        p=folder(wid);w=read_json(p/'workflow.json')
        entries=w.setdefault('activity',[])
        entry=dict(id=uuid.uuid4().hex,at=time.time(),actor=actor,stage=stage,
                   text=str(text)[:1500],status=status,case_key=case_key)
        if details:entry['details']=[str(v)[:3000] for v in details[:80]]
        entries.append(entry)
        if len(entries)>200:
            w['activity_omitted']=w.get('activity_omitted',0)+len(entries)-200
            del entries[:-200]
        w['updated_at']=time.time();write_json(p/'workflow.json',w)


def cancelled(wid):
    with LOCK:return read_json(folder(wid)/'workflow.json').get('cancel_requested',False)


def save_cases(wid,cases):update(wid,cases=cases)


def create(request:WorkflowRequest):
    if request.config.get('model_id',request.model_id)!=request.model_id:raise ValueError('当前配置属于其他模型')
    evidence=model_evidence(MODELS/request.model_id)
    if not evidence['topology']['closed']:raise ValueError('模型存在开口或非流形边，请先修复几何')
    wid=uuid.uuid4().hex;p=WORKFLOWS/wid;p.mkdir(parents=True)
    with LOCK:
        write_json(p/'workflow.json',dict(id=wid,name='正在生成多工况计划',model_id=request.model_id,
            prompt=request.prompt,base_config=request.config,geometry=evidence,phase='planning',detail='Agent正在拆分工况与依赖',
            cases=[],questions=[],limitations=[],created_at=time.time(),updated_at=time.time(),cancel_requested=False))
        activity(wid,'user','request','提交仿真目标，交给Agent规划')
        activity(wid,'host','geometry','模型几何已核验',details=[evidence['name'],'尺寸(mm)：'+' × '.join(f'{x*1000:.4g}' for x in evidence['dimensions_m'])])
        thread=threading.Thread(target=prepare,args=(wid,request),daemon=True,name='workflow-plan-'+wid[:8]);THREADS[wid]=thread;thread.start()
    return dict(id=wid)


def prepare(wid,request,blueprint=None):
    cases=[]
    try:
        previous={c['key']:c for c in get(wid,False).get('cases',[])}
        resumed=blueprint is not None
        if blueprint is None:activity(wid,'agent','planning','正在读取任务目标，拆分工况和温度场依赖','started')
        else:activity(wid,'host','planning','读取已保存的工况计划，继续交给配置Agent校验')
        blueprint=blueprint or plan(request,folder(wid))
        write_json(folder(wid)/'blueprint.json',blueprint.model_dump())
        if cancelled(wid):update(wid,phase='cancelled',detail='计划生成已取消');return
        update(wid,name=blueprint.name,limitations=blueprint.limitations,questions=blueprint.questions,planned_cases=[c.model_dump() for c in blueprint.cases])
        activity(wid,'host' if resumed else 'agent','planning',f'已读取{len(blueprint.cases)}个工况的原有计划' if resumed else f'已形成{len(blueprint.cases)}个工况的计划',details=[c.name+('；温度场来自 '+c.initial_from if c.initial_from else '') for c in blueprint.cases]+blueprint.limitations)
        if blueprint.questions:
            activity(wid,'agent','planning','需要补充以下信息才能继续','needs_input',details=blueprint.questions)
            update(wid,phase='needs_input',detail='请补充计划中的必要信息后重新生成');return
        configs={}
        for spec in blueprint.cases:
            if cancelled(wid):update(wid,phase='cancelled',detail='计划生成已取消');return
            cached=previous.get(spec.key)
            if cached and cached.get('status')=='ready' and cached.get('config') and all(cached.get(k)==v for k,v in spec.model_dump().items()):
                activity(wid,'host','configuration','保留已通过Agent校验的工况：'+spec.name,case_key=spec.key)
                configs[spec.key]=cached['config'];cases.append(cached);save_cases(wid,cases);continue
            current=copy.deepcopy(configs[spec.inherit_from] if spec.inherit_from else request.config)
            prompt=f'当前仅配置独立工况“{spec.name}”；其他工况及综合报告由宿主工作流处理。'+spec.prompt
            prompt+=f'。算例名称设为“{spec.name}”。'
            if spec.initial_from:
                prompt+=(f'本工况保留对计划工况“{spec.initial_from}”完整末帧节点温度场的依赖。'
                    '计划阶段仅清除旧结果绑定（initial_from_job设为null），不取消上述计划依赖。'
                    '父工况结果完成并通过校核后，由宿主再次调用配置Agent绑定实际结果ID；'
                    '此时尚未绑定实际温度场，不用标量初温替代父温度场。')
            else:
                prompt+='本工况没有计划内温度场依赖，清除续算（initial_from_job设为null），使用本工况指定的初始条件。'
            update(wid,detail='Agent正在配置并校验：'+spec.name)
            activity(wid,'agent','configuration','正在配置并校验：'+spec.name,'started',spec.key)
            workflow_context=dict(phase='planning',deferred_initial_from=spec.initial_from,
                binding_policy=('保留计划内温度场依赖；父工况完成后由宿主再次调用Agent绑定实际结果ID；当前仅清除旧结果绑定并校验待执行参数'
                    if spec.initial_from else '独立初始条件；清除旧结果绑定，不继承计划内其他工况温度场'))
            result=agent.codex_plan_request(request.model_id,prompt,current,{'history':[],'applied_message_count':0,'workflow_context':workflow_context})
            if cancelled(wid):update(wid,phase='cancelled',detail='计划生成已取消');return
            row=spec.model_dump()|dict(status='ready',agent_prompt=prompt,changes=result.get('changes',[]),warnings=result.get('warnings',[]),job_id=None,reused=False)
            if not result.get('ok') or result.get('questions'):
                row['status']='needs_input';row['questions']=result.get('questions',[]);cases.append(row);save_cases(wid,cases)
                activity(wid,'agent','configuration',spec.name+'：需要补充参数','needs_input',spec.key,result.get('questions',[]))
                update(wid,phase='needs_input',questions=[spec.name+'：'+q for q in result.get('questions',[])],detail='工况参数尚未完整');return
            cfg=Simulation.model_validate(result['config']).model_dump()
            if spec.initial_from:
                parent=configs[spec.initial_from]
                for field in ('model_id','mesh_size_m','base_material','component_materials','regions'):
                    if cfg[field]!=parent[field]:raise ValueError(spec.name+'：续算工况必须保留父工况的模型、网格和材料')
            row['config']=cfg;configs[spec.key]=cfg;cases.append(row);save_cases(wid,cases)
            activity(wid,'agent','configuration','已完成配置并通过校验：'+spec.name,case_key=spec.key,
                details=result.get('changes',[])+result.get('warnings',[]))
        if cancelled(wid):update(wid,phase='cancelled',detail='计划生成已取消');return
        update(wid,phase='ready',plan_complete=True,detail='所有工况已通过Agent校验，整体确认后按依赖执行',questions=[])
        activity(wid,'host','confirmation','Agent配置已完成，请核对工况后整体确认')
    except Exception as error:
        activity(wid,'host','error','规划未完成：'+str(error),'failed')
        update(wid,phase='cancelled' if cancelled(wid) else 'failed',detail=str(error)[:1200])


def retry_plan(wid,recheck=False):
    with LOCK:
        w=get(wid,False)
        if w['phase'] in ACTIVE:return dict(id=wid,phase=w['phase'])
        if w.get('plan_complete') and not recheck:return dict(id=wid,phase=w['phase'])
        if recheck:
            for case in w['cases']:case['status']='pending_review'
            update(wid,plan_complete=False,report_ready=False,cases=w['cases'])
        evidence=model_evidence(MODELS/w['model_id'])
        if any(evidence[k]!=w['geometry'][k] for k in ('source_sha256','bounds_m','dimensions_m')):raise ValueError('模型已改变，请重新生成计划')
        blueprint_path=folder(wid)/'blueprint.json'
        if not blueprint_path.exists():blueprint_path=folder(wid)/'blueprint-raw.json'
        blueprint=Blueprint.model_validate(read_json(blueprint_path)) if blueprint_path.exists() else None
        request=WorkflowRequest(model_id=w['model_id'],prompt=w['prompt'],config=w['base_config'])
        update(wid,phase='planning',cancel_requested=False,questions=[],detail='正在继续校验未完成工况')
        activity(wid,'user','request','要求Agent重新校验全部工况' if recheck else '要求Agent继续校验未完成工况')
        thread=threading.Thread(target=prepare,args=(wid,request,blueprint),daemon=True,name='workflow-retry-'+wid[:8]);THREADS[wid]=thread;thread.start()
    return dict(id=wid,phase='planning')


def reusable(cfg):
    sig=signature(cfg)
    for p in sorted(JOBS.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            if read_json(p/'status.json')['phase']!='completed':continue
            if not all((p/f).is_file() for f in ('config.json','audit.json','mesh.npz','temperatures.npy','report.pdf')):continue
            if signature(read_json(p/'config.json'))==sig:return p.name
        except (OSError,ValueError,KeyError):continue
    return None


def verify_result(jid,cfg,parent=None):
    p=JOBS/jid;a=read_json(p/'audit.json')
    if read_json(p/'status.json')['phase']!='completed':raise ValueError('算例尚未完成')
    if signature(read_json(p/'config.json'))!=signature(cfg):raise ValueError('算例配置与计划不一致')
    times=[s['time_s'] for s in a['stats']]
    temperatures=np.load(p/'temperatures.npy',mmap_mode='r')
    with np.load(p/'mesh.npz') as mesh:
        nodes=len(mesh['points']);cells=len(mesh['tets'])
    if temperatures.shape!=(len(times),nodes) or not np.isfinite(temperatures).all():raise ValueError('温度场帧数、节点数或数值无效')
    if cfg.get('structural'):
        for name,count in [('displacement.bin',nodes*3),('stress.bin',cells*6)]:
            if not (p/name).is_file() or (p/name).stat().st_size!=len(times)*count*4:raise ValueError('结构结果文件缺失或长度不符：'+name)
    energy=a['energy']
    if cfg['analysis_mode']=='steady':
        residual=abs(energy.get('steady_residual',energy.get('energy_balance_error_J',0)))
        tolerance=1e-6*max(1,sum(h['power_W'] for h in cfg['heat_sources']))
    else:
        residual=abs(energy.get('energy_balance_error_J',0));tolerance=1e-6*max(1,sum(abs(energy.get(k,0)) for k in ('initial_stored_energy_J','input_energy_J','convective_energy_J')))
    if not np.isfinite(residual) or residual>tolerance:raise ValueError('能量平衡校核未通过')
    missing=[point['time_s'] for point in cfg.get('ambient_profile',[]) if not any(abs(t-point['time_s'])<1e-7 for t in times)]
    if missing:raise ValueError('环境转折点未保存：'+str(missing))
    equilibrium=max((x.get('relative_equilibrium_residual',0) for x in a['stats']),default=0)
    if not np.isfinite(equilibrium) or equilibrium>1e-6:raise ValueError('力学平衡校核未通过')
    evidence=dict(saved_frames=len(times),all_profile_knots_saved=True,energy=a['energy'],maximum_relative_equilibrium_residual=equilibrium,warnings=a.get('warnings',[]),peak_scope='saved_frames')
    if cfg.get('surface_evaluation'):
        value=a.get('surface_evaluation')
        if not value or value['area_m2']<=0:raise ValueError('接触面结果选区为空')
        evidence['contact_area_m2']=value['area_m2']
    if parent:
        with np.load(p/'mesh.npz') as m,np.load(JOBS/parent/'mesh.npz') as m0:
            if any(not np.array_equal(m[k],m0[k]) for k in ('points','tets')):raise ValueError('续算网格未与父结果一致')
        a0=np.load(JOBS/parent/'temperatures.npy',mmap_mode='r');t=np.load(p/'temperatures.npy',mmap_mode='r')
        error=float(np.max(np.abs(t[0]-a0[-1])))
        if error>max(1e-5,float(np.max(np.abs(a0[-1])))*3e-7):raise ValueError('续算初温未继承完整父温度场')
        evidence.update(parent_job=parent,initial_field_max_difference_C=error,identical_mesh=True)
    return evidence


def start(wid,submit,status,cancel_job):
    with LOCK:
        w=get(wid,False)
        if w['phase'] in ('running','reporting','completed'):return dict(id=wid,phase=w['phase'])
        if w['phase'] not in ('ready','failed','interrupted','cancelled','partial') or not w.get('plan_complete') or not w['cases'] or any('config' not in c for c in w['cases']):raise ValueError('计划尚未通过校验')
        evidence=model_evidence(MODELS/w['model_id'])
        if any(evidence[k]!=w['geometry'][k] for k in ('source_sha256','bounds_m','dimensions_m')):raise ValueError('模型已改变，请重新生成计划')
        update(wid,phase='running',cancel_requested=False,detail='开始按依赖执行或复用完成结果')
        activity(wid,'user','confirmation','已确认工况计划，开始执行')
        thread=threading.Thread(target=execute,args=(wid,submit,status,cancel_job),daemon=True,name='workflow-run-'+wid[:8]);THREADS[wid]=thread;thread.start()
    return dict(id=wid,phase='running')


def execute(wid,submit,status,cancel_job):
    cases=get(wid,False)['cases'];finished={};failed=set();active={};prepared={}
    pending=list(cases);limit=execution_policy()['max_parallel_jobs']
    def fail(c,detail,skipped=False):
        c.update(status='skipped' if skipped else 'failed',detail=detail);failed.add(c['key'])
        activity(wid,'host','execution',c['name']+'：'+detail,'failed',c['key'])
        save_cases(wid,cases)
    def verify(c,jid):
        c.update(status='verifying');save_cases(wid,cases)
        try:
            c['verification']=verify_result(jid,c['effective_config'],finished.get(c.get('initial_from')))
            c.update(status='completed',progress=100,detail='结果校核通过');finished[c['key']]=jid
            activity(wid,'host','verification','结果校核通过：'+c['name'],case_key=c['key'],details=['已复用现有结果' if c.get('reused') else '本次完成求解'])
        except Exception as error:fail(c,'结果校核失败：'+str(error))
        save_cases(wid,cases)
    def stop_active():
        for key,c in list(active.items()):
            try:cancel_job(c['job_id'])
            except Exception as error:activity(wid,'host','error','停止算例失败：'+str(error),'failed',key)
            c.update(status='cancelled',detail='工作流已停止，已完成结果保留')
        active.clear();save_cases(wid,cases)
    try:
        activity(wid,'host','execution',f'按温度场依赖调度，最多同时计算 {limit} 个工况；提交时检查可用内存')
        while pending or active:
            if cancelled(wid):break
            for key,c in list(active.items()):
                state=status(c['job_id']);c['detail']=state.get('detail','');c['progress']=state.get('progress',0)
                if state['phase'] in ('running','pending'):continue
                del active[key]
                if state['phase']=='completed':verify(c,c['job_id'])
                else:fail(c,'求解未完成：'+state.get('detail',state['phase']))
            for c in list(pending):
                if cancelled(wid):break
                dependency=c.get('initial_from')
                if dependency in failed:
                    pending.remove(c);fail(c,'前置温度场未通过校核，已跳过',skipped=True);continue
                if dependency and dependency not in finished:continue
                if len(active)>=limit:break
                try:
                    if c['key'] not in prepared:
                        cfg=copy.deepcopy(c['config'])
                        if dependency:
                            # Bind only a verified parent field, through the configuration Agent.
                            binding=f'从算例“{finished[dependency]}”的末帧温度场续算。'
                            activity(wid,'agent','binding','正在通过配置Agent绑定完整初始温度场：'+c['name'],'started',c['key'])
                            result=agent.plan_request(cfg['model_id'],binding,cfg)
                            if not result['ok']:raise ValueError('续算绑定未通过Agent：'+str(result['questions']))
                            cfg=result['config'];c['binding_prompt']=binding
                            if cfg.get('initial_from_job')!=finished[dependency]:raise ValueError('Agent未绑定已校核的前置结果')
                            activity(wid,'agent','binding','温度场续算已通过配置Agent校验：'+c['name'],case_key=c['key'],details=[binding])
                        c['effective_config']=cfg;prepared[c['key']]=cfg
                    if cancelled(wid):break
                    cfg=prepared[c['key']];jid=reusable(cfg)
                    if jid:
                        pending.remove(c);c.update(job_id=jid,reused=True)
                        activity(wid,'host','execution','找到配置匹配的已有结果，正在校核：'+c['name'],'started',c['key'])
                        verify(c,jid);continue
                    c.update(status='queued',reused=False)
                    try:jid=submit(Simulation.model_validate(cfg))['id']
                    except Exception as error:
                        if getattr(error,'status_code',None)!=409 or '已有算例正在计算' not in str(getattr(error,'detail','')):raise
                        c['detail']='等待并行槽位或可用内存';break
                    pending.remove(c);c.update(job_id=jid,status='running',progress=0,detail='计算已启动')
                    active[c['key']]=c
                    activity(wid,'host','execution','有限元求解已启动：'+c['name'],'started',c['key'])
                except Exception as error:
                    pending.remove(c);fail(c,str(error))
            save_cases(wid,cases)
            if cancelled(wid):break
            if pending and not active and all(c.get('initial_from') and c['initial_from'] not in finished for c in pending):
                for c in pending:fail(c,'前置温度场不可用，已跳过',skipped=True)
                pending.clear()
            if active or pending:
                detail='；'.join(c['name']+'：'+c.get('detail','计算中') for c in active.values()) or '等待并行槽位或可用内存'
                update(wid,detail=f'并行计算 {len(active)}/{limit}：'+detail)
                time.sleep(2)
        if cancelled(wid):
            stop_active()
            for c in pending:c.update(status='cancelled',detail='已取消，未提交计算')
            save_cases(wid,cases);update(wid,phase='cancelled',detail='任务已取消；已完成工况保留');return
        update(wid,phase='reporting',detail='正在汇总真实计算结果及未完成项目')
        activity(wid,'host','reporting','正在整理实际结果、指标对比和中文综合报告','started')
        from thermal_legacy.workflow_report import generate
        generate(folder(wid),get(wid,False))
        if cancelled(wid):update(wid,phase='cancelled',detail='任务已取消，已生成文件保留');return
        complete=all(c['status']=='completed' for c in cases)
        update(wid,phase='completed' if complete else 'partial',detail='综合报告已生成' if complete else '已生成部分结果报告，请查看未完成工况',report_ready=True)
        activity(wid,'host','reporting','综合报告已生成，可下载PDF、指标CSV和配置包',status='completed' if complete else 'partial')
    except Exception as error:
        stop_active()
        activity(wid,'host','error','执行未完成：'+str(error),'failed')
        update(wid,phase='cancelled' if cancelled(wid) else 'failed',detail=str(error)[:1200])


def cancel(wid):
    with LOCK:
        w=get(wid,False)
        if w['phase'] in ('completed','partial','cancelled'):return w
        activity(wid,'user','request','请求停止任务，保留已完成结果')
        return update(wid,cancel_requested=True,phase=w['phase'] if w['phase'] in ACTIVE else 'cancelled',detail='正在停止；已有结果保留')
