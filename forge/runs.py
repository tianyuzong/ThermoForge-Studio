"""Persistent project-scoped study graphs and an explicit legacy demo template."""
import json,os,threading,time,subprocess,sys,shutil,zipfile
from pathlib import Path
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import Field
from strength.config import ROOT,DATA
from strength.schemas import Strict
from strength import store,service
from .schemas import AnalysisCase
from . import api
from .provenance import digest,file_hash,solver_signature,case_signature

router=APIRouter(prefix='/api/v1/analysis-runs')
VERSION='motor-bearing-1'
TERMINAL={'completed','partial','failed','cancelled','interrupted'}
LOCK=threading.RLock();THREADS={};EXPORTS={};EXPORT_LOCK=threading.Lock()
SPECS=[('static','电机静力','motor','static.json'),('fatigue','电机高周疲劳','motor','fatigue.json'),
       ('thermal_steady','电机稳态热','motor','thermal_steady.json'),('thermal_transient','电机瞬态热','motor','thermal_transient.json'),
       ('thermo_mechanical','电机瞬态双向接触','motor','thermo-contact-transient.json'),('contact_fatigue','局部轴承接触疲劳与磨损','bearing','case.json')]


class RunRequest(Strict):
    request_id:str=Field(min_length=1,max_length=150)
    project_id:str|None=None
    prompt:str='运行全套分析'
    force:bool=False
    plan_id:str|None=None
    template: str|None=None


def folder(id):return DATA/'analysis-runs'/id
def get(id):return store.get('analysis_run',id)
def patch(id,**values):return store.update('analysis_run',id,**values)
def stopped(id):return get(id).get('cancel_requested',False)


def intent(prompt):
    import re
    text=re.sub(r'[\s，,。、！!：:；;]','',prompt)
    starts=('运行全套分析','运行电机全套分析','力学热学全仿','一键全仿','强制重新计算全套分析')
    endings=('','生成动画视频和中文报告','并生成动画视频和中文报告',
             '包含力学热学疲劳热结构耦合和局部轴承接触生成动画视频和中文报告',
             '包括力学热学疲劳热结构耦合和局部轴承接触生成动画视频和中文报告')
    if text not in {a+b for a in starts for b in endings}:
        raise ValueError('请输入“运行全套分析”或页面中的标准指令。此入口使用已保存的电机＋局部轴承参数；自定义条件需在研究配置中明确修改，不能静默忽略。')
    return '强制' in text


def project_for(family):
    directory=ROOT/'examples'/('motor-unified' if family=='motor' else 'local-bearing')
    source=directory/('motor-simplified-10.step' if family=='motor' else 'source.step')
    sha=file_hash(source)
    for p in store.listing('project',10000):
        if p.get('example_family')!=('motor-unified' if family=='motor' else 'local-bearing'):continue
        try:
            geo=store.get('model',p['model_id'])
            model=DATA/'models'/p['model_id'];source_info=json.loads((model/'source.json').read_text(encoding='utf-8'))
            if geo['source_sha256']==sha and file_hash(model/source_info['file'])==sha:return p,geo
        except (KeyError,FileNotFoundError):pass
    p=api.motor_example() if family=='motor' else api.benchmark_example('local-bearing')
    return p,store.get('model',p['model_id'])


def preset_key(case,geo,signature):return digest({'case':case_signature(case,geo),'solver':signature,'suite':VERSION})


def build_nodes(signature):
    projects={f:project_for(f) for f in ('motor','bearing')};nodes=[]
    for key,label,family,name in SPECS:
        project,geo=projects[family];path=ROOT/'examples'/('motor-unified' if family=='motor' else 'local-bearing')/name
        case=AnalysisCase.model_validate_json(path.read_text(encoding='utf-8'));case.resources.cpu_threads=6
        case=api.freeze_named_selections(case,geo);api.validate(case,geo)
        node={'key':key,'label':label,'project_id':project['id'],'model_id':geo['id'],'case':case.model_dump(),'status':'pending','study_id':None,'reused':False,'refinements':[]}
        if key=='thermo_mechanical':
            node['preset_key']=preset_key(case,geo,signature)
            cache=DATA/'run-presets'/(node['preset_key']+'.json')
            if cache.exists():
                cached=json.loads(cache.read_text(encoding='utf-8'))
                if valid_validation(cached.get('validation')):
                    node['case']=cached['case'];node['validation']=cached['validation']
        nodes.append(node)
    return nodes


