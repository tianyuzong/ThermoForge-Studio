from pathlib import Path
import sys, json, os
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.runtime/pythonlib'))
DATA = Path(os.environ.get('THERMAL_DATA', str(ROOT.parent / 'data' / 'legacy-thermal'))).resolve()
MODELS = DATA / 'models'
JOBS = DATA / 'jobs'
for path in (MODELS, JOBS): path.mkdir(parents=True, exist_ok=True)

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(',',':'), allow_nan=False), encoding='utf-8')
    os.replace(tmp, path)

def progress(folder, phase, percent, detail='', **extra):
    write_json(folder/'status.json', dict(phase=phase, progress=percent, detail=detail, **extra))
