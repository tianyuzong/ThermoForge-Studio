"""Independent, versioned export jobs. Publication never overwrites a prior generation."""
import json,os,shutil,subprocess,sys,threading,time
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from pydantic import Field
from strength import store,service
from strength.config import DATA,ROOT
from strength.schemas import Strict
from .provenance import digest,file_hash

router=APIRouter(prefix='/api/v1/analysis-runs')
PROCESSES={};THREADS={};LOCK=threading.RLock()
TERMINAL={'completed','partial','failed','cancelled','interrupted'}


def stopped(id):
    task=store.get('export_task',id)
    return task.get('cancel_requested',False) or task.get('status') in TERMINAL


def update_active(id,**patch):
    def apply(current):
        if current.get('cancel_requested') or current.get('status') in TERMINAL:return None
        return {**current,**patch}
    return store.mutate('export_task',id,apply)


def sync_progress(id,stage):
    """Consume only this child's newest progress, atomically preserving cancellation."""
    path=Path(stage)/'progress.json'
    try:
        if path.stat().st_size>65536:return
        record=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return
    if not isinstance(record,dict) or record.get('schema_version')!=1 or record.get('export_task_id')!=id:return
    sequence=record.get('sequence')
    if type(sequence) is not int or sequence<1 or not isinstance(record.get('detail'),str):return
    allowed={'sequence','updated_at','stage','detail','node_key','node_label','field','frame','frame_total',
             'completed_frames','physical_frame','physical_time_s','item','item_total','file','scope','complete'}
    progress={k:v for k,v in record.items() if k in allowed}
    def apply(current):
        if current.get('cancel_requested') or current.get('status') in TERMINAL:return None
        if current.get('run_id')!=record.get('run_id') or sequence<=current.get('progress',{}).get('sequence',0):return None
        return {**current,'detail':record['detail'][:2000],'phase':record.get('stage'),'progress':progress}
    return store.mutate('export_task',id,apply)


def export_version(run,raw):
    from .run_exports import renderer_fingerprint
    paths=[ROOT/'forge'/'report.py',ROOT/'forge'/'version.py',*sorted((ROOT/'forge'/'reporting').glob('*.py'))]
    report_code={p.relative_to(ROOT).as_posix():file_hash(p) if p.is_file() else None for p in paths}
    inputs={}
    for sid,sha in raw.items():
        source=DATA/'studies'/sid
        inputs[sid]={'fields_sha256':sha,**{name:file_hash(source/name) if (source/name).is_file() else None
            for name in ('config.json','summary.json','audit.json','run-step-validation.json','verification.json','initial-state.json')}}
        config_path=source/'config.json'
        config=json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
        try:
            project=store.get('project',config.get('project_id',''))
            if project.get('component_aliases_model_id')==config.get('model_id'):
                inputs[sid]['display_aliases']=project.get('component_aliases',{})
        except KeyError:pass
    return digest({'renderer':renderer_fingerprint(),'report_code':report_code,'report_inputs':inputs,
                   'run':{key:run.get(key) for key in ('title','nodes','verification','prompt','requested_prompt')}})


class ExportRequest(Strict):
    request_id:str=Field(min_length=1,max_length=150)
    scope:Literal['report','video','all']='all'


def latest_root(run):
    root=DATA/'analysis-runs'/run['id'];relative=run.get('export_generation')
    if relative:
        base=DATA if relative.startswith('exports/') else root
        candidate=(base/relative).resolve();allowed=(DATA/'exports').resolve() if base==DATA else root.resolve()
        if candidate.is_relative_to(allowed) and candidate.is_dir():return candidate
    return root


def launch(id):
    thread=threading.Thread(target=execute,args=(id,),daemon=True,name='export-'+id[:8]);THREADS[id]=thread;thread.start()


@router.post('/{id}/exports',status_code=202)
def create(id:str,body:ExportRequest):
    from . import runs
    with LOCK:
        run=runs.get(id)
        if run['status'] not in TERMINAL:raise HTTPException(409,'套件仍在执行，请等待求解或原导出停止')
        scope='export:'+id;old=store.request_lookup(scope,body.request_id)
        if old:return store.get('export_task',old)
        for task in store.listing('export_task',10000):
            if task.get('run_id')==id and task['status'] not in TERMINAL:
                if task['scope']!=body.scope:raise HTTPException(409,'此套件已有导出任务，请等待或取消后重试')
                store.request_save(scope,body.request_id,task['id']);return task
        raw={}
        for node in run['nodes']:
            sid=node.get('study_id')
            if not sid:continue
            study=store.get('study',sid)
            if study['status'] not in ('completed','partial'):continue
            source=DATA/'studies'/sid
            manifest=json.loads((source/'raw-fields.sha256.json').read_text(encoding='utf-8'))
            actual=file_hash(source/'fields.h5')
            if actual!=manifest.get('sha256'):raise ValueError('原始场校验失败，未启动导出：'+sid)
            raw[sid]=actual
        if not raw:raise ValueError('没有可导出的完整或部分原始结果')
        version=export_version(run,raw)
        tid=store.uid();task=store.put('export_task',{'id':tid,'run_id':id,'project_id':run.get('origin_project_id'),'scope':body.scope,'status':'queued',
            'detail':'等待导出资源，不启动求解','created_at':store.now(),'cancel_requested':False,'raw_sha256':raw,'export_version':version})
        store.request_save(scope,body.request_id,tid);runs.patch(id,latest_export_id=tid);launch(tid);return task