def valid_validation(value):
    if not value or not value.get('passed'):return False
    try:
        checks=value['checks'];hashes=value['raw_sha256'];coarse=value['coarse'];fine=value['fine']
        if not checks or not hashes or coarse==fine or not {coarse,fine}<=hashes.keys():return False
        import math
        if not all(c.get('passed') is True and all(math.isfinite(c[k]) for k in ('value','limit') if k in c) for c in checks):return False
        return all(file_hash(DATA/'studies'/sid/'fields.h5')==sha for sid,sha in hashes.items())
    except (OSError,KeyError,TypeError):return False


def source_versions_match(config,signature,seen=None):
    """A new post-processing record cannot upgrade legacy native provenance."""
    if config.get('solver_signature')!=signature:return False
    seen=set() if seen is None else seen
    for key in ('resume_from','reprocess_from'):
        parent=config.get(key)
        if not parent:continue
        if parent in seen:return False
        path=DATA/'studies'/parent/'config.json'
        try:
            prior=json.loads(path.read_text(encoding='utf-8'))
            unsigned={k:v for k,v in prior.items() if k!='sha256'}
            if __import__('hashlib').sha256(json.dumps(unsigned,sort_keys=True).encode()).hexdigest()!=prior.get('sha256'):return False
            if not source_versions_match(prior,signature,seen|{parent}):return False
        except (OSError,ValueError,TypeError):return False
    return True


def intact(study,case,geo,signature):
    if study.get('status')!='completed' or study.get('model_id')!=geo['id']:return False
    source=DATA/'studies'/study['id']
    try:
        config=json.loads((source/'config.json').read_text(encoding='utf-8'))
        if not source_versions_match(config,signature,{study['id']}) or config.get('case_signature')!=case_signature(case,geo):return False
        unsigned={k:v for k,v in config.items() if k!='sha256'}
        if __import__('hashlib').sha256(json.dumps(unsigned,sort_keys=True).encode()).hexdigest()!=config.get('sha256'):return False
        manifest=json.loads((source/'raw-fields.sha256.json').read_text(encoding='utf-8'))
        if file_hash(source/'fields.h5')!=manifest['sha256']:return False
        if json.loads((source/'summary.json').read_text(encoding='utf-8'))!=study.get('summary'):return False
        import numpy as np,h5py
        with np.load(source/'mesh.npz') as mesh,h5py.File(source/'fields.h5') as h:
            if not np.array_equal(mesh['points'],h['points'][:]) or not np.array_equal(mesh['tets'],h['tets'][:]):return False
            if len(h['times_s'])!=study['summary']['frames']:return False
            if case.analysis_type=='contact_fatigue' and 'wear_history' not in h:return False
        return all((source/n).is_file() and (source/n).stat().st_size>0 for n in ('summary.json','audit.json'))
    except (OSError,ValueError,KeyError,TypeError):return False


def save_node(id,index,node):
    with LOCK:
        def change(run):
            nodes=run['nodes'];value=dict(node)
            if run.get('cancel_requested') and value.get('status') not in ('completed','partial','failed','blocked'):
                value.update(status='cancelled',detail='套件已取消，已完成阶段保留')
            nodes[index]=value
            return {**run,'nodes':nodes}
        store.mutate('analysis_run',id,change)


def demo_refinement(run,node):
    return bool(node.get('time_step_validation')) or run.get('suite_version')==VERSION and node['key']=='thermo_mechanical'


