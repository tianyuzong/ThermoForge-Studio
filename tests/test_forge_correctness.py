import json,hashlib
from types import SimpleNamespace
import h5py,numpy as np,pytest
from forge.schemas import AnalysisCase,ThermalBoundary,Thermostat,coupling_mode
from forge.initial_state import load_initial_temperature
from forge.convergence import compare
from forge.preflight import validate_case,validate_initial_dependency
from forge.checkpoints import append_step,committed_steps
from test_forge_numerics import material,tetra_mesh


@pytest.mark.parametrize('kind,value,point',[
    ('convection',10,-.1),('radiation',.5,1.01),('temperature',25,-273.16)])
def test_every_boundary_curve_point_has_same_physical_bounds(kind,value,point):
    with pytest.raises(ValueError,match='第 2 点'):
        ThermalBoundary(name='受热面',kind=kind,selection={'kind':'faces','face_ids':[1]},value=value,
                        profile=[{'x':0,'y':value},{'x':10,'y':point}])


def test_ambient_curve_time_and_thermostat_limits():
    base=dict(kind='convection',selection={'kind':'faces','face_ids':[1]},value=10)
    with pytest.raises(ValueError,match='绝对零度'):ThermalBoundary(**base,ambient_profile=[{'x':0,'y':-274}])
    with pytest.raises(ValueError,match='时间不能为负'):ThermalBoundary(**base,profile=[{'x':-1,'y':10}])
    with pytest.raises(ValueError,match='最小功率'):Thermostat(target_C=30,hysteresis_K=1,minimum_W=100,maximum_W=10,sensor=base['selection'])
    # Negative prescribed heat flux represents extraction, not an invalid h.
    assert ThermalBoundary(**{**base,'kind':'heat_flux'},profile=[{'x':0,'y':-10}]).profile[0].y==-10


def source_fixture(root,mesh,case):
    folder=root/'hot-source';folder.mkdir()
    source=case.model_dump();source.update(analysis_type='thermal_transient');source['thermal']['initial_from_study']=None
    (folder/'config.json').write_text(json.dumps({'case':source}),encoding='utf-8')
    (folder/'status.json').write_text(json.dumps({'status':'completed'}))
    with h5py.File(folder/'fields.h5','w') as f:
        for key in ('points','tets','body_ids','faces','surface_ids','owners'):f[key]=mesh[key]
        f['temperature_C']=np.full((2,len(mesh['points'])),100.)
        f['times_s']=[0.,600.]
    checksum=hashlib.sha256((folder/'fields.h5').read_bytes()).hexdigest()
    (folder/'raw-fields.sha256.json').write_text(json.dumps({'sha256':checksum}))
    return folder


def test_initial_source_integrity_and_full_node_component_identity(tmp_path):
    mesh=tetra_mesh();case=AnalysisCase(name='续算',analysis_type='thermo_mechanical',material=material(),
        geometry_sha256='geometry-v1',thermal={'initial_C':25,'initial_from_study':'hot-source'})
    source=source_fixture(tmp_path,mesh,case)
    T,p=load_initial_temperature(mesh,case,tmp_path)
    assert np.all(T==100) and p['source_time_s']==600 and p['physical_start_time_s']==0
    changed={**mesh,'body_ids':np.array([2])}
    with pytest.raises(ValueError,match='body_ids'):load_initial_temperature(changed,case,tmp_path)
    with (source/'fields.h5').open('ab') as f:f.write(b'corruption')
    with pytest.raises(ValueError,match='SHA-256'):load_initial_temperature(mesh,case,tmp_path)


def test_coupled_solver_really_uses_saved_initial_temperature(monkeypatch,tmp_path):
    from forge import coupling,initial_state
    from strength import config
    mesh=tetra_mesh();case=AnalysisCase(name='耦合续算',analysis_type='thermo_mechanical',material=material(),
        thermal={'initial_C':25,'duration_s':1,'dt_s':1,'save_s':1,'initial_from_study':'hot-source'})
    (tmp_path/'studies').mkdir();source_fixture(tmp_path/'studies',mesh,case);monkeypatch.setattr(config,'DATA',tmp_path)
    system=SimpleNamespace(n=10,mesh=mesh,interface_records={})
    frame={'displacements':np.zeros((10,3)),'forces':np.zeros((10,3)),'stresses':np.zeros((1,4,6)),'strains':np.zeros((1,4,6))}
    seen=[]
    def static(*a,**k):seen.append(k['temperature'].copy());return frame
    monkeypatch.setattr(coupling,'solve_static',static);monkeypatch.setattr(coupling,'recover_contact',lambda *a,**k:{})
    monkeypatch.setattr(coupling,'coupled_step',lambda s,c,e,f,T,*args:(T+1,frame,{'thermal':{},'contact':{}}))
    times,T,_,audits=coupling.solve_coupled(system,case,{},tmp_path/'coupled')
    assert np.all(seen[0]==100) and np.all(T[0]==100) and np.all(T[-1]==101)
    assert audits[0]['initial_state']['study_id']=='hot-source'
    assert committed_steps(tmp_path/'coupled')==[{'index':0,'time_s':0.},{'index':1,'time_s':1.}]


