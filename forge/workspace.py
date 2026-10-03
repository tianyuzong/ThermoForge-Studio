"""Versioned project editing and read-only cross-analysis workspace views."""
import json,math,time
from fastapi import APIRouter,HTTPException,Query
from pydantic import Field
from strength import store
from strength.config import DATA
from strength.schemas import Strict
from .schemas import AnalysisCase
from .provenance import digest

router=APIRouter(prefix='/api/v1')


def changes_between(before,after,path=''):
    if isinstance(before,dict) and isinstance(after,dict):
        return [item for key in sorted(before.keys()|after.keys()) for item in changes_between(before.get(key),after.get(key),(path+'.' if path else '')+key)]
    return [] if before==after else [{'path':path,'before':before,'after':after}]


def draft_key(project,analysis_type):
    return 'draft-'+digest([project['id'],project['model_id'],analysis_type])[:32]


class DraftRequest(Strict):
    case:dict
    expected_revision:int|None=Field(default=None,ge=0)
    base_case_id:str|None=None
    parameter_sources:dict[str,str]=Field(default_factory=dict)


@router.get('/projects/{id}/analysis-draft')
def read_draft(id:str,analysis_type:str='thermal_steady'):
    from .api import template
    p=store.get('project',id)
    if not p.get('model_id'):raise ValueError('请先导入模型')
    try:return store.get('analysis_draft',draft_key(p,analysis_type))
    except KeyError:
        current=(p.get('current_analysis') or {}) if p.get('current_analysis_model_id',p['model_id'])==p['model_id'] else {}
        case=current if current.get('analysis_type')==analysis_type and current.get('geometry_sha256') in (None,store.get('model',p['model_id'])['source_sha256']) else template(id,analysis_type)
        return {'case':case,'revision':0,'project_id':id,'model_id':p['model_id'],'base_case_id':'current_analysis' if case is current else None,'parameter_sources':{},'changes':[],'updated_at':None}


@router.put('/projects/{id}/analysis-draft')
def save_draft(id:str,body:DraftRequest,analysis_type:str='thermal_steady'):
    p=store.get('project',id);geo=store.get('model',p['model_id'])
    if analysis_type not in AnalysisCase.model_fields['analysis_type'].annotation.__args__:raise ValueError('未知分析类型')
    if body.case.get('analysis_type')!=analysis_type:raise ValueError('草稿类型不一致')
    if len(json.dumps(body.case))>500000:raise ValueError('工况草稿过大')
    if body.case.get('geometry_sha256') not in (None,geo['source_sha256']):raise ValueError('草稿绑定了另一版几何')
    from .api import validate
    errors=[]
    try:validate(AnalysisCase.model_validate(body.case),geo)
    except ValueError as exc:errors=[str(exc)]
    def edit(old):
        return {**old,'project_id':id,'model_id':p['model_id'],'analysis_type':analysis_type,'case':body.case,'base_case_id':body.base_case_id,
                'parameter_sources':body.parameter_sources,'changes':changes_between(old.get('case',{}),body.case),'valid':not errors,'validation_errors':errors}
    key=draft_key(p,analysis_type)
    def publish(values):
        old=values['analysis_draft',key];current=values['project',id]
        if current.get('model_id')!=p['model_id']:raise store.RevisionConflict('保存期间项目模型已更换，请重新读取草稿')
        if body.expected_revision is not None and old.get('revision',0)!=body.expected_revision:raise store.RevisionConflict('配置已在另一窗口更新，请重新读取后合并修改')
        changed=edit(old)
        current={**current,'current_draft_id':key,**({'current_analysis':body.case,'current_analysis_model_id':p['model_id']} if not errors else {})}
        return {('analysis_draft',key):changed,('project',id):current}
    try:result=store.mutate_many({('analysis_draft',key):{},('project',id):None},publish)['analysis_draft',key]
    except store.RevisionConflict as exc:raise HTTPException(409,str(exc))
    return result


class AliasesRequest(Strict):
    aliases:dict[str,str]


@router.patch('/projects/{id}/component-aliases')
def aliases(id:str,body:AliasesRequest):
    p=store.get('project',id);geo=store.get('model',p['model_id'])
    if set(body.aliases)-{str(c['id']) for c in geo['components']}:raise ValueError('别名引用不存在的组件')
    if any(not name.strip() or len(name)>120 for name in body.aliases.values()):raise ValueError('组件别名须为 1–120 个字符')
    return store.update('project',id,component_aliases={k:v.strip() for k,v in body.aliases.items()},component_aliases_model_id=p['model_id'])


def scalar_metrics(summary):
    keys=('maximum_temperature_C','maximum_temperature_rise_K','maximum_von_mises_Pa','maximum_displacement_m','maximum_contact_pressure_Pa','maximum_wear_depth_m','strain_energy_J')
    return {k:summary[k] for k in keys if isinstance(summary.get(k),(float,int)) and math.isfinite(summary[k])}


