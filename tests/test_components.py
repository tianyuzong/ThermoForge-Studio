import json
import numpy as np
import pytest
from strength.geometry import session,inspect_model,build_mesh,validate_execution,resolve_selection
from strength.store import write_json
from strength.schemas import LoadCase,DEMO_MATERIAL
from strength.fem import assemble_loads,build_constraints
from strength.solver import write_deck,run_ccx,parse_dat,assess
from strength.gpu_solver import solve_gpu,clear_cache

@pytest.fixture
def assembly(tmp_path):
    import gmsh
    with session():
        gmsh.model.occ.addBox(0,0,0,.1,.01,.01)
        gmsh.model.occ.addBox(0,.03,0,.1,.01,.01)
        gmsh.model.occ.synchronize();gmsh.write(str(tmp_path/'source.step'))
    write_json(tmp_path/'source.json',{'file':'source.step','original_name':'双组件材料验证.step'})
    geo=inspect_model(tmp_path)
    assert len(geo['components'])==2
    materials={str(b['id']):{**DEMO_MATERIAL,'name':f'验证材料 {i+1}','young_modulus_Pa':210e9/(i+1),'density_kg_m3':7850/(i+1),'poisson_ratio':0.,'yield_strength_Pa':235e6/(i+1)} for i,b in enumerate(geo['components'])}
    case=LoadCase(material=DEMO_MATERIAL,component_materials=materials,assembly_mode='independent',
        supports=[{'selection':{'kind':'direction','direction':'-x','body_id':b['id']}} for b in geo['components']],
        loads=[{'kind':'force','selection':{'kind':'direction','direction':'+x','body_id':b['id']},'vector':[1000,0,0]} for b in geo['components']],mesh={'size_m':.006})
    build_mesh(tmp_path,case,tmp_path/'mesh.npz');mesh=dict(np.load(tmp_path/'mesh.npz'))
    return geo,case,mesh

def test_component_materials_cpu_gpu(assembly,tmp_path):
    geo,case,mesh=assembly;validate_execution(case,geo)
    f,a=assemble_loads(mesh,case,geo);fixed,groups=build_constraints(mesh,case,geo)
    write_deck(tmp_path,mesh,case,f,fixed);run_ccx(tmp_path)
    cpu=parse_dat(tmp_path/'analysis.dat',len(mesh['points']),len(mesh['tets']))
    clear_cache();gpu,meta=solve_gpu(mesh,case,f,fixed)
    try:
        for field,tol in [('displacements',1e-6),('stresses',1e-5)]:
            assert np.linalg.norm(gpu[field]-cpu[field])/np.linalg.norm(cpu[field])<tol
        summary,_=assess(mesh,case,gpu,f,fixed,groups,a,{'nodes':len(mesh['points'])})
        one,two=summary['components'];assert summary['balance']['passed']
        assert abs(two['maximum_displacement_m']/one['maximum_displacement_m']-2)<1e-6
        assert abs(one['minimum_safety_factor']/two['minimum_safety_factor']-2)<1e-6
        # Altering only the second component must invalidate the factor cache.
        body=str(geo['components'][1]['id']);case.component_materials[body].young_modulus_Pa*=.8
        _,again=solve_gpu(mesh,case,f,fixed);assert not again['factorization_reused']
    finally:clear_cache()

def test_component_gravity_and_missing_connection(assembly):
    geo,case,mesh=assembly;body=geo['components'][1]['id']
    case.loads=[type(case.loads[0])(kind='gravity',body_id=body,vector=[0,0,-10])]
    f,a=assemble_loads(mesh,case,geo)
    np.testing.assert_allclose(f.sum(0),[0,0,-10*.1*.01*.01*3925],atol=1e-10)
    other=np.unique(mesh['tets'][mesh['body_ids']!=body]);assert np.all(f[other]==0)
    case.assembly_mode='unconfigured'
    with pytest.raises(ValueError,match='连接尚未定义'):validate_execution(case,geo)
    case.assembly_mode='independent';case.supports=case.supports[:1]
    with pytest.raises(ValueError,match='欠约束'):build_constraints(mesh,case,geo)
    case.component_materials.pop(str(body))
    with pytest.raises(ValueError,match='尚未指定材料'):validate_execution(case,geo)

def test_library_saved_case_snapshots(client,models):
    from strength import store
    geo=models['bar'];store.put('model',geo)
    p=client.post('/api/v1/projects',json={'name':'材料与工况'}).json();store.update('project',p['id'],model_id=geo['id'])
    card=client.post('/api/v1/materials',json={'material':DEMO_MATERIAL});assert card.status_code==200
    assert card.json()['id'] in [c['id'] for c in client.get('/api/v1/materials').json()]
    from test_physics import axial_case
    case=axial_case().model_dump();r=client.post(f'/api/v1/projects/{p["id"]}/cases',json={'case':case})
    assert r.status_code==200,r.text
    client.patch(f'/api/v1/projects/{p["id"]}',json={'material':{**DEMO_MATERIAL,'young_modulus_Pa':70e9}})
    saved=client.get(f'/api/v1/projects/{p["id"]}/cases').json()
    assert saved[0]['case']['material']['young_modulus_Pa']==210e9
    assert not client.get(f'/api/v1/jobs?project_id={p["id"]}').json()

def test_shared_cad_face_is_not_silently_bonded(tmp_path):
    import gmsh
    with session():
        a=gmsh.model.occ.addBox(0,0,0,.02,.01,.01)
        b=gmsh.model.occ.addBox(.02,0,0,.02,.01,.01)
        gmsh.model.occ.fragment([(3,a)],[(3,b)])
        gmsh.model.occ.synchronize();gmsh.write(str(tmp_path/'source.step'))
    write_json(tmp_path/'source.json',{'file':'source.step'})
    geo=inspect_model(tmp_path)
    assert len(geo['components'])==2
    ids=[b['id'] for b in geo['components']]
    case=LoadCase(material=DEMO_MATERIAL,component_materials={str(i):DEMO_MATERIAL for i in ids},assembly_mode='independent',
        supports=[{'selection':{'kind':'direction','direction':'-z','body_id':i}} for i in ids],
        loads=[{'kind':'gravity','body_id':ids[1],'vector':[0,0,-10]}],mesh={'size_m':.005})
    build_mesh(tmp_path,case,tmp_path/'mesh.npz');mesh=dict(np.load(tmp_path/'mesh.npz'))
    one=np.unique(mesh['tets'][mesh['body_ids']==ids[0]]);two=np.unique(mesh['tets'][mesh['body_ids']==ids[1]])
    assert not np.intersect1d(one,two).size
    f,_=assemble_loads(mesh,case,geo);assert np.all(f[one]==0)
    build_constraints(mesh,case,geo)

def test_agent_component_assignment_schema():
    from strength.agent import strict_schema
    from test_physics import axial_case
    prop=strict_schema()['$defs']['LoadCase']['properties']['component_materials']
    assert prop['type']=='array' and prop['items']['additionalProperties'] is False
    assert set(prop['items']['required'])=={'body_id','material'}
    raw=axial_case().model_dump();raw['component_materials']=[{'body_id':2,'material':DEMO_MATERIAL}]
    assert LoadCase.model_validate(raw).component_materials['2'].young_modulus_Pa==210e9
    raw['component_materials']*=2
    with pytest.raises(ValueError,match='重复'):LoadCase.model_validate(raw)
