"""Submit a packaged case to the running workbench's shared queue."""
import json,sys,time,uuid,urllib.request,urllib.error
from pathlib import Path
root=Path(__file__).resolve().parents[1]
kind=sys.argv[1] if len(sys.argv)>1 else 'thermal-gap'
if kind not in ('thermal-gap','local-bearing','gears'):raise SystemExit('Example must be thermal-gap, local-bearing, or gears')
def request(path,data=None):
    req=urllib.request.Request('http://127.0.0.1:8877/api/v1'+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
    except urllib.error.HTTPError as exc:raise RuntimeError(exc.read().decode('utf-8',errors='replace')) from exc
project=request('/examples/benchmark/'+kind,{})
case=project['example_case'];study=request('/projects/'+project['id']+'/studies',{'case':case,'request_id':uuid.uuid4().hex})
print(json.dumps({'project_id':project['id'],'study_id':study['id'],'name':case['name']},ensure_ascii=False),flush=True)
record=root/'validation'/(kind+'-run.json');last=None
while True:
    state=request('/studies/'+study['id']);status=state['status'];key=(status,state.get('detail'))
    if key!=last:print(*key,flush=True);last=key
    if status in ('completed','partial','cancelled','failed','interrupted'):
        record.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');break
    time.sleep(2)
if status!='completed':raise SystemExit(1)