def test_mechanical_contact_without_thermal_feedback_is_one_way(monkeypatch,tmp_path):
    from forge import coupling
    pair={'name':'mechanical','slave':{'kind':'faces','body_id':1,'face_ids':[1]},'master':{'kind':'faces','body_id':2,'face_ids':[2]}}
    case=AnalysisCase(name='单向',analysis_type='thermo_mechanical',material=material(),contacts=[pair])
    assert coupling_mode(case)=='one_way_thermal_structure'
    system=SimpleNamespace(mesh=tetra_mesh(),control_values={},controls={},boundaries=[])
    calls=[]
    def heat(T,*a,**k):calls.append('heat');return T+1,{'interfaces':[]}
    system.solve=heat
    frame={'displacements':np.zeros((10,3))}
    monkeypatch.setattr(coupling,'solve_static',lambda *a,**k:frame)
    monkeypatch.setattr(coupling,'interface_map',lambda *a,**k:[])
    monkeypatch.setattr(coupling,'recover_contact',lambda *a,**k:{'mechanical':{'pressure_Pa':np.array([])}})
    _,_,audit=coupling.coupled_step(system,case,{},tmp_path,np.full(10,25.),1.,1.)
    assert calls==['heat'] and audit['feedback']=='one_way_thermal_structure' and 'mechanical' in audit['contact']


def test_convergence_requires_metrics_completed_physics_and_balance():
    assert compare([{'mesh':{'size_m':x}} for x in (3,2,1)])['status']=='insufficient_evidence'
    summaries=[{'analysis_type':'static','mesh':{'size_m':x,'elements':100//x},'maximum_von_mises_Pa':100.,'maximum_displacement_m':.01,
                'mechanical_balance':{'passed':True}} for x in (3,2,1)]
    assert compare(summaries)['passed'] is True
    summaries[1]['mechanical_balance']['passed']=False
    assert compare(summaries)['status']=='invalid'
    summaries[1]['mechanical_balance']['passed']=True;summaries[-1]['maximum_von_mises_Pa']=float('nan')
    assert compare(summaries)['status']=='insufficient_evidence'


def test_shared_preflight_rejects_nonexistent_components_before_meshing(models):
    geo=models['bar'];case=AnalysisCase(name='有效热源',analysis_type='thermal_transient',material=material(),
        thermal={'boundaries':[{'kind':'body_power','body_id':1,'value':10}]})
    assert validate_case(case,geo)['ok']
    case.thermal.boundaries[0].body_id=999
    with pytest.raises(ValueError,match='不存在的组件 999'):validate_case(case,geo)
    case.thermal.boundaries[0].body_id=1;case.fatigue.body_ids=[999]
    with pytest.raises(ValueError,match='疲劳评估'):validate_case(case,geo)


def test_initial_dependency_disallows_partial_and_nonthermal_results():
    case=AnalysisCase(name='热',analysis_type='thermal_transient',material=material())
    parent={'model_id':'m','project_id':'p','status':'queued','case':case.model_dump()}
    assert validate_initial_dependency(case,parent,'m','p',allow_pending=True)
    with pytest.raises(ValueError):validate_initial_dependency(case,parent,'m','p')
    parent['status']='partial'
    with pytest.raises(ValueError):validate_initial_dependency(case,parent,'m','p',allow_pending=True)
    parent['status']='completed';parent['case']['analysis_type']='static'
    with pytest.raises(ValueError,match='不是热结果'):validate_initial_dependency(case,parent,'m','p')


def test_checkpoint_never_publishes_uncommitted_trial(tmp_path):
    append_step(tmp_path,0,0.,np.full(10,25.),audit={'initial':True})
    append_step(tmp_path,1,.5,np.full(10,30.),audit={'thermal':{'energy_balance_relative':0.}})
    with h5py.File(tmp_path/'physical-steps.h5','a') as f:
        g=f.create_group('2');g.attrs.update(time_s=1.,committed=False)
    assert committed_steps(tmp_path)==[{'index':0,'time_s':0.},{'index':1,'time_s':.5}]


def test_disk_backed_frames_export_without_whole_history_materialization(tmp_path):
    from forge.checkpoints import JournalSeries,derive_thermal_fields
    from forge.results import write_archive
    from forge.thermal import HeatSystem
    class GuardedSeries(JournalSeries):
        def __array__(self,*a,**k):raise AssertionError('Whole history materialized')
    mesh=tetra_mesh();case=AnalysisCase(name='逐帧落盘',analysis_type='thermal_transient',material=material())
    indices=list(range(6));journal=tmp_path/'journal'
    for i in indices:
        state={'displacements':np.full((10,3),i*.001),'stresses':np.full((1,4,6),i),'strains':np.zeros((1,4,6)),
               'forces':np.zeros((10,3)),'contact_stresses':[],'contact_displacements':[],
               'balance':{'passed':True},'axles':{}}
        append_step(journal,i,float(i),np.full(10,25.+i),state)
    temperatures=GuardedSeries(journal,indices,'temperature_C');structures=GuardedSeries(journal,indices)
    assert len(temperatures)==6 and temperatures and structures[-1]['balance']['passed']
    gradients,fluxes=derive_thermal_fields(tmp_path,HeatSystem(mesh,case,{}),temperatures)
    write_archive(tmp_path,mesh,[float(i) for i in indices],temperatures,structures,gradients,fluxes,case=case)
    with h5py.File(tmp_path/'fields.h5') as f:
        for i in (0,3,5):
            assert np.all(f['temperature_C'][i]==25+i)
            assert np.all(f['displacements'][i]==i*.001)
            assert np.all(f['stresses'][i]==i)
