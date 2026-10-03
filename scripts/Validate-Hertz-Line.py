import sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import numpy as np
from strength import store
from strength.config import DATA
from strength.geometry import inspect_model,build_mesh
from forge.benchmark_geometry import line_contact
from forge.examples import demonstration_material
from forge.schemas import AnalysisCase
from forge.mechanics import solve_static

penalty=float(sys.argv[2]) if len(sys.argv)>2 else 1e15
size=float(sys.argv[1]) if len(sys.argv)>1 else .00002
model=DATA/'models'/'hertz-line';model.mkdir(exist_ok=True)
if not (model/'source.step').exists():line_contact(model/'source.step');store.write_json(model/'source.json',{'file':'source.step','units':'m'})
geo=inspect_model(model);store.put('model',geo);sel=lambda b,d:dict(kind='direction',body_id=b,direction=d)
m=demonstration_material(True);m.young_modulus_Pa=2e9;m.name='Hertz 数值验证材料（E=2 GPa，非真实钢材）'
case=AnalysisCase(name='Hertz 平面应变线接触',analysis_type='static',material=m,component_materials={'1':m,'2':m},geometry_sha256=geo['source_sha256'],mesh={'size_m':.0004,
    'regions':[{'center_m':[0,0,.005],'radius_m':.00022,'size_m':size,'transition_m':.0006},{'center_m':[0,0,.005],'radius_m':.0006,'size_m':.00008,'transition_m':.0008}]},
    supports=[{'name':'底部','selection':sel(2,'-z')},{'name':'圆柱压入','selection':sel(1,'+z'),'components':['x','z'],'displacement_m':[0,0,-.000005]},
        *[{'name':str(b)+d,'selection':sel(b,d),'components':['y']} for b in [1,2] for d in ['-y','+y']]],
    contacts=[{'name':'线接触','slave':{'kind':'faces','body_id':1,'face_ids':[s['id'] for s in geo['surfaces'] if 1 in s['body_ids'] and s['type']=='Cylinder']},'master':sel(2,'+z'),'penalty_Pa_m':penalty}],
    assumptions=['平面应变薄截面，双侧限制轴向位移；E=2GPa为验证参数。'])
folder=root/'validation'/f'hertz-line-{size*1e6:g}-K{penalty:.0e}';folder.mkdir(exist_ok=True);store.write_json(folder/'case.json',case.model_dump())
info=build_mesh(model,case,folder/'mesh.npz');mesh=dict(np.load(folder/'mesh.npz'));print('mesh',info['nodes'],info['elements'],flush=True)
s=solve_static(mesh,case,geo,folder/'solve');total=s['contact_totals'][0];F=abs(total['normal_force_N']);length=.0002;R=.002;E=2e9/(2*(1-.3**2))
a=np.sqrt(4*F/length*R/(np.pi*E));p0=2*F/(np.pi*a*length);p=np.array(s['contact_stresses'])[:,2].max();actual_width=total['area_m2']/length/2
result={'normal_force_N':F,'expected_half_width_m':a,'actual_half_width_m':actual_width,'expected_peak_Pa':p0,'actual_peak_Pa':p,
    'pressure_error':abs(p/p0-1),'width_error':abs(actual_width/a-1),'balance':s['balance'],'mesh':info}
result['passed']=bool(result['pressure_error']<=.05 and result['width_error']<=.05 and result['balance']['passed'])
store.write_json(folder/'benchmark.json',result);print(result,flush=True)
if not result['passed']:raise SystemExit(1)
