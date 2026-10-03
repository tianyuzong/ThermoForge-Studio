import base64,gzip,json
import numpy as np
import pytest
import thermal_legacy.thermal_animation as animation
from thermal_legacy.runtime import read_json, write_json
from thermal_legacy.schemas import Simulation


@pytest.fixture
def job(tmp_path,monkeypatch):
    jobs=tmp_path/'jobs';jobs.mkdir();models=tmp_path/'models';models.mkdir()
    p=jobs/'sample';p.mkdir()
    monkeypatch.setattr(animation,'JOBS',jobs);monkeypatch.setattr(animation,'MODELS',models)
    cfg=Simulation(model_id='test',name='无热源环境循环',environment_only=True,initial_C=20,ambient_C=20,
                   duration_s=9,dt_s=1,save_s=4,ambient_profile=[{'time_s':0,'ambient_C':20},{'time_s':9,'ambient_C':-40}]).model_dump()
    write_json(p/'config.json',cfg);write_json(p/'status.json',{'phase':'completed'})
    points=np.array([[0.,0,0],[1,0,0],[0,1,0],[0,0,1]])*.01
    faces=np.array([[0,1,2],[0,1,3],[1,2,3],[0,2,3]])
    np.savez(p/'mesh.npz',points=points,boundary_triangles=faces,tets=np.array([[0,1,2,3]]))
    fields=np.array([[20,20,20,20],[0,1,2,3],[-10,-8,-7,-6]],dtype=np.float32)
    np.save(p/'temperatures.npy',fields)
    stats=[dict(time_s=t,minimum_C=float(row.min()),maximum_C=float(row.max()),average_C=float(row.mean())) for t,row in zip([0,4,9],fields)]
    write_json(p/'result.json',{'times_s':[0,4,9],'stats':stats,'summary':{}})
    return p


def test_preview_keeps_original_nodes_and_handles_environment_without_contact(job):
    before=(job/'config.json').read_bytes();cfg,meta,p,f,t=animation.load_fields(job)
    np.testing.assert_array_equal(t,np.load(job/'temperatures.npy'))
    assert meta['contact'] is None and meta['times']==[0,4,9]
    assert meta['temperature_limits']==[-10.,20.]
    path=animation.prepare_preview(job);text=path.read_text(encoding='utf8')
    assert '无热源，按给定环境条件' in text and '环境温度按给定曲线变化' in text
    assert '150 W' not in text and '停风后' not in text
    assert before==(job/'config.json').read_bytes()
    mtime=path.stat().st_mtime_ns
    assert animation.prepare_preview(job).stat().st_mtime_ns==mtime


def test_preview_embeds_lossless_float32_and_escapes_script_text(job):
    cfg=read_json(job/'config.json');cfg['name']='</script><script>alert(1)</script>';write_json(job/'config.json',cfg)
    text=animation.prepare_preview(job).read_text(encoding='utf8')
    assert cfg['name'] not in text and '\\u003c/script>' in text
    packed=json.loads(text.split(',packed=',1)[1].split(';\n',1)[0])
    actual=np.frombuffer(gzip.decompress(base64.b64decode(packed['temperatures'])),dtype='<f4').reshape(3,4)
    np.testing.assert_array_equal(actual,np.load(job/'temperatures.npy'))


@pytest.mark.parametrize('change',['steady','pending','time_order','frame_count','nonfinite','contact_times'])
def test_invalid_or_nontransient_results_are_rejected(job,change):
    if change=='steady':
        cfg=read_json(job/'config.json');cfg['analysis_mode']='steady';write_json(job/'config.json',cfg)
    elif change=='pending':write_json(job/'status.json',{'phase':'running'})
    elif change in ('time_order','contact_times'):
        data=read_json(job/'result.json')
        if change=='time_order':data['times_s']=[0,9,4]
        else:data['summary']['surface_evaluation']={'rows':[dict(time_s=0)]}
        write_json(job/'result.json',data)
    else:
        values=np.load(job/'temperatures.npy')
        if change=='frame_count':values=values[:2]
        else:values[0,0]=np.nan
        np.save(job/'temperatures.npy',values)
    with pytest.raises(ValueError):animation.prepare_preview(job)


def test_nonuniform_export_preserves_relative_time_and_final_hold():
    d=animation.frame_durations([0,240,480,600])
    assert d==pytest.approx([6,6,3,2])
    assert sum(d)==pytest.approx(17)


def test_changed_result_invalidates_cached_video(job):
    path=animation.prepare_preview(job);dest=path.parent
    write_json(dest/'export.json',dict(phase='completed',signature=animation.fingerprint(job)))
    (dest/'animation.mp4').write_bytes(b'video')
    assert animation.export_status(job)['phase']=='completed'
    cfg=read_json(job/'config.json');cfg['name']='Updated';write_json(job/'config.json',cfg)
    assert animation.export_status(job)['phase']=='idle'


def test_export_submission_is_deduplicated(job,monkeypatch):
    calls=[]
    class Process:
        pid=1234
    monkeypatch.setattr(animation.subprocess,'Popen',lambda *a,**kw:(calls.append((a,kw)) or Process()))
    monkeypatch.setattr(animation,'process_alive',lambda pid:pid==1234)
    a=animation.start_export(job);b=animation.start_export(job)
    assert a['pid']==b['pid']==1234 and len(calls)==1
    assert calls[0][1]['cwd']==animation.ROOT


def test_steady_preview_endpoint_returns_clear_error(job,monkeypatch):
    import thermal_legacy.server as server
    from fastapi import HTTPException
    cfg=read_json(job/'config.json');cfg['analysis_mode']='steady';write_json(job/'config.json',cfg)
    monkeypatch.setattr(server,'JOBS',job.parent)
    with pytest.raises(HTTPException) as error:server.animation_manifest(job.name)
    assert error.value.status_code==422 and '稳态' in error.value.detail
