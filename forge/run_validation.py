"""Physical-step balance and matched-output time refinement for any thermal study."""
import json
import h5py,numpy as np
from .provenance import file_hash


def publish(node):
    """Attach refinement evidence to the selected study without changing raw fields."""
    from strength.config import DATA
    from strength.store import write_json
    from .schemas import AnalysisCase
    from .results import write_report
    value=node.get('validation')
    if not value or not value.get('passed'):return
    if value.get('fine')!=node['study_id']:raise ValueError('时间步验证尚未绑定当前细步长结果；请重新比较后导出')
    if not all(file_hash(DATA/'studies'/sid/'fields.h5')==sha for sid,sha in value['raw_sha256'].items()):raise ValueError('步长验证的原始场已改变')
    root=DATA/'studies'/node['study_id']
    evidence={'config_sha256':file_hash(root/'config.json'),'summary_sha256':file_hash(root/'summary.json'),
              'raw_fields_sha256':file_hash(root/'fields.h5'),'checks':value['checks'],'scope':value['scope'],'comparison':value}
    existing=root/'verification.json'
    if existing.exists() and json.loads(existing.read_text(encoding='utf-8'))==evidence:return
    write_json(existing,evidence)
    case=AnalysisCase.model_validate(json.loads((root/'config.json').read_text(encoding='utf-8'))['case'])
    write_report(root,case,json.loads((root/'summary.json').read_text(encoding='utf-8')))


def compare(coarse,fine):
    coarse,fine=__import__('pathlib').Path(coarse),__import__('pathlib').Path(fine)
    a=json.loads((coarse/'summary.json').read_text(encoding='utf-8'));b=json.loads((fine/'summary.json').read_text(encoding='utf-8'))
    left=json.loads((coarse/'config.json').read_text(encoding='utf-8'))['case']
    right=json.loads((fine/'config.json').read_text(encoding='utf-8'))['case']
    duration=right['thermal']['duration_s'];mechanical=right['analysis_type']=='thermo_mechanical'
    has_contact=mechanical and bool(right.get('contacts'));expected_probes={p['name'] for p in right.get('clearance_probes',[])}
    if a.get('solve_failure') or b.get('solve_failure'):raise ValueError('不能使用未完成的物理时间历程进行验证')
    dt_left=left['thermal'].pop('dt_s');dt_right=right['thermal'].pop('dt_s')
    left.pop('name',None);right.pop('name',None)
    if left!=right or not np.isclose(dt_left,2*dt_right,rtol=1e-12,atol=0):raise ValueError('步长比较只允许将时间步减半，其他物理配置必须相同')
    with h5py.File(coarse/'fields.h5') as x,h5py.File(fine/'fields.h5') as y:
        if not np.array_equal(x['points'][:],y['points'][:]) or not np.array_equal(x['tets'][:],y['tets'][:]):raise ValueError('步长比较必须使用同一网格')
        tx=x['times_s'][:];ty=y['times_s'][:]
        if len(tx)!=len(ty) or not np.allclose(tx,ty,rtol=0,atol=1e-7):raise ValueError('步长比较保存时刻不一致')
        if not len(tx) or abs(tx[0])>1e-7 or abs(tx[-1]-duration)>1e-7:raise ValueError('步长比较缺少完整物理时间范围')
        T=x['temperature_C'][:];U=y['temperature_C'][:]
        if T.shape!=U.shape or not np.isfinite(T).all() or not np.isfinite(U).all():raise ValueError('温度比较缺少有限且匹配的原始场')
        thermal=float(np.max(np.abs(T-U))/max(np.max(np.abs(U-U[0])),1e-8))
        values=[('关键温升相对差',thermal,.02)]
        if mechanical:
            d=x['displacements'][:];e=y['displacements'][:]
            if d.shape!=e.shape or not np.isfinite(d).all() or not np.isfinite(e).all():raise ValueError('位移比较缺少有限且匹配的原始场')
            displacement=float(np.linalg.norm(d-e,axis=-1).max()/max(np.linalg.norm(e,axis=-1).max(),1e-12))
            values.append(('位移相对差',displacement,.02))
        if has_contact:
            def peaks(f):
                out=[]
                for i in range(len(tx)):
                    path=f'native_contact/{i}/contact_stresses'
                    if path not in f:raise ValueError('接触步长验证缺少原生接触应力')
                    v=np.asarray(f[path])
                    if v.size and (v.ndim!=2 or v.shape[1]<3 or not np.isfinite(v).all()):raise ValueError('原生接触应力无效')
                    out.append(float(v[:,2].max(initial=0)) if v.size else 0.)
                return np.array(out)
            p,q=peaks(x),peaks(y);pressure=float(np.max(np.abs(p-q))/max(q.max(),1.))
            values.append(('原生接触压力相对差',pressure,.05))
        if expected_probes:
            if not expected_probes<=set(x.get('clearance_probes',{})) or not expected_probes<=set(y.get('clearance_probes',{})):raise ValueError('步长比较缺少配置要求的装配间隙探针')
            gap=0.
            for name in expected_probes:
                left_gap=x['clearance_probes'][name]['minimum_gap_m'][:];right_gap=y['clearance_probes'][name]['minimum_gap_m'][:]
                if left_gap.shape!=right_gap.shape or not np.isfinite(left_gap).all() or not np.isfinite(right_gap).all():raise ValueError('间隙比较数据无效')
                gap=max(gap,float(np.max(np.abs(left_gap-right_gap))))
            values.append(('间隙绝对差 m',gap,1e-6))
    balance=all(s.get('thermal_balance',{}).get('passed') is True and (not mechanical or s.get('mechanical_balance',{}).get('passed') is True) for s in (a,b))
    checks=[{'label':label,'value':value,'limit':limit,'passed':bool(np.isfinite(value) and value<=limit)} for label,value,limit in values]
    checks.append({'label':'全部物理步力与能量平衡 ≤1%','passed':balance})
    return {'passed':all(c['passed'] for c in checks),'checks':checks,'coarse':coarse.name,'fine':fine.name,
            'raw_sha256':{coarse.name:file_hash(coarse/'fields.h5'),fine.name:file_hash(fine/'fields.h5')},
            'scope':'时间步比较与全局平衡；不证明整机局部接触峰值网格收敛或材料实测标定'}
