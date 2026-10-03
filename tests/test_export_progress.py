from pathlib import Path
import pytest
from strength import store
from forge import exports,run_exports


def task():
    tid=store.uid();rid=store.uid()
    store.put('export_task',{'id':tid,'run_id':rid,'status':'running','detail':'original','cancel_requested':False})
    return tid,{'id':rid}


def test_progress_is_task_scoped_monotonic_and_visible(tmp_path,monkeypatch):
    tid,run=task();monkeypatch.setenv('STUDIO_EXPORT_TASK_ID',tid)
    node={'key':'thermal','label':'瞬态热分析'}
    run_exports.write_progress(tmp_path,run,'capture','瞬态热分析 · 温度：截图 3/11',node,field='temperature',frame=3,frame_total=11)
    exports.sync_progress(tid,tmp_path);saved=store.get('export_task',tid)
    assert saved['detail']=='瞬态热分析 · 温度：截图 3/11'
    assert saved['phase']=='capture' and saved['progress']['frame']==3
    assert saved['progress']['node_label']=='瞬态热分析'
    exports.sync_progress(tid,tmp_path)
    assert store.get('export_task',tid)['revision']==saved['revision']
    record=run_exports.write_progress(tmp_path,run,'zip','打包文件 2/20',item=2,item_total=20)
    record['export_task_id']='another-task';store.write_json(tmp_path/'progress.json',record)
    exports.sync_progress(tid,tmp_path)
    assert store.get('export_task',tid)==saved


@pytest.mark.parametrize('status',['cancelled','completed','interrupted'])
def test_stale_progress_and_failure_cannot_revive_terminal_task(tmp_path,monkeypatch,status):
    tid,run=task();monkeypatch.setenv('STUDIO_EXPORT_TASK_ID',tid)
    run_exports.write_progress(tmp_path,run,'capture','late progress',field='stress',frame=9,frame_total=10)
    store.update('export_task',tid,status=status,cancel_requested=status=='cancelled',detail='terminal detail')
    before=store.get('export_task',tid)
    exports.sync_progress(tid,tmp_path);exports.update_active(tid,status='failed',detail='late failure')
    assert store.get('export_task',tid)==before


def test_cancel_during_progress_read_is_checked_inside_transaction(tmp_path,monkeypatch):
    tid,run=task();monkeypatch.setenv('STUDIO_EXPORT_TASK_ID',tid)
    run_exports.write_progress(tmp_path,run,'zip','stale publication progress')
    original=store.mutate
    def race(kind,id,transform,**kwargs):
        original(kind,id,lambda value:{**value,'status':'cancelled','cancel_requested':True,'detail':'cancelled by user'})
        return original(kind,id,transform,**kwargs)
    monkeypatch.setattr(store,'mutate',race)
    exports.sync_progress(tid,tmp_path)
    result=store.get('export_task',tid)
    assert result['status']=='cancelled' and result['detail']=='cancelled by user' and 'progress' not in result


@pytest.mark.parametrize('name',['frontend/dist/index.html','frontend/dist/assets/main.js','frontend/src/resultClient.ts',
    'forge/results.py','forge/result_index.py'])
def test_renderer_cache_tracks_served_build_and_field_reader(tmp_path,name):
    path=tmp_path/name;path.parent.mkdir(parents=True);path.write_text('before',encoding='utf-8')
    before=run_exports.renderer_fingerprint(tmp_path)
    path.write_text('after',encoding='utf-8')
    assert run_exports.renderer_fingerprint(tmp_path)!=before


def test_export_version_tracks_report_inputs_and_renderer(tmp_path,monkeypatch):
    monkeypatch.setattr(exports,'DATA',tmp_path);monkeypatch.setattr(exports,'ROOT',tmp_path)
    monkeypatch.setattr(run_exports,'renderer_fingerprint',lambda:'first renderer')
    source=tmp_path/'studies'/'s';source.mkdir(parents=True)
    store.write_json(source/'summary.json',{'temperature_max_C':32})
    run={'title':'saved result','nodes':[{'study_id':'s'}]};raw={'s':'raw hash'}
    first=exports.export_version(run,raw)
    assert exports.export_version({**run,'prompt':'将导热杆加热到平衡'},raw)!=first
    store.write_json(source/'summary.json',{'temperature_max_C':33})
    second=exports.export_version(run,raw);assert second!=first
    monkeypatch.setattr(run_exports,'renderer_fingerprint',lambda:'changed renderer')
    assert exports.export_version(run,raw)!=second
    renderer_version=exports.export_version(run,raw)
    path=tmp_path/'forge'/'reporting'/'document.py';path.parent.mkdir(parents=True);path.write_text('report changed',encoding='utf-8')
    assert exports.export_version(run,raw)!=renderer_version


def test_background_export_consumes_child_progress_without_starting_solver(tmp_path,monkeypatch):
    from test_workspace_workflow import export_fixture
    rid,_,_,_=export_fixture(tmp_path,monkeypatch)
    requested=exports.create(rid,exports.ExportRequest(request_id='progress',scope='report'))
    observed=[]
    class Child:
        returncode=0
        def __init__(self,args,**kwargs):
            self.stage=Path(args[args.index('--output-dir')+1]);self.calls=0
            assert kwargs['env']['STUDIO_EXPORT_TASK_ID']==requested['id']
            (self.stage/'summary.pdf').write_bytes(b'new report');(self.stage/'bundle.zip').write_bytes(b'package')
            store.write_json(self.stage/'manifest.json',{'complete':True})
            store.write_json(self.stage/'run.json',{'exports':[]});store.write_json(self.stage/'artifacts.json',{})
        def poll(self):
            self.calls+=1
            if self.calls==1:
                store.write_json(self.stage/'progress.json',{'schema_version':1,'export_task_id':requested['id'],
                    'run_id':rid,'sequence':1,'stage':'study_report','detail':'正在生成导热杆报告'})
                return None
            return 0
    monkeypatch.setattr(exports.subprocess,'Popen',Child)
    monkeypatch.setattr(exports.time,'sleep',lambda seconds:observed.append(store.get('export_task',requested['id'])['detail']))
    exports.execute(requested['id'])
    assert observed==['正在生成导热杆报告']
    assert store.get('export_task',requested['id'])['status']=='completed'
