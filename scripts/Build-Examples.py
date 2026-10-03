"""Create portable, explicitly synthetic contact and coupling example projects."""
import sys,json,shutil
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import numpy as np
from strength import store
from strength.config import DATA
from strength.geometry import inspect_model
from forge.examples import demonstration_material
from forge.benchmark_geometry import line_contact,thermal_gap,involute_gears
from forge.schemas import AnalysisCase

sel=lambda b,d:dict(kind='direction',body_id=b,direction=d)
for kind in ['local-bearing','thermal-gap','gears']:
    folder=root/'examples'/kind;folder.mkdir(exist_ok=True)
    step=folder/'source.step'
    if not step.exists():
        if kind=='local-bearing':line_contact(step)
        elif kind=='thermal-gap':thermal_gap(step)
        else:store.write_json(folder/'geometry-parameters.json',involute_gears(step))
    mid='example-'+kind;model=DATA/'models'/mid;model.mkdir(exist_ok=True)
    shutil.copy2(step,model/'source.step');store.write_json(model/'source.json',{'file':'source.step','units':'m'})
    geo=inspect_model(model);store.put('model',geo)
    m=demonstration_material(True)
    if kind=='thermal-gap':
        case=AnalysisCase(name='热致闭合 · 10 μm 初始空气间隙',analysis_type='thermo_mechanical',material=m,
            mesh={'size_m':.008},component_materials={'1':m,'2':m},
            supports=[{'name':'底面固定','selection':sel(2,'-z')},{'name':'上端固定','selection':sel(1,'+z')}],
            thermal={'initial_C':25,'reference_C':25,'dt_s':.15625,'duration_s':40,'save_s':5,'boundaries':[
                {'name':'上部升温','kind':'temperature','selection':sel(1,'+z'),'value':100},
                {'name':'下部恒温','kind':'temperature','selection':sel(2,'-z'),'value':25}]},
            contacts=[{'name':'受热开闭界面','slave':sel(1,'-z'),'master':sel(2,'+z'),'thermal':True,'penalty_Pa_m':1e15,
                'conductance':[{'pressure_Pa':0,'conductance_W_m2K':26000},{'pressure_Pa':1e8,'conductance_W_m2K':36000}],
                'gap_conductivity_W_mK':.026,'search_distance_m':.001}],
            assumptions=['独立双块体数值示例：真实 CAD 初始间隙10μm；上、下端固定，温升使界面闭合。','热导表、空气导热与材料均为演示假设，未经过实物标定。'])
    elif kind=='local-bearing':
        m.young_modulus_Pa=2e9;m.name='滚动接触数值验证材料（E=2 GPa，非真实钢材）'
        curve={'points':[{'x':1e3,'y':12e6},{'x':1e5,'y':6e6},{'x':1e7,'y':3e6},{'x':1e9,'y':1.5e6}],
            'endurance_limit_Pa':1.5e6,'source':'滚动接触软件流程专用合成 Findley 曲线，非真实轴承试验数据','demonstration':True}
        m.findley.curve=type(m.findley.curve).model_validate(curve)
        times=np.linspace(0,.1,65);angles=.02*np.sin(2*np.pi*times/.1)
        case=AnalysisCase(name='局部滚动体—滚道 · 往复滚动周期',analysis_type='contact_fatigue',material=m,component_materials={'1':m,'2':m},
            mesh={'size_m':.0004,'regions':[{'center_m':[0,0,.005],'radius_m':.00025,'size_m':.00003,'transition_m':.0006}]},
            supports=[{'name':'滚道底面','selection':sel(2,'-z')},*[{'name':str(b)+d,'selection':sel(b,d),'components':['y']} for b in [1,2] for d in ['-y','+y']]],
            history={'times_s':times.tolist(),'factors':{},'periodic':True,'stabilization_cycles':1},fatigue={'method':'findley','plane_count':162,'target_cycles':1e6},
            motions=[{'name':'往复滚动','body_id':1,'selection':sel(1,'+z'),'center_m':[0,0,.007],'axis':[0,1,0],
                'angles_rad':angles.tolist(),'translations_m':[[.002*float(a),0,-.000005] for a in angles],'components':['x','z']}],
            contacts=[{'name':'滚动体／滚道','slave':{'kind':'faces','body_id':1,'face_ids':[s['id'] for s in geo['surfaces'] if 1 in s['body_ids'] and s['type']=='Cylinder']},
                'master':sel(2,'+z'),'penalty_Pa_m':2e14,'friction':0,'wear':True}],
            assumptions=['独立的薄截面滚动体和滚道，不使用电机等效轴承环。','平面应变截面；滚动体顶部按半径2mm、转角±0.02rad往复，压入5μm，时间轴0.1s。','无摩擦法向接触；磨损由计算得到的相对滑移与演示 Archard 系数评估。名义滚动不等同于所有弹性接触点都无滑移。','E=2GPa和合成滚动接触曲线仅用于可运行软件示例；必须替换成实物标定材料才能评价轴承。'])
    else:
        m.young_modulus_Pa=2e9;m.name='齿轮接触数值验证材料（E=2 GPa，非真实钢材）';times=np.linspace(0,6,361)
        def bore(b):return {'kind':'faces','body_id':b,'face_ids':[s['id'] for s in geo['surfaces'] if b in s['body_ids'] and s['type']=='Cylinder' and abs(s['bounds_m'][1][0]-s['bounds_m'][0][0]-.006)<1e-7]}
        def flank(b):
            center=np.array([-.01 if b==1 else .01,0])
            return {'kind':'faces','body_id':b,'face_ids':[s['id'] for s in geo['surfaces'] if b in s['body_ids'] and s['bounds_m'][1][2]-s['bounds_m'][0][2]>.0019 and np.linalg.norm(np.array(s['centroid_m'][:2])-center)>.008]}
        case=AnalysisCase(name='20° 标准渐开线齿轮 · 20:20 完整转动周期',analysis_type='contact_cycle',material=m,component_materials={'1':m,'2':m},
            mesh={'size_m':.001},history={'times_s':times.tolist(),'factors':{},'periodic':True},
            axles=[{'name':'驱动轴','body_id':1,'selection':bore(1),'center_m':[-.01,0,0],'axis':'z','angles_rad':np.linspace(0,2*np.pi,361).tolist()},
                {'name':'从动轴','body_id':2,'selection':bore(2),'center_m':[.01,0,0],'axis':'z','torque_Nm':.01}],
            contacts=[{'name':'齿面啮合','slave':flank(1),'master':flank(2),'friction':0,'penalty_Pa_m':2e14}],
            assumptions=['20齿、模数1mm、压力角20°、齿宽2mm，标准渐开线基准几何；未加入制造齿根圆角。','驱动轴6秒转一圈；从动轴旋转自由，施加+0.01Nm阻力矩；理论转角比−1。','几何中驱动轮预置0.0003rad以建立初始齿面接触，此微小装配干涉明确保存在生成参数中。','E=2GPa仅用于接触算法数值示例，不代表真实钢制齿轮。','默认网格用于流程演示，接触压力、齿根峰值和寿命需要独立加密验证；不把固定位置 GPU 齿轮结果用于周期寿命。'])
    if kind=='gears':
        full=case.model_copy(deep=True)
        full.name+=' · 尚未通过完整一周验收'
        full.assumptions.append('完整一周工况仍需独立细化和验收；不能依据18°单齿节基准通过而认定此工况通过。')
        full.geometry_sha256=geo['source_sha256']
        store.write_json(folder/'full-revolution-unverified.json',full.model_dump())
        case.name='20° 标准渐开线齿轮 · 18° 单齿节啮合验证'
        case.history.times_s=np.linspace(0,.3,73).tolist()
        case.history.periodic=False
        case.axles[0].angles_rad=np.linspace(0,np.pi/10,73).tolist()
        case.assumptions[1]='驱动轴0.3秒转18°；从动轴旋转自由，施加+0.01Nm阻力矩；理论转角比−1。此单齿节不是材料点完整一周疲劳历史。'
    from forge.api import validate
    validate(case,geo)
    case.geometry_sha256=geo['source_sha256'];store.write_json(folder/'case.json',case.model_dump())
    project={'id':mid,'name':case.name,'model_id':mid,'example_family':kind,'example_case':case.model_dump(),'preferred_analysis_type':case.analysis_type,'created_at':store.now()}
    store.put('project',project);print(kind,mid,len(geo['surfaces']),flush=True)
