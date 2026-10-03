from pathlib import Path
import sys,time,json,zipfile,io
import numpy as np
app=Path(__file__).resolve().parents[1];work=app/'.runtime';sys.path.insert(0,str(app))
from thermal_legacy.runtime import *
from thermal_legacy.schemas import Simulation
import gmsh,trimesh,httpx
fixtures=work/'platform-fixtures';fixtures.mkdir(exist_ok=True)
trimesh.creation.box(extents=[20,20,20]).export(fixtures/'validation-cube.stl')
gmsh.initialize();gmsh.option.setNumber('General.Terminal',0);gmsh.model.add('test');gmsh.model.occ.addBox(0,0,0,20,20,20);gmsh.model.occ.synchronize();gmsh.write(str(fixtures/'validation-cube.step'));gmsh.finalize()
client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=60,trust_env=False)
def api(method,path,**kw):
    r=client.request(method,path,**kw)
    if r.is_error:raise RuntimeError((r.status_code,r.text))
    return r.json()
def wait_model(id):
    for _ in range(180):
        m=api('GET','/api/models/'+id)
        if m['status']['phase']=='failed':raise RuntimeError(m)
        if m['state']=='ready':return m
        time.sleep(.5)
    raise RuntimeError('Model import timed out')
def wait_job(id):
    for _ in range(240):
        s=api('GET','/api/jobs/'+id)
        if s['phase']=='failed':raise RuntimeError((id,s))
        if s['phase']=='completed':return api('GET','/api/jobs/'+id+'/result')
        time.sleep(.5)
    raise RuntimeError('Job timed out')
results=[]
for ext in ('stl','step'):
    path=fixtures/('validation-cube.'+ext)
    with path.open('rb') as f:m=api('POST','/api/models',files={'file':(path.name,f)},data={'units':'mm','scale_factor':'1'})
    m=wait_model(m['id']);print('IMPORTED',ext,m['dimensions_m'],flush=True)
    np.testing.assert_allclose(m['dimensions_m'],[.02,.02,.02],rtol=.005)
    d=api('GET','/api/models/'+m['id']+'/display');points=np.array(d['points']).reshape(-1,3);faces=np.array(d['faces']).reshape(-1,3);centers=points[faces].mean(1)
    lo=np.array(m['bounds_m'][0]);hi=np.array(m['bounds_m'][1]);selected=np.where(np.abs(centers[:,0]-lo[0])<1e-7)[0].tolist();cooled=np.where(np.abs(centers[:,0]-hi[0])<1e-7)[0].tolist()
    assert selected and cooled
    cfg=dict(model_id=m['id'],name='验证 · '+ext.upper()+' 铜铝双材料',base_material=dict(name='Copper',k=391,rho=8910,cp=385),regions=[dict(name='Aluminium half',min_m=(lo-.001).tolist(),max_m=[(lo[0]+hi[0])/2,hi[1]+.001,hi[2]+.001],material=dict(name='Aluminium',k=205,rho=2700,cp=900))],heat_sources=[dict(name='Heater 2W',power_W=2,start_s=2,end_s=18,faces=selected)],cooling=[dict(name='Far side',h=50,ambient_C=25,faces=cooled)],initial_C=25,ambient_C=25,default_h=0,heat_convection=False,duration_s=20,dt_s=1,save_s=5,mesh_size_m=.004)
    saved=api('POST','/api/projects',json=cfg);assert api('GET','/api/projects/'+saved['id'])['config']==Simulation(**cfg).model_dump(mode='json')
    job=api('POST','/api/jobs',json=cfg);r=wait_job(job['id'])
    assert r['frames']==5 and r['stats'][-1]['maximum_C']>25
    assert abs(r['summary']['energy']['input_energy_J']-32)<1e-7
    assert abs(r['summary']['energy']['energy_balance_error_J'])<1e-6
    fractions=[x['volume_m3']/r['summary']['volume_m3'] for x in r['summary']['materials']]
    if ext=='step':np.testing.assert_allclose(fractions,[.5,.5],atol=.005)
    else:assert all(.35<x<.65 for x in fractions)
    sl=api('GET',f'/api/jobs/{job["id"]}/slice',params={'axis':0,'value':(lo[0]+hi[0])/2});assert sl['triangles']>0
    archive=client.get(f'/api/jobs/{job["id"]}/files/result.zip');assert archive.status_code==200
    z=zipfile.ZipFile(io.BytesIO(archive.content));assert {'temperature.xdmf','thermal-fields.h5','history.csv','config.json','audit.json'}<=set(z.namelist())
    # Bounds and finite numeric inputs are rejected before launching a worker.
    bad={**cfg,'heat_sources':[{**cfg['heat_sources'][0],'faces':[m['triangles']+1]}]}
    assert client.post('/api/jobs',json=bad).status_code==422
    assert client.post('/api/projects',json=cfg,headers={'Origin':'https://unrelated.example'}).status_code==403
    results.append(dict(format=ext,model_id=m['id'],job_id=job['id'],final=r['stats'][-1],material_volume_fractions=fractions,nodes=r['summary']['nodes'],tetrahedra=r['summary']['tetrahedra']))
    print('PASSED',json.dumps(results[-1]),flush=True)
(app/'validation-api.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print('ALL API + GEOMETRY + MULTIMATERIAL + EXPORT CHECKS PASSED',flush=True)