@router.get('/{id}/exports')
def listing(id:str):
    store.get('analysis_run',id)
    return [t for t in store.listing('export_task',10000) if t.get('run_id')==id]


@router.post('/{id}/exports/{export_id}/cancel')
def cancel(id:str,export_id:str):
    with LOCK:
        task=store.get('export_task',export_id)
        if task.get('run_id')!=id:raise HTTPException(404)
        if task['status'] in TERMINAL:return task
        task=store.update('export_task',export_id,cancel_requested=True,status='cancelled',detail='导出已取消，上一版本交付保留')
        process=PROCESSES.get(export_id)
        if process:service.agent.terminate_tree(process)
    return task


@router.get('/{id}/exports/{export_id}/log')
def export_log(id:str,export_id:str):
    task=store.get('export_task',export_id)
    if task.get('run_id')!=id:raise HTTPException(404)
    path=DATA/'exports'/export_id[:16]/'export.log'
    if not path.is_file():raise HTTPException(404,'该任务尚未生成导出日志')
    return FileResponse(path,media_type='text/plain; charset=utf-8',filename='export.log')


def execute(id):
    from . import runs
    task=store.get('export_task',id);run=runs.get(task['run_id']);acquired=False;process=None
    root=DATA/'analysis-runs'/run['id'];stage=DATA/'exports'/id[:16];generation=stage.relative_to(DATA).as_posix()
    try:
        while not runs.EXPORT_LOCK.acquire(timeout=.3):
            if stopped(id):return
        acquired=True
        if stopped(id):return
        update_active(id,status='running',phase='preparing',detail='校验原始场并准备独立导出版本')
        if stopped(id):return
        for sid,sha in task['raw_sha256'].items():
            if file_hash(DATA/'studies'/sid/'fields.h5')!=sha:raise ValueError('排队期间原始场发生变化')
        previous=latest_root(run);stage.mkdir(parents=True)
        # Retain reusable rendered media. Never hard-link files an exporter may rewrite.
        for p in previous.rglob('*'):
            relative=p.relative_to(previous)
            if 'export-generations' in relative.parts or not p.is_file() or p.name in ('bundle.zip','export.log','progress.json'):continue
            if p.suffix not in ('.png','.mp4','.pdf','.json','.csv','.md'):continue
            dest=stage/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        with (stage/'export.log').open('ab') as log:
            process=subprocess.Popen([sys.executable,'-m','forge.run_exports',run['id'],'--scope',task['scope'],'--output-dir',str(stage)],cwd=ROOT,
                stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONIOENCODING':'utf-8','STUDIO_EXPORT_TASK_ID':id},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            with LOCK:PROCESSES[id]=process
            while process.poll() is None:
                if stopped(id):service.agent.terminate_tree(process);return
                sync_progress(id,stage)
                from .resources import ensure_disk_headroom
                ensure_disk_headroom(stage)
                time.sleep(.4)
        sync_progress(id,stage)
        if stopped(id):return
        if process.returncode:raise ValueError('导出失败，可查看本次日志后重试；上一版本交付保留')
        update_active(id,phase='publishing',detail='核对文件清单与原始场，发布本次交付版本')
        if not (stage/'summary.pdf').is_file() or not (stage/'bundle.zip').is_file():raise ValueError('导出未生成完整报告与下载包，未发布新版本')
        publication={'export_task_id':id,'scope':task['scope'],'export_version':task['export_version'],'raw_sha256':task['raw_sha256'],
            'artifacts':{p.relative_to(stage).as_posix():file_hash(p) for p in stage.rglob('*') if p.is_file()},'published_at':store.now()}
        store.write_json(stage/'publication.json',publication)
        # Publication is a single database transaction; readers see an entire generation.
        manifest=json.loads((stage/'manifest.json').read_text(encoding='utf-8'))
        output_run=json.loads((stage/'run.json').read_text(encoding='utf-8'))
        artifacts=json.loads((stage/'artifacts.json').read_text(encoding='utf-8'))
        for sid,sha in task['raw_sha256'].items():
            if file_hash(DATA/'studies'/sid/'fields.h5')!=sha:raise ValueError('导出期间原始场发生变化，未发布新版本')
        with LOCK:
            if stopped(id):return
            def publish(values):
                current=values['export_task',id]
                if current.get('cancel_requested') or current.get('status') in TERMINAL:return {}
                record=values['analysis_run',run['id']]
                return {('analysis_run',run['id']):{**record,'export_generation':generation,'latest_export_id':id,'exports':output_run['exports'],'artifacts':artifacts},
                    ('export_task',id):{**current,'status':'completed' if manifest.get('complete') else 'partial','detail':'报告、媒体和下载包已发布；求解结果未改动' if manifest.get('complete') else '部分交付已发布，请查看缺项诊断','generation':generation}}
            store.mutate_many({('analysis_run',run['id']):None,('export_task',id):None},publish)
    except Exception as exc:
        update_active(id,status='failed',detail=str(exc),log_path=generation+'/export.log')
    finally:
        if process and process.poll() is None:service.agent.terminate_tree(process)
        with LOCK:PROCESSES.pop(id,None)
        if acquired:runs.EXPORT_LOCK.release()


def recover():
    for task in store.listing('export_task',10000):
        if task['status'] not in TERMINAL:store.update('export_task',task['id'],status='interrupted',detail='服务重启中断导出，上一版本仍可下载；请重试导出')
