"""Selective, non-destructive local imports. Originals stay byte-for-byte intact."""
import hashlib,json,re,shutil,sqlite3
from pathlib import Path
from typing import Literal
from fastapi import APIRouter
from pydantic import Field
from strength.config import DATA
from strength.schemas import Strict
from strength import store

router=APIRouter(prefix='/api/v1/imports')


class Source(Strict):
    path:str
    platform:Literal['strength','thermal']


class ImportRequest(Source):
    project_ids:list[str]=Field(min_length=1,max_length=100)


def source_root(value):
    root=Path(value).expanduser().resolve()
    if not root.is_dir():raise ValueError('来源数据目录不存在')
    if root==DATA or DATA in root.parents:raise ValueError('不能把当前平台数据重新导入自身')
    return root


def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):raise ValueError('来源项目编号非法')
    return value


def entities(root):
    db=root/'studio.sqlite'
    if not db.is_file():raise ValueError('请选择包含 studio.sqlite 的旧 Strength Studio data 目录')
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
        return [(kind,json.loads(data)) for kind,data in connection.execute('SELECT kind,data FROM entities')]


@router.post('/preview')
def preview(request:Source):
    root=source_root(request.path)
    if request.platform=='strength':projects=[v for k,v in entities(root) if k=='project']
    else:projects=[json.loads(p.read_text(encoding='utf-8-sig')) for p in (root/'projects').glob('*.json')]
    return {'source':str(root),'platform':request.platform,'projects':[{'id':p['id'],'name':p.get('name',p['id']),'model_id':p.get('model_id')} for p in projects]}


def copy_tree(source,destination,manifest):
    if source.is_symlink():raise ValueError('导入源不能包含符号链接')
    if not source.is_dir():raise ValueError('来源缺少目录：'+str(source))
    from .resources import ensure_disk_headroom
    candidates=list(source.rglob('*'))
    total=sum(p.stat().st_size for p in candidates if p.is_file() and not p.is_symlink())
    if total>ensure_disk_headroom(DATA)*.8:raise ValueError('导入复制所需空间超过可用预算')
    for file in candidates:
        if file.is_symlink():raise ValueError('导入源不能包含符号链接')
        if not file.resolve().is_relative_to(source.resolve()):raise ValueError('导入文件超出来源目录')
        if file.is_file():
            with file.open('rb') as stream:original_checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            target=destination/file.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
            with target.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            with file.open('rb') as stream:current_checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            if checksum!=original_checksum or current_checksum!=original_checksum:raise ValueError('导入期间源文件变化或复制校验失败：'+file.name)
            manifest.append({'source':str(file),'copy':str(target.relative_to(DATA)),'source_sha256':original_checksum})


def native_thermal_geometry(folder,metadata):
    """Bind new native selections to a fresh CAD version, never legacy face IDs."""
    from strength.geometry import inspect_model,session
    import gmsh
    source=(folder/metadata['source']).resolve()
    if source.parent!=folder.resolve() or not source.is_file():raise ValueError('旧热模型源文件路径无效')
    mid=store.uid();target=DATA/'models'/mid;target.mkdir()
    suffix=source.suffix.lower();scale=float(metadata.get('step_scale',1) if suffix in ('.step','.stp') else metadata.get('scale_factor',1))
    if not __import__('math').isfinite(scale) or scale<=0:raise ValueError('旧模型缩放无效')
    units=metadata.get('units','mm');filename='source'+suffix
    shutil.copy2(source,target/('original'+suffix))
    if scale==1:shutil.copy2(source,target/filename)
    elif suffix in ('.step','.stp'):
        with session():
            gmsh.model.occ.importShapes(str(source));gmsh.model.occ.synchronize()
            gmsh.model.occ.dilate(gmsh.model.getEntities(3),0,0,0,scale,scale,scale)
            gmsh.model.occ.synchronize();gmsh.write(str(target/filename))
        units='m'
    else:
        import trimesh
        surface=trimesh.load(source,force='mesh');surface.vertices*=scale;surface.export(target/filename)
    store.write_json(target/'source.json',{'file':filename,'units':units})
    geo=inspect_model(target);store.put('model',geo)
    store.write_json(target/'legacy-provenance.json',{'legacy_model_id':metadata['id'],'scale':scale,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'native_geometry_sha256':geo['source_sha256'],'selections_rebound':False})
    return mid


