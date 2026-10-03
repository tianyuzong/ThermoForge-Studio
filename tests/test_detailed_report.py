import json
import numpy as np
import h5py
import pytest
from forge.reporting.data import native_statistics, selection, validation_rows


def test_native_peaks_do_not_use_smoothed_display_and_retain_time(tmp_path):
    points=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[.5,0,0],[.5,.5,0],[0,.5,0],[0,0,.5],[.5,0,.5],[0,.5,.5]])
    with h5py.File(tmp_path/'fields.h5','w') as f:
        f['points']=points;f['tets']=[np.arange(10)];f['body_ids']=[7];f['times_s']=[0,60]
        f['temperature_C']=np.array([np.full(10,25),np.arange(10)+25])
        stress=np.zeros((2,1,4,6));stress[0,0,:,0]=50e6;stress[1,0,:,0]=100e6;stress[1,0,2,0]=180e6;f['stresses']=stress
        u=np.zeros((2,10,3));u[1,8,2]=.002;f['displacements']=u
        f['native_contact/1/contact_stresses']=[[1,1,20e6,0,0],[1,1,40e6,0,0]]
        g=f.create_group('contact/1/pair');g['pressure_Pa']=[15e6,10e6];g['gap_m']=[-1e-7,2e-6]
    s=native_statistics(tmp_path)
    assert s['hotspots']['stress']['value']==pytest.approx(180)
    assert s['hotspots']['stress']['integration_point']==3
    assert s['hotspots']['stress']['time_s']==60
    assert s['hotspots']['temperature']['node']==10
    assert s['hotspots']['displacement']['value']==pytest.approx(2)
    assert s['components']['7']['stress_max_MPa']==pytest.approx(180)
    assert s['frames'][1]['native_pressure_max_MPa']==pytest.approx(40)
    assert s['contact_pairs']['pair'][0]['projected_pressure_max_MPa']==pytest.approx(15)
    assert s['frames'][0].get('native_pressure_max_MPa') is None


def test_selection_compacts_face_ranges_without_losing_identity():
    assert selection({'kind':'faces','body_id':2,'face_ids':[1,2,3,9,10,12]})=='组件 2 / 面 1-3, 9-10, 12（6 面）'


def test_balance_pass_does_not_promote_mesh_or_step_verification():
    rows=validation_rows({'thermal_balance':{'passed':True,'maximum_relative_error':1e-12},
        'mechanical_balance':{'frames':[{'force_relative':1e-5,'moment_relative':1e-6}]}},[],{})
    assert rows[0][-1]=='通过'
    assert next(r for r in rows if r[0]=='三档网格收敛')[1]=='未验证'
    assert next(r for r in rows if r[0]=='时间步减半比较')[1]=='本批次未绑定通过证据'


def test_static_report_does_not_create_fake_history_and_thermal_audit_not_coupling(tmp_path):
    from forge.reporting.figures import Figures
    f=object.__new__(Figures);f.stats={'frames':[{'time_s':0}]};f.case={'analysis_type':'thermal_steady'}
    assert f.history() is None
    assert f.convergence([{'iterations':[{'residual':1e-6}]}]) is None


def test_report_only_checks_completed_artifact_integrity_before_authoring(monkeypatch,tmp_path):
    from forge import run_exports
    root=tmp_path/'analysis-runs'/'r';root.mkdir(parents=True)
    (root/'video.mp4').write_bytes(b'corrupt')
    monkeypatch.setattr(run_exports,'DATA',tmp_path)
    monkeypatch.setattr(run_exports.store,'get',lambda *a:{'status':'completed','exports':[{'status':'completed','files':[{'path':'video.mp4','sha256':'different'}]}]})
    monkeypatch.setattr(run_exports,'report',lambda *a:pytest.fail('must not author using corrupted exports'))
    with pytest.raises(ValueError,match='损坏'):run_exports.main('r',report_only=True)
