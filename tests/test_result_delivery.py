import json
import time
import h5py
import numpy as np
import pytest
from forge import results
from forge.reporting.data import native_statistics


def mesh():
    points=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[.5,0,0],[.5,.5,0],[0,.5,0],[0,0,.5],[.5,0,.5],[0,.5,.5]])
    return {'points':points,'tets':np.arange(10).reshape(1,10),'faces':np.array([[0,1,2,4,5,6]]),'surface_ids':np.array([1]),'owners':np.array([0]),'body_ids':np.array([7])}


def thermal(folder):
    results.write_archive(folder,mesh(),[0,5,20],[np.full(10,25.),np.arange(10)+25.,np.arange(10)+35.],[],
        [np.ones((1,4,3))*i for i in (0,1,2)],[np.ones((1,4,3))*i for i in (0,2,4)])


def test_topology_once_and_filtered_frame_match_legacy(tmp_path):
    thermal(tmp_path)
    topology=results.surface_topology(tmp_path);frame=results.frame_values(tmp_path,1,'temperature')
    assert not {'points','triangles','surface_ids','node_ids'} & frame.keys()
    assert topology['topology_id']==frame['topology_id']
    assert set(frame['fields'])=={'temperature'}
    old=results.frame_display(tmp_path,1)
    assert frame['fields']['temperature']==old['fields']['temperature']
    assert old['points']==topology['points']
    assert frame['time_s']==5
    with pytest.raises(ValueError,match='未知'):results.frame_values(tmp_path,1,'invented')


def test_cached_native_index_never_projects_full_frame_and_detects_changed_raw(tmp_path,monkeypatch):
    from forge import result_index
    thermal(tmp_path)
    result_index._index.cache_clear()
    monkeypatch.setattr(results,'frame_display',lambda *a:pytest.fail('metadata cannot project complete display'))
    value=results.result_index(tmp_path)
    assert value['ranges']['temperature']==[25,44]
    assert value['hotspots']['temperature']['node_id']==10
    assert value['hotspots']['temperature']['body_id']==7
    history=next(h for h in value['histories'] if h['field']=='temperature')
    assert history['times_s']==[0,5,20] and history['maximum']==[25,34,44]
    durations=[]
    for _ in range(10):
        t=time.perf_counter();assert results.result_index(tmp_path)==value;durations.append(time.perf_counter()-t)
    assert np.percentile(durations,95)<.5
    with h5py.File(tmp_path/'fields.h5','a') as archive:archive['temperature_C'][2,9]=51
    updated=results.result_index(tmp_path)
    assert updated['raw_sha256']!=value['raw_sha256'] and updated['ranges']['temperature'][1]==51


def test_corrupt_derived_index_is_rebuilt_from_raw(tmp_path):
    from forge import result_index
    thermal(tmp_path);path=tmp_path/'result-index.json';saved=json.loads(path.read_text(encoding='utf-8'))
    saved['ranges']['temperature']=[-999,999];path.write_text(json.dumps(saved),encoding='utf-8');result_index._index.cache_clear()
    assert results.result_index(tmp_path)['ranges']['temperature']==[25,44]


def test_empty_contact_remains_no_data_in_report_index_and_frame(tmp_path):
    thermal(tmp_path)
    with h5py.File(tmp_path/'fields.h5','a') as archive:
        pair=archive.create_group('contact/2/open')
        pair['pressure_Pa']=[];pair['gap_m']=[];pair['slave_face']=np.array([],int)
    stats=native_statistics(tmp_path);contact=stats['contact_pairs']['open'][0]
    assert contact['sample_status']=='no_mapped_samples'
    assert contact['projected_pressure_max_MPa'] is None and contact['gap_min_um'] is None
    assert 'contact_pressure' not in results.result_index(tmp_path)['ranges']
    assert all(v is None for v in results.frame_values(tmp_path,2,'contact_pressure')['fields']['contact_pressure'])


