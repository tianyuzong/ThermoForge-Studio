"""Reproducible browser captures, MP4 and a Chinese run report, off the HTTP thread."""
import json,sys,os,time,subprocess,traceback,csv,zipfile,hashlib
from pathlib import Path
from functools import lru_cache
import numpy as np,h5py
from strength.config import ROOT,DATA
from strength import store
from .provenance import digest,file_hash

REACTION_POSTPROCESS_FILES=('forge/run_exports.py','forge/loads.py','forge/mechanics.py','forge/schemas.py',
                            'strength/bonding.py','strength/fem.py','strength/schemas.py')
_PROGRESS_SEQUENCES={}


def renderer_fingerprint(root=None):
    """Bind capture reuse to the served build and its field/display implementation."""
    root=Path(root or ROOT)
    required=('forge/run_exports.py','forge/results.py','forge/result_index.py','frontend/src/RunPanel.tsx',
              'frontend/src/Viewport.tsx','frontend/src/resultClient.ts','frontend/src/visualization.ts','frontend/dist/index.html')
    files={root/name for name in required}
    files.update(p for p in (root/'frontend/src').rglob('*') if p.is_file() and p.suffix in ('.ts','.tsx','.css'))
    files.update(p for p in (root/'frontend/dist').rglob('*') if p.is_file())
    return digest({p.relative_to(root).as_posix():file_hash(p) if p.is_file() else None for p in sorted(files)})


def write_progress(root,run,stage,detail,node=None,**values):
    """The parent consumes a task-scoped sidecar; the child cannot revive jobs."""
    root=Path(root);key=str(root.resolve());sequence=_PROGRESS_SEQUENCES.get(key,0)+1;_PROGRESS_SEQUENCES[key]=sequence
    record={'schema_version':1,'export_task_id':os.environ.get('STUDIO_EXPORT_TASK_ID'),'run_id':run['id'],
            'sequence':sequence,'updated_at':time.time(),'stage':stage,'detail':detail}
    if node:record.update(node_key=node['key'],node_label=node.get('label',node['key']))
    record.update(values);store.write_json(root/'progress.json',record)
    return record


def ranges(source,summary=None):
    from .results import result_index
    return result_index(source)['ranges']


def static_support_reactions(source):
    """Recover support reactions from saved native forces, including bonded MPCs."""
    from .integrity import identity
    source=Path(source).resolve()
    config=json.loads((source/'config.json').read_text(encoding='utf-8'))
    if config['case']['analysis_type']!='static':return None
    paths=[source/name for name in ('fields.h5','config.json','mesh.npz')]+[DATA/'models'/config['model_id']/'geometry.json']
    sources=tuple((str(path),identity(path)) for path in paths)
    code=tuple((str(ROOT/name),identity(ROOT/name)) for name in REACTION_POSTPROCESS_FILES)
    return _static_support_reactions(str(source),sources,code)


@lru_cache(maxsize=8)
def _static_support_reactions(source,sources,code):
    from .integrity import file_digest,identity
    source=Path(source)
    # File digests are cached only while size, timestamps and file identity match.
    signature=digest({'schema_version':1,'sources':{path:file_digest(path) for path,stamp in sources},
                      'postprocess_code':{path:file_digest(path) for path,stamp in code}})
    cache=source/'support-reactions.json'
    try:
        saved=json.loads(cache.read_text(encoding='utf-8'))
        if saved.get('signature')==signature and saved.get('result_sha256')==digest(saved['result']):return saved['result']
    except (OSError,ValueError,KeyError,TypeError):pass
    result=_compute_static_support_reactions(source)
    if any(identity(path)!=stamp for path,stamp in (*sources,*code)):raise ValueError('恢复支反力期间源文件发生变化，请重新读取')
    try:store.write_json(cache,{'schema_version':1,'signature':signature,'result_sha256':digest(result),'result':result})
    except OSError:pass  # Read-only imported result folders still support uncached recovery.
    return result