@router.get('/projects/{id}/analysis-comparison')
def comparison(id:str,limit:int=Query(100,ge=1,le=1000),offset:int=Query(0,ge=0)):
    store.get('project',id);entries=[]
    for kind in ('study','job'):
        for item in store.listing(kind,limit,project_id=id,offset=offset):
            if item.get('status') not in ('completed','partial'):continue
            summary=item.get('summary') or {};case=item.get('case') or {};mid=item.get('model_id')
            try:geometry=store.get('model',mid)
            except KeyError:geometry={}
            analysis=item.get('analysis_type','static');route='studies' if kind=='study' else 'jobs'
            fields=['stress','displacement'] if analysis in ('static','fatigue') else ['temperature','temperature_gradient','heat_flux'] if analysis.startswith('thermal_') else ['stress','displacement','contact_pressure','contact_gap']
            if analysis=='thermo_mechanical':fields+=['temperature','temperature_gradient','heat_flux']
            entries.append({'id':item['id'],'kind':kind,'name':item.get('name',case.get('name',item['id'])),'analysis_type':analysis,'model_id':mid,
                'geometry_sha256':case.get('geometry_sha256',geometry.get('source_sha256')),'status':item['status'],'metrics':scalar_metrics(summary),
                'fields':fields,'times_s':summary.get('times_s',[0]),'created_at':item.get('created_at'),'report_url':f'/api/v1/{route}/{item["id"]}/files/report.pdf',
                'result_url':f'/api/v1/{route}/{item["id"]}','field_basis':'raw_integration_points','units':'SI; temperature °C','updated_at':item.get('updated_at',0)})
    entries.sort(key=lambda e:e['updated_at'],reverse=True)
    pairs=[]
    for i,a in enumerate(entries):
        for b in entries[i+1:]:
            reasons=[]
            if not a['geometry_sha256'] or a['geometry_sha256']!=b['geometry_sha256']:reasons.append('几何不同')
            if a['analysis_type']!=b['analysis_type']:reasons.append('分析类型不同，仅并列展示，不计算差值')
            if a['times_s']!=b['times_s']:reasons.append('保存时刻不同，不直接逐帧相减')
            pairs.append({'left':a['id'],'right':b['id'],'comparable':not reasons,'reasons':reasons})
    return {'entries':entries,'comparability':pairs,'offset':offset,'limit':limit}


@router.get('/execution/queue')
def queue():
    from .api import TERMINAL
    from .resources import available_memory
    active=[]
    for kind in ('study','job','export_task'):
        for s in store.listing(kind,10000):
            if s.get('status') in TERMINAL:continue
            active.append({'id':s['id'],'kind':kind,'name':s.get('name',s.get('scope',s['id'])),'project_id':s.get('project_id'),'status':s.get('status'),
                'detail':s.get('detail',''),'created_at':s.get('created_at',0),'last_progress_at':s.get('updated_at'),'cpu_threads':s.get('case',{}).get('resources',{}).get('cpu_threads')})
    active.sort(key=lambda s:s['created_at']);position=0
    for s in active:
        if s['status']=='queued':position+=1;s['queue_position']=position
    import shutil
    return {'items':active,'parallel_heavy_jobs':1,'available_memory_bytes':available_memory(),'free_disk_bytes':shutil.disk_usage(DATA).free,'sampled_at':time.time()}


class ProbeRequest(Strict):
    name:str=Field(min_length=1,max_length=120)
    field:str
    node_index:int=Field(ge=0)


@router.get('/studies/{id}/probes')
def probes(id:str):
    store.get('study',id)
    return store.listing('result_probe',1000,model_id=id)


@router.post('/studies/{id}/probes')
def save_probe(id:str,body:ProbeRequest):
    import h5py,numpy as np
    study=store.get('study',id)
    from .results import CATALOG
    if body.field not in CATALOG:raise ValueError('未知结果场')
    with h5py.File(DATA/'studies'/id/'fields.h5') as f:
        if body.node_index>=len(f['points']):raise ValueError('探针节点超出范围')
        point=f['points'][body.node_index].tolist()
    return store.put('result_probe',{'id':store.uid(),'project_id':study['project_id'],'model_id':id,'study_id':id,**body.model_dump(),'point_m':point,'created_at':store.now()})


@router.get('/studies/{id}/probes/{probe_id}')
def probe_history(id:str,probe_id:str):
    from .results import result_index,surface_topology,frame_values,CATALOG
    probe=store.get('result_probe',probe_id)
    if probe['study_id']!=id:raise HTTPException(404)
    root=DATA/'studies'/id;index=result_index(root);cache=root/'probes'/(probe_id+'.json')
    if cache.is_file():
        saved=json.loads(cache.read_text(encoding='utf-8'))
        if saved.get('raw_sha256')==index['raw_sha256']:return saved
    field=probe['field'];values=[];node=probe['node_index'];times=index['times_s']
    import h5py,numpy as np
    with h5py.File(root/'fields.h5') as f:
        if field=='temperature' and 'temperature_C' in f:values=f['temperature_C'][:,node].tolist()
        elif field=='displacement' and 'displacements' in f:values=np.linalg.norm(f['displacements'][:,node,:],axis=-1).tolist()
    basis='native_node'
    if not values:
        topology=surface_topology(root)
        try:surface=topology['node_ids'].index(node+1)
        except ValueError:raise ValueError('此结果场的交互探针需要选择外表面节点')
        for i in range(len(times)):
            data=frame_values(root,i,[field]);v=data['fields'].get(field);values.append(v[surface] if v is not None else None)
        basis='surface_projection_for_display'
    result={**probe,'times_s':times,'values':values,'raw_sha256':index['raw_sha256'],'basis':basis,'unit':CATALOG[field][1],'factor':CATALOG[field][2]}
    store.write_json(cache,result);return result
