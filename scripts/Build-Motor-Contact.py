"""Build the distinct motor bearing-seat contact study without changing legacy cases."""
import sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from forge.schemas import AnalysisCase
from strength.store import write_json

def probes():
    selection=lambda b,f:dict(kind='faces',body_id=b,face_ids=f)
    return [dict(name='转子与定子径向气隙',inner=selection(10,[589]),outer=selection(9,list(range(508,544))),nominal_gap_m=.001,search_distance_m=.003,source='原 STEP 名义半径44mm与45mm；按初始法向插值小位移间隙变化'),
        *[dict(name=name,inner=selection(b,[f]),outer=selection(c,[g]),nominal_gap_m=0,search_distance_m=.001,source='原 STEP 轴承外圈和端盖同名义半径31mm；小位移配合探针') for name,b,f,c,g in [('前轴承座配合',6,340,4,292),('后轴承座配合',7,344,5,338)]]]

if __name__=='__main__':
    folder=root/'examples'/'motor-unified'
    case=AnalysisCase.model_validate_json((folder/'thermo_mechanical.json').read_text(encoding='utf-8'))
    case.name='电机 · 轴承座接触与双向热结构稳态'
    for body_id,m in case.component_materials.items():
        m.name='演示铸铁参数（非实测）' if int(body_id)<=5 else '演示钢材参数（非实测）'
    case.coupling.transient=False
    case.clearance_probes=probes()
    outer=[c for c in case.bonded_connections if c.slave.body_id in (6,7)]
    case.bonded_connections=[c for c in case.bonded_connections if c.slave.body_id not in (6,7)]
    case.thermal_connections=[c for c in case.thermal_connections if c.slave.body_id not in (6,7)]
    case.contacts=[dict(name='前轴承座' if c.slave.body_id==6 else '后轴承座',slave=c.slave.model_dump(),master=c.master.model_dump(),
        friction=.15,penalty_Pa_m=3e14,tangential_stiffness_Pa_m=1e13,thermal=True,search_distance_m=.002,
        conductance=[{'pressure_Pa':0,'conductance_W_m2K':2000},{'pressure_Pa':1e8,'conductance_W_m2K':12000}],gap_conductivity_W_mK=.026) for c in outer]
    case.supports.append(dict(name='轴端推力定位',selection={'kind':'faces','body_id':8,'face_ids':[361]},components=['x']))
    case.mesh.regions=[dict(center_m=[x,0,0],radius_m=.036,size_m=.006,transition_m=.025) for x in (-.08,.15)]
    case.resources.max_dofs=600000
    case.assumptions+=['该独立工况将两个轴承外圈与端盖的粘结改为摩擦可开闭接触，其余机械连接保持理想粘结。',
        '轴端仅作轴向推力定位；轴承座摩擦系数0.15、接触罚刚度和压力相关热导表为公开的演示假设。',
        '局部6mm网格仅用于整机流程演示，接触压力与配合应力尚需局部收敛验证；等效环不能给出滚动轴承寿命。']
    case=AnalysisCase.model_validate(case.model_dump(warnings=False))
    write_json(folder/'thermo-contact.json',case.model_dump())
    print('Created thermo-contact.json')
