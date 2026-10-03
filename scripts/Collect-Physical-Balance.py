"""Audit every committed thermal/mechanical step from saved native DAT files.

This performs postprocessing only; it never reruns or changes a solver stage.
"""
import argparse,hashlib,json,sys
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from strength import store
from forge.schemas import AnalysisCase
from forge.mechanics import parse_history,balance,constraints
from forge.loads import assemble_loads
from forge.axles import annotate

parser=argparse.ArgumentParser();parser.add_argument('records',nargs='+');args=parser.parse_args()
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
output=root/'validation'/'thermal-physical-balance.json'
result=read(output) if output.exists() else {'studies':{}}
for name in args.records:
    record=read(root/'validation'/(name+'.json'))
    if record['status']!='completed':raise SystemExit(name+' is not completed')
    folder=root/'data'/'studies'/record['id'];config=read(folder/'config.json')
    case=AnalysisCase.model_validate(config['case']);geo=store.get('model',config['model_id'])
    mesh=dict(np.load(folder/'mesh.npz'));force,_=assemble_loads(mesh,case,geo);prescribed,_=constraints(mesh,case,geo)
    audits=read(folder/'audit.json');checks=[];physical_index=0
    for audit in audits:
        if audit.get('initial'):stage=folder/'coupling'/'initial'
        else:
            base=folder/'coupling'/(f"s{physical_index:04d}c{audit.get('cutbacks',0)}" if case.coupling.transient else 'steady')
            stage=base/(f"i{len(audit['iterations'])-1:02d}" if audit['iterations'] else 'direct');physical_index+=1
        assert (stage/'completed-stage.json').exists(),stage
        frame=parse_history(stage/'analysis.dat',len(mesh['points']),len(mesh['tets']))[-1]
        annotate(mesh,case,frame)
        check=balance(mesh,frame,force,prescribed)
        with (stage/'analysis.dat').open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        checks.append({'time_s':audit.get('time_s',0.),'native_stage':stage.relative_to(folder).as_posix(),'native_dat_sha256':checksum,**check})
    entry={'source_study':folder.name,'physical_states':len(checks),'checks':checks,
           'maximum_force_relative':max(v['force_relative'] for v in checks),
           'maximum_moment_relative':max(v['moment_relative'] for v in checks),
           'limit':.01,'passed':all(v['passed'] for v in checks)}
    result['studies'][name]=entry
    print(name,entry['physical_states'],'states; max force',entry['maximum_force_relative'],'max moment',entry['maximum_moment_relative'],'passed',entry['passed'],flush=True)
result['passed']=all(v['passed'] for v in result['studies'].values())
output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if not result['passed']:raise SystemExit(1)
