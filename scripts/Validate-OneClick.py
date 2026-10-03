"""Check a finished suite, raw provenance, MP4 streams and every ZIP member."""
import argparse,json,hashlib,zipfile,subprocess,re,time,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(root))
from strength.config import DATA
from forge.provenance import file_hash

p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--url',default='http://127.0.0.1:8877');p.add_argument('--wait',action='store_true');args=p.parse_args()
last=None
while True:
    with urllib.request.urlopen(args.url+'/api/v1/analysis-runs/'+args.run,timeout=120) as f:run=json.load(f)
    key=run['status'],run.get('detail')
    if key!=last:print(*key,flush=True);last=key
    if run['status'] in ('completed','partial','failed','cancelled','interrupted') or not args.wait:break
    time.sleep(30)
folder=DATA/'analysis-runs'/args.run;checks=[]
def check(name,passed,**details):checks.append(dict(name=name,passed=bool(passed),**details))
check('six completed studies',len(run['nodes'])==6 and all(n['status']=='completed' for n in run['nodes']))
check('six completed exports',len(run.get('exports',[]))==6 and all(e['status']=='completed' for e in run.get('exports',[])))
coupling=next(n for n in run['nodes'] if n['key']=='thermo_mechanical');check('motor transient refinement',coupling.get('validation',{}).get('passed',False),validation=coupling.get('validation'))
import h5py,numpy as np,imageio_ffmpeg
from forge.results import frame_display
for node in run['nodes']:
    if not node.get('study_id'):continue
    source=DATA/'studies'/node['study_id']
    if not (source/'fields.h5').exists():check(node['key']+' archive exists',False);continue
    manifest=json.loads((source/'raw-fields.sha256.json').read_text(encoding='utf-8'));check(node['key']+' raw checksum',file_hash(source/'fields.h5')==manifest['sha256'])
    with h5py.File(source/'fields.h5') as f:
        for i in sorted({0,len(f['times_s'])//2,len(f['times_s'])-1}):
            d=frame_display(source,i);check(node['key']+f' frame {i} time',d['time_s']==float(f['times_s'][i]))
            if 'temperature_C' in f:check(node['key']+f' frame {i} temperature',np.array_equal(np.asarray(d['fields']['temperature']),f['temperature_C'][i][np.array(d['node_ids'])-1]))
            if 'wear_history' in f:check(node['key']+f' frame {i} wear',np.array_equal(np.asarray(d['fields']['wear_depth']),f['wear_history/depth_m'][i][np.array(d['node_ids'])-1]))
        if 'wear_history' in f:
            final=sum((x['depth_m'][:] for x in f['wear'].values()),np.zeros(len(f['points'])));check('final wear equals cumulative',np.array_equal(final,f['wear_history/depth_m'][-1]))
for export in run.get('exports',[]):
    for item in export.get('files',[]):
        file=folder/item['path'];check(item['path']+' checksum',file_hash(file)==item['sha256'])
        if file.suffix=='.mp4':
            result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',str(file),'-f','null','-'],capture_output=True,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            check(item['path']+' decoded 1080p / 24fps',result.returncode==0 and '1920x1080' in result.stderr and '24 fps' in result.stderr)
archive=folder/'bundle.zip'
if archive.exists():
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('manifest.json'));bad=[]
        for item in manifest['files']:
            with z.open(item['path']) as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
            if sha!=item['sha256']:bad.append(item['path'])
        check('ZIP hashes and CRC',not bad and z.testzip() is None,files=len(manifest['files']),bad=bad)
        check('archived run terminal status',json.loads(z.read('run.json'))['status']==run['status'])
else:check('ZIP exists',False)
result={'run_id':run['id'],'passed':all(c['passed'] for c in checks),'checks':checks}
(root/'validation'/'one-click-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(checks),'passed':result['passed']},ensure_ascii=False),flush=True)
if not result['passed']:raise SystemExit(1)
