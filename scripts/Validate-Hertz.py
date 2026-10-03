import sys,json,time
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import numpy as np
from strength import store
from strength.geometry import inspect_model,build_mesh
from strength.config import DATA
from forge.examples import make_contact_geometry,demonstration_material
from forge.schemas import AnalysisCase
from forge.mechanics import solve_static
model=DATA/'models'/'hertz-point';model.mkdir(exist_ok=True)
if not (model/'source.step').exists():make_contact_geometry(model/'source.step','hertz');store.write_json(model/'source.json',{'file':'source.step','units':'m'})
geo=inspect_model(model);store.put('model',geo)
sel=lambda b,d:{'kind':'direction','body_id':b,'direction':d}
material=demonstration_material(True);material.young_modulus_Pa=2e9;material.name='Hertz 数值验证材料（E=2 GPa，非真实钢材）'
E=2e9/(2*(1-.3**2));R=.002;F=1.;a=(3*F*R/(4*E))**(1/3);p0=3*F/(2*np.pi*a*a)
size=float(sys.argv[1]) if len(sys.argv)>1 else 3e-5
penalty=float(sys.argv[2]) if len(sys.argv)>2 else 2e14
global_size=float(sys.argv[3]) if len(sys.argv)>3 else .0004
case=AnalysisCase(name='Hertz 球面接触解析基准',analysis_type='static',material=material,component_materials={'1':material,'2':material},geometry_sha256=geo['source_sha256'],
 mesh={'size_m':global_size,'regions':[{'center_m':[0,0,.005],'radius_m':a*2,'size_m':size,'transition_m':.0006},
     {'center_m':[0,0,.005],'radius_m':.0006,'size_m':.00008,'transition_m':.0008}]},
 supports=[{'name':'底面固定','selection':sel(2,'-z')},{'name':'球顶压入','selection':sel(1,'+z'),'displacement_m':[0,0,-a*a/R]}],
 loads=[],
 contacts=[{'name':'球面／平面','slave':{'kind':'faces','body_id':1,'face_ids':[s['id'] for s in geo['surfaces'] if 1 in s['body_ids'] and s['type']=='Sphere']},'master':sel(2,'+z'),'penalty_Pa_m':penalty,'search_distance_m':.001}],
 assumptions=['参数化球面—平面半空间基准，弹性模量2GPa为数值验证参数；并非真实轴承。'])
folder=root/'validation'/('hertz-'+str(round(size*1e6))+'-k'+format(penalty,'.1e')+'-g'+str(round(global_size*1e6)));folder.mkdir(parents=True,exist_ok=True)
store.write_json(folder/'case.json',case.model_dump());info=build_mesh(model,case,folder/'mesh.npz');mesh=dict(np.load(folder/'mesh.npz'))
print('mesh',info['nodes'],info['elements'],'a',a,'p0',p0,flush=True)
s=solve_static(mesh,case,geo,folder/'solve');raw=np.array(s['contact_stresses']);actual=raw[:,2].max()
from strength.materials import selection_mask
from strength.schemas import Selection
nodes=np.unique(mesh['faces'][selection_mask(mesh,Selection.model_validate(sel(1,'+z')),geo)])
F=abs(s['forces'][nodes,2].sum());a=(3*F*R/(4*E))**(1/3);p0=3*F/(2*np.pi*a*a)
area=s['contact_totals'][0]['area_m2'];radius=float(np.sqrt(area/np.pi))
result={'native_contact_area_m2':area,'actual_radius_m':radius,'radius_error':abs(radius/a-1),'normal_force_N':F,'expected_radius_m':a,'expected_peak_Pa':p0,'actual_peak_Pa':actual,'pressure_error':abs(actual/p0-1),'mesh':info,'balance':s['balance']}
result['passed']=bool(result['pressure_error']<=.05 and result['radius_error']<=.05 and result['balance']['passed'])
store.write_json(folder/'benchmark.json',result);print(result,flush=True)
if not result['passed']:raise SystemExit(1)
