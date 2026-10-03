"""Create a persistent analytical bar example with v2 queries and comparison curves."""
import json,time,uuid,sys
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from strength.schemas import LoadCase,DEMO_MATERIAL

root=Path(__file__).resolve().parents[1]
client=httpx.Client(base_url='http://127.0.0.1:8876/api/v1',timeout=120,trust_env=False)
def post(url,data=None):
    r=client.post(url,json=data or {});r.raise_for_status();return r.json()
p=post('/projects',{'name':'标准拉杆 · 结果与曲线验证'});post(f'/projects/{p["id"]}/demo?kind=bar')
record={'project_id':p['id'],'jobs':[]}
for force in (1000,1500):
    case=LoadCase.model_validate({'name':f'标准拉杆 {force} N','material':DEMO_MATERIAL,
        'supports':[{'selection':{'kind':'direction','direction':'-'+a},'components':[a]} for a in 'xyz'],
        'loads':[{'name':'轴向拉力','kind':'force','selection':{'kind':'direction','direction':'+x'},'vector':[force,0,0]}],
        'mesh':{'size_m':.005},'moment_origin_m':[0,0,0],
        'result_requests':[
            {'name':'中点位移','kind':'probe','field':'displacement_x','point_m':[.05,.005,.005]},
            {'name':'轴向应力路径','kind':'path','field':'stress_xx','point_m':[.01,.005,.005],'end_m':[.09,.005,.005],'samples':11},
            {'name':'两端相对位移','kind':'relative','point_m':[0,.005,.005],'end_m':[.1,.005,.005]},
            {'name':'参考原点反力矩','kind':'reactions','moment_origin_m':[0,0,0]},
            {'name':'轴向刚度','kind':'stiffness','field':'displacement_x','point_m':[.1,.005,.005],'direction':[1,0,0],'load_name':'轴向拉力'}]})
    t=post(f'/projects/{p["id"]}/jobs',{'case':case.model_dump(),'request_id':uuid.uuid4().hex})
    for _ in range(180):
        t=client.get('/agent/tasks/'+t['id']).json()
        if t['status'] in ('completed','failed','partial','cancelled'):break
        time.sleep(1)
    assert t['status']=='completed',t.get('detail')
    j=t['jobs'][0];s=j['summary'];exact=force*.1/(1e-4*210e9)
    axial=next(q['displacement_m'] for q in s['result_queries'] if q['kind']=='stiffness')
    errors={'axial_displacement':abs(axial/exact-1),'stress':abs(s['maximum_von_mises_Pa']/(force/1e-4)-1),'energy':abs(s['strain_energy_J']/(force*exact/2)-1)}
    assert max(errors.values())<.01 and s['balance']['passed']
    assert all(q['status']=='ok' for q in s['result_queries'])
    record['jobs'].append({'id':j['id'],'load_N':force,'errors':errors,'summary':s})
    print(force,'N PASSED',flush=True)
comparison=client.get(f'/projects/{p["id"]}/results/comparison').json()
assert len(comparison['load_displacement_curves'])==1 and len(comparison['envelopes'])==39
record['comparison']=comparison;record['passed']=True
(root/'.runtime/acceptance-results.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
