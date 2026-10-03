import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
import numpy as np
import trimesh
from thermal_legacy.schemas import Simulation

parser=argparse.ArgumentParser(description='Opt-in comparison of Thermal Studio configuration parsers; no simulation jobs are submitted.')
parser.add_argument('mode',choices=['baseline','skill','local'])
parser.add_argument('--cases',default='simple,negation,component,point')
parser.add_argument('--label')
parser.add_argument('--live',action='store_true',help='Allow actual Codex calls using existing authentication')
parser.add_argument('--model',help='Optional explicit Codex CLI model for comparable runs')
parser.add_argument('--output',type=Path,default=REPO/'.runtime/agent-benchmark')
parser.add_argument('--baseline-agent',type=Path,help='An earlier agent.py snapshot for baseline mode')
args=parser.parse_args()
if args.mode != 'local' and not args.live:
    parser.error('Pass --live to authorize real Codex calls')
if args.mode == 'baseline' and not args.baseline_agent:
    parser.error('baseline mode requires --baseline-agent')
WORK=args.output.resolve();WORK.mkdir(parents=True,exist_ok=True)
if args.model:
    os.environ['THERMAL_CODEX_MODEL']=args.model
os.environ['THERMAL_CODEX_PROVIDER']='cli'
if args.mode=='baseline':
    spec=importlib.util.spec_from_file_location('baseline_agent',args.baseline_agent.resolve())
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
else:
    import thermal_legacy.agent as engine

models=WORK/'skill-evaluation-models'
folder=models/'two-blocks';folder.mkdir(parents=True,exist_ok=True)
a=trimesh.creation.box(extents=[.02,.02,.02]);a.apply_translation([-.02,0,0])
b=trimesh.creation.box(extents=[.02,.02,.02]);b.apply_translation([.02,0,0])
mesh=trimesh.util.concatenate([a,b])
display={'points':mesh.vertices.ravel().tolist(),'faces':mesh.faces.ravel().tolist(),'is_outer':[1]*len(mesh.faces)}
meta={'id':'two-blocks','name':'two-blocks.stl','kind':'stl','state':'ready','triangles':len(mesh.faces),
      'dimensions_m':mesh.extents.tolist(),'bounds_m':mesh.bounds.tolist(),
      'components':[{'component_id':i,'name':f'Block {i+1}','bounds_m':x.bounds.tolist(),'triangles':len(x.faces)} for i,x in enumerate([a,b])]}
for name,value in [('display.json',display),('metadata.json',meta)]:
    (folder/name).write_text(json.dumps(value),encoding='utf-8')
np.savez(folder/'shells.npz',shell_id=np.r_[np.zeros(len(a.faces),dtype=int),np.ones(len(b.faces),dtype=int)])
engine.MODELS=models
cfg=Simulation(model_id='two-blocks',mesh_size_m=.002).model_dump()
top=np.flatnonzero(mesh.face_normals[:,2]>.99).tolist()
prompts={
 'simple':'材料设为铝，顶部20W面热源，仿真600秒，计算步长5秒，保存间隔5秒。',
 'negation':'材料设为铝，顶部20W面热源，从0秒加热到600秒，仿真600秒，计算步长5秒，保存间隔5秒。全部外表面对流散热，换热系数10，包括受热面。关闭辐射，关闭空气间隙耦合。',
 'component':'组件1设为铜，组件2设为铝。在组件2的外表面施加20W面热源，从10秒到90秒。仿真120秒，计算步长2秒，保存间隔4秒，关闭辐射。基础材料和网格保持不变。',
 'point':'在实体内部设置一个点热源，位置x=20mm，y=0mm，z=0mm，功率0.05kW，从30秒到90秒加热。仿真总时长120秒，计算步长2秒，保存间隔4秒。网格尺寸1mm，关闭辐射和空气间隙耦合。',
}
results=[]
for name in args.cases.split(','):
    prompt=prompts[name]
    start=time.perf_counter()
    if args.mode=='local': result=engine.plan_request('two-blocks',prompt,copy.deepcopy(cfg))
    else: result=engine.codex_plan_request('two-blocks',prompt,copy.deepcopy(cfg))
    elapsed=time.perf_counter()-start
    c=result.get('config',{});h=(c.get('heat_sources') or [{}])[0]
    checks={'success':result.get('ok')==True,'model_preserved':c.get('model_id')=='two-blocks','one_heat':len(c.get('heat_sources',[]))==1}
    if name in ('simple','negation'):
        checks.update(material_aluminium='铝' in c.get('base_material',{}).get('name',''),power=h.get('power_W')==20,
                      top=h.get('faces')==top,duration=c.get('duration_s')==600,dt=c.get('dt_s')==5,save=c.get('save_s')==5)
    if name=='negation': checks.update(no_radiation=c.get('radiation_enabled')==False,no_air=c.get('air_gap_enabled')==False,convection=c.get('heat_convection')==True and c.get('default_h')==10)
    if name=='component':
        materials={x['component_id']:x['material']['name'] for x in c.get('component_materials',[])}
        checks.update(component_materials='铜' in materials.get(0,'') and '铝' in materials.get(1,''),
                      component_faces=h.get('faces')==list(range(12,24)),start=h.get('start_s')==10,end=h.get('end_s')==90,
                      duration=c.get('duration_s')==120,dt=c.get('dt_s')==2,save=c.get('save_s')==4,
                      base_preserved=c.get('base_material')==cfg['base_material'])
    if name=='point': checks.update(point=h.get('source_type')=='point',embedded=h.get('placement')=='embedded',
        position=h.get('position_m')==[.02,0,0],power=h.get('power_W')==50,start=h.get('start_s')==30,end=h.get('end_s')==90,
        duration=c.get('duration_s')==120,dt=c.get('dt_s')==2,save=c.get('save_s')==4,mesh=c.get('mesh_size_m')==.001,
        no_radiation=c.get('radiation_enabled')==False,no_air=c.get('air_gap_enabled')==False)
    row={'case':name,'mode':args.mode,'elapsed_s':round(elapsed,3),'checks':checks,'questions':result.get('questions'),
         'metrics':result.get('metrics'),'config':c,'prompt':prompt}
    results.append(row)
    (WORK/f'skill-benchmark-{args.label or args.mode}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in row.items() if k not in ('config','prompt')},ensure_ascii=True),flush=True)
