import hashlib,json,sqlite3
from pathlib import Path
import numpy as np
import pytest
from strength import store
from strength.config import DATA
from forge.schemas import AnalysisCase,ContactPair
from forge.interfaces import thermal_conductances
from forge.thermal import HeatSystem,solve_history
from forge.api import StudyRequest,submit,cancel
from forge.imports import ImportRequest,import_projects
from test_forge_numerics import tetra_mesh,material


def test_heat_switches_preserve_energy_between_output_frames():
    mesh=tetra_mesh();m=material();m.density_kg_m3=6;m.specific_heat_J_kgK=100
    case=AnalysisCase(name='分段热源',analysis_type='thermal_transient',material=m,
        thermal={'initial_C':25,'duration_s':10,'dt_s':8,'save_s':10,'boundaries':[{'kind':'body_power','body_id':1,'value':100,'start_s':2,'end_s':7}]})
    times,T,audits=solve_history(HeatSystem(mesh,case,{}),case)
    assert times==[0.,10.]
    assert np.allclose(T[-1],30.,atol=1e-9)
    assert [a['time_s'] for a in audits[1:]]==[2,7,10]
    assert max(a['energy_balance_relative'] for a in audits[1:])<1e-8


def test_pressure_temperature_table_and_open_gap():
    pair=ContactPair(name='界面',slave={'kind':'faces','body_id':1,'face_ids':[1]},master={'kind':'faces','body_id':2,'face_ids':[2]},thermal=True,
        gap_conductivity_W_mK=.026,conductance=[{'pressure_Pa':p,'temperature_C':T,'conductance_W_m2K':h} for T,hs in [(0,[100,200]),(100,[300,500])] for p,h in zip([0,1000],hs)])
    assert thermal_conductances(pair,[{'gap_m':0}],[500],[50])[0]==pytest.approx(275)
    assert thermal_conductances(pair,[{'gap_m':.0001}],[0],[50])[0]==pytest.approx(260)


def test_snapshot_idempotency_and_cancel_dependency(models,monkeypatch):
    from forge import api
    monkeypatch.setattr(api.threading.Thread,'start',lambda self:None)
    geo=models['bar'];store.put('model',geo)
    pid=store.uid();store.put('project',{'id':pid,'model_id':geo['id']})
    case=AnalysisCase(name='依赖验证',analysis_type='thermal_transient',material=material(),thermal={'boundaries':[{'kind':'body_power','body_id':1,'value':0}]})
    request=StudyRequest(case=case,request_id='one')
    first=submit(pid,request);assert submit(pid,request)['id']==first['id']
    snapshot=json.loads((DATA/'studies'/first['id']/'config.json').read_text(encoding='utf-8'))
    checksum=snapshot.pop('sha256');assert checksum==hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
    case.name='变更后的草案';assert store.get('study',first['id'])['case']['name']=='依赖验证'
    case.thermal.initial_from_study=first['id'];second=submit(pid,StudyRequest(case=case,request_id='two'))
    cancel(first['id']);assert store.get('study',second['id'])['status']=='cancelled'


