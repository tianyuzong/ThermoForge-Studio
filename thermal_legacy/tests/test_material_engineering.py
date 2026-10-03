import copy
import numpy as np
import pytest
from thermal_legacy.schemas import Material, PRESETS
from thermal_legacy.agent_rules import material
from thermal_legacy.agent_config_rules import update_materials, normalize
from thermal_legacy.agent_structural import update_structural
from thermal_legacy.thermoelastic import assess_design, solve_thermoelastic
from test_thermoelastic import cube,MAT


def test_all_presets_are_valid_and_nonmetals_are_not_metals():
    assert len(PRESETS)==12
    for row in PRESETS:Material.model_validate(row)
    for name in ('POM','尼龙','PC','PEEK','PVDF'):
        m=material('基础材料使用'+name)
        assert m['category']=='polymer' and m['data_source'].startswith('https://')
        assert m['young_modulus_Pa']>0 and '泊松比' in m['property_notes']
    assert material('氧化铝陶瓷')['category']=='ceramic'
    assert material('铝')['category']=='metal'
    assert material('硼硅玻璃')['strength_criterion']=='principal'


def test_agent_scoped_material_limits_and_strength_do_not_change_design_limits():
    cfg=dict(base_material=copy.deepcopy(PRESETS[0]),component_materials=[],regions=[],design_limits={'maximum_C':90})
    model={'components':[{'component_id':0}]};questions=[]
    prompt=normalize('基础材料使用PC，物性有效最低温度负10摄氏度，物性有效最高温度60摄氏度，材料使用温度上限110摄氏度，抗拉强度70MPa，抗压强度90MPa。组件1材料使用硼硅玻璃，抗拉强度20MPa，强度判据主应力。')
    update_materials(prompt,cfg,model,questions);update_structural(None,'test',prompt,cfg,questions)
    assert not questions
    assert cfg['base_material']['valid_min_C']==-10
    assert cfg['base_material']['valid_max_C']==60
    assert cfg['base_material']['service_max_C']==110
    assert cfg['base_material']['tensile_strength_Pa']==70e6
    assert cfg['component_materials'][0]['material']['tensile_strength_Pa']==20e6
    assert cfg['design_limits']=={'maximum_C':90}


def test_brittle_hydrostatic_tension_is_detected_even_when_von_mises_is_zero():
    mesh=cube();n=len(mesh['points']);labels=np.zeros(len(mesh['tets']),int)
    m={**MAT,'name':'glass','category':'glass','strength_criterion':'principal','tensile_strength_Pa':10e6,'compressive_strength_Pa':400e6}
    T=np.asarray([np.full(n,20.),np.full(n,-30.)])
    structural=solve_thermoelastic(mesh,T,[m],labels,dict(mode='constrained',reference_C=20),np.arange(n*3))
    a=assess_design({},mesh,T,[0,100],structural,[m],labels)
    tensile=next(c for c in a['checks'] if '主拉应力' in c['name'])
    assert tensile['value']==pytest.approx(300e6)
    assert tensile['status']=='exceeded' and tensile['time_s']==100
    assert structural['von_mises'].max()<1e-3


def test_polymer_tg_is_review_not_automatic_failure_and_validity_is_separate():
    mesh=cube();labels=np.zeros(len(mesh['tets']),int);m=copy.deepcopy(PRESETS[5])
    T=np.full((2,len(mesh['points'])),25.)
    a=assess_design({},mesh,T,[0,10],None,[m],labels)
    assert not any(c['status']=='exceeded' for c in a['checks'])
    assert next(c for c in a['checks'] if '玻璃化' in c['name'])['status']=='review_required'
    m['valid_max_C']=20
    a=assess_design({},mesh,T,[0,10],None,[m],labels)
    assert next(c for c in a['checks'] if '物性温区上限' in c['name'])['status']=='review_required'
    m['service_max_C']=20
    a=assess_design({},mesh,T,[0,10],None,[m],labels)
    assert a['status']=='exceeded'


def test_invalid_material_range_and_ceramic_mises_rejected():
    for overrides in (dict(valid_min_C=100,valid_max_C=20),dict(category='ceramic',strength_criterion='von_mises')):
        with pytest.raises(ValueError):Material.model_validate({**PRESETS[0],**overrides})
