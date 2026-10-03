import copy
import json

import h5py
import numpy as np
import pytest

from strength.agent import strict_schema
from strength.geometry import validate_case
from strength.schemas import StressRegion
from strength.service import bind_case
from strength.stress_regions import region_result, stress_measure
from test_physics import axial_case, solve


def test_signed_tensor_projection_and_principal_invariance():
    # Pure shear: VM=sqrt(3)*tau, principal tension=tau, directional
    # normal +/-tau. These must never be silently interchanged.
    shear = np.array([0., 0., 0., 17., 0., 0.])
    assert stress_measure(shear, 'normal', [10.,10.,0.]) == pytest.approx(17.)
    assert stress_measure(shear, 'normal', [1.,-1.,0.]) == pytest.approx(-17.)
    assert stress_measure(shear, 'principal_max') == pytest.approx(17.)
    assert stress_measure([-8.,-5.,-9.,0.,0.,0.], 'principal_max') == -5.
    rng=np.random.default_rng(84)
    q,_=np.linalg.qr(rng.normal(size=(3,3)))
    tensor=q@np.diag([-90.,20.,130.])@q.T
    voigt=tensor[[0,1,2,0,0,1],[0,1,2,1,2,2]]
    assert stress_measure(voigt,'principal_max') == pytest.approx(130.)
    assert stress_measure(voigt,'normal',q[:,0]) == pytest.approx(-90.)


def test_definition_rejects_ambiguous_body_and_zero_direction():
    base={'name':'齿根选区','selection':{'kind':'faces','face_ids':[1],'body_id':1}}
    for changes in ({'measure':'normal','direction':[0,0,0]},
                    {'measure':'normal','direction':[float('nan'),0,1]},
                    {'measure':'principal_max','direction':[1,0,0]},
                    {'selection':{'kind':'faces','face_ids':[1]}}):
        with pytest.raises(ValueError):StressRegion.model_validate({**base,**changes})
    props=strict_schema()['$defs']['StressRegion']['properties']
    assert 'direction' in props and 'selection' in props


@pytest.fixture(scope='module')
def region_job(models):
    case=axial_case()
    case.stress_regions=[StressRegion(name='拉杆侧面方向正应力',selection={'body_id':1,'kind':'direction','direction':'+y'},
                                     measure='normal',direction=[4,0,0]),
                         StressRegion(name='拉杆端面主应力',selection={'body_id':1,'kind':'direction','direction':'+x'})]
    validate_case(case,models['bar'])
    case=bind_case(case,models['bar'])
    assert case.stress_regions[0].selection.face_ids
    return solve('bar',case)


def test_region_report_matches_analytic_tension_and_raw_location(region_job):
    summary,folder=region_job
    evidence=json.loads((folder/'report-evidence.json').read_text('utf-8'))
    assert evidence['evaluations']['tooth_root_bending']=='not_evaluated'
    with h5py.File(folder/'fields.h5') as f:
        for i,region in enumerate(evidence['stress_regions']):
            assert abs(region['maximum']['value_Pa']/1e7-1)<.01
            assert abs(region['minimum']['value_Pa']/1e7-1)<.01
            assert region['strength_status']=='not_evaluated'
            definition=summary['configuration']['case']['stress_regions'][i]
            actual,face_values=region_result(f,definition)
            np.testing.assert_array_equal(np.isfinite(face_values),np.isin(f['surface_ids'][:],region['face_ids']))
            peak=actual['maximum'];raw=f['stresses'][peak['element']-1,peak['integration_point']-1]
            assert peak['value_Pa']==pytest.approx(stress_measure(raw,region['measure'],region['direction']))
            assert (folder/'report-figures'/f'11-region-{i+1}.png').stat().st_size>10000
    assert '指定方向正应力' in (folder/'metrics.csv').read_text('utf-8-sig')


def test_region_refuses_unknown_faces_and_other_component(region_job):
    summary,folder=region_job
    definition=copy.deepcopy(summary['configuration']['case']['stress_regions'][0])
    with h5py.File(folder/'fields.h5') as f:
        definition['selection']['body_id']=2
        with pytest.raises(ValueError,match='其他组件'):region_result(f,definition)
        definition['selection']['body_id']=1
        definition['selection']['face_ids'].append(999999)
        with pytest.raises(ValueError,match='完整映射'):region_result(f,definition)


def test_region_selection_validation_rejects_unknown_component(models):
    case=axial_case()
    case.stress_regions=[StressRegion(name='无效区域',selection={'body_id':999,'kind':'direction','direction':'+x'})]
    with pytest.raises(ValueError,match='未知组件'):validate_case(case,models['bar'])
