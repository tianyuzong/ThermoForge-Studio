"""Attest source/dist/runtime versions without packaging local models or credentials."""
import argparse,hashlib,json,platform,sys,importlib.metadata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge.version import VERSION

SOURCE_DIRECTORIES=('forge','strength','thermal_legacy','integrations','scripts')
FRONTEND_CONFIGS=('package.json','package-lock.json','vite.config.ts','tsconfig.json','index.html')
RUNTIME_PACKAGES=('numpy','scipy','h5py','gmsh','fastapi','pydantic','reportlab','matplotlib','Pillow','playwright','imageio-ffmpeg')


def checksum(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def collect_frontend():
    return {p.relative_to(ROOT).as_posix():checksum(p) for part in ('src','public') for p in sorted((ROOT/'frontend'/part).rglob('*')) if p.is_file()}|{
        'frontend/'+name:checksum(ROOT/'frontend'/name) for name in FRONTEND_CONFIGS}


def collect_dist():
    files={p.relative_to(ROOT).as_posix():checksum(p) for p in sorted((ROOT/'frontend'/'dist').rglob('*')) if p.is_file()}
    if 'frontend/dist/index.html' not in files or not any(name.endswith('.js') for name in files):raise ValueError('Frontend distribution is missing index.html or JavaScript; build before generating the manifest')
    return files


def collect_sources():
    return {p.relative_to(ROOT).as_posix():checksum(p) for part in SOURCE_DIRECTORIES for p in sorted((ROOT/part).rglob('*.py'))
            if p.is_file() and not set(p.relative_to(ROOT).parts)&{'__pycache__','.venv','.runtime','data','node_modules'}}


def manifest():
    sources=collect_sources()
    packages={name:importlib.metadata.version(name) for name in RUNTIME_PACKAGES}
    return {'product':'ThermoForge Studio','version':VERSION,'python':sys.version.split()[0],'platform':platform.platform(),
        'validated_runtime':'Windows x64 / Python 3.14 CPU','frontend_sources':collect_frontend(),
        'frontend_dist':collect_dist(),
        'solver_sources':sources,'packages':packages,'calculix_sha256':checksum(ROOT/'vendor'/'ccx.exe') if (ROOT/'vendor'/'ccx.exe').is_file() else None}


def verify(path=None):
    path=path or ROOT/'BUILD-MANIFEST.json';saved=json.loads(path.read_text(encoding='utf-8'))
    current=manifest()
    for key in ('product','version','python','platform','frontend_sources','frontend_dist','solver_sources','packages','calculix_sha256'):
        if saved.get(key)!=current[key]:raise ValueError('Build manifest mismatch: '+key+'; rebuild affected outputs and regenerate the manifest')
    return saved


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    if args.verify:verify();print('Build manifest verified')
    else:
        value=manifest();(ROOT/'BUILD-MANIFEST.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');print('Build manifest recorded: '+value['version'])
