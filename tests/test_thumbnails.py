import hashlib,io,json
import numpy as np,pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from strength import store
from forge import thumbnails


@pytest.fixture
def thumbnail_client(tmp_path,monkeypatch):
    monkeypatch.setattr(thumbnails,'DATA',tmp_path)
    mid=store.uid();pid=store.uid();folder=tmp_path/'models'/mid;folder.mkdir(parents=True)
    source=folder/'source.step';source.write_bytes(b'original CAD fixture')
    store.write_json(folder/'source.json',{'file':'source.step'})
    display={'points':[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],'triangles':[[0,2,1],[0,1,3],[1,2,3],[2,0,3]],'surface_ids':[1,2,3,4]}
    store.write_json(folder/'display.json',display)
    store.put('model',{'id':mid,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    store.put('project',{'id':pid,'model_id':mid})
    app=FastAPI();app.include_router(thumbnails.router)
    with TestClient(app) as client:yield client,pid,folder,display


def test_thumbnail_is_actual_mesh_png_with_bounded_projection_and_no_external_refs(thumbnail_client):
    client,pid,folder,display=thumbnail_client
    xy,_,_=thumbnails.project_mesh(display['points'])
    assert np.all(xy.min(axis=0)>=9.99) and np.all(xy.max(axis=0)<=np.array([192,128])-9.99)
    response=client.get(f'/api/v1/projects/{pid}/thumbnail')
    assert response.status_code==200 and response.headers['content-type']=='image/png'
    image=Image.open(io.BytesIO(response.content));assert image.size==(192,128)
    assert image.info['DisplaySHA256']==hashlib.sha256((folder/'display.json').read_bytes()).hexdigest()
    assert image.info['SourceCADSHA256']==hashlib.sha256((folder/'source.step').read_bytes()).hexdigest()
    values=np.asarray(image);background=np.array([240,246,247]);foreground=np.any(values!=background,axis=-1)
    y,x=np.where(foreground);assert x.max()-x.min()>50 and y.max()-y.min()>50
    assert b'<svg' not in response.content and b'href=' not in response.content and b'http://' not in response.content
    cached=client.get(f'/api/v1/projects/{pid}/thumbnail',headers={'If-None-Match':response.headers['etag']})
    assert cached.status_code==304 and not cached.content


def test_thumbnail_cache_identity_changes_with_geometry_and_rejects_changed_source(thumbnail_client):
    client,pid,folder,display=thumbnail_client;url=f'/api/v1/projects/{pid}/thumbnail'
    first=client.get(url);display['points'][1]=[3,0,0];store.write_json(folder/'display.json',display)
    changed=client.get(url,headers={'If-None-Match':first.headers['etag']})
    assert changed.status_code==200 and changed.headers['etag']!=first.headers['etag'] and changed.content!=first.content
    (folder/'source.step').write_bytes(b'unimported changed source')
    assert client.get(url).status_code==404


def test_thumbnail_missing_or_invalid_geometry_has_explicit_404(thumbnail_client):
    client,pid,folder,display=thumbnail_client;url=f'/api/v1/projects/{pid}/thumbnail'
    display['triangles'][0][0]=999;store.write_json(folder/'display.json',display)
    assert client.get(url).status_code==404
    (folder/'display.json').unlink()
    assert client.get(url).status_code==404
    assert client.get('/api/v1/projects/missing/thumbnail').status_code==404
