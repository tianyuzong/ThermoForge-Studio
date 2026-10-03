"""Opt-in service interruption check. Stops/restarts the managed local server."""
import json,time,uuid,subprocess
from pathlib import Path
import httpx
root=Path(__file__).resolve().parents[1]
c=httpx.Client(base_url='http://127.0.0.1:8876/api/v1',timeout=120,trust_env=False)
def post(url,data=None):
 r=c.post(url,json=data or {});r.raise_for_status();return r.json()
r=json.loads((root/'.runtime/acceptance-results.json').read_text('utf-8'))
case=r['jobs'][0]['summary']['configuration']['case'];case['name']='服务中断恢复验证';case['mesh']['size_m']=.0015;case['mesh']['convergence']=True
p=post('/projects',{'name':'服务中断恢复验证'});post(f'/projects/{p["id"]}/demo?kind=bar')
t=post(f'/projects/{p["id"]}/jobs',{'case':case,'request_id':uuid.uuid4().hex})
for _ in range(500):
 t=c.get('/agent/tasks/'+t['id']).json()
 if t['status'] in ('meshing','solving'):break
 time.sleep(.02)
assert t['status'] in ('meshing','solving'),t['status']
record={'task_id':t['id'],'project_id':p['id'],'before_status':t['status'],'job_ids':t['job_ids']}
for script in ('Stop.ps1','Start.ps1'):
 command=['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/script)]+(['-NoBrowser'] if script=='Start.ps1' else [])
 subprocess.run(command,cwd=root,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
t=c.get('/agent/tasks/'+t['id']).json()
assert t['status']=='interrupted' and all(j['status']=='interrupted' for j in t['jobs'])
assert all((root/'data/jobs'/j['id']/'config.json').exists() for j in t['jobs'])
record.update(after_status=t['status'],passed=True)
(root/'.runtime/acceptance-restart.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print('RESTART AND IMMUTABLE CONFIG PRESERVATION PASSED',flush=True)
