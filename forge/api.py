"""Local typed API and the shared heavy-job queue."""
import csv,hashlib,io,json,os,subprocess,sys,threading,time
from pathlib import Path
from fastapi import APIRouter,UploadFile,File,HTTPException
from fastapi.responses import FileResponse
from pydantic import Field
from strength.config import DATA,ROOT
from strength import store,service
from strength.schemas import Strict
from strength.geometry import resolve_selection
from .schemas import AnalysisCase,LoadHistory
from .examples import default_case
from .results import CATALOG,frame_display

router=APIRouter(prefix='/api/v1')
TERMINAL={'completed','partial','failed','cancelled','interrupted'}
RUNNING={};LOCK=threading.RLock()


class StudyRequest(Strict):
    case:AnalysisCase
    request_id:str=Field(min_length=1,max_length=150)
    results_from_study:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    resume_from_study:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    source_case_id:str|None=None
    source_revision:int|None=Field(default=None,ge=0)
    parameter_sources:dict[str,str]=Field(default_factory=dict)


def selections_for(case):
    from .preflight import selections_for as collect
    return collect(case)


def freeze_named_selections(case,geometry):
    case=case.model_copy(deep=True,update={'geometry_sha256':geometry['source_sha256']})
    for selection in selections_for(case):
        if selection.kind=='named':
            selection.face_ids=resolve_selection(selection,geometry)
            selection.kind='faces'
    return case


def validate(case,geometry):
    from .preflight import validate_case
    return validate_case(case,geometry)


@router.get('/studies/catalog')
def catalog():
    return {'schema_version':3,'types':list(AnalysisCase.model_fields['analysis_type'].annotation.__args__),
        'fields':[{'key':k,'label':v[0],'unit':v[1],'factor':v[2]} for k,v in CATALOG.items()]}


@router.get('/studies/schema')
def schema():return AnalysisCase.model_json_schema()


@router.get('/projects/{id}/studies/template')
def template(id:str,analysis_type:str='thermal_steady',variant:str='default'):
    project=store.get('project',id);geo=store.get('model',project['model_id'])
    if analysis_type not in AnalysisCase.model_fields['analysis_type'].annotation.__args__:raise ValueError('未知分析类型')
    if variant not in ('default','bearing_contact'):raise ValueError('未知示例工况变体')
    if variant=='bearing_contact' and (analysis_type!='thermo_mechanical' or not (project.get('example_family')=='motor-unified' or project['model_id']=='motor-ten-component')):raise ValueError('轴承座接触变体仅适用于十组件电机热结构工况')
    if variant=='default' and project.get('example_case',{}).get('analysis_type')==analysis_type:return project['example_case']
    if project.get('example_family')=='motor-unified' or project['model_id']=='motor-ten-component':
        path=ROOT/'examples'/'motor-unified'/('thermo-contact.json' if variant=='bearing_contact' else analysis_type+'.json')
        if path.is_file():return json.loads(path.read_text(encoding='utf-8'))
    return default_case(geo,analysis_type,project.get('current_case'))


@router.post('/examples/benchmark/{kind}')
def benchmark_example(kind:str):
    import shutil
    from strength.geometry import inspect_model
    if kind not in ('local-bearing','thermal-gap','gears'):raise ValueError('未知示例类型')
    source=ROOT/'examples'/kind;case=json.loads((source/'case.json').read_text(encoding='utf-8'))
    mid=store.uid();pid=store.uid();folder=DATA/'models'/mid;folder.mkdir()
    shutil.copy2(source/'source.step',folder/'source.step');store.write_json(folder/'source.json',{'file':'source.step','units':'m'})
    geo=inspect_model(folder);store.put('model',geo);case['geometry_sha256']=geo['source_sha256']
    return store.put('project',{'id':pid,'model_id':mid,'name':case['name'],'example_family':kind,'example_case':case,'preferred_analysis_type':case['analysis_type'],'created_at':store.now()})


