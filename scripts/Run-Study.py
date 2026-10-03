"""Submit a JSON case and retain a reproducible API execution record."""
import argparse,json,time,uuid,urllib.request,urllib.error
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('case',type=Path);p.add_argument('--project',required=True);p.add_argument('--reuse');p.add_argument('--resume');p.add_argument('--record',default='study-run');args=p.parse_args()
def request(path,data=None):
    req=urllib.request.Request('http://127.0.0.1:8877/api/v1'+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=300) as response:return json.load(response)
    except urllib.error.HTTPError as exc:raise RuntimeError(exc.read().decode('utf-8',errors='replace')) from exc
payload={'case':json.loads(args.case.read_text(encoding='utf-8')),'request_id':uuid.uuid4().hex}
if args.reuse:payload['results_from_study']=args.reuse
if args.resume:payload['resume_from_study']=args.resume
study=request('/projects/'+args.project+'/studies',payload);last=None
print('Study: '+study['id'],flush=True)
record=root/'validation'/(args.record+'.json')
while True:
    study=request('/studies/'+study['id']);key=study['status'],study.get('detail')
    if key!=last:print(*key,flush=True);last=key
    record.write_text(json.dumps(study,ensure_ascii=False,indent=2),encoding='utf-8')
    if key[0] in ('completed','partial','cancelled','failed','interrupted'):break
    time.sleep(2)
if key[0]!='completed':raise SystemExit(1)
