"""Changing contact histories must accumulate damage at the same material point."""
from types import SimpleNamespace
import numpy as np
from forge.worker import accumulate_fatigue_block


def setup():
    vertices=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float)
    points=np.vstack([vertices,*[(vertices[a]+vertices[b])[None]/2 for a,b in [(0,1),(1,2),(0,2),(0,3),(1,3),(2,3)]]])
    mesh={'points':points,'tets':np.arange(10)[None],'body_ids':np.array([3])}
    case=SimpleNamespace(fatigue=SimpleNamespace(target_cycles=100),cycles_per_wear_block=10,wear_blocks=2)
    return mesh,case


def block(values,status='evaluated'):
    damage=np.array([values],float)
    return {'damage':damage,'life_cycles':np.divide(100,damage,out=np.full_like(damage,np.inf),where=damage>0),'evaluated':np.isfinite(damage),'hotspot':{'damage':float(np.nanmax(damage))},'failures':{} if status=='evaluated' else {'材料未标定':1},'status':status}


def test_blocks_accumulate_identity_and_report_latest_geometry():
    mesh,case=setup()
    first=accumulate_fatigue_block(mesh,case,None,block([1,0,0,0]),0)
    second=accumulate_fatigue_block(mesh,case,first,block([0,2,0,0]),1)
    np.testing.assert_allclose(second['accumulated_block_damage'],[[.1,.2,0,0]])
    np.testing.assert_allclose(second['damage'],[[0,2,0,0]])
    np.testing.assert_allclose(first['accumulated_block_damage'],[[.1,0,0,0]])
    assert second['accumulated_cycles']==20
    assert second['accumulated_hotspot']['body_id']==3
    assert second['accumulated_hotspot']['integration_point']==2
    assert second['life_basis']=='last_completed_wear_block_held_fixed'
    assert len(second['blocks'])==2 and second['status']=='evaluated'


def test_unassessed_earlier_block_cannot_be_erased_by_later_success():
    mesh,case=setup()
    first=accumulate_fatigue_block(mesh,case,None,block([np.nan,1,0,0],'partial'),0)
    second=accumulate_fatigue_block(mesh,case,first,block([2,0,0,0]),1)
    assert np.isnan(second['accumulated_block_damage'][0,0])
    assert second['status']=='partial'
    assert second['failures']=={'第 1 磨损块：材料未标定':1}


def test_later_block_failure_is_retained():
    mesh,case=setup()
    first=accumulate_fatigue_block(mesh,case,None,block([1,0,0,0]),0)
    second=accumulate_fatigue_block(mesh,case,first,block([1,np.nan,0,0],'partial'),1)
    assert second['status']=='partial'
    assert second['blocks'][1]['status']=='partial'
    assert second['failures']=={'第 2 磨损块：材料未标定':1}


def test_actual_two_block_contact_preserves_identity_and_recomputes_geometry(tmp_path):
    import json,h5py
    from strength.config import DATA
    from strength.geometry import inspect_model
    from strength.store import write_json
    from forge.examples import make_contact_geometry,demonstration_material
    from forge.schemas import AnalysisCase
    from forge.worker import run
    model=DATA/'models'/'wear-blocks';model.mkdir()
    make_contact_geometry(model/'source.step');write_json(model/'source.json',{'file':'source.step','units':'m'})
    geo=inspect_model(model);sel=lambda b,d:{'kind':'direction','body_id':b,'direction':d}
    m=demonstration_material(True);m.young_modulus_Pa=2e9;m.poisson_ratio=0;m.wear_coefficient=.01;m.hardness_Pa=1e9
    case=AnalysisCase(name='双磨损块接触归档',analysis_type='contact_fatigue',material=m,mesh={'size_m':.012},
        supports=[{'name':'底面','selection':sel(2,'-z')}],history={'times_s':[0,1,2],'factors':{},'periodic':True,'stabilization_cycles':1},
        motions=[{'name':'顶面往复','body_id':1,'selection':sel(1,'+z'),'center_m':[0,0,0],'axis':[0,0,1],'angles_rad':[0,0,0],'translations_m':[[x,0,-1e-6] for x in (0,2e-5,0)]}],
        contacts=[{'name':'接触','slave':sel(1,'-z'),'master':sel(2,'+z'),'friction':0,'penalty_Pa_m':1e15,'wear':True}],
        fatigue={'method':'findley','plane_count':24},wear_blocks=2,cycles_per_wear_block=10000)
    folder=tmp_path/'study';folder.mkdir();write_json(folder/'config.json',{'model_id':geo['id'],'case':case.model_dump()})
    run(folder)
    summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    assert summary['frames']==6 and len(summary['fatigue']['blocks'])==2
    assert summary['fatigue']['accumulated_cycles']==20000
    assert summary['fatigue']['life_basis']=='last_completed_wear_block_held_fixed'
    with h5py.File(folder/'fields.h5') as f:
        assert np.max(abs(f['mesh_offset_m'][0]))==0
        assert np.max(abs(f['mesh_offset_m'][3]))>0
        p0=np.max(f['native_contact/0/contact_stresses'][:,2]);p1=np.max(f['native_contact/3/contact_stresses'][:,2])
        assert p1<p0*.99
        assert f['fatigue/accumulated_block_damage'].shape==f['fatigue/damage'].shape
        assert f['fatigue/evaluated'][:].all()
    assert json.loads((folder/'status.json').read_text(encoding='utf-8'))['status']=='completed'


def test_unsupported_wear_or_offset_is_rejected_before_submitting():
    import pytest
    from forge.schemas import AnalysisCase
    from forge.examples import demonstration_material
    contact={'name':'pair','slave':{'kind':'direction','body_id':1,'direction':'-z'},'master':{'kind':'direction','body_id':2,'direction':'+z'},'wear':True}
    m=demonstration_material(True)
    with pytest.raises(ValueError,match='周期接触研究'):
        AnalysisCase(name='no silent wear omission',analysis_type='thermo_mechanical',material=m,contacts=[contact])
    contact['wear']=False;contact['initial_offset_m']=1e-5
    with pytest.raises(ValueError,match='CAD'):
        AnalysisCase(name='no silent geometry offset',analysis_type='static',material=m,contacts=[contact])