def test_selective_import_preserves_source_and_records_hashes(tmp_path):
    source=tmp_path/'legacy';source.mkdir();db=source/'studio.sqlite'
    original={'id':'old','name':'保留原件','model_id':'cad'}
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE entities(kind TEXT,id TEXT,data TEXT)')
        for k,r in [('project',original),('project',{'id':'untouched','name':'未选择'}),('model',{'id':'cad','source_sha256':'example'})]:conn.execute('INSERT INTO entities VALUES(?,?,?)',(k,r['id'],json.dumps(r)))
    folder=source/'models'/'cad';folder.mkdir(parents=True);(folder/'source.step').write_bytes(b'original model')
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    result=import_projects(ImportRequest(path=str(source),platform='strength',project_ids=['old']))
    assert result['originals_modified'] is False and len(result['project_ids'])==1
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before
    assert (folder/'source.step').read_bytes()==b'original model'
    manifest=json.loads((DATA/'imports'/result['import_id']/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['files'][0]['source_sha256']==manifest['files'][0]['installed_sha256']
    imported=store.get('project',result['project_ids'][0]);assert imported['model_id']!='cad'


def test_queued_named_selection_is_frozen_before_geometry_labels_change(models,monkeypatch):
    from forge import api
    from strength.geometry import resolve_selection
    monkeypatch.setattr(api.threading.Thread,'start',lambda self:None)
    geo=json.loads(json.dumps(models['bar']));geo['named_selections']['受热端']=resolve_selection({'kind':'direction','direction':'+x'},geo)
    store.put('model',geo);pid=store.uid();store.put('project',{'id':pid,'model_id':geo['id']})
    case=AnalysisCase(name='固定命名选区',analysis_type='thermal_steady',material=material(),thermal={'boundaries':[{'name':'热端','kind':'temperature','value':100,'selection':{'kind':'named','name':'受热端'}}]})
    record=submit(pid,StudyRequest(case=case,request_id='frozen-name'))
    old_faces=geo['named_selections']['受热端'][:]
    geo['named_selections']['受热端']=resolve_selection({'kind':'direction','direction':'-x'},geo);store.put('model',geo)
    snapshot=json.loads((DATA/'studies'/record['id']/'config.json').read_text(encoding='utf-8'))
    saved=snapshot['case']['thermal']['boundaries'][0]['selection']
    assert saved['kind']=='faces' and saved['name']=='受热端'
    assert resolve_selection(saved,geo)==old_faces
    assert case.thermal.boundaries[0].selection.kind=='named'


def test_explicit_motor_contact_variant_overrides_generic_example(models):
    from forge.api import template
    geo=models['bar'];store.put('model',geo);pid=store.uid()
    store.put('project',{'id':pid,'model_id':geo['id'],'example_family':'motor-unified','example_case':{'analysis_type':'thermo_mechanical','name':'generic-placeholder'}})
    assert template(pid,'thermo_mechanical')['name']=='generic-placeholder'
    contact=template(pid,'thermo_mechanical','bearing_contact')
    assert len(contact['contacts'])==2 and contact['coupling']['transient'] is False
    assert all(c['thermal'] and len(c['conductance'])>1 for c in contact['contacts'])


def test_local_parameter_units_are_converted_and_unknown_units_rejected():
    from forge.assistant import parameter
    assert parameter('初始温度 300 K','初始温度','temperature')==pytest.approx(26.85)
    assert parameter('环境温度 77°F','环境温度','temperature')==pytest.approx(25.)
    assert parameter('时长 2 小时','时长','time')==7200
    assert parameter('时间步 0.5 min','时间步','time')==30
    assert parameter('网格 6 mm','网格','length')==.006
    with pytest.raises(ValueError,match='单位'):parameter('时长 2 bananas','时长','time')


def test_concurrent_cad_imports_have_independent_model_state(models,tmp_path):
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    from strength.geometry import inspect_model
    source=DATA/'models'/models['bar']['id']
    def inspect(i):
        folder=tmp_path/str(i);folder.mkdir()
        for name in ('source.step','source.json'):shutil.copy2(source/name,folder/name)
        return inspect_model(folder)
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(inspect,range(3)))
    assert all(r['source_sha256']==models['bar']['source_sha256'] for r in results)
    assert all(len(r['surfaces'])==6 and len(r['components'])==1 for r in results)
def test_thermal_import_preserves_original_and_creates_scaled_native_geometry(models,tmp_path):
    import shutil
    source=tmp_path/'thermal';(source/'projects').mkdir(parents=True);folder=source/'models'/'original';folder.mkdir(parents=True)
    original=DATA/'models'/models['bar']['id']/'source.step';shutil.copy2(original,folder/'source.step')
    store.write_json(folder/'metadata.json',{'id':'original','source':'source.step','kind':'step','units':'m','step_scale':2})
    store.write_json(source/'projects'/'old.json',{'id':'old','name':'热模型','model_id':'original'})
    result=import_projects(ImportRequest(path=str(source),platform='thermal',project_ids=['old']))
    project=store.get('project',result['project_ids'][0]);assert project['model_id'] and not project['native_import_error']
    geo=store.get('model',project['model_id'])
    assert np.allclose(geo['dimensions_m'],np.asarray(models['bar']['dimensions_m'])*2,atol=1e-6)
    assert (folder/'source.step').read_bytes()==original.read_bytes()
    assert project['legacy_thermal_project_id']

