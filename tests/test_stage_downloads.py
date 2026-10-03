"""A live HDF5 journal must never be served while its worker can append."""
import pytest
from fastapi import HTTPException
from strength import store
from forge import api,exports,runs


def test_checkpoint_download_waits_until_worker_stops(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DATA',tmp_path)
    sid=store.uid();store.put('study',{'id':sid,'status':'running'})
    folder=tmp_path/'studies'/sid/'thermal';folder.mkdir(parents=True)
    (folder/'physical-steps.h5').write_bytes(b'committed journal')
    store.write_json(folder/'checkpoint.json',{'last_committed_index':2,'last_converged_time_s':60})
    assert api.checkpoints(sid)[0]['download_url'] is None
    with pytest.raises(HTTPException) as exc:api.checkpoint_file(sid,'thermal')
    assert exc.value.status_code==409
    store.update('study',sid,status='interrupted')
    assert api.checkpoints(sid)[0]['download_url'].endswith('/thermal')
    assert api.checkpoint_file(sid,'thermal').path==folder/'physical-steps.h5'


def test_export_log_is_scoped_to_its_run(tmp_path,monkeypatch):
    monkeypatch.setattr(exports,'DATA',tmp_path)
    tid=store.uid();store.put('export_task',{'id':tid,'run_id':'owned','status':'failed'})
    folder=tmp_path/'exports'/tid[:16];folder.mkdir(parents=True)
    (folder/'export.log').write_text('diagnosis',encoding='utf-8')
    assert exports.export_log('owned',tid).path==folder/'export.log'
    with pytest.raises(HTTPException) as exc:exports.export_log('another-run',tid)
    assert exc.value.status_code==404


def test_run_history_retains_export_progress_after_refresh():
    rid=store.uid();tid=store.uid();pid=store.uid()
    store.put('export_task',{'id':tid,'run_id':rid,'status':'running','scope':'video'})
    store.put('analysis_run',{'id':rid,'origin_project_id':pid,'status':'completed','latest_export_id':tid})
    assert runs.listing(project_id=pid)[0]['latest_export']==runs.read(rid)['latest_export']
    assert runs.listing(project_id=pid)[0]['latest_export']['status']=='running'