def resolve_dependencies(id,index):
    run=get(id);node=run['nodes'][index];lookup={n['key']:n for n in run['nodes']}
    bad=[k for k in node.get('depends_on',[]) if lookup[k]['status']!='completed']
    if bad:
        node.update(status='blocked',blocked_study_id=node.get('study_id'),study_id=None,detail='前置工况未完整完成：'+', '.join(bad));save_node(id,index,node);return False
    parent=node.get('initial_temperature_from')
    if parent:
        case=AnalysisCase.model_validate(node['case']);case.thermal.initial_from_study=lookup[parent]['study_id']
        node['case']=case.model_dump();save_node(id,index,node)
    return True


def own_study(id,index,case,tag,resume_from=None):
    with LOCK:
        if stopped(id):raise InterruptedError('已取消')
        run=get(id);node=run['nodes'][index]
        study=api.submit(node['project_id'],api.StudyRequest(case=case,request_id=f'run:{id}:{index}:{tag}:g{run.get("generation",0)}',resume_from_study=resume_from,
            source_case_id=node.get('source_case_id'),source_revision=node.get('source_revision'),parameter_sources=node.get('parameter_sources',{})))
        owned=list(dict.fromkeys([*run.get('owned_studies',[]),study['id']]));patch(id,owned_studies=owned)
    return study


def wait_study(id,study):
    while True:
        if stopped(id):raise InterruptedError('已取消')
        study=store.get('study',study['id'])
        if study['status'] in api.TERMINAL:return study
        patch(id,detail=study.get('detail','求解中'));time.sleep(1)


def bind_validation(node,study,geo,signature):
    """Recheck a fresh result against the accepted coarse reference.

    A forced calculation gets a new fine field archive; historical verification
    must not be attached to it without comparing that new archive.
    """
    value=node.get('validation')
    if not valid_validation(value):return None
    if value.get('fine')==study['id']:return value
    coarse=store.get('study',value['coarse'])
    expected=AnalysisCase.model_validate(study['case']);expected.thermal.dt_s*=2
    if not intact(coarse,expected,geo,signature):return None
    from .run_validation import compare
    try:return compare(DATA/'studies'/coarse['id'],DATA/'studies'/study['id'])
    except ValueError:return None


