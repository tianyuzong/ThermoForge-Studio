"""Reproduce or inspect the original motor's same-mesh 0.1% static regression."""
import argparse,io,json,time,uuid,urllib.request
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8877');p.add_argument('--existing-study');args=p.parse_args()
def request(path,data=None,binary=False):
    req=urllib.request.Request(args.url+'/api/v1'+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=300) as f:return f.read() if binary else json.load(f)
if args.existing_study:sid=args.existing_study
else:
    project=request('/examples/motor',{})
    case=json.loads((root/'examples/motor-unified/static.json').read_text(encoding='utf-8'))
    sid=request('/projects/'+project['id']+'/studies',{'case':case,'request_id':uuid.uuid4().hex})['id']
last=None
while True:
    study=request('/studies/'+sid);state=study['status'],study.get('detail')
    if state!=last:print(*state,flush=True);last=state
    if state[0] in ('completed','partial','failed','cancelled','interrupted'):break
    time.sleep(2)
if study['status']!='completed':raise SystemExit('Motor calculation did not complete')
reference=root/'examples/motor-unified/reference';original=json.loads((reference/'summary.json').read_text(encoding='utf-8'))
new=dict(np.load(io.BytesIO(request('/studies/'+sid+'/files/mesh.npz',binary=True))));old=dict(np.load(reference/'mesh.npz'))
same_mesh=all(np.array_equal(new[k],old[k]) for k in ('points','tets','body_ids'))
changes={key:abs(study['summary'][key]/original[key]-1) for key in ('maximum_displacement_m','maximum_von_mises_Pa')}
result={'study_id':sid,'reference_job':json.loads((root/'examples/motor-unified/provenance.json').read_text(encoding='utf-8'))['legacy_job_id'],
    'identical_mesh':same_mesh,'relative_changes':changes,'limit':.001,'passed':same_mesh and max(changes.values())<=.001}
(root/'validation/motor-static-reproduction.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
if not result['passed']:raise SystemExit(1)
