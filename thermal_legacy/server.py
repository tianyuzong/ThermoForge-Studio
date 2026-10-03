from thermal_legacy.runtime import *
import time, uuid, subprocess, threading, math
from urllib.parse import urlparse
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from thermal_legacy.schemas import Simulation, PRESETS, CalibrationRequest, RefrigerationCycle, CpuSimulation, AgentRequest
from thermal_legacy.analysis import run_calibration, run_cycle, run_cpu
from thermal_legacy.agent import codex_plan_request, plan_request
from thermal_legacy.solver import gpu_status
from thermal_legacy.compute_policy import execution_policy,can_admit_job,worker_environment

app=FastAPI(title='Thermal Studio Local',version='0.1.0')
app.add_middleware(GZipMiddleware,minimum_size=2000)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
processes={};process_lock=threading.Lock();slice_lock=threading.Lock()
PROJECTS=DATA/'projects';PROJECTS.mkdir(exist_ok=True)

@app.middleware('http')
async def local_origin(request,call_next):
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin and urlparse(origin).hostname not in ('127.0.0.1','localhost'):
        return JSONResponse({'detail':'仅接受本地平台页面发起的操作'},status_code=403)
    return await call_next(request)

def located(parent,id):
    if not id or len(id)>60 or not all(c.isalnum() or c in '_-' for c in id):raise HTTPException(404,'未找到记录')
    path=parent/id
    if not path.is_dir():raise HTTPException(404,'未找到记录')
    return path

def status(folder):
    value=read_json(folder/'status.json') if (folder/'status.json').exists() else dict(phase='pending',progress=0,detail='等待处理')
    key=folder.name
    process=processes.get(key)
    if process and process.poll() is None and value.get('phase')=='failed' and value.get('detail')=='上次运行被中断，请重新运行':
        # An older local viewer may incorrectly mark another server's live job interrupted.
        progress(folder,'running',value.get('progress',0),'计算进程仍在运行，等待下一次进度更新');value=read_json(folder/'status.json')
    if process and process.poll() is not None and value['phase'] in ('pending','running'):
        progress(folder,'failed',0,'计算进程已结束，详情见本地 worker.log');value=read_json(folder/'status.json')
    elif not process and value['phase'] in ('pending','running'):
        # Recover interrupted work after an application restart.
        progress(folder,'failed',0,'上次运行被中断，请重新运行');value=read_json(folder/'status.json')
    return value

