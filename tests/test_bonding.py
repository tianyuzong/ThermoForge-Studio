import json
from pathlib import Path
import numpy as np
import pytest
from strength.schemas import LoadCase,DEMO_MATERIAL
from strength.geometry import session,inspect_model,build_mesh,validate_execution
from strength.fem import assemble_loads,build_constraints
from strength.bonding import prepare_bonds,recover_bonds,interpolation
from strength.solver import write_deck,run_ccx,parse_dat,assess
from strength.store import write_json


@pytest.fixture
def bonded_bar(tmp_path):
    import gmsh
    with session():
        for i in range(3):gmsh.model.occ.addBox(i*.1,0,0,.1,.01,.01)
        gmsh.model.occ.synchronize();gmsh.write(str(tmp_path/'source.step'))
    write_json(tmp_path/'source.json',{'file':'source.step'})
    geo=inspect_model(tmp_path);ids=[b['id'] for b in geo['components']]
    def sel(body,direction):return {'body_id':body,'kind':'direction','direction':direction}
    mat={str(b):{**DEMO_MATERIAL,'young_modulus_Pa':210e9/(3 if i==1 else 1),'poisson_ratio':0} for i,b in enumerate(ids)}
    case=LoadCase(material=mat[str(ids[0])],component_materials=mat,assembly_mode='bonded',
        bonded_connections=[{'name':f'joint {i}','slave':sel(ids[i],'-x'),'master':sel(ids[i-1],'+x'),'max_gap_m':1e-6,'assumptions':'Analytic bonded axial bar'} for i in (1,2)],
        supports=[{'selection':sel(ids[0],'-x'),'components':['x','y','z']}],
        loads=[{'kind':'force','selection':sel(ids[-1],'+x'),'vector':[1000,0,0]}],mesh={'size_m':.006})
    build_mesh(tmp_path,case,tmp_path/'mesh.npz');mesh=dict(np.load(tmp_path/'mesh.npz'))
    return geo,case,mesh,tmp_path


def test_real_calculix_bonded_series_bar(bonded_bar):
    geo,case,mesh,folder=bonded_bar;validate_execution(case,geo)
    force,audit=assemble_loads(mesh,case,geo)
    fixed,groups=build_constraints(mesh,case,geo,check_components=False)
    bonding=prepare_bonds(mesh,case,geo,fixed)
    write_deck(folder,mesh,case,force,fixed,bonding=bonding);run_ccx(folder)
    data=parse_dat(folder/'analysis.dat',len(mesh['points']),len(mesh['tets']))
    info=recover_bonds(mesh,data,force,fixed,bonding)
    summary,_=assess(mesh,case,data,force,fixed,groups,audit,{})
    assert summary['balance']['passed'],summary['balance']
    assert data['displacements'][:,0].max()==pytest.approx(1000*.1/1e-4*(2/210e9+1/70e9),rel=.005)
    np.testing.assert_allclose(data['stresses'][:,:,0],1e7,rtol=.005)
    assert info['compatibility_relative']<1e-5
    assert len(info['interfaces'])==2
    for pair in info['interfaces']:
        assert abs(pair['transfer_to_slave_N'][0])==pytest.approx(1000,rel=1e-5)


def test_bonded_model_rejects_missing_interface_and_cycles(bonded_bar):
    geo,case,mesh,_=bonded_bar;fixed,_=build_constraints(mesh,case,geo,check_components=False)
    case.bonded_connections=case.bonded_connections[:1]
    with pytest.raises(ValueError,match='欠约束'):prepare_bonds(mesh,case,geo,fixed)
    link=case.bonded_connections[0]
    case.bonded_connections.append(link.model_copy(update={'slave':link.master,'master':link.slave}))
    with pytest.raises(ValueError,match='环'):validate_execution(case,geo)


def test_interface_gap_does_not_silently_attach_remote_faces(bonded_bar):
    geo,case,mesh,_=bonded_bar
    case.bonded_connections[0].master.direction='-x'
    fixed,_=build_constraints(mesh,case,geo,check_components=False)
    with pytest.raises(ValueError,match='未找到可用连接'):prepare_bonds(mesh,case,geo,fixed)


def test_bond_extension_preserves_rigid_motion_and_moment():
    corners=np.array([[0,0,0],[.01,0,0],[0,.01,0],[0,0,.01]])
    from strength.fem import EDGES
    points=np.vstack([corners,[(corners[a]+corners[b])/2 for a,b in EDGES]])
    point=np.array([.003,.002,-.00005]);w=interpolation(points,point)
    assert w is not None
    rotation=np.array([.002,.004,.007]);shift=np.array([.01,.02,.03])
    np.testing.assert_allclose(w@(shift+np.cross(rotation,points)),shift+np.cross(rotation,point),atol=1e-12)
    force=np.array([30,-20,70.])
    np.testing.assert_allclose(np.cross(points,w[:,None]*force).sum(0),np.cross(point,force),atol=1e-12)


def test_real_calculix_bonded_bending_transfers_moments(bonded_bar):
    geo,case,mesh,folder=bonded_bar
    case.loads[0].vector=[0,0,-10]
    force,audit=assemble_loads(mesh,case,geo)
    fixed,groups=build_constraints(mesh,case,geo,check_components=False)
    bond=prepare_bonds(mesh,case,geo,fixed)
    write_deck(folder,mesh,case,force,fixed,bonding=bond);run_ccx(folder)
    data=parse_dat(folder/'analysis.dat',len(mesh['points']),len(mesh['tets']))
    info=recover_bonds(mesh,data,force,fixed,bond)
    summary,_=assess(mesh,case,data,force,fixed,groups,audit,{})
    assert summary['balance']['passed'],summary['balance']
    inertia=.01**4/12
    expected=10/(3*inertia)*sum(((.3-i*.1)**3-(.3-(i+1)*.1)**3)/e for i,e in enumerate([210e9,70e9,210e9]))
    tip=mesh['points'][:,0]>.3-1e-10
    assert -data['displacements'][tip,2].mean()==pytest.approx(expected,rel=.03)
    for pair in info['interfaces']:
        assert abs(pair['transfer_to_slave_N'][2])==pytest.approx(10,rel=1e-5)
        assert abs(pair['transfer_moment_about_origin_Nm'][1])==pytest.approx(3,rel=1e-5)


def test_slave_spc_overlap_is_explicit_error(bonded_bar):
    geo,case,mesh,_=bonded_bar
    fixed,_=build_constraints(mesh,case,geo,check_components=False)
    from strength.materials import selection_mask
    node=int(np.unique(mesh['faces'][selection_mask(mesh,case.bonded_connections[0].slave,geo)])[0])
    fixed[node*3]=0
    with pytest.raises(ValueError,match='固定自由度'):prepare_bonds(mesh,case,geo,fixed)


def test_bond_selections_are_bound_in_immutable_snapshot(bonded_bar):
    from strength.service import bind_case
    geo,case,_,_=bonded_bar
    saved=bind_case(case,geo)
    assert saved.bonded_connections[0].slave.kind=='faces'
    assert saved.bonded_connections[0].slave.face_ids
    assert case.bonded_connections[0].slave.kind=='direction'
    assert LoadCase.model_validate(saved.model_dump())==saved


def test_large_assembly_hotspot_figures_preserve_body_identity():
    from strength.report_figures import hotspot_components
    comps=[{'body_id':i,'maximum_von_mises_Pa':100-i} for i in range(1,159)]
    chosen=hotspot_components({'components':list(reversed(comps))})
    assert [c['body_id'] for c in chosen]==list(range(1,9))
