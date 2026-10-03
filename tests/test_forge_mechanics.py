import json
import numpy as np
import pytest
from strength.config import DATA
from strength.geometry import build_mesh
from forge.schemas import AnalysisCase
from forge.mechanics import solve_static
from test_forge_numerics import material


def test_uniform_restrained_thermal_stress(models,tmp_path):
    geo=models['bar'];surfaces=[s['id'] for s in geo['surfaces']]
    case=AnalysisCase(name='完全约束热应力',analysis_type='thermo_mechanical',material=material(),
        supports=[{'name':'固定全部外面','selection':{'kind':'faces','face_ids':surfaces}}],mesh={'size_m':.012},
        thermal={'reference_C':25})
    folder=DATA/'models'/geo['id'];path=tmp_path/'mesh.npz'
    build_mesh(folder,case,path);mesh=dict(np.load(path));T=np.full(len(mesh['points']),75.)
    result=solve_static(mesh,case,geo,tmp_path/'solve',temperature=T)
    expected=-material().young_modulus_Pa*12e-6*50/(1-2*.3)
    assert np.max(np.abs(result['displacements']))<1e-12
    assert np.allclose(result['stresses'][...,:3],expected,rtol=2e-5)
    assert np.max(np.abs(result['stresses'][...,3:]))<abs(expected)*1e-5


def test_flat_contact_pressure(tmp_path):
    from forge.examples import make_contact_geometry,demonstration_material
    from strength.geometry import inspect_model
    from strength.store import write_json
    from forge.schemas import ContactPair
    folder=tmp_path/'contact';folder.mkdir();make_contact_geometry(folder/'source.step')
    write_json(folder/'source.json',{'file':'source.step','units':'m'})
    geo=inspect_model(folder)
    def sel(body,direction):return {'kind':'direction','body_id':body,'direction':direction}
    case=AnalysisCase(name='平面接触',analysis_type='static',material=demonstration_material(True),mesh={'size_m':.006},
        supports=[{'name':'底面固定','selection':sel(2,'-z')},{'name':'上块横向约束','selection':sel(1,'+z'),'components':['x','y']}],
        loads=[{'name':'压力','kind':'force','selection':sel(1,'+z'),'vector':[0,0,-1000]}],
        contacts=[{'name':'接触','slave':sel(1,'-z'),'master':sel(2,'+z'),'penalty_Pa_m':1e15}])
    path=tmp_path/'contactmesh.npz';build_mesh(folder,case,path);mesh=dict(np.load(path))
    result=solve_static(mesh,case,geo,tmp_path/'contactsolve')
    assert result.get('contact_stresses')
    assert np.isfinite(result['stresses']).all()
    pressures=np.array(result['contact_stresses'])[:,2]
    assert pressures.mean()==pytest.approx(1000/.02**2,rel=.02)
    assert result['contact_totals'][0]['area_m2']==pytest.approx(.02**2,rel=1e-6)
    assert abs(result['contact_totals'][0]['normal_force_N'])==pytest.approx(1000,rel=1e-6)


def test_contact_cycle_and_thermal_interface(tmp_path):
    from forge.examples import make_contact_geometry,demonstration_material
    from strength.geometry import inspect_model
    from strength.store import write_json
    from forge.mechanics import solve_cycle
    from forge.thermal import HeatSystem
    from forge.coupling import solve_coupled
    folder=tmp_path/'geometry';folder.mkdir();make_contact_geometry(folder/'source.step')
    write_json(folder/'source.json',{'file':'source.step','units':'m'});geo=inspect_model(folder)
    def sel(body,direction):return {'kind':'direction','body_id':body,'direction':direction}
    case=AnalysisCase(name='周期接触',analysis_type='contact_cycle',material=demonstration_material(True),mesh={'size_m':.009},
        supports=[{'name':'底面固定','selection':sel(2,'-z')},{'name':'横向约束','selection':sel(1,'+z'),'components':['x','y']}],
        loads=[{'name':'压力','kind':'force','selection':sel(1,'+z'),'vector':[0,0,-1000]}],
        history={'times_s':[0,1,2],'factors':{'压力':[.2,1,.2]},'periodic':True},
        contacts=[{'name':'界面','slave':sel(1,'-z'),'master':sel(2,'+z'),'thermal':True,
            'conductance':[{'pressure_Pa':0,'conductance_W_m2K':1000}],'gap_conductivity_W_mK':.026}])
    path=tmp_path/'mesh.npz';build_mesh(folder,case,path);mesh=dict(np.load(path))
    frames=solve_cycle(mesh,case,geo,tmp_path/'cycle')
    assert len(frames)==3
    pressure=[np.mean(np.array(f['contact_stresses'])[:,2]) for f in frames]
    assert pressure[1]/pressure[0]==pytest.approx(5,rel=.01)
    coupled=case.model_copy(deep=True);coupled.analysis_type='thermo_mechanical';coupled.coupling.transient=False
    # The series-resistance oracle assumes a flat, closed interface. Isolate it
    # from thermal bending; expansion itself is tested independently above.
    coupled.material.thermal_expansion_per_K=0
    from forge.schemas import ThermalBoundary
    coupled.thermal.boundaries=[ThermalBoundary(kind='temperature',selection=sel(1,'+z'),value=100),ThermalBoundary(kind='temperature',selection=sel(2,'-z'),value=0)]
    system=HeatSystem(mesh,coupled,geo)
    times,temps,structures,audits=solve_coupled(system,coupled,geo,tmp_path/'coupled')
    expected=100/(.02/(45*.02**2)+1/(1000*.02**2))
    actual=audits[0]['thermal']['interfaces'][0]['heatflow_W']
    assert actual==pytest.approx(expected,rel=.015)
    assert audits[0]['iterations'][-1]['temperature_change_K']<=.01
    assert np.max(temps[-1])==pytest.approx(100,abs=1e-9)
    assert np.min(temps[-1])==pytest.approx(0,abs=1e-9)
    assert audits[0]['thermal']['mechanical_temperature_consistency_K']<=.01
    assert audits[0]['mechanical']['passed']
