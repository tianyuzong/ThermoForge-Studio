import numpy as np,h5py
from forge.results import write_archive,frame_display
from forge.run_validation import compare
from strength.store import write_json
from forge.provenance import file_hash
from forge.run_exports import reusable_export,ensure_study_report


def mesh():
    p=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[.5,0,0],[.5,.5,0],[0,.5,0],[0,0,.5],[.5,0,.5],[0,.5,.5]])
    return dict(points=p,tets=np.arange(10).reshape(1,10),faces=np.array([[0,1,2,4,5,6]]),surface_ids=np.array([1]),owners=np.array([0]),body_ids=np.array([1]))


def test_wear_frames_and_legacy_final_only(tmp_path):
    m=mesh();end=np.linspace(0,1e-6,10);history=[end*0,end*.5,end]
    write_archive(tmp_path,m,[0,1,2],[],[],[],[],wear={'p':{'depth_m':end}},wear_frames=history)
    with h5py.File(tmp_path/'fields.h5') as f:assert f.attrs['result_schema_version']==4
    for i in range(3):
        d=frame_display(tmp_path,i);ids=np.array(d['node_ids'])-1
        assert np.array_equal(d['fields']['wear_depth'],history[i][ids]);assert d['time_s']==i
    with h5py.File(tmp_path/'fields.h5','a') as f:del f['wear_history'];f.attrs['result_schema_version']=3
    assert 'wear_depth' not in frame_display(tmp_path,0)['fields']
    assert frame_display(tmp_path,2)['field_temporal_kind']['wear_depth']=='final_only'


def test_time_comparison_checks_all_balances_and_gap(tmp_path):
    for name,offset in [('coarse',0.),('fine',.01)]:
        root=tmp_path/name;root.mkdir()
        with h5py.File(root/'fields.h5','w') as f:
            f['points']=mesh()['points'];f['tets']=mesh()['tets'];f['times_s']=[0.,1.]
            f['temperature_C']=np.array([[25.]*10,[35.+offset]*10]);f['displacements']=np.ones((2,10,3))*.00001
            g=f.create_group('native_contact')
            for i in range(2):g.create_group(str(i))['contact_stresses']=[[1,1,100,0,0]]
            f.create_group('clearance_probes').create_group('p')['minimum_gap_m']=[0.,1e-5]
        write_json(root/'config.json',{'case':{'analysis_type':'thermo_mechanical','contacts':[{'name':'pair'}],'clearance_probes':[{'name':'p'}],'thermal':{'initial_C':25,'duration_s':1,'dt_s':1 if name=='coarse' else .5}}})
        write_json(root/'summary.json',{'mechanical_balance':{'passed':True},'thermal_balance':{'passed':True}})
    assert compare(tmp_path/'coarse',tmp_path/'fine')['passed']
    write_json(tmp_path/'fine'/'summary.json',{'mechanical_balance':{'passed':False},'thermal_balance':{'passed':True}})
    assert not compare(tmp_path/'coarse',tmp_path/'fine')['passed']


def test_video_reuse_checks_version_integrity_and_scope(tmp_path):
    folder=tmp_path/'static';folder.mkdir()
    movie=folder/'stress.mp4';movie.write_bytes(b'encoded result')
    write_json(folder/'view.json',{'ranges':{'stress':[0,1]}})
    entry={'signature':'export-v1','status':'completed','files':[{'path':'static/stress.mp4','sha256':file_hash(movie)},
           {'path':'static/view.json','sha256':file_hash(folder/'view.json')}]}
    assert reusable_export(tmp_path,entry,'export-v1','static')
    assert not reusable_export(tmp_path,entry,'export-v2','static')
    movie.write_bytes(b'corrupt');assert not reusable_export(tmp_path,entry,'export-v1','static')
    entry['files'][0]['path']='../other/stress.mp4'
    assert not reusable_export(tmp_path,entry,'export-v1','static')


def test_damaged_report_is_regenerated_without_solving(tmp_path,monkeypatch):
    import json
    from pathlib import Path
    from forge import results
    case=json.loads((Path(__file__).parents[1]/'examples/motor-unified/static.json').read_text(encoding='utf-8'))
    write_json(tmp_path/'config.json',{'case':case});write_json(tmp_path/'summary.json',{})
    (tmp_path/'fields.h5').write_bytes(b'unchanged native fields')
    calls=[]
    def write(folder,*args):
        calls.append(folder)
        for name in ('report.pdf','report.md','metrics.csv'):(folder/name).write_text('fresh report',encoding='utf-8')
    monkeypatch.setattr(results,'write_report',write)
    ensure_study_report(tmp_path);ensure_study_report(tmp_path);assert len(calls)==1
    (tmp_path/'report.pdf').write_bytes(b'corrupt')
    ensure_study_report(tmp_path);assert len(calls)==2
    assert (tmp_path/'fields.h5').read_bytes()==b'unchanged native fields'