@router.post('/examples/motor')
def motor_example():
    import shutil
    from strength.geometry import inspect_model
    source=ROOT/'examples'/'motor-unified';step=source/'motor-simplified-10.step'
    if not step.exists():raise ValueError('安装目录未包含电机示例 STEP')
    mid=store.uid();pid=store.uid();folder=DATA/'models'/mid;folder.mkdir()
    shutil.copy2(step,folder/'source.step');store.write_json(folder/'source.json',{'file':'source.step','units':'mm'})
    geo=inspect_model(folder);store.put('model',geo);case=json.loads((source/'static-legacy.json').read_text(encoding='utf-8'))
    return store.put('project',{'id':pid,'model_id':mid,'name':'电机统一示例','example_family':'motor-unified','current_case':case,'material':case['material'],'component_materials':case['component_materials'],'created_at':store.now()})


@router.post('/projects/{id}/studies/validate')
def validate_study(id:str,case:AnalysisCase):
    project=store.get('project',id);return validate(case,store.get('model',project['model_id']))


@router.get('/projects/{id}/studies')
def studies(id:str,limit:int=100,offset:int=0,search:str|None=None):
    store.get('project',id)
    return store.listing('study',min(max(limit,1),1000),project_id=id,offset=offset,search=search)


@router.post('/projects/{id}/studies',status_code=202)
def submit(id:str,request:StudyRequest):
    with LOCK:
        previous=store.request_lookup('study:'+id,request.request_id)
        if previous:return store.get('study',previous)
        project=store.get('project',id)
        if not project.get('model_id'):raise ValueError('请先导入模型')
        geometry=store.get('model',project['model_id']);validate(request.case,geometry)
        case=freeze_named_selections(request.case,geometry)
        dependencies=[]
        if request.results_from_study and request.resume_from_study:raise ValueError('不能同时选择后处理复用和恢复计算')
        if request.resume_from_study:
            from .reprocess import validate_source
            parent=store.get('study',request.resume_from_study)
            if parent['model_id']!=project['model_id'] or parent['status'] not in TERMINAL:raise ValueError('只可恢复同一模型的已停止研究')
            validate_source(case,parent)
        if request.results_from_study:
            from .reprocess import validate_source
            parent=store.get('study',request.results_from_study)
            if parent['model_id']!=project['model_id'] or parent['status'] not in ('completed','partial'):raise ValueError('原始场必须来自同一模型的已完成研究')
            validate_source(case,parent);dependencies.append(parent['id'])
        if case.thermal.initial_from_study:
            from .preflight import validate_initial_dependency
            parent=store.get('study',case.thermal.initial_from_study)
            validate_initial_dependency(case,parent,project['model_id'],id,allow_pending=True)
            dependencies=list(dict.fromkeys([*dependencies,parent['id']]))
        sid=store.uid();folder=DATA/'studies'/sid;folder.mkdir(parents=True)
        from .provenance import solver_signature,case_signature
        config={'case':case.model_dump(),'model_id':project['model_id'],'project_id':id,'schema_version':3,'dependencies':dependencies,
                'solver_signature':solver_signature(),'case_signature':case_signature(case,geometry)}
        sources=dict(request.parameter_sources);source_id=request.source_case_id;source_revision=request.source_revision
        if project.get('current_draft_id') and source_id is None:
            try:
                draft=store.get('analysis_draft',project['current_draft_id'])
                if draft['model_id']==project['model_id'] and draft['case']==request.case.model_dump():
                    sources=draft.get('parameter_sources',{});source_id=draft['id'];source_revision=draft['revision']
            except KeyError:pass
        config['input_provenance']={'source_case_id':source_id,'source_revision':source_revision,'parameter_sources':sources}
        if request.resume_from_study:
            import shutil
            config['resume_from']=request.resume_from_study
            source=DATA/'studies'/request.resume_from_study
            # Copy only native stages that reached solver completion. The
            # worker rechecks the entire input deck before reusing any stage.
            for log in source.rglob('solver.log'):
                if 'Job finished' not in log.read_text(errors='replace'):continue
                relative=log.parent.relative_to(source)
                if not relative.parts or relative.parts[0] not in ('static','coupling') and not relative.parts[0].startswith(('cycle','basis')):continue
                dest=folder/relative;dest.mkdir(parents=True,exist_ok=True)
                for name in ('analysis.inp','analysis.dat','analysis.rout','solver.log','analysis.sta','completed-stage.json'):
                    if (log.parent/name).is_file():shutil.copy2(log.parent/name,dest/name)
        if request.results_from_study:config['reprocess_from']=request.results_from_study
        config['sha256']=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
        store.write_json(folder/'config.json',config)
        record=store.put('study',{'id':sid,'project_id':id,'model_id':project['model_id'],'name':case.name,'analysis_type':case.analysis_type,
            'resume_from':request.resume_from_study,'results_from_study':request.results_from_study,
            'case':case.model_dump(),'dependencies':dependencies,'snapshot_sha256':config['sha256'],'status':'queued','detail':'等待本机计算资源','created_at':store.now(),'cancel_requested':False})
        store.request_save('study:'+id,request.request_id,sid)
        store.update('project',id,current_analysis=case.model_dump(),current_analysis_model_id=project['model_id'])
        threading.Thread(target=execute,args=(sid,),daemon=True,name='study-'+sid[:8]).start()
        return record


