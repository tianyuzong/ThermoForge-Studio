import numpy as np
import pytest
from forge.schemas import AnalysisCase,Material
from forge.worker import evaluate_fatigue
from test_forge_numerics import material,tetra_mesh

def test_material_temperature_domain_stops_life_but_preserves_stress_assessment():
    m=material();m.valid_temperature_C=[50,100]
    case=AnalysisCase(name='超出标定温度',analysis_type='fatigue',material=m,history={'times_s':[0,1,2],'factors':{}})
    result=evaluate_fatigue(tetra_mesh(),case,np.zeros((3,1,4,6)))
    assert not result['evaluated'].any() and result['hotspot'] is None
    assert result['failures']=={'疲劳评估温度超出材料参数适用范围':4}
    with pytest.raises(ValueError,match='温度区间'):
        Material.model_validate({**m.model_dump(),'valid_temperature_C':[100,50]})

def test_findley_cycle_basis_cannot_silently_be_a_multilevel_spectrum():
    with pytest.raises(ValueError,match='完整基本周期'):
        AnalysisCase(name='周期基数',analysis_type='fatigue',material=material(),fatigue={'method':'findley'},history={'times_s':[0,1,2],'factors':{},'cycles_per_history':10})
