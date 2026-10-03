"""Read saved raw fields for opening/closure and native-restart regression."""
import json,re
from pathlib import Path
import h5py,numpy as np
root=Path(__file__).resolve().parents[1]
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def completed(name):
    path=root/'validation'/(name+'.json')
    if not path.exists():return None
    r=read(path)
    return root/'data'/'studies'/r['id'] if r['status']=='completed' else None
def save(name,value):
    (root/'validation'/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    print(name,'passed:',value['passed'])
folder=completed('thermal-gap-heating-cooling')
if folder:
    summary=read(folder/'summary.json');pressures=[];gaps=[]
    with h5py.File(folder/'fields.h5') as f:
        times=f['times_s'][:].tolist()
        for i in range(len(times)):
            native=f['native_contact'][str(i)]['contact_stresses'][:]
            pressures.append(float(native[:,2].max(initial=0)))
            gaps.append(min(float(p['gap_m'][:].min()) for p in f['contact'][str(i)].values()))
    result={'source_study':folder.name,'times_s':times,'native_peak_pressure_Pa':pressures,'minimum_gap_m':gaps,
            'opened_initially':gaps[0]>1e-6,'closed_during_heating':max(pressures)>1e6,
            'reopened_after_cooling':gaps[-1]>1e-6 and pressures[-1]<1e3,
            'mechanical_balance_passed':summary['mechanical_balance']['passed'],'thermal_balance_passed':summary['thermal_balance']['passed']}
    result['passed']=all(result[k] for k in ('opened_initially','closed_during_heating','reopened_after_cooling','mechanical_balance_passed','thermal_balance_passed'))
    save('thermal-opening-closure',result)
old=completed('thermal-gap-step-half');new=completed('thermal-gap-restart-regression')
if old and new:
    result={'reference_study':old.name,'restart_study':new.name,'fields':{}}
    with h5py.File(old/'fields.h5') as a,h5py.File(new/'fields.h5') as b:
        result['same_mesh_and_times']=all(np.array_equal(a[k][:],b[k][:]) for k in ('points','tets','times_s'))
        for key,absolute,relative in [('temperature_C',.01,None),('displacements',None,.001),('stresses',None,.001)]:
            error=max(float(np.max(abs(x-y))) for x,y in zip(a[key],b[key]));scale=max(float(np.max(abs(x))) for x in a[key])
            limit=absolute if absolute is not None else relative*max(scale,1e-12)
            result['fields'][key]={'maximum_absolute_difference':error,'absolute_limit':limit,'passed':error<=limit}
    for label,path in [('replay',old),('restart',new)]:
        seconds=[]
        for log in (path/'coupling').rglob('solver.log'):
            matches=re.findall(r'Total CalculiX Time:\s*([0-9.]+)',log.read_text(errors='replace'))
            if matches:seconds.append(float(matches[-1]))
        result[label+'_native_solver_time_s']=sum(seconds);result[label+'_native_stages']=len(seconds)
    result['passed']=result['same_mesh_and_times'] and all(v['passed'] for v in result['fields'].values())
    save('thermal-native-restart-comparison',result)