def _import_into_workspace(request:ImportRequest):
    root=source_root(request.path);iid=store.uid();archive=DATA/'imports'/iid;archive.mkdir(parents=True)
    manifest=[];created=[]
    selected={identifier(p) for p in request.project_ids}
    if request.platform=='strength':
        rows=entities(root);projects=[p for kind,p in rows if kind=='project' and p['id'] in selected]
        if len(projects)!=len(selected):raise ValueError('部分选择的旧项目不存在')
        records=[(k,v) for k,v in rows if (k=='project' and v['id'] in selected) or v.get('project_id') in selected]
        model_ids={v['model_id'] for _,v in records if v.get('model_id')}
        records += [(k,v) for k,v in rows if k=='model' and v['id'] in model_ids]
        if model_ids-{v['id'] for k,v in records if k=='model'}:raise ValueError('来源项目缺少所引用的模型记录')
        for mid in model_ids:
            identifier(mid)
            model_root=root/'models'/mid
            if not model_root.is_dir() or not any(p.is_file() for p in model_root.iterdir()):raise ValueError('来源项目缺少模型文件：'+mid)
        mapping={v['id']:store.uid() for _,v in records}
        def remap(value):
            if isinstance(value,str):return mapping.get(value,value)
            if isinstance(value,list):return [remap(v) for v in value]
            if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
            return value
        for kind,original in records:
            identifier(original['id'])
            new=remap(original);new['import_provenance']={'import_id':iid,'source_platform':'strength','source_id':original['id'],'validation':'retained_original'}
            if kind in ('model','job','task') and (root/(kind+'s')/original['id']).is_dir():
                copy_tree(root/(kind+'s')/original['id'],DATA/(kind+'s')/new['id'],manifest)
                copied=DATA/(kind+'s')/new['id']/'config.json'
                if copied.is_file():
                    shutil.copy2(copied,archive/(kind+'-'+original['id']+'-original-config.json'))
                    store.write_json(copied,remap(json.loads(copied.read_text(encoding='utf-8-sig'))))
                # Geometry identifiers belong to the container; source CAD bytes and result files stay unchanged.
                if kind=='model':store.write_json(DATA/'models'/new['id']/'geometry.json',new)
            if kind in ('job','task') and new.get('status') not in ('completed','partial','failed','cancelled','interrupted'):
                new.update(status='interrupted',detail='导入时来源作业未完成；保留文件，不恢复来源进程')
            store.put(kind,new)
            if kind=='project':created.append(new['id'])
        store.write_json(archive/'original-records.json',{'records':records})
    else:
        from thermal_legacy.runtime import DATA as thermal_data
        for pid in selected:
            path=root/'projects'/(pid+'.json')
            if not path.is_file():raise ValueError('来源热项目不存在')
            original=json.loads(path.read_text(encoding='utf-8-sig'));mid=identifier(original['model_id'])
            newmid=store.uid();newpid=store.uid()
            copy_tree(root/'models'/mid,thermal_data/'models'/newmid,manifest)
            metadata=json.loads((thermal_data/'models'/newmid/'metadata.json').read_text(encoding='utf-8-sig'));metadata['id']=newmid
            store.write_json(thermal_data/'models'/newmid/'metadata.json',metadata)
            new={**original,'id':newpid,'model_id':newmid,'updated_at':store.now()}
            if 'config' in new:new['config']={**new['config'],'model_id':newmid}
            job_configs=[]
            for job in (root/'jobs').glob('*'):
                cfg=job/'config.json'
                if not cfg.is_file():continue
                config=json.loads(cfg.read_text(encoding='utf-8-sig'))
                if config.get('model_id')!=mid:continue
                job_configs.append((job,config))
            job_ids={job.name:store.uid() for job,_ in job_configs}
            for job,config in job_configs:
                jid=job_ids[job.name];copy_tree(job,thermal_data/'jobs'/jid,manifest)
                store.write_json(archive/(job.name+'-original-config.json'),config)
                if config.get('initial_from_job') in job_ids:config['initial_from_job']=job_ids[config['initial_from_job']]
                store.write_json(thermal_data/'jobs'/jid/'config.json',{**config,'model_id':newmid})
            (thermal_data/'projects').mkdir(exist_ok=True);store.write_json(thermal_data/'projects'/(newpid+'.json'),new)
            try:native_id=native_thermal_geometry(thermal_data/'models'/newmid,metadata);native_error=None
            except (ValueError,RuntimeError) as exc:native_id=None;native_error=str(exc)
            uid=store.uid();store.put('project',{'id':uid,'name':original.get('name','旧热项目')+' · 导入','model_id':native_id,'native_import_error':native_error,
                'legacy_thermal_project_id':newpid,'import_provenance':{'import_id':iid,'source_platform':'thermal','source_id':pid,'precision':'P1 original','validation':'retained_original'}})
            created.append(uid);store.write_json(archive/(pid+'.json'),original)
    for record in manifest:
        with (DATA/record['copy']).open('rb') as stream:record['installed_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
    store.write_json(archive/'manifest.json',{'schema_version':1,'source':str(root),'platform':request.platform,'created_project_ids':created,'files':manifest})
    return {'import_id':iid,'project_ids':created,'files_copied':len(manifest),'originals_modified':False}


@router.post('')
def import_projects(request:ImportRequest):
    """Stage in a private worker database, then publish all records atomically."""
    import os,subprocess,sys
    from strength.config import ROOT
    root=source_root(request.path);iid=store.uid();archive=DATA/'imports'/iid;archive.mkdir(parents=True)
    stage=ROOT/'.runtime'/'imports'/iid[:12];stage.mkdir(parents=True);payload=archive/'request.json';store.write_json(payload,request.model_dump())
    record=store.put('import_job',{'id':iid,'status':'running','source':str(root),'staging_path':str(stage),'platform':request.platform,'created_at':store.now(),'detail':'复制到隔离暂存区，尚未发布项目'})
    try:
        with (archive/'worker.log').open('w',encoding='utf-8') as log:
            process=subprocess.run([sys.executable,'-m','forge.imports','--worker',str(payload)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                env={**os.environ,'STRENGTH_DATA':str(stage),'THERMAL_DATA':str(stage/'legacy-thermal'),'PYTHONIOENCODING':'utf-8'},
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=3600)
        if process.returncode:raise ValueError('旧项目导入在暂存校验阶段失败，现有项目未改变；诊断编号 '+iid)
        result=json.loads((stage/'import-result.json').read_text(encoding='utf-8'))
        staged_rows=entities(stage)
        if not result.get('project_ids') or not all(any(k=='project' and v['id']==pid for k,v in staged_rows) for pid in result['project_ids']):raise ValueError('暂存项目清单不完整')
        staged_models={v['id'] for k,v in staged_rows if k=='model'}
        for kind,value in staged_rows:
            if kind=='project' and value.get('model_id') and value['model_id'] not in staged_models:raise ValueError('暂存项目模型引用不完整')
        files=[p for p in stage.rglob('*') if p.is_file() and p.suffix not in ('.sqlite','-wal','-shm')]
        size=sum(p.stat().st_size for p in files)
        from .resources import ensure_disk_headroom
        ensure_disk_headroom(DATA)
        destinations=[]
        for directory in ('models','jobs','tasks','legacy-thermal','imports'):
            source=stage/directory
            if not source.is_dir():continue
            for item in source.iterdir():
                target=DATA/directory/item.name
                if directory=='legacy-thermal':
                    for part in item.iterdir() if item.is_dir() else []:
                        destinations.append((part,DATA/directory/item.name/part.name))
                else:destinations.append((item,target))
        for source,target in destinations:
            if not source.resolve().is_relative_to(stage.resolve()) or not target.resolve().is_relative_to(DATA.resolve()):raise ValueError('导入发布路径超出当前工作空间')
            if target.exists():raise ValueError('导入目标已存在，未覆盖任何项目')
        publication=[]
        # All paths above are checked before any move. Unpublished files are retained on failure.
        for source,target in destinations:
            target.parent.mkdir(parents=True,exist_ok=True);source.rename(target)
            publication.append({'source':str(source),'target':str(target)})
            store.write_json(archive/'publication-journal.json',publication)
        with store.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for kind,value in staged_rows:
                identifier(value['id'])
                if connection.execute('SELECT 1 FROM entities WHERE id=?',(value['id'],)).fetchone():raise ValueError('导入记录编号冲突，现有记录未覆盖')
                value={**value,'updated_at':store.now(),'revision':1}
                connection.execute('INSERT INTO entities VALUES(?,?,?,?)',(kind,value['id'],json.dumps(value,ensure_ascii=False,allow_nan=False),value['updated_at']))
        final={**result,'publication_id':iid,'staged_bytes':size}
        store.write_json(archive/'result.json',final);store.update('import_job',iid,status='completed',detail='文件校验与项目发布完成',project_ids=result['project_ids'])
        return final
    except Exception as exc:
        store.update('import_job',iid,status='failed',detail=str(exc),staging_retained=True)
        raise


@router.get('/jobs/{id}')
def import_status(id:str):return store.get('import_job',id)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--worker',required=True);args=parser.parse_args()
    request=ImportRequest.model_validate_json(Path(args.worker).read_text(encoding='utf-8'))
    result=_import_into_workspace(request);store.write_json(DATA/'import-result.json',result)
