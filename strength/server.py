import asyncio, json, os, shutil, time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,HTTPException,UploadFile,File,Form,Request
from fastapi.responses import FileResponse,StreamingResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from .config import ROOT,DATA,CCX
from . import store,service
from .schemas import TaskRequest,MessageRequest,ProjectRequest,ProjectPatch,NamedSelection,Strict,LoadCase,DEMO_MATERIAL,DEMO_ALUMINUM,MaterialCardRequest,SavedCaseRequest
from .backends import capabilities,resolve_backend,gpu_diagnostic,nvidia_devices
from .schemas import ResultQuery
from .result_catalog import FIELDS, METRICS
from .postprocess import ResultArchive,compare_results
from .material_library import material_library

@asynccontextmanager
async def lifespan(app):
    service.recover()
    from forge.api import recover
    recover()
    from forge.runs import recover as recover_runs
    recover_runs()
    from forge.plans import recover as recover_plans
    recover_plans()
    yield

from forge.version import VERSION
app=FastAPI(title='ThermoForge Studio',version=VERSION,lifespan=lifespan)
app.add_middleware(GZipMiddleware,minimum_size=1000)

@app.middleware('http')
async def local_only(request,call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost','testserver'): return JSONResponse({'detail':'仅允许本机访问'},status_code=403)
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin and origin not in ('http://'+request.headers.get('host',''),'https://'+request.headers.get('host','')):
        return JSONResponse({'detail':'拒绝跨站写入'},status_code=403)
    return await call_next(request)

@app.exception_handler(KeyError)
async def missing(request,e): return JSONResponse({'detail':str(e)},status_code=404)
@app.exception_handler(ValueError)
async def invalid(request,e): return JSONResponse({'detail':str(e)},status_code=422)

@app.get('/api/v1/health')
def health():
    return {'ok':True,'app':'ThermoForge Studio','version':VERSION,'pid':os.getpid(),'solver_available':CCX.is_file(),
        'cli_available':bool(os.environ.get('STRENGTH_CODEX_COMMAND') or shutil.which('codex')),
        'api_available':bool(os.environ.get('OPENAI_API_KEY') and os.environ.get('OPENAI_MODEL')),'execution':{'parallel_jobs':1,**capabilities()},'demo_material':DEMO_MATERIAL}

@app.get('/api/v1/projects')
def projects(): return store.listing('project')
@app.post('/api/v1/projects')
def create_project(req:ProjectRequest):
    return store.put('project',{'id':store.uid(),'name':req.name,'model_id':None,'material':None,'current_case':None,'last_job_ids':[],'created_at':store.now()})

@app.post('/api/v1/execution/gpu-diagnostic')
def diagnose_gpu():
    gpu_diagnostic.cache_clear();nvidia_devices.cache_clear()
    gpu_diagnostic()
    return capabilities()

@app.get('/api/v1/results/catalog')
def result_catalog():
    return {'schema_version':2,'fields':list(FIELDS.values()),'metrics':METRICS}

def completed_job(id):
    j=store.get('job',id)
    if j['status']!='completed':raise ValueError('结果尚未完成或数值校核未通过')
    return j

@app.get('/api/v1/jobs/{id}/results')
def result_overview(id:str):
    j=completed_job(id);folder=DATA/'jobs'/id
    with __import__('h5py').File(folder/'fields.h5') as f: available={k[8:] for k in f if k.startswith('display_')}
    return {'fields':[{**v,'display_available':k in available} for k,v in FIELDS.items()],
        'statistics':j['summary'].get('field_statistics',{}),'queries':j['summary'].get('result_queries',[]),
        'strain_energy_J':j['summary'].get('strain_energy_J'),'legacy':j['summary'].get('result_schema_version',1)<2}

@app.post('/api/v1/jobs/{id}/results/query')
def result_query(id:str,query:ResultQuery):
    completed_job(id)
    return ResultArchive(DATA/'jobs'/id).query(query)

@app.get('/api/v1/projects/{id}/results/comparison')
def result_comparison(id:str):
    store.get('project',id)
    return compare_results([j for j in store.listing('job') if j['project_id']==id and j['status']=='completed'])
@app.get('/api/v1/projects/{id}')
def project(id:str): return store.get('project',id)
@app.patch('/api/v1/projects/{id}')
def update_project(id:str,req:ProjectPatch):
    if req.compute_backend: resolve_backend(req.compute_backend)
    if req.component_materials is not None:
        p=store.get('project',id);g=store.get('model',p['model_id'])
        if set(req.component_materials)-{str(c['id']) for c in g.get('components',[])}: raise ValueError('未知组件材料配置')
    return store.update('project',id,**req.model_dump(exclude_none=True))

@app.get('/api/v1/materials')
def materials(): return material_library()

@app.post('/api/v1/materials')
def save_material(req:MaterialCardRequest):
    return store.put('material_card',{'id':store.uid(),'material':req.material.model_dump()})

@app.get('/api/v1/projects/{id}/cases')
def saved_cases(id:str):
    p=store.get('project',id)
    return [c for c in store.listing('saved_case',1000) if c['project_id']==id and c['model_id']==p.get('model_id')]

@app.post('/api/v1/projects/{id}/cases')
def save_case(id:str,req:SavedCaseRequest):
    from .geometry import validate_case
    p=store.get('project',id);g=store.get('model',p['model_id']);validate_case(req.case,g)
    record=store.put('saved_case',{'id':store.uid(),'project_id':id,'model_id':p['model_id'],'case':req.case.model_dump()})
    store.update('project',id,current_case=record['case'])
    return record

def register_model(project_id,folder):
    evidence=json.loads((folder/'geometry.json').read_text(encoding='utf-8'))
    store.put('model',evidence)
    store.update('project',project_id,model_id=folder.name,current_case=None,last_job_ids=[],component_materials={})
    return evidence

@app.post('/api/v1/projects/{id}/demo')
def demo(id:str,kind:str='bracket'):
    store.get('project',id)
    if kind not in ('bracket','bar','beam','components'): raise ValueError('未知内置模型')
    folder=DATA/'models'/store.uid(); folder.mkdir()
    service.worker('demo',folder,['--kind',kind]); result=register_model(id,folder)
    store.update('project',id,material=DEMO_MATERIAL)
    if kind=='components':
        components=result['components'];mats={str(b['id']):[DEMO_MATERIAL,DEMO_ALUMINUM][i] for i,b in enumerate(components)}
        case=LoadCase(name='双组件独立受拉 · 演示工况',material=DEMO_MATERIAL,component_materials=mats,assembly_mode='independent',
            supports=[{'name':b['name']+' 左端固定','selection':{'kind':'direction','direction':'-x','body_id':b['id']}} for b in components],
            loads=[{'name':b['name']+' 1000 N 拉力','kind':'force','selection':{'kind':'direction','direction':'+x','body_id':b['id']},'vector':[1000,0,0]} for b in components],mesh={'size_m':.006})
        store.update('project',id,component_materials=mats,current_case=case.model_dump())
    return result

@app.post('/api/v1/projects/{id}/models')
def upload_model(id:str,file:UploadFile=File(...),units:str|None=Form(None),body_tag:int|None=Form(None)):
    store.get('project',id)
    ext=Path(file.filename or '').suffix.lower()
    if ext not in ('.step','.stp','.stl'): raise ValueError('只支持 STEP、STP 和 STL')
    if ext=='.stl' and units not in ('mm','cm','m','inch'): raise ValueError('STL 文件必须指定单位')
    folder=DATA/'models'/store.uid(); folder.mkdir(); path=folder/('source'+ext)
    total=0
    with path.open('wb') as out:
        while block:=file.file.read(1024*1024):
            total+=len(block)
            if total>100*1024*1024: raise ValueError('模型文件超过 100 MiB 限制')
            out.write(block)
    store.write_json(folder/'source.json',{'file':path.name,'original_name':file.filename,'units':units,'body_tag':body_tag})
    service.worker('inspect',folder); return register_model(id,folder)

@app.get('/api/v1/models/{id}')
def model(id:str): return store.get('model',id)
@app.get('/api/v1/models/{id}/display')
def model_display(id:str):
    store.get('model',id); return FileResponse(DATA/'models'/id/'display.json',media_type='application/json')
@app.post('/api/v1/models/{id}/selections')
def save_selection(id:str,req:NamedSelection):
    with store.LOCK:
        evidence=store.get('model',id)
        if not set(req.face_ids)<={s['id'] for s in evidence['surfaces']}: raise ValueError('选区包含未知表面')
        evidence['named_selections'][req.name]=sorted(set(req.face_ids))
        store.write_json(DATA/'models'/id/'geometry.json',evidence); return store.put('model',evidence)

class ManualRequest(Strict):
    case:LoadCase
    request_id:str

@app.post('/api/v1/projects/{id}/validate')
def validate(id:str,case:LoadCase):
    from .geometry import validate_case
    p=store.get('project',id); evidence=store.get('model',p['model_id'])
    return {'ok':True,'mappings':validate_case(case,evidence),'note':'网格生成后继续检查刚体约束充分性'}

@app.post('/api/v1/projects/{id}/jobs',status_code=202)
def manual(id:str,req:ManualRequest): return service.submit_manual(id,req.case,req.request_id)

@app.post('/api/v1/agent/tasks',status_code=202)
def create_task(req:TaskRequest): return service.create_task(req)
@app.get('/api/v1/agent/tasks')
def tasks(project_id:str|None=None): return [t for t in store.listing('task') if not project_id or t['project_id']==project_id]
@app.get('/api/v1/agent/tasks/{id}')
def task(id:str): return service.task_view(id)
@app.post('/api/v1/agent/tasks/{id}/messages',status_code=202)
def task_message(id:str,req:MessageRequest): return service.message(id,req)
@app.post('/api/v1/agent/tasks/{id}/cancel')
def cancel(id:str): return service.cancel(id)
@app.get('/api/v1/agent/tasks/{id}/events')
async def events(id:str,request:Request,after:int=0):
    store.get('task',id)
    try: cursor=max(after,int(request.headers.get('last-event-id','0')))
    except ValueError: cursor=after
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            records=store.events(id,cursor)
            for e in records:
                cursor=e['seq']; yield f'id: {cursor}\nevent: progress\ndata: '+json.dumps(e,ensure_ascii=False)+'\n\n'
            state=store.get('task',id)['status']
            if state in service.TERMINAL|{'needs_input'}:
                yield 'event: settled\ndata: '+json.dumps({'status':state})+'\n\n'; break
            yield ': heartbeat\n\n'; await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.get('/api/v1/jobs')
def jobs(project_id:str|None=None): return [j for j in store.listing('job') if not project_id or j['project_id']==project_id]
@app.get('/api/v1/jobs/{id}')
def job(id:str): return store.get('job',id)
@app.get('/api/v1/jobs/{id}/display')
def result_display(id:str):
    j=store.get('job',id)
    if j['status']!='completed': raise ValueError('结果尚未完成或校验未通过')
    return FileResponse(DATA/'jobs'/id/'display.json',media_type='application/json')
@app.get('/api/v1/jobs/{id}/slice')
def result_slice(id:str,axis:int=2,position:float=0,field:str='stress'):
    if axis not in (0,1,2): raise ValueError('剖面方向非法')
    import math
    if not math.isfinite(position): raise ValueError('剖面位置非法')
    completed_job(id)
    from .results import slice_field
    return slice_field(DATA/'jobs'/id,axis,position,field)

JOB_FILES={'report.pdf','report-evidence.json','metrics.csv','result.zip','result.vtu','fields.h5','config.json','summary.json','analysis.inp','analysis.dat','solver.log','worker.log','mesh.npz','contact.json','gpu-execution.json','timings.json','result-queries.json','field-statistics.csv'}

@app.get('/api/v1/jobs/{id}/report')
def report_details(id:str):
    store.get('job',id)
    folder=DATA/'jobs'/id
    path=folder/'report-evidence.json'
    if not path.exists(): return {'available':False,'figures':[]}
    evidence=json.loads(path.read_text(encoding='utf-8'))
    return {'available':True,'evidence':evidence,'figures':[{'name':p.name,'url':f'/api/v1/jobs/{id}/report-figures/{p.name}?v={evidence.get("report_version", "1")}'} for p in sorted((folder/'report-figures').glob('*.png'))]}

@app.get('/api/v1/jobs/{id}/report-figures/{name}')
def report_figure(id:str,name:str):
    store.get('job',id)
    import re
    if not re.fullmatch(r'[a-z0-9-]+\.png',name): raise HTTPException(404)
    path=DATA/'jobs'/id/'report-figures'/name
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path,media_type='image/png',headers={'Cache-Control':'no-cache'})
@app.get('/api/v1/jobs/{id}/files/{name}')
def job_file(id:str,name:str):
    store.get('job',id)
    if name not in JOB_FILES: raise HTTPException(404)
    p=DATA/'jobs'/id/name
    if not p.is_file(): raise HTTPException(404)
    from .report_locale import download_name
    return FileResponse(p,filename=download_name(name,id),headers={'Cache-Control':'no-store'})