def _compute_static_support_reactions(source):
    from .schemas import AnalysisCase
    from .loads import assemble_loads
    from .mechanics import constraints
    from strength.bonding import prepare_bonds,recover_bonds
    config=json.loads((source/'config.json').read_text(encoding='utf-8'))
    case=AnalysisCase.model_validate(config['case'])
    if case.analysis_type!='static':return None
    geo=json.loads((DATA/'models'/config['model_id']/'geometry.json').read_text(encoding='utf-8'))
    with np.load(source/'mesh.npz') as f:mesh=dict(f)
    with h5py.File(source/'fields.h5') as f:data={key:f[key][0] for key in ('forces','displacements')}
    force,_=assemble_loads(mesh,case,geo);prescribed,groups=constraints(mesh,case,geo)
    if case.bonded_connections:
        recover_bonds(mesh,data,force,prescribed,prepare_bonds(mesh,case,geo,prescribed))
        reactions=data['reactions']
    else:
        reactions=np.zeros_like(force);dofs=np.array(sorted(prescribed),int)
        reactions.ravel()[dofs]=(data['forces']-force).ravel()[dofs]
    from .integrity import file_digest
    result={'source_fields_sha256':file_digest(source/'fields.h5'),'basis':'原生节点力减外载，并按原 MPC 消元关系回传至支撑自由度',
            'total_N':reactions.sum(0).tolist(),'applied_total_N':force.sum(0).tolist(),'groups':[]}
    for group in groups:
        selected=reactions[group['nodes']].copy()
        for axis in range(3):
            if 'xyz'[axis] not in group['components']:selected[:,axis]=0
        result['groups'].append({'name':group['name'],'force_N':selected.sum(0).tolist()})
    return result


def catalog(node,summary):
    key=node.get('case',{}).get('analysis_type',node['key'])
    return {'static':['stress'],'fatigue':['stress'],'thermal_steady':['temperature'],'thermal_transient':['temperature'],
            'thermo_mechanical':['temperature','stress','contact_pressure','contact_gap'],
            'contact_cycle':['contact_pressure','stress','contact_gap','wear_depth'],
            'contact_fatigue':['contact_pressure','stress','wear_depth']}[key]


def file_entry(root,path):return {'path':path.relative_to(root).as_posix(),'bytes':path.stat().st_size,'sha256':file_hash(path)}


