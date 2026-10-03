"""Deterministic synthetic benchmarks, always labelled separately from real materials."""
import numpy as np
from strength.geometry import session
import gmsh
from .schemas import Material,AnalysisCase


def demonstration_material(contact=False):
    curve={'points':[{'x':1e3,'y':600e6},{'x':1e5,'y':300e6},{'x':1e7,'y':150e6},{'x':1e9,'y':75e6}],
           'endurance_limit_Pa':75e6,'source':'软件验证合成曲线；不代表该电机实测材料','demonstration':True}
    return Material(name='演示钢材（合成疲劳数据）',young_modulus_Pa=210e9,poisson_ratio=.3,density_kg_m3=7850,
        yield_strength_Pa=1600e6 if contact else 600e6,tensile_strength_Pa=2000e6 if contact else 900e6,
        conductivity_W_mK=45,specific_heat_J_kgK=470,thermal_expansion_per_K=12e-6,
        sn_curve=curve,findley={'k':.2,'curve':curve,'calibration':'rolling_contact' if contact else 'bulk'},
        hardness_Pa=7e9,wear_coefficient=1e-8,source='软件验证演示假设，非实物牌号和认证数据')


def make_contact_geometry(path,kind='blocks'):
    with session():
        occ=gmsh.model.occ
        if kind=='blocks':
            occ.addBox(-.01,-.01,.01,.02,.02,.01)
            occ.addBox(-.01,-.01,0,.02,.02,.01)
        elif kind=='bearing':
            # A spherical rolling element on a flat local raceway specimen.
            # This is a Hertz benchmark, not a reconstructed production bearing.
            occ.addSphere(0,0,.007,.002)
            occ.addBox(-.006,-.004,0,.012,.008,.005)
        elif kind=='hertz':
            occ.addSphere(0,0,.007,.002,angle2=0)
            occ.addBox(-.004,-.004,0,.008,.008,.005)
        elif kind=='cylinder':
            occ.addCylinder(0,-.004,.007,0,.008,0,.002)
            occ.addBox(-.006,-.004,0,.012,.008,.005)
        else:raise ValueError('未知接触基准')
        occ.synchronize();gmsh.write(str(path))


def default_case(geo,kind='thermal_steady',current=None):
    material=demonstration_material();components=geo.get('components') or [{'id':1}]
    allfaces=[s['id'] for s in geo['surfaces']]
    case=AnalysisCase(name={'thermal_steady':'稳态温度','thermal_transient':'瞬态温度','fatigue':'高周疲劳','thermo_mechanical':'热结构装配','contact_cycle':'接触周期','contact_fatigue':'接触疲劳'}.get(kind,'结构静力'),
        analysis_type='thermal_steady',geometry_sha256=geo['source_sha256'],material=material,
        component_materials={str(c['id']):material for c in components},mesh={'size_m':geo['default_mesh_size_m']},
        assumptions=['材料及默认边界为软件演示参数，须按实际工作条件修改'])
    cfg=case.model_dump();cfg['analysis_type']=kind
    cfg['thermal']['boundaries']=[{'name':'外表面对流','kind':'convection','selection':{'kind':'faces','face_ids':allfaces},'value':10,'ambient_C':25},
        {'name':'组件发热','kind':'body_power','body_id':components[0]['id'],'value':10}]
    if current:
        for key in ('supports','loads','bonded_connections','mesh'):cfg[key]=current.get(key,cfg.get(key,[]))
        # Explicitly retained mechanical properties, enriched with tagged demonstration heat/fatigue data.
        for body in components:
            old=current.get('component_materials',{}).get(str(body['id']),current.get('material',{}))
            for key in ('young_modulus_Pa','poisson_ratio','density_kg_m3'):
                if key in old:cfg['component_materials'][str(body['id'])][key]=old[key]
    if kind in ('fatigue','contact_cycle','contact_fatigue'):
        cfg['history']={'times_s':[0,.025,.05,.075,.1], 'factors':{l['name']:[0,.5,1,.5,0] for l in cfg['loads']},'periodic':True,'repetitions':1}
    return cfg