def execute(id):
    folder=DATA/'studies'/id;process=None;acquired=False
    try:
        for dependency in store.get('study',id).get('dependencies',[]):
            while True:
                if store.get('study',id).get('cancel_requested'):return
                parent=store.get('study',dependency)
                if parent['status']=='completed':break
                if parent['status'] in TERMINAL:raise ValueError('上游结果依赖失败：'+dependency)
                store.update('study',id,detail='等待结果依赖 '+dependency[:8]);time.sleep(.5)
        while not service.SLOT.acquire(timeout=.2):
            if store.get('study',id).get('cancel_requested'):return
        acquired=True
        with LOCK:
            if store.get('study',id).get('cancel_requested'):return
            with (folder/'worker.log').open('w',encoding='utf-8') as log:
                process=subprocess.Popen([sys.executable,'-m','forge.worker',str(folder)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                    env={**os.environ,'PYTHONIOENCODING':'utf-8'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            RUNNING[id]=process
        deadline=time.monotonic()+store.get('study',id)['case'].get('resources',{}).get('time_limit_s',86400)
        while process.poll() is None:
            if store.get('study',id).get('cancel_requested'):
                service.agent.terminate_tree(process);return
            if time.monotonic()>deadline:raise TimeoutError('求解超过工况资源中配置的时间上限；结果已保留')
            from .resources import ensure_disk_headroom
            ensure_disk_headroom(folder)
            try:
                state=json.loads((folder/'status.json').read_text(encoding='utf-8'))
                if state['status'] not in TERMINAL:store.update('study',id,**state)
            except (FileNotFoundError,PermissionError,json.JSONDecodeError):pass
            time.sleep(.35)
        if store.get('study',id).get('cancel_requested'):return
        state=json.loads((folder/'status.json').read_text(encoding='utf-8'))
        if process.returncode and state['status']!='failed':state={'status':'failed','detail':'求解进程异常退出，请查看日志'}
        summary=json.loads((folder/'summary.json').read_text(encoding='utf-8')) if (folder/'summary.json').exists() else None
        store.update('study',id,**state,summary=summary)
    except Exception as exc:
        if not store.get('study',id).get('cancel_requested'):store.update('study',id,status='failed',detail=str(exc))
    finally:
        if process and process.poll() is None:service.agent.terminate_tree(process)
        with LOCK:RUNNING.pop(id,None)
        if acquired:service.SLOT.release()


def recover():
    for s in store.listing('study',10000):
        if s['status'] not in TERMINAL:store.update('study',s['id'],status='interrupted',detail='服务重启；已保存阶段保留，可基于快照重新运行')


@router.get('/studies/{id}')
def study(id:str):
    from .verification import read_verification
    result=store.get('study',id)
    result['verification']=read_verification(DATA/'studies'/id)
    result['checkpoints']=checkpoints(id)
    return result


@router.get('/studies/{id}/checkpoints')
def checkpoints(id:str):
    record=store.get('study',id);items=[]
    for kind in ('thermal','coupling'):
        root=DATA/'studies'/id/kind;path=root/'checkpoint.json'
        if not path.is_file():continue
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            items.append({'kind':kind,**value,'download_url':f'/api/v1/studies/{id}/checkpoints/{kind}' if record['status'] in TERMINAL else None})
        except (OSError,ValueError):pass
    return items


@router.get('/studies/{id}/checkpoints/{kind}')
def checkpoint_file(id:str,kind:str):
    record=store.get('study',id)
    if kind not in ('thermal','coupling'):raise HTTPException(404)
    if record['status'] not in TERMINAL:raise HTTPException(409,'物理步日志仍在写入；计算停止后可下载完整日志')
    path=DATA/'studies'/id/kind/'physical-steps.h5'
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,filename=kind+'-physical-steps.h5',headers={'Cache-Control':'no-store'})


@router.post('/studies/{id}/cancel')
def cancel(id:str,cascade:bool=True):
    with LOCK:
        s=store.get('study',id)
        if s['status'] in TERMINAL:return s
        s=store.update('study',id,cancel_requested=True,status='cancelled',detail='已取消，已完成阶段保留')
        process=RUNNING.get(id)
        if process:service.agent.terminate_tree(process)
        if cascade:
            for dependent in store.listing('study',10000):
                if id in dependent.get('dependencies',[]) and dependent['status'] not in TERMINAL:cancel(dependent['id'])
        return s


@router.get('/studies/{id}/frames/{index}')
def frame(id:str,index:int):
    s=store.get('study',id)
    if s['status'] not in ('completed','partial'):raise ValueError('完整结果尚未生成')
    return frame_display(DATA/'studies'/id,index)


def result_folder(id):
    s=store.get('study',id)
    if s['status'] not in ('completed','partial'):raise HTTPException(409,'完整结果尚未生成')
    return DATA/'studies'/id


@router.get('/studies/{id}/result-index')
def index_result(id:str):
    from .results import result_index
    return result_index(result_folder(id))


@router.get('/studies/{id}/topology')
def topology_result(id:str):
    from .results import surface_topology
    return surface_topology(result_folder(id))


@router.get('/studies/{id}/frames/{index}/values')
def values_result(id:str,index:int,fields:str|None=None):
    from .results import frame_values
    return frame_values(result_folder(id),index,fields.split(',') if fields else None)


@router.get('/studies/{id}/files/{name}')
def file(id:str,name:str):
    store.get('study',id)
    if name not in {'report.pdf','report.md','metrics.csv','fields.h5','raw-fields.sha256.json','summary.json','config.json','audit.json','verification.json','worker.log','mesh.npz'}:raise HTTPException(404)
    path=DATA/'studies'/id/name
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,filename=name,headers={'Cache-Control':'no-store'})


@router.post('/load-histories/import')
async def import_history(file:UploadFile=File(...)):
    raw=await file.read(10*1024*1024+1)
    if len(raw)>10*1024*1024:raise ValueError('载荷 CSV 超过 10 MiB')
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or reader.fieldnames[0]!='time_s':raise ValueError('CSV 第一列必须是 time_s，后续列为载荷名称对应的倍率')
    columns=reader.fieldnames[1:];times=[];factors={k:[] for k in columns}
    if not columns or len(set(columns))!=len(columns):raise ValueError('载荷列必须非空且名称唯一')
    for row in reader:
        times.append(float(row['time_s']))
        for k in columns:factors[k].append(float(row[k]))
    if len(times)<2:raise ValueError('CSV 至少需要两个时间点')
    from .fatigue import rainflow
    periodic=all(v[0]==v[-1] for v in factors.values())
    count=max((sum(c[2] for c in rainflow(v,periodic)) for v in factors.values()),default=1.)
    history=LoadHistory(times_s=times,factors=factors,periodic=periodic,cycles_per_history=max(count,1.))
    return history.model_dump()