def test_old_wear_is_final_only_not_synthetic_history(tmp_path):
    results.write_archive(tmp_path,mesh(),[0,3,8],[],[],[],[],wear={'p':{'depth_m':np.ones(10)*1e-6}})
    index=results.result_index(tmp_path);wear=next(h for h in index['histories'] if h['field']=='wear_depth')
    assert wear['times_s']==[8] and wear['maximum']==[1e-6]
    assert index['field_temporal_kind']['wear_depth']=='final_only'
    assert 'wear_depth' not in results.frame_values(tmp_path,0)['fields']
    assert 'wear_final_only_um' not in native_statistics(tmp_path)['frames'][0]


def test_critical_life_and_gap_use_minimum_not_largest_value(tmp_path):
    fatigue={'life_cycles':np.array([[1000,500,100,900]]),'damage':np.array([[.1,.2,1,.11]]),'evaluated':np.ones((1,4),bool),'life_definition':'initiation'}
    contacts=[{'p':{'pressure_Pa':np.array([10.,0.]),'gap_m':np.array([-1e-6,2e-5]),'slave_face':np.array([0,0])}}]
    results.write_archive(tmp_path,mesh(),[0],[],[],[],[],fatigue=fatigue,contacts=contacts)
    hot=results.result_index(tmp_path)['hotspots']
    assert hot['fatigue_life']['value']==100 and hot['fatigue_life']['integration_point']==3
    assert hot['fatigue_life']['extremum']=='minimum' and hot['contact_gap']['value']==-1e-6


def test_archive_consumes_lazy_series_one_frame_at_a_time(tmp_path):
    class Lazy:
        def __len__(self):return 3
        def __getitem__(self,index):
            if index>=3:raise IndexError(index)
            return np.full(10,25+index,dtype=float)
        def __array__(self,*a,**kw):pytest.fail('must never materialize the whole result sequence')
    class Vectors(Lazy):
        def __getitem__(self,index):return np.zeros((1,4,3))
    results.write_archive(tmp_path,mesh(),[0,1,2],Lazy(),[],Vectors(),Vectors())
    with h5py.File(tmp_path/'fields.h5') as archive:assert archive['temperature_C'][2,0]==27


def test_detailed_report_load_skips_damaged_study_and_retains_diagnostic(tmp_path,monkeypatch):
    from forge.reporting.document import Report
    source=tmp_path/'bad';source.mkdir();(source/'fields.h5').write_bytes(b'invalid hdf5');(source/'summary.json').write_text('{}')
    (source/'config.json').write_text(json.dumps({'case':{},'model_id':'m'}))
    run={'id':'r','suite_version':'test','nodes':[{'key':'bad','study_id':'bad','label':'损坏研究','status':'completed'}]}
    report=Report(run,tmp_path,sources={'bad':source});report.load()
    assert not report.loaded and report.errors[0]['study_id']=='bad'
    assert report.errors[0]['stage']=='read_saved_result'


def test_detached_report_export_never_runs_solver_or_mutates_run(tmp_path,monkeypatch):
    from forge import run_exports, exports
    original=tmp_path/'analysis-runs'/'r';original.mkdir(parents=True);stage=original/'export-generations'/'test';stage.mkdir(parents=True)
    source=tmp_path/'studies'/'s';source.mkdir(parents=True);(source/'fields.h5').write_bytes(b'immutable native result')
    (source/'summary.json').write_text('{}');(source/'config.json').write_text('{}')
    model=tmp_path/'models'/'m';model.mkdir(parents=True);(model/'source.json').write_text('{"file":"source.step"}');(model/'geometry.json').write_text('{}');(model/'source.step').write_text('cad')
    run={'id':'r','status':'completed','suite_version':'test','owned_studies':[],'exports':[],
         'nodes':[{'key':'thermal','study_id':'s','model_id':'m','label':'热场','status':'completed','case':{'analysis_type':'thermal_steady'}}]}
    monkeypatch.setattr(run_exports,'DATA',tmp_path);monkeypatch.setattr(exports,'latest_root',lambda value:original)
    monkeypatch.setattr(run_exports.store,'get',lambda kind,id:run if kind=='analysis_run' else {'status':'completed','summary':{'maximum_temperature_C':40}})
    monkeypatch.setattr(run_exports.store,'update',lambda *a,**kw:pytest.fail('staged export must not mutate original run'))
    monkeypatch.setattr(run_exports,'render_node',lambda *a:pytest.fail('report-only cannot render video or solve'))
    def study_report(source,destination):
        destination.mkdir(parents=True)
        for name in ('report.pdf','report.md','metrics.csv'):(destination/name).write_bytes(b'derived')
    monkeypatch.setattr(run_exports,'ensure_study_report',study_report)
    def report(run,root):
        (root/'summary.pdf').write_bytes(b'report');return {'complete':True,'diagnostics':[]}
    monkeypatch.setattr(run_exports,'report',report)
    result=run_exports.main('r',scope='report',output_root=stage)
    assert result['complete'] and (stage/'bundle.zip').is_file()
    assert not (original/'summary.pdf').exists()
    assert (source/'fields.h5').read_bytes()==b'immutable native result'
    import zipfile
    with zipfile.ZipFile(stage/'bundle.zip') as archive:
        assert archive.read('studies/thermal/fields.h5')==b'immutable native result'
        assert archive.read('studies/thermal/report.pdf')==b'derived'