def spawn(kind,id):
    folder=(MODELS if kind=='model' else JOBS)/id
    log=(folder/'worker.log').open('wb')
    try:
        proc=subprocess.Popen([sys.executable,'-m','thermal_legacy.worker',kind,id],cwd=ROOT.parent,stdout=log,stderr=subprocess.STDOUT,
            env=worker_environment() if kind=='solve' else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    finally:log.close()
    processes[id]=proc

def validate_geometry(cfg):
    folder=located(MODELS,cfg.model_id);meta=read_json(folder/'metadata.json')
    if meta.get('state')!='ready':raise HTTPException(409,'几何尚未准备完成')
    for group in [*cfg.heat_sources,*cfg.cooling,*(cfg.structural.supports if cfg.structural else []),*([cfg.surface_evaluation] if cfg.surface_evaluation else [])]:
        if group.faces and max(group.faces)>=meta['triangles']:raise HTTPException(422,'面选区不属于当前模型，请重新选取')
    if max(meta['dimensions_m'])/cfg.mesh_size_m>130:raise HTTPException(422,'网格尺寸不得小于最长边的 1/130')
    return folder

@app.get('/api/health')
def health():return dict(ok=True,app='Thermal Studio Local',version='0.1.0',pid=os.getpid(),compute=gpu_status(),
                         execution={**execution_policy(), 'transient_compute':gpu_status('transient'),
                                    'structural_device':'cpu'})

@app.post('/api/analysis/calibrate')
def calibrate(request:CalibrationRequest):
    return run_calibration(request)

@app.post('/api/analysis/refrigeration')
def refrigeration(request:RefrigerationCycle):
    return run_cycle(request)

@app.post('/api/analysis/cpu')
def cpu_simulation(request:CpuSimulation):
    """Run the standalone CPU cooling workbench model.

    This endpoint is intentionally independent of uploaded geometry and the
    finite-element job queue, so changing a cooler type remains interactive.
    """
    return run_cpu(request)

@app.post('/api/agent/plan')
def agent_plan(request:AgentRequest):
    located(MODELS, request.model_id)
    if request.mode == 'codex':
        return codex_plan_request(request.model_id, request.prompt, request.config, request.conversation())
    result = plan_request(request.model_id, request.prompt, request.config, request.conversation())
    result['mode'] = 'local'
    return result

@app.get('/api/bootstrap')
def bootstrap():
    models=[]
    for folder in MODELS.iterdir():
        if (folder/'metadata.json').exists():models.append({**read_json(folder/'metadata.json'),'status':status(folder)})
    projects=[]
    for path in PROJECTS.glob('*.json'):
        p=read_json(path);projects.append({k:p[k] for k in ('id','name','model_id','updated_at')})
    jobs=[]
    for folder in JOBS.iterdir():
        if (folder/'config.json').exists():
            cfg=read_json(folder/'config.json');jobs.append(dict(id=folder.name,name=cfg['name'],model_id=cfg['model_id'],created_at=folder.stat().st_ctime,status=status(folder)))
    return dict(materials=PRESETS,models=sorted(models,key=lambda m:m.get('created_at',0),reverse=True),projects=sorted(projects,key=lambda p:p['updated_at'],reverse=True),jobs=sorted(jobs,key=lambda j:j['created_at'],reverse=True))

@app.post('/api/models',status_code=202)
async def upload_model(file:UploadFile=File(...),units:str=Form('mm'),scale_factor:float=Form(1)):
    ext=Path(file.filename or '').suffix.lower()
    if ext not in ('.step','.stp','.stl'):raise HTTPException(422,'支持 STL、STEP 或 STP 文件')
    if units not in ('mm','cm','m') or not math.isfinite(scale_factor) or not 1e-6<=scale_factor<=1e6:raise HTTPException(422,'无效的尺寸单位或缩放')
    id=uuid.uuid4().hex;folder=MODELS/id;folder.mkdir()
    source='source'+ext;size=0
    with (folder/source).open('wb') as dest:
        while chunk:=await file.read(1024*1024):
            size+=len(chunk)
            if size>100*1024*1024:raise HTTPException(413,'单个模型不超过 100 MB')
            dest.write(chunk)
    if not size:raise HTTPException(422,'文件为空')
    meta=dict(id=id,name=Path(file.filename).name[:160],source=source,kind='stl' if ext=='.stl' else 'step',units=units,scale_factor=scale_factor,step_scale=scale_factor,state='processing',created_at=time.time())
    write_json(folder/'metadata.json',meta);progress(folder,'pending',0,'等待导入');spawn('model',id)
    return meta

@app.get('/api/models/{id}')
def model(id:str):
    folder=located(MODELS,id);return {**read_json(folder/'metadata.json'),'status':status(folder)}

@app.get('/api/models/{id}/display')
def display(id:str):
    folder=located(MODELS,id)
    if not (folder/'display.json').exists():raise HTTPException(409,'表面尚未就绪')
    return FileResponse(folder/'display.json',media_type='application/json')

@app.post('/api/projects')
def save_project(cfg:Simulation):
    validate_geometry(cfg);id=uuid.uuid4().hex
    value=dict(id=id,name=cfg.name,model_id=cfg.model_id,config=cfg.model_dump(),updated_at=time.time());write_json(PROJECTS/(id+'.json'),value)
    return value

@app.get('/api/projects/{id}')
def project(id:str):
    if not id.isalnum() or not (PROJECTS/(id+'.json')).exists():raise HTTPException(404,'算例不存在')
    return read_json(PROJECTS/(id+'.json'))

@app.post('/api/jobs',status_code=202)
def run(cfg:Simulation):
    folder=validate_geometry(cfg)
    if not cfg.heat_sources and not cfg.environment_only:
        raise HTTPException(422,'请先添加至少一个热源并选择受热面或点位置，再运行仿真')
    if not read_json(folder/'metadata.json').get('mesh_ready'):raise HTTPException(422,'STL 尚未封闭，请修复后导入，或改用 STEP')
    with process_lock:
        active=sum((JOBS/id).exists() and process.poll() is None for id,process in processes.items())
        if not can_admit_job(active):raise HTTPException(409,'已有算例正在计算，等待并行槽位或可用内存')
        id=uuid.uuid4().hex;job=JOBS/id;job.mkdir();write_json(job/'config.json',cfg.model_dump());progress(job,'pending',0,'准备启动');spawn('solve',id)
    return dict(id=id)

@app.get('/api/jobs/{id}')
def job_status(id:str):return status(located(JOBS,id))

@app.post('/api/jobs/{id}/cancel')
def cancel(id:str):
    folder=located(JOBS,id);p=processes.get(id)
    if p and p.poll() is None:p.terminate();p.wait(timeout=10);progress(folder,'cancelled',0,'已取消计算')
    return status(folder)

@app.get('/api/jobs/{id}/result')
def result(id:str):
    folder=located(JOBS,id)
    if status(folder)['phase']!='completed':raise HTTPException(409,'结果尚未完成')
    return read_json(folder/'result.json')

@app.get('/api/jobs/{id}/slice')
def slice_result(id:str,axis:int=0,value:float=0):
    folder=located(JOBS,id)
    if status(folder)['phase']!='completed':raise HTTPException(409,'结果尚未完成')
    try:
        with slice_lock:
            from thermal_legacy.solver import export_slice
            return export_slice(folder,axis,value)
    except ValueError as error:raise HTTPException(422,str(error))

@app.get('/api/jobs/{id}/slices/{key}/{name}')
def slice_file(id:str,key:str,name:str):
    folder=located(located(JOBS,id)/'slices',key)
    if name not in ('points.bin','faces.bin','values.bin'):raise HTTPException(404)
    return FileResponse(folder/name,media_type='application/octet-stream')

@app.get('/api/jobs/{id}/files/{name}')
def download(id:str,name:str):
    folder=located(JOBS,id)
    if name not in ('surface.bin','displacement.bin','surface-displacement.bin','surface-von-mises.bin','stress.bin','assessment.json','config.json','history.csv','audit.json','report.md','report.pdf','result.zip'):raise HTTPException(404)
    if name in ('report.pdf','report.md','result.zip') and (folder/'report.md').exists():
        from thermal_legacy.engineering_report import ensure_current_report
        ensure_current_report(folder)
    if not (folder/name).exists():raise HTTPException(404,'文件尚未生成')
    media_type='application/pdf' if name=='report.pdf' else None
    return FileResponse(folder/name,filename=None if name=='surface.bin' else name,media_type=media_type)

from thermal_legacy import thermal_animation

def animation_action(id,action):
    job=located(JOBS,id)
    if status(job)['phase']!='completed':raise HTTPException(409,'请等待仿真计算完成后再查看热扩散动画')
    try:return action(job)
    except (ValueError,KeyError,FileNotFoundError) as error:raise HTTPException(422,str(error)) from error
    except RuntimeError as error:raise HTTPException(409,str(error)) from error

@app.get('/api/jobs/{id}/animation')
def animation_manifest(id:str):
    def prepare(job):
        path=thermal_animation.prepare_preview(job)
        return read_json(path.parent/'manifest.json')
    return animation_action(id,prepare)

@app.get('/api/jobs/{id}/animation/view')
def animation_view(id:str):
    path=animation_action(id,thermal_animation.prepare_preview)
    return FileResponse(path,media_type='text/html')

@app.get('/api/jobs/{id}/animation/html')
def animation_html(id:str):
    path=animation_action(id,thermal_animation.prepare_preview)
    return FileResponse(path,media_type='text/html',filename='thermal-animation.html')

@app.get('/api/jobs/{id}/animation/status')
def animation_status(id:str):return animation_action(id,thermal_animation.export_status)

@app.post('/api/jobs/{id}/animation/video',status_code=202)
def animation_export(id:str):return animation_action(id,thermal_animation.start_export)

@app.get('/api/jobs/{id}/animation/video')
def animation_video(id:str):
    state=animation_action(id,thermal_animation.export_status)
    if state['phase']!='completed':raise HTTPException(409,'MP4视频尚未生成，请先导出')
    return FileResponse(located(JOBS,id)/'thermal-animation/animation.mp4',media_type='video/mp4',filename='thermal-animation.mp4')

from thermal_legacy.workflow_agent import WorkflowRequest
from thermal_legacy import workflows

@app.get('/api/capabilities')
def host_capabilities():
    from thermal_legacy.host_geometry import capabilities
    return capabilities()

@app.get('/api/models/{id}/preflight')
def geometry_preflight(id:str):
    from thermal_legacy.host_geometry import model_evidence
    return model_evidence(located(MODELS,id))

@app.post('/api/workflows/plan',status_code=202)
def plan_workflow(request:WorkflowRequest):
    located(MODELS,request.model_id)
    try:return workflows.create(request)
    except (ValueError,OSError) as error:raise HTTPException(422,str(error))

@app.get('/api/workflows')
def list_workflows():
    if not workflows.WORKFLOWS.exists():return []
    values=[]
    for p in workflows.WORKFLOWS.iterdir():
        if (p/'workflow.json').is_file():
            w=workflows.get(p.name)
            values.append({k:w.get(k) for k in ('id','name','model_id','phase','updated_at','detail')})
    return sorted(values,key=lambda w:w['updated_at'],reverse=True)

@app.get('/api/workflows/{id}')
def workflow_state(id:str):
    try:return workflows.get(id)
    except ValueError as error:raise HTTPException(404,str(error))

@app.post('/api/workflows/{id}/start',status_code=202)
def start_workflow(id:str):
    try:return workflows.start(id,run,job_status,cancel)
    except ValueError as error:raise HTTPException(409,str(error))

@app.post('/api/workflows/{id}/cancel')
def cancel_workflow(id:str):
    try:return workflows.cancel(id)
    except ValueError as error:raise HTTPException(404,str(error))

@app.post('/api/workflows/{id}/retry-plan',status_code=202)
def retry_workflow_plan(id:str):
    try:return workflows.retry_plan(id)
    except ValueError as error:raise HTTPException(409,str(error))

@app.post('/api/workflows/{id}/recheck',status_code=202)
def recheck_workflow_plan(id:str):
    try:return workflows.retry_plan(id,recheck=True)
    except ValueError as error:raise HTTPException(409,str(error))

@app.get('/api/workflows/{id}/files/{name}')
def workflow_file(id:str,name:str):
    if name not in ('workflow.json','report.md','report.pdf','comparison.json','comparison.csv','manifest.json','reports.zip'):raise HTTPException(404)
    try:p=workflows.folder(id)/name
    except ValueError as error:raise HTTPException(404,str(error))
    if not p.is_file():raise HTTPException(404,'文件尚未生成')
    return FileResponse(p,filename=name,media_type='application/pdf' if name.endswith('.pdf') else None)

app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='ui')

if __name__=='__main__':
    import uvicorn,argparse
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);args=p.parse_args()
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='info')