def test_bonded_group_can_be_supported_through_mechanical_contact(tmp_path):
    import gmsh
    import numpy as np
    from strength.geometry import session,inspect_model,build_mesh
    from strength.store import write_json
    from forge.schemas import AnalysisCase
    from forge.mechanics import solve_static
    from test_forge_numerics import material
    model=tmp_path/'cad';model.mkdir()
    with session():
        for z in (.02,.01,0):gmsh.model.occ.addBox(0,0,z,.02,.02,.01)
        gmsh.model.occ.synchronize();gmsh.write(str(model/'source.step'))
    write_json(model/'source.json',{'file':'source.step','units':'m'});geo=inspect_model(model)
    sel=lambda b,d:{'kind':'direction','body_id':b,'direction':d}
    case=AnalysisCase(name='粘结与接触混合传力',analysis_type='static',material=material(),mesh={'size_m':.008},
        supports=[{'name':'基座','selection':sel(3,'-z')},{'name':'上端压入','selection':sel(1,'+z'),'displacement_m':[0,0,-1e-6]}],
        bonded_connections=[{'name':'上两层粘结','slave':sel(1,'-z'),'master':sel(2,'+z'),'max_gap_m':1e-5,'assumptions':'解析分层压缩试件，上两层理想粘结'}],
        contacts=[{'name':'下接触','slave':sel(2,'-z'),'master':sel(3,'+z'),'penalty_Pa_m':1e15}])
    path=tmp_path/'mesh.npz';build_mesh(model,case,path);mesh=dict(np.load(path))
    frame=solve_static(mesh,case,geo,tmp_path/'solve')
    assert frame['balance']['passed']
    assert max(v[2] for v in frame['contact_stresses'])>1e6

def test_coulomb_sliding_transmits_friction_and_accumulates_wear(tmp_path):
    from forge.examples import make_contact_geometry,demonstration_material
    from forge.schemas import AnalysisCase
    from forge.mechanics import solve_cycle
    from forge.contact import wear_history
    from strength.geometry import inspect_model,build_mesh
    from strength.store import write_json
    import numpy as np
    folder=tmp_path/'model';folder.mkdir();make_contact_geometry(folder/'source.step');write_json(folder/'source.json',{'file':'source.step','units':'m'})
    geo=inspect_model(folder);sel=lambda b,d:{'kind':'direction','body_id':b,'direction':d}
    m=demonstration_material(True);m.young_modulus_Pa=2e9;m.poisson_ratio=0.;m.wear_coefficient=1e-3
    case=AnalysisCase(name='库仑滑动验证',analysis_type='contact_cycle',material=m,mesh={'size_m':.008},
        supports=[{'name':'底面','selection':sel(2,'-z')}],history={'times_s':[0,1,2],'factors':{},'periodic':False},
        motions=[{'name':'顶面平移','body_id':1,'selection':sel(1,'+z'),'center_m':[0,0,0],'axis':[0,0,1],'angles_rad':[0,0,0],'translations_m':[[x,0,-1e-6] for x in (0,5e-5,1e-4)]}],
        contacts=[{'name':'滑动面','slave':sel(1,'-z'),'master':sel(2,'+z'),'friction':.2,'penalty_Pa_m':1e15,'tangential_stiffness_Pa_m':1e12,'wear':True}])
    path=tmp_path/'mesh.npz';build_mesh(folder,case,path);mesh=dict(np.load(path));frames=solve_cycle(mesh,case,geo,tmp_path/'solve')
    final=frames[-1]['contact_totals'][0]
    assert abs(final['force_N'][0]/final['force_N'][2])==pytest.approx(.2,rel=.01)
    assert all(f['balance']['passed'] for f in frames)
    wear=wear_history(mesh,case,frames,geo)['滑动面']
    assert wear['depth_m'].max()>0 and wear['sliding_distance_m'].max()>9e-5

