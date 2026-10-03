"""Portable source release with examples and compact verification evidence."""
import argparse,hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DIRECTORIES={'strength','forge','thermal_legacy','tests','docs','examples','scripts','integrations','skills'}
BLOCKED={'__pycache__','.git','.venv','.runtime','node_modules','.pytest_cache','.visual-backups','test-results','playwright-report'}
PRIVATE={'local-settings.json','local-connection.json','GEAR_CONTACT_GEOMETRY.json','GEAR_STUDY_TASK.json'}
ROOT_JSON={'.mcp.json','local-settings.example.json','SOURCE_VERSIONS.json','BUILD-MANIFEST.json'}
FRONTEND_ROOT={'package.json','package-lock.json','index.html','tsconfig.json','vite.config.ts','THIRD_PARTY_LICENSES.txt'}
def files(root):
    for p in root.rglob('*'):
        rel=p.relative_to(root)
        if not p.is_file() or p.is_symlink() or set(rel.parts)&BLOCKED or p.name.lower() in {name.lower() for name in PRIVATE} or p.name.lower().startswith('.env') or p.suffix.lower() in ('.pyc','.pem','.key','.pfx','.p12','.exe','.dll','.zip','.tsbuildinfo'):continue
        if not p.resolve().is_relative_to(root.resolve()):raise ValueError('Release source escaped its root')
        allow=rel.parts[0] in DIRECTORIES
        if rel.parts[0]=='thermal_legacy' and set(rel.parts)&{'data','jobs','models'}:allow=False
        if len(rel.parts)==1:allow=p.suffix in ('.md','.txt','.lock','.ps1','.bat') or p.name in ROOT_JSON|{'.gitignore','.gitattributes'}
        if rel.parts[0]=='frontend':allow=(len(rel.parts)==2 and p.name in FRONTEND_ROOT) or rel.parts[1] in ('src','public','dist','tests')
        if rel.parts[0]=='vendor':allow=p.suffix in ('.txt','.md')
        if rel.parts[0]=='validation':allow=len(rel.parts)==2 and p.suffix in ('.json','.xml','.log','.png','.pdf','.csv') or p.name in ('benchmark.json','case.json')
        if allow:yield p
def package(destination):
    from build_manifest import verify
    build=verify()
    selected=sorted(files(ROOT));hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in selected}
    manifest={'product':'ThermoForge Studio','version':build['version'],'source_versions':json.loads((ROOT/'SOURCE_VERSIONS.json').read_text()),'runtime':{k:build[k] for k in ('python','platform','packages','calculix_sha256')},'sha256':hashes}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as out:
        for p in selected:out.write(p,'ThermoForge-Studio/'+p.relative_to(ROOT).as_posix())
        out.writestr('ThermoForge-Studio/RELEASE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(destination) as out:
        if out.testzip():raise ValueError('Release ZIP verification failed')
    return {'file':destination.name,'files':len(selected),'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT.parent/'ThermoForge-Studio-0.8.0-source.zip');args=parser.parse_args()
    result=package(args.output.resolve());args.output.with_suffix('.sha256.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