def capture_samples(times,temporal_kind='saved_frame',videos=True):
    """Select only saved physical states; final assessments never gain a time history."""
    times=np.asarray(times,float)
    if not len(times) or not np.isfinite(times).all() or np.any(np.diff(times)<0):raise ValueError('保存结果时间轴无效')
    assessment=temporal_kind in ('final_only','cumulative_assessment');static=len(times)==1 or assessment
    indices=list(range(72)) if static else list(range(len(times)))
    if not videos:indices=[0] if static else sorted({0,len(times)//2,len(times)-1})
    frames=[(len(times)-1 if assessment else 0) if static else i for i in indices]
    angles=[position*2*np.pi/len(indices) if static else None for position in range(len(indices))]
    durations=[.1]*len(indices) if static else (np.diff(times[indices])*15/max(times[-1]-times[0],1e-12)).tolist()+[2.]
    return {'frames':frames,'angles':angles,'durations_s':[max(v,1/24) for v in durations],
            'mode':'assessment_rotation_non_transient' if assessment else 'view_rotation_non_transient' if static else 'saved_physical_frames'}


def ensure_study_report(source,destination=None):
    destination=Path(destination or source);destination.mkdir(parents=True,exist_ok=True)
    from .schemas import AnalysisCase
    from .results import write_report
    verification=source/'verification.json'
    config=json.loads((source/'config.json').read_text(encoding='utf-8'));aliases={}
    try:
        project=store.get('project',config.get('project_id',''))
        if project.get('component_aliases_model_id')==config.get('model_id'):aliases=project.get('component_aliases',{})
    except KeyError:pass
    signature=digest({'display_component_aliases':aliases,'raw':file_hash(source/'fields.h5'),'config':file_hash(source/'config.json'),
                      'summary':file_hash(source/'summary.json'),'report_code':file_hash(ROOT/'forge/report.py'),'detailed_report_code':{p.name:file_hash(p) for p in (ROOT/'forge/reporting').glob('*.py')},
                      'export_code':file_hash(Path(__file__)),'results_code':file_hash(ROOT/'forge/results.py'),
                      'verification':file_hash(verification) if verification.exists() else None})
    stamp=destination/'report-provenance.json'
    if stamp.exists():
        try:
            saved=json.loads(stamp.read_text(encoding='utf-8'))
            if saved['signature']==signature and all(file_hash(destination/name)==saved['sha256'][name] for name in ('report.pdf','report.md','metrics.csv')):return
        except (OSError,ValueError,KeyError):pass
    case=AnalysisCase.model_validate(json.loads((source/'config.json').read_text(encoding='utf-8'))['case'])
    summary=json.loads((source/'summary.json').read_text(encoding='utf-8'))
    if destination.resolve()==source.resolve():write_report(source,case,summary)
    else:write_report(destination,case,summary,source=source)
    store.write_json(stamp,{'signature':signature,'sha256':{name:file_hash(destination/name) for name in ('report.pdf','report.md','metrics.csv')}})


def reusable_export(root,entry,signature,key):
    if entry.get('signature')!=signature or entry.get('status')!='completed' or not entry.get('files'):return False
    if not any(f.get('path')==key+'/view.json' for f in entry['files']):return False
    try:
        for item in entry['files']:
            relative=Path(item['path']);path=(root/relative).resolve()
            if not relative.parts or relative.parts[0]!=key or not path.is_relative_to(root.resolve()):return False
            if not path.is_file() or file_hash(path)!=item['sha256']:return False
        return (root/key/'view.json').is_file()
    except (OSError,KeyError,TypeError):return False


def render_node(run,node,index,root):
    source=DATA/'studies'/node['study_id'];summary=json.loads((source/'summary.json').read_text(encoding='utf-8'))
    dest=root/node['key'];dest.mkdir(exist_ok=True)
    signature=digest({'raw':file_hash(source/'fields.h5'),'summary':file_hash(source/'summary.json'),'validation':node.get('validation'),
        'renderer':renderer_fingerprint(),'schema':4,'videos':run.get('videos',True),'label':node['label'],'view':node.get('hidden_body_ids',[]),'fields':catalog(node,summary)})
    previous=dest/'export.json'
    if previous.exists():
        cached=json.loads(previous.read_text(encoding='utf-8'))
        if reusable_export(root,cached,signature,node['key']):
            write_progress(root,run,'reusing',node['label']+'：已核验复用现有动画',node)
            return cached
    for prior in store.listing('analysis_run',1000):
        if prior['id']==run['id']:continue
        other=DATA/'analysis-runs'/prior['id'];cached_path=other/node['key']/'export.json'
        if not cached_path.exists():continue
        try:cached=json.loads(cached_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        if not reusable_export(other,cached,signature,node['key']):continue
        import shutil
        for item in cached['files']:
            target=root/item['path'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(other/item['path'],target)
        shutil.copy2(other/node['key']/'view.json',dest/'view.json')
        result={**cached,'reused_from_run':prior['id']}
        write_progress(root,run,'reusing',node['label']+'：复用已核验动画',node)
        store.write_json(previous,result);return result
    kind=node.get('case',{}).get('analysis_type',node['key'])
    from .results import result_index
    temporal=result_index(source)['field_temporal_kind']
    scales={field:(100 if kind=='fatigue' else 30 if kind=='thermo_mechanical' and field=='stress' else 1) for field in catalog(node,summary)}
    metadata={'ranges':ranges(source,summary),'reactions':static_support_reactions(source),'summary':summary,'times_s':summary['times_s'],'signature':signature,
              'video':{'width':1920,'height':1080,'fps':24,'physical_history_playback_s':15,'end_hold_s':2,
                       'mode':'view_rotation_non_transient' if len(summary['times_s'])==1 else 'saved_physical_frames',
                       'deformation_scales':scales,'field_modes':{field:('assessment_rotation_non_transient' if temporal.get(field) in ('final_only','cumulative_assessment') else 'view_rotation_non_transient' if len(summary['times_s'])==1 else 'saved_physical_frames') for field in catalog(node,summary)},'interpolation':'duplicate_frames_only'}}
    store.write_json(dest/'view.json',metadata)
    files=[file_entry(root,dest/'view.json')]
    from playwright.sync_api import sync_playwright
    import imageio_ffmpeg
    write_progress(root,run,'node',node['label']+'：准备结果视图',node)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel=os.environ.get('STUDIO_VIDEO_BROWSER','msedge'),headless=True,args=['--enable-unsafe-swiftshader'])
        try:
            page=browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=1)
            page.goto(run['base_url']+f'/?run={run["id"]}&node={index}&capture=1',wait_until='domcontentloaded')
            page.wait_for_function('typeof window.studioCapture === "function"',timeout=120000)
            for field in catalog(node,summary):
                if field not in metadata['ranges']:continue
                from .results import CATALOG
                field_label=CATALOG.get(field,(field,))[0]
                if not run.get('_detached_export'):store.update('analysis_run',run['id'],detail=node['label']+'：导出 '+field+' 视频')
                images=dest/('frames-'+field);images.mkdir(exist_ok=True)
                sample_plan=capture_samples(summary['times_s'],temporal.get(field,'saved_frame'),run.get('videos',True))
                samples=sample_plan['frames']
                for position,(frame,angle) in enumerate(zip(samples,sample_plan['angles'])):
                    write_progress(root,run,'capture',f'{node["label"]} · {field_label}：截图 {position+1}/{len(samples)}',node,
                                   field=field,frame=position+1,frame_total=len(samples),completed_frames=position,
                                   physical_frame=frame,physical_time_s=summary['times_s'][frame])
                    page.evaluate('(v)=>window.studioCapture(v)',{'frame':frame,'field':field,'angle':angle,'deformation':scales[field]})
                    page.screenshot(path=str(images/f'{position:04d}.png'))
                if run.get('videos',True):
                    write_progress(root,run,'encoding',f'{node["label"]} · {field_label}：编码 MP4',node,
                                   field=field,frame=len(samples),frame_total=len(samples),completed_frames=len(samples))
                    durations=sample_plan['durations_s']
                    # Duplicate boundary states between wear blocks have no elapsed physical time.
                    durations=[max(v,1/24) for v in durations]
                    concat=['ffconcat version 1.0']
                    for i,seconds in enumerate(durations):concat.extend([f"file '{i:04d}.png'",f'duration {seconds:.12f}'])
                    concat.append(f"file '{len(samples)-1:04d}.png'")
                    (images/'frames.txt').write_text('\n'.join(concat),encoding='ascii')
                    output=dest/(field+'.mp4');pending=dest/(field+'.pending.mp4')
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-f','concat','-safe','1','-i',str(images/'frames.txt'),
                        '-vf','fps=24,format=yuv420p','-c:v','libx264','-crf','18','-preset','fast','-threads','2','-movflags','+faststart','-an',str(pending)],check=True,timeout=600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if pending.stat().st_size<1000:raise ValueError('视频输出不完整')
                    pending.replace(output);files.append(file_entry(root,output))
                for label,i in [('first',0),('middle',len(samples)//2),('last',len(samples)-1)]:
                    import shutil
                    target=dest/(field+'-'+label+'.png');shutil.copy2(images/f'{i:04d}.png',target);files.append(file_entry(root,target))
                # Only remove the verified, task-owned temporary frames directory.
                resolved=images.resolve()
                if not resolved.is_relative_to(root.resolve()):raise ValueError('帧目录越界')
                __import__('shutil').rmtree(resolved)
        finally:browser.close()
    result={'key':node['key'],'status':'completed','signature':signature,'files':files}
    store.write_json(previous,result);return result


def report(run,root):
    from .reporting.document import build_report
    return build_report(run,root)


def main(id,report_only=False,scope='all',output_root=None):
    import shutil
    if report_only:scope='report'
    if scope not in ('report','video','all'):raise ValueError('未知导出范围')
    original=DATA/'analysis-runs'/id;root=Path(output_root or original);root.mkdir(parents=True,exist_ok=True)
    detached=root.resolve()!=original.resolve();run=store.get('analysis_run',id);run['_detached_export']=detached;exports=[]
    write_progress(root,run,'preparing','检查原始结果与请求的导出范围',scope=scope)
    try:
        from .exports import latest_root
        prior=latest_root(run)
    except (ImportError,KeyError):prior=original
    if detached:
        # Copy only derived deliverables. Raw fields stay immutable in study folders.
        for name in ('snapshot.json','plan-context.json','plan-decision.json'):
            if (original/name).is_file():shutil.copy2(original/name,root/name)
        if scope=='video':
            for name in ('summary.pdf','report-evidence.json'):
                if (prior/name).is_file():shutil.copy2(prior/name,root/name)
            if (prior/'report-assets').is_dir():shutil.copytree(prior/'report-assets',root/'report-assets',dirs_exist_ok=True)
    if scope=='report':
        if run['status'] not in ('completed','partial','failed','interrupted','cancelled'):
            raise ValueError('当前批次仍在执行，不能并行替换报告')
        exports=[e for e in run.get('exports',[]) if e.get('key')!='report-diagnostics']
        for export in exports:
            if export.get('status')!='completed':continue
            for item in export.get('files',[]):
                candidate=(prior/item['path']).resolve()
                if not candidate.is_relative_to(prior.resolve()) or not candidate.is_file() or file_hash(candidate)!=item['sha256']:
                    raise ValueError('已有导出损坏，不能标为已核验：'+item['path'])
                if detached:
                    target=root/item['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(candidate,target)
            if detached and (prior/export['key']/'export.json').is_file():shutil.copy2(prior/export['key']/'export.json',root/export['key']/'export.json')
    else:
        for index,node in enumerate(run['nodes']):
            write_progress(root,run,'node',f'准备研究 {index+1}/{len(run["nodes"])}：{node["label"]}',node,item=index+1,item_total=len(run['nodes']))
            if not node.get('study_id') or store.get('study',node['study_id'])['status'] not in ('completed','partial'):
                result={'key':node['key'],'status':'blocked','detail':'该研究无可用完整结果','files':[]}
            else:
                try:
                    from .run_validation import publish
                    if not detached:publish(node)
                    result=render_node(run,node,index,root)
                except Exception as exc:
                    traceback.print_exc();result={'key':node['key'],'status':'failed','detail':str(exc),'files':[]}
            exports.append(result)
            if not detached:store.update('analysis_run',id,exports=exports)
    if not detached:store.update('analysis_run',id,detail='生成中文汇总报告与数据包')
    report_diagnostics=[]
    if scope=='video' and (root/'report-evidence.json').is_file():
        prior_evidence=json.loads((root/'report-evidence.json').read_text(encoding='utf-8'))
        report_diagnostics.extend(prior_evidence.get('diagnostics',[]))
    for node in run.get('nodes',[]):
        if not node.get('study_id'):continue
        source=DATA/'studies'/node['study_id']
        if not (source/'fields.h5').is_file() or not (source/'summary.json').is_file():continue
        try:
            target=root/'studies'/node['key']
            if scope=='video' and (prior/'studies'/node['key']/'report.pdf').is_file():
                if target.resolve()!=(prior/'studies'/node['key']).resolve():shutil.copytree(prior/'studies'/node['key'],target,dirs_exist_ok=True)
            elif scope!='video':
                write_progress(root,run,'study_report',node['label']+'：生成分项中文报告',node)
                ensure_study_report(source,target)
                if (target/'report-evidence.json').is_file():
                    single=json.loads((target/'report-evidence.json').read_text(encoding='utf-8'))
                    report_diagnostics.extend(single.get('diagnostics',[]))
        except Exception as exc:report_diagnostics.append({'key':node['key'],'detail':str(exc)})
    if scope!='video' or not (root/'summary.pdf').is_file():
        write_progress(root,run,'summary_report','生成中文汇总报告、目录与图表')
        try:
            evidence=report(run,root) or {}
            report_diagnostics.extend(evidence.get('diagnostics',[]))
            if evidence.get('complete') is False and not evidence.get('diagnostics'):report_diagnostics.append({'key':'summary','detail':'部分研究没有可用报告数据，已保留诊断章节'})
        except Exception as exc:
            report_diagnostics.append({'key':'summary','detail':str(exc)})
            from .reporting.document import build_diagnostic_report
            build_diagnostic_report(run,root,str(exc))
    if report_diagnostics:
        exports.append({'key':'report-diagnostics','status':'failed','detail':'部分报告章节未生成','files':[]})
    store.write_json(root/'report-diagnostics.json',report_diagnostics)
    if not detached:store.update('analysis_run',id,exports=exports)
    run={**run,'exports':exports}
    run.pop('_detached_export',None)

    rows=[]
    write_progress(root,run,'metrics','整理统一指标表与交付清单')
    for node in run['nodes']:
        if node.get('study_id'):
            study=store.get('study',node['study_id'])
            rows.extend([node['label'],node['status'],key,value] for key,value in (study.get('summary') or {}).items() if isinstance(value,(str,int,float,bool)))
            hot=(study.get('summary') or {}).get('fatigue',{}).get('hotspot') or {}
            for key in ('life_cycles','life_status','life_definition','damage','body_id','element','integration_point'):
                if key in hot:rows.append([node['label'],node['status'],'fatigue.'+key,hot[key]])
            for axis,value in zip('xyz',hot.get('position_m',[])):rows.append([node['label'],node['status'],'fatigue.position_'+axis+'_m',value])
            for name,probe in (study.get('summary') or {}).get('clearance_probes',{}).items():
                if probe.get('minimum_gap_m'):rows.append([node['label'],node['status'],'clearance.'+name+'.minimum_gap_m',min(probe['minimum_gap_m'])])
            export=next((e for e in exports if e['key']==node['key']),{})
            if node.get('case',{}).get('analysis_type',node['key'])=='static' and export.get('status')=='completed':
                reaction=json.loads((root/node['key']/'view.json').read_text(encoding='utf-8')).get('reactions')
                if reaction:
                    for axis,value in zip('xyz',reaction['total_N']):rows.append([node['label'],node['status'],'reaction_total_'+axis+'_N',value])
    with (root/'metrics.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['study','status','metric','value']);writer.writerows(rows)
    complete=all(n['status']=='completed' for n in run['nodes']) and all(e['status']=='completed' for e in exports)
    if run.get('videos') is True:
        complete=complete and {n['key'] for n in run['nodes']} <= {e['key'] for e in exports if e.get('status')=='completed'}
    store.write_json(root/'run.json',{**run,'status':'completed' if complete else 'partial','phase':'finished'})
    names={'summary.pdf','report-diagnostics.json','metrics.csv','run.json','snapshot.json','plan-context.json','plan-decision.json','report-evidence.json'}
    names.update(p.relative_to(root).as_posix() for p in (root/'report-assets').rglob('*') if p.is_file())
    names.update(p.relative_to(root).as_posix() for p in (root/'studies').rglob('*') if p.is_file())
    for export in exports:
        if export['status']=='completed':
            names.update(f['path'] for f in export['files']);names.update((export['key']+'/view.json',export['key']+'/export.json'))
    members=[(root/name,name) for name in sorted(names) if (root/name).is_file()]
    for node in run['nodes']:
        if not node.get('study_id'):continue
        source=DATA/'studies'/node['study_id']
        for name in ('report.pdf','report.md','report-provenance.json','metrics.csv','fields.h5','mesh.npz','config.json','summary.json','audit.json','raw-fields.sha256.json','verification.json','run-step-validation.json','history.png','contact-history.png','initial-state.json'):
            relative='studies/'+node['key']+'/'+name
            if (source/name).is_file() and relative not in names:members.append((source/name,relative))
    verified_ids={sid for node in run['nodes'] for sid in node.get('validation',{}).get('raw_sha256',{})}
    validation_paths={node['study_id']:'studies/'+node['key'] for node in run['nodes'] if node.get('study_id') in verified_ids}
    for sid in sorted(verified_ids):
        if sid in validation_paths:continue
        validation_paths[sid]='time-step-validation/'+sid
        source=DATA/'studies'/sid
        for name in ('fields.h5','config.json','summary.json','audit.json','raw-fields.sha256.json'):
            if (source/name).is_file():members.append((source/name,'time-step-validation/'+sid+'/'+name))
    selected={n.get('study_id') for n in run['nodes']}|verified_ids
    for sid in run.get('owned_studies',[]):
        if sid in selected:continue
        source=DATA/'studies'/sid;prefix='diagnostics/'+sid
        record=root/prefix/'study.json';store.write_json(record,store.get('study',sid));members.append((record,prefix+'/study.json'))
        for name in ('config.json','summary.json','audit.json','status.json','worker.log'):
            if (source/name).is_file():members.append((source/name,prefix+'/'+name))
    for mid in sorted({n['model_id'] for n in run['nodes']}):
        model=DATA/'models'/mid
        info=json.loads((model/'source.json').read_text(encoding='utf-8'))
        for name in dict.fromkeys((info['file'],'source.json','geometry.json')):
            members.append((model/name,'models/'+mid+'/'+name))
    manifest_files=[]
    for i,(path,name) in enumerate(members,1):
        write_progress(root,run,'manifest',f'校验交付文件 {i}/{len(members)}：{name}',item=i,item_total=len(members),file=name)
        manifest_files.append({'path':name,'bytes':path.stat().st_size,'sha256':file_hash(path)})
    manifest={'run_id':id,'suite_version':run['suite_version'],'complete':complete,'scope':scope,'report_diagnostics':report_diagnostics,
        'time_step_validation_sources':validation_paths,'files':manifest_files}
    store.write_json(root/'manifest.json',manifest)
    pending=root/'bundle.pending.zip'
    with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for i,(p,name) in enumerate(members,1):
            write_progress(root,run,'zip',f'打包下载文件 {i}/{len(members)}：{name}',item=i,item_total=len(members),file=name)
            z.write(p,name)
        z.write(root/'manifest.json','manifest.json')
    pending.replace(root/'bundle.zip')
    artifacts={'report':'summary.pdf','metrics':'metrics.csv','manifest':'manifest.json','bundle':'bundle.zip','bundle_sha256':file_hash(root/'bundle.zip')}
    store.write_json(root/'artifacts.json',artifacts)
    if not detached:store.update('analysis_run',id,detail=('部分交付已保存；请查看诊断' if not complete else '分析及请求的导出已完成'),artifacts=artifacts)
    write_progress(root,run,'finished','导出文件已生成，等待发布校验',complete=complete)
    return {'complete':complete,'artifacts':artifacts,'exports':exports,'diagnostics':report_diagnostics}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('id');parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--scope',choices=['report','video','all'],default='all');parser.add_argument('--output-dir')
    args=parser.parse_args();main(args.id,report_only=args.report_only,scope=args.scope,output_root=args.output_dir)