@app.get('/api/v1/agent/tasks/{id}/files/{name}')
def task_file(id:str,name:str):
    store.get('task',id)
    if name not in ('comparison.csv','comparison.json','report.md','report.pdf','feedback.json'): raise HTTPException(404)
    p=DATA/'tasks'/id/name
    if not p.exists(): raise HTTPException(404)
    from .report_locale import download_name
    return FileResponse(p,filename=download_name(name,id,task=True),headers={'Cache-Control':'no-store'})

from forge.api import router as forge_router
app.include_router(forge_router)
from forge.imports import router as imports_router
app.include_router(imports_router)
from forge.assistant import router as study_assistant_router
app.include_router(study_assistant_router)
from forge.runs import router as analysis_runs_router
app.include_router(analysis_runs_router)
from forge.exports import router as export_jobs_router
app.include_router(export_jobs_router)
from forge.plans import router as analysis_plans_router
app.include_router(analysis_plans_router)
from forge.workspace import router as workspace_router
app.include_router(workspace_router)
from forge.storage import router as storage_router
app.include_router(storage_router)
from forge.thumbnails import router as thumbnails_router
app.include_router(thumbnails_router)
from forge.legacy import install as install_legacy
install_legacy(app)

dist=Path(os.environ.get('STUDIO_FRONTEND_DIST',str(ROOT/'frontend'/'dist')))
if dist.exists(): app.mount('/',StaticFiles(directory=dist,html=True),name='frontend')
else:
    @app.get('/')
    def not_built(): return {'detail':'请运行 Install.ps1 构建前端，或 npm run build'}
