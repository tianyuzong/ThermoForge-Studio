import json
import numpy as np, pytest, trimesh
from strength.geometry import inspect_model,build_mesh,resolve_selection
from strength.store import write_json
from strength.config import DATA
from strength.schemas import LoadCase,DEMO_MATERIAL
from strength.fem import assemble_loads

def test_stl_units_and_mesh(tmp_path):
    shape=trimesh.creation.box(extents=[20,10,10]); shape.export(tmp_path/'source.stl')
    write_json(tmp_path/'source.json',{'file':'source.stl','units':'mm'})
    evidence=inspect_model(tmp_path)
    np.testing.assert_allclose(evidence['dimensions_m'],[.02,.01,.01],rtol=1e-6)
    c=LoadCase.model_validate({'material':DEMO_MATERIAL,'supports':[{'selection':{'kind':'direction','direction':'-x'}}],
        'loads':[{'kind':'force','selection':{'kind':'direction','direction':'+x'},'vector':[100,0,0]}],'mesh':{'size_m':.004}})
    build_mesh(tmp_path,c,tmp_path/'mesh.npz')
    mesh=dict(np.load(tmp_path/'mesh.npz'));force,_=assemble_loads(mesh,c,evidence)
    np.testing.assert_allclose(force.sum(0),[100,0,0],atol=1e-8)

def test_unsealed_stl_rejected(tmp_path):
    box=trimesh.creation.box();box.update_faces(np.arange(len(box.faces)-1));box.export(tmp_path/'source.stl')
    write_json(tmp_path/'source.json',{'file':'source.stl','units':'mm'})
    with pytest.raises(ValueError,match='非封闭'):inspect_model(tmp_path)

def test_bracket_holes_and_surface_load_mapping(models,tmp_path):
    evidence=models['bracket'];assert len(evidence['named_selections']['安装孔'])==4
    assert all(s['is_hole'] for s in evidence['surfaces'] if s['id'] in evidence['named_selections']['安装孔'])
    c=LoadCase.model_validate({'material':DEMO_MATERIAL,'supports':[{'selection':{'kind':'holes'}}],
        'loads':[{'kind':'pressure','selection':{'kind':'named','name':'全部表面'},'pressure_Pa':1e6}]})
    build_mesh(DATA/'models'/'bracket',c,tmp_path/'mesh.npz');m=dict(np.load(tmp_path/'mesh.npz'))
    f,_=assemble_loads(m,c,evidence)
    assert np.linalg.norm(f.sum(0))/np.linalg.norm(f,axis=1).sum()<1e-8
    moment=np.cross(m['points'],f).sum(0)
    assert np.linalg.norm(moment)<1e-8