def solve_node(id,index):
    run=get(id);node=run['nodes'][index];case=AnalysisCase.model_validate(node['case']);geo=store.get('model',node['model_id'])
    candidate=None
    # Resume a completed refinement instead of reverting to the original coarse
    # input. An unfinished refinement is recorded before waiting for its worker.
    refined=node.get('refinement_coarse_id')
    if refined:
        previous=store.get('study',refined);refined_case=AnalysisCase.model_validate(previous['case'])
        if intact(previous,refined_case,geo,run['solver_signature']):
            candidate=previous;case=refined_case
        else:
            node.update(refinement_coarse_id=None,refinement_level=0,active_refinement=None,refinements=[],validation=None)
            save_node(id,index,node)
    if not run['force']:
        candidate=candidate or next((s for s in store.listing('study',10000,project_id=node['project_id'],model_id=geo['id']) if intact(s,case,geo,run['solver_signature'])),None)
    if candidate:
        node.update(study_id=candidate['id'],status='completed',reused=candidate['id'] not in run.get('owned_studies',[]));save_node(id,index,node)
    else:
        node.update(status='running');save_node(id,index,node)
        attempts=node.get('attempts',0)
        if node.get('study_id'):
            existing=store.get('study',node['study_id'])
            if existing['status'] not in api.TERMINAL:study=existing
            elif existing['status']=='completed' and intact(existing,case,geo,run['solver_signature']):study=existing
            else:
                attempts+=1
                same=case_signature(AnalysisCase.model_validate(existing['case']),geo)==case_signature(case,geo)
                study=own_study(id,index,case,f'solve-{attempts}',existing['id'] if same else None)
        else:study=own_study(id,index,case,f'solve-{attempts}')
        node.update(study_id=study['id'],attempts=attempts);save_node(id,index,node)
        candidate=wait_study(id,study);node.update(status=candidate['status'],detail=candidate.get('detail'));save_node(id,index,node)
    if demo_refinement(run,node) and node['status']=='completed' and node.get('validation'):
        node['validation']=bind_validation(node,candidate,geo,run['solver_signature']);save_node(id,index,node)
        if valid_validation(node.get('validation')) and node.get('preset_key'):
            store.write_json(DATA/'run-presets'/(node['preset_key']+'.json'),{'case':candidate['case'],'validation':node['validation']})
    if demo_refinement(run,node) and node['status']=='completed' and not valid_validation(node.get('validation')):
        from .run_validation import compare
        coarse=candidate
        node.update(refinement_coarse_id=coarse['id'],status='running');save_node(id,index,node)
        maximum=node.get('max_time_step_halvings',6)
        for level in range(node.get('refinement_level',0)+1,maximum+1):
            fine_case=AnalysisCase.model_validate(coarse['case']);fine_case.thermal.dt_s/=2
            fine_case.name=node['label']+' · 时间步 '+str(fine_case.thermal.dt_s)+' s' if node.get('label') else '时间步验证 '+str(fine_case.thermal.dt_s)+' s'
            patch(id,detail=f'时间步验证 {level}/{maximum}：{fine_case.thermal.dt_s:g} s')
            active=node.get('active_refinement') or {}
            prior=store.get('study',active['study_id']) if active.get('coarse_id')==coarse['id'] and active.get('level')==level else None
            if prior and (prior['status'] not in api.TERMINAL or intact(prior,fine_case,geo,run['solver_signature'])):
                fine=prior
            else:
                fine=own_study(id,index,fine_case,f'refine-{level}-{node.get("attempts",0)}',prior['id'] if prior else None)
            node['active_refinement']={'study_id':fine['id'],'coarse_id':coarse['id'],'level':level,'dt_s':fine_case.thermal.dt_s}
            save_node(id,index,node)
            fine=wait_study(id,fine)
            if fine['id'] not in node['refinements']:node['refinements'].append(fine['id'])
            save_node(id,index,node)
            if fine['status']!='completed':node['validation']={'passed':False,'detail':fine.get('detail')};break
            validation=compare(DATA/'studies'/coarse['id'],DATA/'studies'/fine['id']);node['validation']=validation
            node.update(study_id=fine['id'],resolved_case=fine_case.model_dump(),reused=False,active_refinement=None,refinement_level=level,refinement_coarse_id=fine['id'])
            store.write_json(DATA/'studies'/fine['id']/'run-step-validation.json',validation)
            save_node(id,index,node)
            if validation['passed']:
                if node.get('preset_key'):store.write_json(DATA/'run-presets'/(node['preset_key']+'.json'),{'case':fine_case.model_dump(),'validation':validation})
                node['status']='completed'
                break
            coarse=fine
        if not node.get('validation',{}).get('passed'):node['status']='partial';node['detail']='瞬态双向接触时间步验证未通过'
        save_node(id,index,node)


def launch(id):
    with LOCK:
        if id in THREADS and THREADS[id].is_alive():return
        thread=threading.Thread(target=execute,args=(id,),daemon=True,name='analysis-run-'+id[:8]);THREADS[id]=thread;thread.start()