def test_video_uses_actual_time_and_never_fakes_final_wear_history():
    from forge.run_exports import capture_samples
    transient=capture_samples([0,3,15])
    assert transient['frames']==[0,1,2] and transient['durations_s']==[3,12,2]
    assert transient['angles']==[None,None,None]
    final=capture_samples([0,3,15],'final_only')
    assert set(final['frames'])=={2} and final['mode']=='assessment_rotation_non_transient'
    assert all(a is not None for a in final['angles'])
    assert capture_samples([0,3,15],'final_only',videos=False)['frames']==[2]
    with pytest.raises(ValueError,match='时间轴'):capture_samples([1,0])


def test_support_reaction_cache_survives_restart_and_invalidates_every_source(tmp_path,monkeypatch):
    from forge import run_exports
    from forge.integrity import file_digest
    source=tmp_path/'studies'/'s';source.mkdir(parents=True)
    config={'case':{'analysis_type':'static'},'model_id':'m'}
    (source/'config.json').write_text(json.dumps(config))
    (source/'fields.h5').write_bytes(b'native fields');(source/'mesh.npz').write_bytes(b'saved mesh')
    geometry=tmp_path/'models'/'m'/'geometry.json';geometry.parent.mkdir(parents=True);geometry.write_text('{}')
    for name in run_exports.REACTION_POSTPROCESS_FILES:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('postprocessing code')
    monkeypatch.setattr(run_exports,'ROOT',tmp_path);monkeypatch.setattr(run_exports,'DATA',tmp_path)
    calls=[]
    def recover(folder):
        calls.append(folder)
        return {'source_fields_sha256':file_digest(folder/'fields.h5'),'total_N':[0,0,float(len(calls))],'groups':[],'applied_total_N':[0,0,0]}
    monkeypatch.setattr(run_exports,'_compute_static_support_reactions',recover)
    first=run_exports.static_support_reactions(source)
    assert len(calls)==1 and (source/'support-reactions.json').is_file()
    run_exports._static_support_reactions.cache_clear()
    assert run_exports.static_support_reactions(source)==first and len(calls)==1
    for path in [source/'fields.h5',source/'mesh.npz',geometry,tmp_path/run_exports.REACTION_POSTPROCESS_FILES[-1]]:
        path.write_bytes(path.read_bytes()+b' changed')
        previous=len(calls);run_exports.static_support_reactions(source);assert len(calls)==previous+1
    config['case']['name']='new boundary snapshot';(source/'config.json').write_text(json.dumps(config))
    previous=len(calls);run_exports.static_support_reactions(source);assert len(calls)==previous+1
    # Corrupted derived numbers are never trusted just because their source signature matches.
    cache=source/'support-reactions.json';saved=json.loads(cache.read_text());saved['result']['total_N']=[999,999,999];cache.write_text(json.dumps(saved))
    run_exports._static_support_reactions.cache_clear()
    previous=len(calls);assert run_exports.static_support_reactions(source)['total_N']!=[999,999,999];assert len(calls)==previous+1
