import hashlib,json,sqlite3
from types import SimpleNamespace
import h5py,numpy as np,pytest
from fastapi import HTTPException
from strength import store
from forge import workspace,runs,exports,resources
from forge.schemas import AnalysisCase
from test_forge_numerics import material,tetra_mesh


def project_case(models):
    geo=models['bar'];store.put('model',geo);pid=store.uid()
    store.put('project',{'id':pid,'name':'非电机导热杆','model_id':geo['id']})
    case=AnalysisCase(name='导热杆',analysis_type='thermal_transient',material=material(),
        geometry_sha256=geo['source_sha256'],thermal={'boundaries':[{'kind':'body_power','body_id':1,'value':10}]})
    return pid,case.model_dump()


def test_invalid_draft_is_saved_but_not_adopted_and_stale_save_conflicts(models):
    pid,case=project_case(models)
    first=workspace.save_draft(pid,workspace.DraftRequest(case=case,expected_revision=0),'thermal_transient')
    assert first['valid'] and first['revision']==1
    bad=json.loads(json.dumps(case));bad['thermal']['boundaries'][0]['body_id']=999
    draft=workspace.save_draft(pid,workspace.DraftRequest(case=bad,expected_revision=1),'thermal_transient')
    assert not draft['valid'] and '999' in draft['validation_errors'][0]
    assert store.get('project',pid)['current_analysis']==case
    with pytest.raises(HTTPException) as exc:workspace.save_draft(pid,workspace.DraftRequest(case=case,expected_revision=1),'thermal_transient')
    assert exc.value.status_code==409
    assert workspace.read_draft(pid,'thermal_transient')['case']==bad


def test_interleaved_draft_save_cannot_revert_current_analysis(models,monkeypatch):
    pid,case=project_case(models);new=json.loads(json.dumps(case));new['name']='newest'
    original=store.mutate_many;interleaved=False
    def race(entries,transform,*a,**k):
        nonlocal interleaved
        result=original(entries,transform,*a,**k)
        draft=next((value for (kind,id),value in result.items() if kind=='analysis_draft'),None)
        if draft is not None and not interleaved:
            interleaved=True
            workspace.save_draft(pid,workspace.DraftRequest(case=new,expected_revision=draft['revision']),'thermal_transient')
        return result
    monkeypatch.setattr(store,'mutate_many',race)
    workspace.save_draft(pid,workspace.DraftRequest(case=case,expected_revision=0),'thermal_transient')
    assert workspace.read_draft(pid,'thermal_transient')['case']['name']=='newest'
    assert store.get('project',pid)['current_analysis']['name']=='newest'
    assert interleaved


def test_cancelled_nodes_cannot_be_revived_by_late_progress(monkeypatch):
    rid=store.uid();store.put('analysis_run',{'id':rid,'status':'running','nodes':[{'key':'a','status':'running'},{'key':'b','status':'pending'}],
        'owned_studies':[],'cancel_requested':False})
    runs.cancel(rid);runs.save_node(rid,0,{'key':'a','status':'running','detail':'late progress'})
    record=runs.get(rid)
    assert record['status']=='cancelled' and all(n['status']=='cancelled' for n in record['nodes'])


def test_cancelled_run_stays_cancelled_when_late_execution_fails(monkeypatch):
    rid=store.uid();store.put('analysis_run',{'id':rid,'status':'cancelled','nodes':[],'owned_studies':[],
        'solver_signature':'old','cancel_requested':True})
    monkeypatch.setattr(runs,'solver_signature',lambda:'changed')
    runs.execute(rid)
    assert runs.get(rid)['status']=='cancelled'


def export_fixture(tmp_path,monkeypatch,partial=False):
    monkeypatch.setattr(exports,'DATA',tmp_path);monkeypatch.setattr(runs,'DATA',tmp_path)
    monkeypatch.setattr(exports,'launch',lambda id:None)
    monkeypatch.setattr(runs.api,'submit',lambda *a,**k:pytest.fail('Export attempted a solve'))
    rid=store.uid();sid=store.uid();source=tmp_path/'studies'/sid;source.mkdir(parents=True)
    raw=source/'fields.h5';raw.write_bytes(b'unchanged raw evidence')
    store.write_json(source/'raw-fields.sha256.json',{'sha256':hashlib.sha256(raw.read_bytes()).hexdigest()})
    store.put('study',{'id':sid,'project_id':'p','status':'partial' if partial else 'completed'})
    root=tmp_path/'analysis-runs'/rid;root.mkdir(parents=True);(root/'summary.pdf').write_bytes(b'previous report')
    store.put('analysis_run',{'id':rid,'origin_project_id':'p','status':'partial' if partial else 'completed','nodes':[{'key':'heat','study_id':sid,'status':'partial' if partial else 'completed'}],
        'exports':[],'solver_signature':'older solver deliberately','owned_studies':[],'cancel_requested':False})
    return rid,sid,root,raw


