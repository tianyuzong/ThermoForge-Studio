import copy, hashlib, json, os, subprocess, sys, threading, time
from pathlib import Path
from .config import DATA,ROOT,CPU_THREADS,MAX_DOFS
from . import store,agent
from .geometry import validate_case, validate_execution, resolve_selection
from .schemas import LoadCase,TaskRequest,Selection
from .backends import resolve_backend
from .result_catalog import FIELDS
from .postprocess import compare_results
from .feedback import interpret
from .material_library import material_library

TERMINAL={'completed','partial','failed','cancelled','interrupted','draft'}
SLOT=threading.Semaphore(1)
RUNNING={}; THREADS={}; MUTEX=threading.RLock(); GPU_SLOTS=set()

def worker(action,folder,extra=None):
    with (folder/'worker.log').open('w',encoding='utf-8') as log:
        result=subprocess.run([sys.executable,'-m','strength.worker',action,str(folder),*(extra or [])],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            env={**os.environ,'PYTHONIOENCODING':'utf-8'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=240)
    if result.returncode:
        status=folder/'status.json'
        raise ValueError(json.loads(status.read_text(encoding='utf-8'))['detail'] if status.exists() else (folder/'worker.log').read_text(encoding='utf-8')[-1200:])

def recover():
    for kind in ('task','job'):
        for obj in store.listing(kind,10000):
            if obj.get('status') not in TERMINAL|{'needs_input'}:
                store.update(kind,obj['id'],status='interrupted',detail='服务已重启；原任务已中断，可基于保存的配置新建计算')

def is_cancelled(id): return store.get('task',id).get('cancel_requested',False)

def stage(id,name,text,details=None):
    if is_cancelled(id): raise InterruptedError('任务已取消')
    store.update('task',id,status=name,detail=text); store.event(id,name,text,details)

def tool(id,name,arguments,fn):
    if is_cancelled(id): raise InterruptedError('任务已取消')
    token=store.uid(); store.event(id,'tool',name,{'execution_id':token,'tool':name,'arguments':arguments},'started')
    try:
        value=fn()
        if is_cancelled(id): raise InterruptedError('任务已取消')
        # Persist facts returned by tools, never hidden model reasoning.
        summary=value if isinstance(value,(list,dict,str,int,float,type(None))) else '完成'
        store.event(id,'tool',name+' 完成',{'execution_id':token,'tool':name,'result':summary},'completed')
        return value
    except Exception as e:
        store.event(id,'tool',name+' 未完成',{'execution_id':token,'error':str(e)},'failed'); raise

def bind_case(case,evidence):
    case=case.model_copy(deep=True)
    def bind(s): return Selection(kind='faces',name=s.name,body_id=s.body_id,face_ids=resolve_selection(s,evidence))
    for s in case.supports: s.selection=bind(s.selection)
    for l in case.loads:
        if l.selection: l.selection=bind(l.selection)
    case.mesh.local_selections=[bind(s) for s in case.mesh.local_selections]
    if case.mesh.evaluation_selection: case.mesh.evaluation_selection=bind(case.mesh.evaluation_selection)
    for region in case.stress_regions: region.selection=bind(region.selection)
    for pair in case.bonded_connections:
        pair.slave=bind(pair.slave);pair.master=bind(pair.master)
    for query in case.result_requests:
        if query.selection:query.selection=bind(query.selection)
    if case.gear_contact:
        for hub in case.gear_contact.hubs:hub.bore=bind(hub.bore)
    return case

def task_view(id):
    t=store.get('task',id); t['events']=store.events(id)
    t['jobs']=[store.get('job',i) for i in t.get('job_ids',[])]
    t['report_available']=(DATA/'tasks'/id/'report.pdf').is_file()
    return t

def create_task(req,conversation=None,current_case=None):
    with MUTEX:
        old=store.request_lookup('task:'+req.project_id,req.request_id)
        if old: return task_view(old)
        project=store.get('project',req.project_id)
        if not project.get('model_id'): raise ValueError('请先导入工件或加载内置支架')
        id=store.uid(); mode='draft' if agent.draft_requested(req.prompt) else req.execution_mode
        history=copy.deepcopy(conversation or []); history.append({'role':'user','text':req.prompt,'time':store.now()})
        snapshot=current_case if current_case is not None else project.get('current_case')
        task={'id':id,'project_id':project['id'],'model_id':project['model_id'],'provider':req.provider,'execution_mode':mode,
            'status':'parsing','detail':'读取工件与会话上下文','conversation':history,'current_case':snapshot,'material':project.get('material'),
            'created_at':store.now(),'questions':[],'cases':[],'job_ids':[],'cancel_requested':False,
            'previous_jobs':project.get('last_job_ids',[]),'compare_with_previous':False,'backend_override':req.compute_backend,'compute_backend':agent.requested_backend(req.prompt) or req.compute_backend or project.get('compute_backend','calculix_cpu')}
        store.put('task',task); store.request_save('task:'+req.project_id,req.request_id,id)
        (DATA/'tasks'/id).mkdir(parents=True,exist_ok=True)
        launch(id); return task_view(id)

def launch(id,run_draft=False):
    thread=threading.Thread(target=execute_task,args=(id,run_draft),daemon=True,name='agent-'+id[:8]); THREADS[id]=thread; thread.start()

def message(id,req):
    with MUTEX:
        old=store.request_lookup('message:'+id,req.request_id)
        if old: return task_view(old)
        t=store.get('task',id)
        if t['status'] in {'completed','partial','failed','cancelled','interrupted'}:
            child=create_task(TaskRequest(project_id=t['project_id'],prompt=req.prompt,execution_mode='auto',provider=t['provider'],request_id='followup-'+req.request_id,compute_backend=t.get('compute_backend','calculix_cpu')),
                conversation=t['conversation'],current_case=t['cases'][-1] if t.get('cases') else t.get('current_case'))
            store.request_save('message:'+id,req.request_id,child['id']); return child
        if t['status'] not in ('needs_input','draft'): raise ValueError('任务正在运行；可以取消，或新建独立任务。')
        history=t['conversation']+[{'role':'user','text':req.prompt,'time':store.now()}]
        run_draft=t['status']=='draft' and agent.start_requested(req.prompt) and bool(t.get('cases'))
        mode='auto' if agent.start_requested(req.prompt) else t['execution_mode']
        selected_backend=agent.requested_backend(req.prompt) or t.get('compute_backend','calculix_cpu')
        if agent.draft_requested(req.prompt): mode='draft'
        store.update('task',id,conversation=history,status='parsing',questions=[],execution_mode=mode,detail='根据补充信息继续',cancel_requested=False,compute_backend=selected_backend)
        store.request_save('message:'+id,req.request_id,id); launch(id,run_draft); return task_view(id)

def cancel(id):
    with MUTEX:
        t=store.get('task',id)
        if t['status'] in {'completed','partial','failed','cancelled','interrupted'}: return task_view(id)
        store.update('task',id,cancel_requested=True,status='cancelled',detail='任务已取消；已完成结果保留')
        p=RUNNING.get(id)
        if p: agent.terminate_tree(p)
        for jid in t.get('job_ids',[]):
            j=store.get('job',jid)
            if j['status'] not in TERMINAL: store.update('job',jid,status='cancelled',detail='任务已取消')
        store.event(id,'cancelled','停止任务及全部关联计算')
    return task_view(id)

def run_case(task,case,index):
    evidence=json.loads((DATA/'models'/task['model_id']/'geometry.json').read_text(encoding='utf-8'))
    validate_execution(case,evidence)
    if case.assembly_mode=='gear_contact' and task.get('compute_backend')!='cudss_cuda':raise ValueError('齿轮接触工况要求 GPU cudss_cuda，请显式选择该后端')
    id=task['id']; signature=hashlib.sha256(json.dumps({'case':case.model_dump(),'backend':task.get('compute_backend','calculix_cpu'),'precision':'float64','solver_revision':4,'model_id':task['model_id']},sort_keys=True).encode()).hexdigest()
    token=f'{id}:{index}:{signature}'
    with MUTEX:
        if is_cancelled(id): raise InterruptedError('任务已取消')
        existing=store.request_lookup('job',token)
        if existing:
            j=store.get('job',existing)
            if j['status']=='completed': return j
            raise ValueError('同一动作已有执行记录，未重复提交')
        if len(store.get('task',id).get('job_ids',[]))>=12:raise ValueError('任务已达到 12 个工况上限')
        jid=store.uid(); folder=DATA/'jobs'/jid; folder.mkdir()
        geo=json.loads((DATA/'models'/task['model_id']/'geometry.json').read_text(encoding='utf-8'))
        config={'schema_version':2,'precision':'float64','solver_revision':4,'model_id':task['model_id'],'geometry_sha256':geo['source_sha256'],'case':case.model_dump(),
            'task_id':id,'project_id':task['project_id'],'config_sha256':signature,'compute_backend':task.get('compute_backend','calculix_cpu')}
        store.write_json(folder/'config.json',config)
        j={'id':jid,'task_id':id,'project_id':task['project_id'],'model_id':task['model_id'],'name':case.name,'case':case.model_dump(),
            'status':'queued','detail':'等待本机求解槽位','created_at':store.now()}
        store.put('job',j); store.request_save('job',token,jid)
        store.update('task',id,job_ids=store.get('task',id)['job_ids']+[jid])
    stage(id,'queued','工况已校验，自动提交：'+case.name,{'job_id':jid})
    if task.get('compute_backend')=='cudss_cuda': return run_gpu_case(task,case,jid,folder)
    while not SLOT.acquire(timeout=.2):
        if is_cancelled(id): store.update('job',jid,status='cancelled'); raise InterruptedError('任务已取消')
    try:
        if is_cancelled(id): raise InterruptedError('任务已取消')
        with (folder/'worker.log').open('w',encoding='utf-8') as log:
            with MUTEX:
                if is_cancelled(id): raise InterruptedError('任务已取消')
                for attempt in range(2):
                    try:
                        p=subprocess.Popen([sys.executable,'-m','strength.worker','solve',str(folder)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                            env={**os.environ,'PYTHONIOENCODING':'utf-8'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        break
                    except OSError:
                        if attempt: raise
                RUNNING[id]=p
            last=None;deadline=time.monotonic()+1800
            try:
                while p.poll() is None:
                    if is_cancelled(id): agent.terminate_tree(p); raise InterruptedError('任务已取消')
                    if time.monotonic()>deadline:raise TimeoutError('CPU 作业超过 1800 秒，已保留计算记录')
                    try:
                        state=json.loads((folder/'status.json').read_text(encoding='utf-8')); key=(state['status'],state.get('detail'))
                        if key!=last:
                            store.update('job',jid,**state)
                            stage(id,state['status'] if state['status'] not in TERMINAL else 'verifying',state['detail'],{'job_id':jid}); last=key
                    except (FileNotFoundError,json.JSONDecodeError,PermissionError): pass
                    time.sleep(.25)
            finally:
                if p.poll() is None: agent.terminate_tree(p)
                with MUTEX: RUNNING.pop(id,None)
        if is_cancelled(id): raise InterruptedError('任务已取消')
        state=json.loads((folder/'status.json').read_text(encoding='utf-8'))
        if p.returncode or state['status']!='completed': raise RuntimeError(state.get('detail','计算进程异常退出'))
        summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        j=store.update('job',jid,status='completed',detail='结果校核与报告完成',summary=summary)
        store.event(id,'verified',case.name+' 已完成',{'job_id':jid,'stress_MPa':summary['maximum_von_mises_Pa']/1e6,'displacement_mm':summary['maximum_displacement_m']*1000,'balance':summary['balance']})
        return j
    except InterruptedError:
        store.update('job',jid,status='cancelled',detail='已停止网格/求解/报告进程'); raise
    except Exception as e:
        store.update('job',jid,status='failed',detail=str(e)); raise
    finally: SLOT.release()

def comparison(jobs):
    return [{'job_id':j['id'],'name':j['name'],'stress_MPa':j['summary']['maximum_von_mises_Pa']/1e6,
        'displacement_mm':j['summary']['maximum_displacement_m']*1000,'safety_factor':j['summary']['minimum_safety_factor'],
        'strength':j['summary']['checks']['strength']['status'],'displacement':j['summary']['checks']['displacement']['status']} for j in jobs if j.get('summary')]

def close_gpu_task(id):
    with MUTEX:
        if id not in GPU_SLOTS:return
        GPU_SLOTS.remove(id);p=RUNNING.pop(id,None)
    try:
        if p:
            if p.stdin:p.stdin.close()
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:agent.terminate_tree(p)
    finally:SLOT.release()

def run_gpu_case(task,case,jid,folder):
    """One isolated CUDA process and one global slot per Agent task."""
    id=task['id']
    try:
        if id not in GPU_SLOTS:
            while not SLOT.acquire(timeout=.2):
                if is_cancelled(id):raise InterruptedError('任务已取消')
            with MUTEX:GPU_SLOTS.add(id)
        with MUTEX:
            if is_cancelled(id):raise InterruptedError('任务已取消')
            p=RUNNING.get(id)
            if p is None:
                with (folder/'worker.log').open('w',encoding='utf-8') as log:
                    for attempt in range(2):
                        try:
                            p=subprocess.Popen([sys.executable,'-m','strength.worker','gpu-session',str(DATA/'tasks'/id)],cwd=ROOT,
                                stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONIOENCODING':'utf-8'},
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));break
                        except OSError:
                            if attempt:raise
                RUNNING[id]=p
            elif p.poll() is not None:raise RuntimeError('GPU 进程已退出；未自动重启计算')
            p.stdin.write((json.dumps({'folder':str(folder)})+'\n').encode());p.stdin.flush()
        last=None;deadline=time.monotonic()+1800
        while True:
            if is_cancelled(id):raise InterruptedError('任务已取消')
            if p.poll() is not None:raise RuntimeError('GPU 工作进程异常退出；请检查 worker.log')
            if time.monotonic()>deadline:raise TimeoutError('GPU 工况超过 1800 秒时间限制')
            try:
                state=json.loads((folder/'status.json').read_text(encoding='utf-8'));key=(state['status'],state.get('detail'))
                if key!=last:
                    store.update('job',jid,**state);stage(id,state['status'] if state['status'] not in TERMINAL else 'verifying',state['detail'],{'job_id':jid});last=key
                if state['status']=='failed':raise RuntimeError(state['detail'])
                if state['status']=='completed':break
            except (FileNotFoundError,json.JSONDecodeError,PermissionError):pass
            time.sleep(.2)
        if is_cancelled(id):raise InterruptedError('任务已取消')
        summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        j=store.update('job',jid,status='completed',detail='GPU 计算、结果校核与报告完成',summary=summary)
        store.event(id,'verified',case.name+' GPU 已完成',{'job_id':jid,'execution':summary['execution'],'balance':summary['balance']})
        return j
    except InterruptedError:
        store.update('job',jid,status='cancelled',detail='GPU 任务已取消');raise
    except Exception as e:
        store.update('job',jid,status='failed',detail=str(e));close_gpu_task(id);raise

def result_message(jobs,failed):
    lines=[]
    for row in comparison(jobs):
        factor=f'{row["safety_factor"]:.3f}' if row['safety_factor'] is not None else '未评估'
        lines.append(f'{row["name"]}：最大等效应力 {row["stress_MPa"]:.4g} MPa，最大位移 {row["displacement_mm"]:.4g} mm，最小屈服安全系数 {factor}。')
    if jobs:
        controlling=max(jobs,key=lambda j:j['summary']['maximum_von_mises_Pa'])
        lines.append('最大应力控制工况：'+controlling['name']+'。')
        warnings=list(dict.fromkeys(w for j in jobs for w in j['summary']['warnings']))
        lines+=warnings
        if any(j['summary']['convergence']['status']=='not_verified' for j in jobs): lines.append('网格收敛尚未验证；可继续输入“对当前工况做网格收敛分析”。')
        if any(any(c['status']=='exceeded' for c in j['summary']['checks'].values()) for j in jobs): lines.append('存在超出已设限值的工况，请查看结果评估；可检查载荷、支撑与关键几何，并用新工况验证修改。')
        lines.append('PDF、CSV 和完整计算包已生成，所有数值均来自上述计算记录。')
    if failed: lines.append('未完成：'+'；'.join(failed))
    return '\n'.join(lines)

def execute_task(id,run_draft=False):
    try:
        task=store.get('task',id); project=tool(id,'read_project',{'project_id':task['project_id']},lambda:store.get('project',task['project_id']))
        if project['model_id']!=task['model_id']: raise ValueError('当前工件已改变，请针对新工件创建任务')
        geometry=tool(id,'query_geometry',{'model_id':task['model_id']},lambda:json.loads((DATA/'models'/task['model_id']/'geometry.json').read_text(encoding='utf-8')))
        if run_draft:
            cases=[LoadCase.model_validate(c) for c in task['cases']]
        else:
            stage(id,'parsing',{'cli':'Codex 正在理解仿真指令','api':'模型 API 正在理解仿真指令','local':'本地规则正在解析明确指令'}[task['provider']])
            context={'conversation':task['conversation'][-16:],'geometry':geometry,'material':project.get('material'),
                'component_materials':project.get('component_materials',{}),'material_library':material_library(),
                'saved_cases':[c['case'] for c in store.listing('saved_case',1000) if c['project_id']==task['project_id'] and c['model_id']==task['model_id']],
                'current_case':task.get('current_case'),'execution_mode':task['execution_mode'],
                'compute_backend':task.get('compute_backend','calculix_cpu'),
                'capabilities':{'default_backend':'calculix_cpu','cpu_threads':CPU_THREADS,'max_dofs':MAX_DOFS,
                    'convergence_mesh_factors':[1.,1.5,2.25],'analysis':['linear_static_solid','ideal_bonded_assembly'],'unsupported_on_cpu':['gear_contact','friction','plasticity','buckling','fatigue']},
                'result_catalog':[{'key':v['key'],'label':v['label'],'unit':v['unit']} for v in FIELDS.values()],
                'previous_results':[{'job_id':j,**{k:v for k,v in store.get('job',j).get('summary',{}).items() if k in ('name','maximum_displacement_m','maximum_von_mises_Pa','minimum_safety_factor','checks','warnings','convergence','displacement_peak','stress_peak','mesh')}} for j in task.get('previous_jobs',[])][-3:]}
            for attempt in range(3):
                try:
                    decision=agent.plan(context,task['provider'],DATA/'tasks'/id/'planner',lambda:is_cancelled(id))
                    if is_cancelled(id): raise InterruptedError('任务已取消')
                    if decision.action=='cancel': cancel(id); return
                    if decision.action=='clarify' or decision.questions:
                        candidates=[{'name':s.name or s.direction or '候选选区','face_ids':resolve_selection(s,geometry)} for s in decision.candidate_selections]
                        history=store.get('task',id)['conversation']+[{'role':'assistant','text':'\n'.join(decision.questions) or decision.message,'time':store.now()}]
                        store.update('task',id,status='needs_input',detail='等待补充关键条件',questions=decision.questions,conversation=history,candidate_selections=candidates)
                        store.event(id,'needs_input','需要补充关键条件',decision.questions); return
                    if decision.action=='explain':
                        jobs=[store.get('job',j) for j in task.get('previous_jobs',[])]
                        feedback=interpret(task,jobs,[],lambda:is_cancelled(id),lambda name,text:stage(id,name,text),decision.result_requests) if jobs else {'status':'not_requested','message':'当前尚无计算结果，请先指定工况。'}
                        msg=feedback['message']+'\n\n'+result_message(jobs,[])
                        if is_cancelled(id):return
                        store.update('task',id,status='completed',feedback=feedback,detail=msg,conversation=task['conversation']+[{'role':'assistant','text':msg,'time':store.now()}]); return
                    requested=agent.requested_backend(task['conversation'][-1]['text'])
                    selected=requested or task.get('backend_override') or decision.compute_backend or task.get('compute_backend','calculix_cpu')
                    task['compute_backend']=selected;store.update('task',id,compute_backend=selected)
                    cases=decision.cases
                    for c in cases:
                        if decision.result_requests and not c.result_requests:c.result_requests=decision.result_requests
                    if not cases: raise ValueError('规划未生成可执行工况')
                    resolve_backend(task.get('compute_backend','calculix_cpu'))
                    stage(id,'validating','校验材料、载荷、选区与工况边界')
                    for c in cases:
                        tool(id,'validate_case',{'name':c.name},lambda c=c:validate_case(c,geometry))
                        if task['execution_mode']!='draft': validate_execution(c,geometry)
                    cases=[bind_case(c,geometry) for c in cases]
                    store.update('task',id,cases=[c.model_dump() for c in cases],compare_with_previous=decision.compare_with_previous,
                        conversation=task['conversation']+[{'role':'assistant','text':decision.message,'time':store.now()}],candidate_selections=[])
                    break
                except (ValueError,json.JSONDecodeError) as e:
                    if attempt==2 or task['provider']=='local':
                        store.update('task',id,status='needs_input',detail='配置需要补充或修正',questions=[str(e)]); store.event(id,'needs_input','配置校验未通过',str(e)); return
                    context['validation_errors']=str(e); store.event(id,'repair','结构化配置校验失败，要求模型修正',{'attempt':attempt+1,'error':str(e)})
            store.update('project',task['project_id'],current_case=cases[-1].model_dump())
        if is_cancelled(id): return
        if store.get('task',id)['execution_mode']=='draft':
            stage(id,'draft','草案已校验；尚未求解。输入“开始计算”执行。'); return
        try:
            for c in cases:validate_execution(c,geometry)
        except ValueError as e:
            store.update('task',id,status='needs_input',detail='组件配置需要补充',questions=[str(e)])
            store.event(id,'needs_input','连接或组件配置尚未满足求解条件',str(e));return
        done=[]; failed=[]
        for i,c in enumerate(cases):
            if is_cancelled(id): return
            try: done.append(run_case(task,c,i))
            except InterruptedError: raise
            except Exception as e: failed.append(c.name+'：'+str(e)); store.event(id,'failed',c.name+' 计算失败',str(e),'failed')
        if is_cancelled(id): return
        alljobs=list(done)
        latest=store.get('task',id)
        if latest.get('compare_with_previous'):
            alljobs=[store.get('job',j) for j in task.get('previous_jobs',[])]+alljobs
        rows=tool(id,'compare_cases',{'job_ids':[j['id'] for j in alljobs]},lambda:comparison(alljobs))
        msg=result_message(alljobs,failed)
        feedback=interpret(task,alljobs,failed,lambda:is_cancelled(id),lambda name,text:stage(id,name,text))
        if is_cancelled(id):return
        store.update('task',id,feedback=feedback,result_comparison=compare_results(alljobs))
        if feedback['status']!='not_requested':msg+='\n\nCodex 结果分析\n'+feedback['message']
        folder=DATA/'tasks'/id; store.write_json(folder/'comparison.json',rows)
        from .report import write_comparison_csv, write_task_markdown
        write_comparison_csv(folder/'comparison.csv', rows)
        write_task_markdown(folder/'report.md', msg, alljobs)
        if alljobs:
            stage(id,'reporting','汇总图文分析报告与多工况对比')
            from .report import make_task_report
            tool(id,'generate_visual_report',{'job_ids':[j['id'] for j in alljobs]},lambda:make_task_report(folder,alljobs))
        if is_cancelled(id): return
        status='partial' if failed and done else ('failed' if failed else 'completed')
        history=store.get('task',id)['conversation']+[{'role':'assistant','text':msg,'time':store.now()}]
        store.update('task',id,status=status,detail=msg,comparison=rows,conversation=history,questions=[])
        if done: store.update('project',task['project_id'],last_job_ids=[j['id'] for j in done],current_case=cases[-1].model_dump())
        store.event(id,status,'任务结束；已保存真实计算结果',rows)
    except InterruptedError:
        if not is_cancelled(id): cancel(id)
    except Exception as e:
        if not is_cancelled(id):
            store.update('task',id,status='failed',detail=str(e)); store.event(id,'failed',str(e),status='failed')
    finally: close_gpu_task(id)

def submit_manual(project_id,case,request_id):
    req=TaskRequest(project_id=project_id,prompt='运行手动配置的工况',provider='local',request_id=request_id)
    with MUTEX:
        old=store.request_lookup('task:'+project_id,request_id)
        if old: return task_view(old)
        project=store.get('project',project_id)
        if not project.get('model_id'): raise ValueError('请先导入模型')
        geometry=json.loads((DATA/'models'/project['model_id']/'geometry.json').read_text(encoding='utf-8'))
        validate_execution(case,geometry)
        case=bind_case(case,geometry)
        id=store.uid(); store.put('task',{'id':id,'project_id':project_id,'model_id':project['model_id'],'provider':'local','execution_mode':'auto','status':'queued',
            'detail':'手动工况已提交','conversation':[{'role':'user','text':req.prompt,'time':store.now()}],'current_case':case.model_dump(),'material':case.material.model_dump(),
            'created_at':store.now(),'questions':[],'cases':[case.model_dump()],'job_ids':[],'cancel_requested':False,'previous_jobs':project.get('last_job_ids',[]),'compare_with_previous':False,'compute_backend':project.get('compute_backend','calculix_cpu')})
        store.request_save('task:'+project_id,request_id,id); (DATA/'tasks'/id).mkdir()
        launch(id,True); return task_view(id)