def execute(id):
    try:
        run=get(id)
        run=store.mutate('analysis_run',id,lambda current:None if current.get('cancel_requested') else {**current,'status':'running','phase':'solving','detail':f'检查 {len(current["nodes"])} 项研究与可复用结果'})
        if run.get('cancel_requested'):raise InterruptedError('已取消')
        if run['solver_signature']!=solver_signature():raise ValueError('求解程序版本已改变；请新建全套任务，旧快照保留')
        for i,node in enumerate(run['nodes']):
            if stopped(id):raise InterruptedError('已取消')
            if not resolve_dependencies(id,i):continue
            node=get(id)['nodes'][i]
            if node['status']=='completed' and node.get('study_id'):
                case=AnalysisCase.model_validate(node.get('resolved_case',node['case']))
                if intact(store.get('study',node['study_id']),case,store.get('model',node['model_id']),run['solver_signature']) and (not demo_refinement(run,node) or valid_validation(node.get('validation')) and node['validation'].get('fine')==node['study_id']):continue
            try:solve_node(id,i)
            except InterruptedError:raise
            except Exception as exc:
                node=get(id)['nodes'][i];node.update(status='failed',detail=str(exc));save_node(id,i,node)
        if stopped(id):raise InterruptedError('已取消')
        patch(id,phase='exporting',detail='准备网页、MP4、中文汇总报告与数据包')
        while not EXPORT_LOCK.acquire(timeout=.5):
            if stopped(id):raise InterruptedError('已取消')
        try:
            if stopped(id):raise InterruptedError('已取消')
            log=folder(id)/'export.log'
            with log.open('ab') as stream:
                with LOCK:
                    if stopped(id):raise InterruptedError('已取消')
                    process=subprocess.Popen([sys.executable,'-m','forge.run_exports',id],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONIOENCODING':'utf-8'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));EXPORTS[id]=process
                while process.poll() is None:
                    if stopped(id):service.agent.terminate_tree(process);raise InterruptedError('已取消')
                    time.sleep(.5)
                if process.returncode:raise RuntimeError('导出未完成，请查看导出日志；可继续任务单独重试导出')
        finally:EXPORTS.pop(id,None);EXPORT_LOCK.release()
        if stopped(id):raise InterruptedError('已取消')
        run=get(id);complete=all(n['status']=='completed' for n in run['nodes']) and all(a.get('status')=='completed' for a in run.get('exports',[]))
        store.mutate('analysis_run',id,lambda current:None if current.get('cancel_requested') else {**current,'status':'completed' if complete else 'partial','phase':'finished','detail':f'{len(run["nodes"])} 项分析及请求的导出已完成' if complete else '部分交付已生成；请查看未完成项'})
    except InterruptedError:patch(id,status='cancelled',phase='stopped',detail='任务已取消，完整计算与已生成产物保留')
    except Exception as exc:store.mutate('analysis_run',id,lambda current:None if current.get('cancel_requested') else {**current,'status':'failed','detail':str(exc)})


@router.post('',status_code=202)
def create(body:RunRequest,request:Request):
    if body.plan_id and body.template:raise ValueError('计划和示例模板不能同时使用')
    # Legacy clients must now explicitly opt in to the demo. A current project
    # plus prose can never silently switch to motor geometry.
    if not body.plan_id and body.template!='motor-bearing':raise ValueError('请先通过当前项目的 analysis-plans 接口解析指令；电机示例须显式指定 template=motor-bearing')
    plan=None
    if body.plan_id:
        from .plans import prepared_nodes
        nodes,plan=prepared_nodes(body.plan_id,body.project_id);force=body.force
        identity=digest({'nodes':nodes,'videos':plan['decision']['videos'],'force':force,'project':body.project_id})
    else:
        force=intent(body.prompt) or body.force;identity=digest({'template':VERSION,'project':body.project_id,'force':force})
    with LOCK:
        previous=store.request_lookup('analysis_run',body.request_id)
        if previous:return get(previous)
        if body.project_id:store.get('project',body.project_id)
        for run in store.listing('analysis_run',10000):
            if run['status'] not in TERMINAL and run.get('request_identity')==identity:
                store.request_save('analysis_run',body.request_id,run['id']);return run
        signature=solver_signature()
        if not plan:nodes=build_nodes(signature)
        id=store.uid();folder(id).mkdir(parents=True)
        origin=str(request.base_url).rstrip('/')
        run=store.put('analysis_run',{'id':id,'suite_version':'project-plan-1' if plan else VERSION,'origin_project_id':body.project_id,'force':force,'prompt':body.prompt,
            'title':plan['decision']['title'] if plan else '电机与局部轴承示例','plan_id':body.plan_id,'request_identity':identity,'videos':plan['decision']['videos'] if plan else True,
            'solver_signature':signature,'nodes':nodes,'owned_studies':[],'exports':[],'status':'queued','phase':'validating','detail':'配置校验通过','created_at':store.now(),'cancel_requested':False,'base_url':origin})
        store.write_json(folder(id)/'snapshot.json',run)
        if plan:
            from .plans import folder as plan_folder
            for name in ('context.json','decision.json'):shutil.copy2(plan_folder(plan['id'])/name,folder(id)/('plan-'+name))
        store.request_save('analysis_run',body.request_id,id);launch(id);return run


@router.get('')
def listing(project_id:str|None=None,limit:int=100,offset:int=0):
    return [with_export(record) for record in store.listing('analysis_run',min(max(limit,1),1000),project_id=project_id,offset=offset)]

@router.get('/{id}')
def read(id:str):
    return with_export(get(id))

def with_export(run):
    if run.get('latest_export_id'):
        try:run['latest_export']=store.get('export_task',run['latest_export_id'])
        except KeyError:pass
    return run

@router.get('/{id}/results/{key}')
def result_metadata(id:str,key:str):
    node=next((n for n in get(id)['nodes'] if n['key']==key),None)
    if not node or not node.get('study_id'):raise HTTPException(404)
    study=store.get('study',node['study_id'])
    if study['status'] not in ('completed','partial'):raise HTTPException(409,'研究结果尚未生成')
    from .run_exports import ranges,static_support_reactions
    source=DATA/'studies'/study['id']
    summary=json.loads((source/'summary.json').read_text(encoding='utf-8'))
    return {'study_id':study['id'],'ranges':ranges(source,summary),'reactions':static_support_reactions(source)}

@router.post('/{id}/cancel')
def cancel(id:str):
    with LOCK:
        run=get(id)
        if run['status'] in TERMINAL:return run
        def mark(value):
            for node in value['nodes']:
                if node.get('status') not in ('completed','partial','failed','blocked'):
                    node.update(status='cancelled',detail='套件已取消，已完成阶段保留')
            return {**value,'cancel_requested':True,'status':'cancelled','phase':'stopped','detail':'正在停止本套件的计算与导出'}
        store.mutate('analysis_run',id,mark)
        for sid in run.get('owned_studies',[]):
            if store.get('study',sid)['status'] not in api.TERMINAL:api.cancel(sid,cascade=False)
        if id in EXPORTS:service.agent.terminate_tree(EXPORTS[id])
        return get(id)


@router.post('/{id}/resume',status_code=202)
def resume(id:str):
    with LOCK:
        run=get(id)
        if run['status'] not in TERMINAL:return run
        if run['solver_signature']!=solver_signature():raise ValueError('求解程序版本已改变，请创建新套件；旧记录保持不变')
        if id in THREADS and THREADS[id].is_alive():raise ValueError('停止操作尚未结束，请稍后继续')
        patch(id,cancel_requested=False,status='queued',phase='recovering',detail='核验完成阶段并继续',generation=run.get('generation',0)+1);launch(id);return get(id)


@router.get('/{id}/files/{name:path}')
def files(id:str,name:str):
    from .exports import latest_root
    run=get(id);root=latest_root(run).resolve();path=(root/name).resolve()
    if not path.is_relative_to(root):raise HTTPException(404)
    if not path.is_file():root=folder(id).resolve();path=(root/name).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix not in ('.pdf','.mp4','.png','.zip','.json','.csv','.log'):raise HTTPException(404)
    return FileResponse(path,filename=path.name,headers={'Cache-Control':'no-store'})


def recover():
    for run in store.listing('analysis_run',10000):
        if run['status'] not in TERMINAL:
            for node in run['nodes']:
                if node.get('status')=='running':node.update(status='interrupted',detail='服务重启中断该阶段，可显式恢复')
            patch(run['id'],nodes=run['nodes'],status='interrupted',detail='服务重启；完整阶段保留，可继续未完成任务')
    from .exports import recover as recover_exports
    recover_exports()