@pytest.mark.parametrize('partial',[False,True])
def test_independent_reexport_publishes_generation_without_solver(tmp_path,monkeypatch,partial):
    rid,sid,root,raw=export_fixture(tmp_path,monkeypatch,partial)
    before=raw.read_bytes();task=exports.create(rid,exports.ExportRequest(request_id='one',scope='report'))
    assert exports.create(rid,exports.ExportRequest(request_id='one',scope='report'))['id']==task['id']
    def process(args,**kwargs):
        from pathlib import Path
        stage=Path(args[args.index('--output-dir')+1]);(stage/'summary.pdf').write_bytes(b'new report');(stage/'bundle.zip').write_bytes(b'package')
        store.write_json(stage/'manifest.json',{'complete':not partial})
        store.write_json(stage/'run.json',{'exports':[]});store.write_json(stage/'artifacts.json',{'report':'summary.pdf','bundle':'bundle.zip'})
        return SimpleNamespace(returncode=0,poll=lambda:0)
    monkeypatch.setattr(exports.subprocess,'Popen',process)
    exports.execute(task['id'])
    completed=store.get('export_task',task['id'])
    assert completed['status']==('partial' if partial else 'completed')
    assert raw.read_bytes()==before and (root/'summary.pdf').read_bytes()==b'previous report'
    assert exports.latest_root(runs.get(rid))!=root


def test_cancelled_export_never_publishes_or_launches(tmp_path,monkeypatch):
    rid,sid,root,raw=export_fixture(tmp_path,monkeypatch)
    task=exports.create(rid,exports.ExportRequest(request_id='cancel',scope='report'));exports.cancel(rid,task['id'])
    monkeypatch.setattr(exports.subprocess,'Popen',lambda *a,**k:pytest.fail('Cancelled export launched'))
    exports.execute(task['id'])
    assert store.get('export_task',task['id'])['status']=='cancelled'
    assert 'export_generation' not in runs.get(rid)


def test_changed_raw_during_export_is_not_published(tmp_path,monkeypatch):
    rid,sid,root,raw=export_fixture(tmp_path,monkeypatch)
    task=exports.create(rid,exports.ExportRequest(request_id='change',scope='report'))
    def process(args,**kwargs):
        from pathlib import Path
        stage=Path(args[args.index('--output-dir')+1]);(stage/'summary.pdf').write_bytes(b'new report');(stage/'bundle.zip').write_bytes(b'package')
        store.write_json(stage/'manifest.json',{'complete':True});store.write_json(stage/'run.json',{'exports':[]});store.write_json(stage/'artifacts.json',{})
        raw.write_bytes(b'changed while exporting')
        return SimpleNamespace(returncode=0,poll=lambda:0)
    monkeypatch.setattr(exports.subprocess,'Popen',process);exports.execute(task['id'])
    assert store.get('export_task',task['id'])['status']=='failed'
    assert 'export_generation' not in runs.get(rid)


def test_non_motor_transient_time_validation_requires_energy_balance(tmp_path):
    from forge.run_validation import compare
    mesh=tetra_mesh();case=AnalysisCase(name='非电机均匀热源',analysis_type='thermal_transient',material=material(),thermal={'duration_s':2,'dt_s':1,'save_s':1})
    folders=[]
    for name,dt in [('coarse',1.),('fine',.5)]:
        folder=tmp_path/name;folder.mkdir();folders.append(folder);cfg=case.model_dump();cfg['thermal']['dt_s']=dt
        store.write_json(folder/'config.json',{'case':cfg});store.write_json(folder/'summary.json',{'thermal_balance':{'passed':True}})
        with h5py.File(folder/'fields.h5','w') as f:
            f['points']=mesh['points'];f['tets']=mesh['tets'];f['times_s']=[0.,1.,2.]
            f['temperature_C']=np.repeat(np.array([[25.],[26.],[27.]]),10,axis=1)
    result=compare(*folders)
    assert result['passed'] and len(result['checks'])==2
    store.write_json(folders[1]/'summary.json',{'thermal_balance':{'passed':False}})
    assert not compare(*folders)['passed']


def test_disk_shortage_rejects_admission_without_changing_mesh_or_load(tmp_path,monkeypatch):
    case=AnalysisCase(name='资源',analysis_type='thermo_mechanical',material=material());before=case.model_dump()
    monkeypatch.setattr(resources,'available_memory',lambda:64*1024**3)
    monkeypatch.setattr(resources.shutil,'disk_usage',lambda p:SimpleNamespace(free=100*1024**2))
    with pytest.raises(ValueError,match='可用磁盘'):resources.admit({'nodes':10,'elements':1,'dofs':30},case,tmp_path)
    assert case.model_dump()==before


def test_failed_import_does_not_publish_dangling_project(tmp_path):
    from forge.imports import import_projects,ImportRequest
    source=tmp_path/'broken-legacy';source.mkdir()
    with sqlite3.connect(source/'studio.sqlite') as c:
        c.execute('CREATE TABLE entities(kind TEXT,id TEXT,data TEXT)')
        c.execute('INSERT INTO entities VALUES(?,?,?)',('project','broken',json.dumps({'id':'broken','name':'missing CAD','model_id':'absent'})))
    before={p['id'] for p in store.listing('project',10000)}
    with pytest.raises(ValueError):import_projects(ImportRequest(path=str(source),platform='strength',project_ids=['broken']))
    assert {p['id'] for p in store.listing('project',10000)}==before


@pytest.mark.parametrize('evidence',[
    {'passed':True,'raw_sha256':{}},
    {'passed':True,'raw_sha256':{'a':'a','b':'b'},'checks':[],'coarse':'a','fine':'b'},
    {'passed':True,'raw_sha256':{'a':'a'},'checks':[{'passed':True}],'coarse':'a','fine':'a'},
    {'passed':True,'raw_sha256':{'a':'a','b':'b'},'checks':[{'passed':True,'value':float('nan')}],'coarse':'a','fine':'b'},
])
def test_empty_or_nonfinite_validation_evidence_never_verified(evidence,monkeypatch):
    monkeypatch.setattr(runs,'file_hash',lambda p:p.parent.name)
    assert not runs.valid_validation(evidence)
