"""Native restart must preserve friction history and isolate failed trial branches."""
import hashlib
import numpy as np
import pytest
from strength.geometry import inspect_model,build_mesh
from strength.store import write_json
from forge.examples import make_contact_geometry,demonstration_material
from forge.schemas import AnalysisCase
from forge.mechanics import solve_static


def test_native_restart_matches_replayed_friction_history_and_isolates_trials(tmp_path):
    model=tmp_path/'cad';model.mkdir();make_contact_geometry(model/'source.step')
    write_json(model/'source.json',{'file':'source.step','units':'m'});geo=inspect_model(model)
    sel=lambda b,d:{'kind':'direction','body_id':b,'direction':d}
    m=demonstration_material(True);m.young_modulus_Pa=2e9
    case=AnalysisCase(name='restart friction validation',analysis_type='thermo_mechanical',material=m,mesh={'size_m':.012},
        supports=[{'name':'base','selection':sel(2,'-z')},{'name':'lateral','selection':sel(1,'+z'),'components':['x','y']}],
        loads=[{'name':'preload','kind':'force','selection':sel(1,'+z'),'vector':[0,0,-1000]}],
        contacts=[{'name':'friction','slave':sel(1,'-z'),'master':sel(2,'+z'),'friction':.2,'penalty_Pa_m':1e15,'tangential_stiffness_Pa_m':1e12}])
    meshpath=tmp_path/'mesh.npz';build_mesh(model,case,meshpath);mesh=dict(np.load(meshpath))
    cold=np.full(len(mesh['points']),25.);hot=cold.copy();hot[np.unique(mesh['tets'][mesh['body_ids']==1])]=100.
    initial=solve_static(mesh,case,geo,tmp_path/'initial',temperature=cold,write_restart=True)
    heated=solve_static(mesh,case,geo,tmp_path/'heated',temperature=hot,restart_from=initial['native_restart'],write_restart=True)
    reference=solve_static(mesh,case,geo,tmp_path/'reference',temperature=hot,temperature_history=[cold])
    np.testing.assert_allclose(heated['displacements'],reference['displacements'],atol=2e-10,rtol=2e-4)
    np.testing.assert_allclose(heated['stresses'],reference['stresses'],atol=30,rtol=2e-4)
    assert heated['balance']['passed']
    checksum=heated['native_restart']['sha256']
    # A discarded hotter branch must not alter the committed restart file.
    solve_static(mesh,case,geo,tmp_path/'discarded-trial',temperature=hot+5,restart_from=heated['native_restart'],write_restart=True)
    cooled=solve_static(mesh,case,geo,tmp_path/'cooled',temperature=cold,restart_from=heated['native_restart'],write_restart=True)
    replay=solve_static(mesh,case,geo,tmp_path/'replay',temperature=cold,temperature_history=[cold,hot])
    np.testing.assert_allclose(cooled['displacements'],replay['displacements'],atol=2e-10,rtol=2e-4)
    np.testing.assert_allclose(cooled['stresses'],replay['stresses'],atol=30,rtol=2e-4)
    assert cooled['balance']['passed']
    contact=np.asarray(cooled['contact_stresses'])
    assert np.linalg.norm(contact[:,3:5],axis=1).max()>1e5  # residual friction after a real slip/reversal path
    with open(heated['native_restart']['path'],'rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==checksum
    assert '*RESTART,READ' in (tmp_path/'cooled'/'analysis.inp').read_text()
    assert (tmp_path/'cooled'/'analysis.inp').read_text().count('*STEP')==1
    changed={**mesh,'points':mesh['points'].copy()};changed['points'][0,0]+=1e-9
    with pytest.raises(ValueError,match='网格'):
        solve_static(changed,case,geo,tmp_path/'wrong-model',temperature=cold,restart_from=initial['native_restart'])
