"""Reproducible same-mesh CPU6/GPU benchmark. No result or mesh substitution."""
import argparse,json,os,sys,time,statistics,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['STRENGTH_CPU_THREADS']='6'
import numpy as np
from strength.geometry import make_demo,inspect_model,build_mesh
from strength.store import write_json
from strength.schemas import LoadCase,DEMO_MATERIAL
from strength.fem import assemble_loads,build_constraints
from strength.solver import write_deck,run_ccx,parse_dat,assess
from strength.gpu_solver import solve_gpu,clear_cache

def main():
    p=argparse.ArgumentParser();p.add_argument('--targets',nargs='+',type=int,default=[500000,1000000]);p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--gpu-only',action='store_true');p.add_argument('--work',type=Path,default=ROOT/'.runtime'/'gpu-benchmark');p.add_argument('--output',type=Path,default=ROOT/'docs'/'GPU_BENCHMARK.json');a=p.parse_args()
    a.work.mkdir(parents=True,exist_ok=True);model=a.work/'bracket';model.mkdir(exist_ok=True)
    if not (model/'geometry.json').exists():
        make_demo(model/'source.step','bracket');write_json(model/'source.json',{'file':'source.step','units':'m'});inspect_model(model)
    geo=json.loads((model/'geometry.json').read_text(encoding='utf-8'));results=[]
    for target in a.targets:
        folder=a.work/str(target);folder.mkdir(exist_ok=True)
        case=LoadCase(material=DEMO_MATERIAL,supports=[{'selection':{'kind':'named','name':'安装孔'}}],loads=[{'kind':'force','selection':{'kind':'named','name':'顶面'},'vector':[0,0,-1000]}],mesh={'size_m':.005*(115524/target)**(1/3)})
        start=time.perf_counter()
        if (folder/'frozen.json').exists():
            frozen=json.loads((folder/'frozen.json').read_text());case.mesh.size_m=frozen['size_m'];info=frozen['mesh']
        else:
            for iteration in range(6):
                info=build_mesh(model,case,folder/'mesh.npz',max_dofs=1200000)
                print('mesh',target,info['dofs'],case.mesh.size_m,flush=True)
                if abs(info['dofs']/target-1)<=.1:break
                case.mesh.size_m*=float(info['dofs']/target)**(1/3)
            else:raise RuntimeError('Failed to calibrate benchmark size; no substitute model used')
            write_json(folder/'frozen.json',{'size_m':case.mesh.size_m,'mesh':info})
        mesh_s=time.perf_counter()-start;m=dict(np.load(folder/'mesh.npz'));f,audit=assemble_loads(m,case,geo);fixed,groups=build_constraints(m,case,geo)
        row={'target_dofs':target,'mesh':info,'mesh_sha256':hashlib.sha256((folder/'mesh.npz').read_bytes()).hexdigest(),'mesh_prepare_s':mesh_s,'cpu_threads':6,'gpu':[],'cpu':[],'cpu_gpu_errors':[]}
        cold=time.perf_counter();warm,meta=solve_gpu(m,case,f,fixed);row['gpu_cold_s']=time.perf_counter()-cold;clear_cache()
        for repeat in range(a.repeats):
            print('gpu',target,repeat,flush=True);start=time.perf_counter();g,execution=solve_gpu(m,case,f,fixed);dt=time.perf_counter()-start
            summary,_=assess(m,case,g,f,fixed,groups,audit,info);assert summary['balance']['passed']
            row['gpu'].append({'wall_s':dt,'execution':execution,'balance':summary['balance']})
            if repeat==0:
                _,reuse=solve_gpu(m,case,f*1.5,fixed);row['reuse']=reuse
            clear_cache()
        if not a.gpu_only:
            for repeat in range(a.repeats+1):
                print('cpu',target,repeat,flush=True);cpu_dir=folder/'cpu';cpu_dir.mkdir(exist_ok=True)
                start=time.perf_counter();write_deck(cpu_dir,m,case,f,fixed);run_ccx(cpu_dir);cpu=parse_dat(cpu_dir/'analysis.dat',len(m['points']),len(m['tets']));dt=time.perf_counter()-start
                if repeat==0:row['cpu_cold_s']=dt;continue
                s,_=assess(m,case,cpu,f,fixed,groups,audit,info);assert s['balance']['passed']
                errors={k:float(np.linalg.norm(cpu[k]-g[k])/max(np.linalg.norm(cpu[k]),1e-30)) for k in ('displacements','stresses')}
                assert errors['displacements']<=1e-6 and errors['stresses']<=1e-5,errors
                row['cpu'].append({'wall_s':dt,'balance':s['balance']});row['cpu_gpu_errors'].append(errors)
            row['speedup']=statistics.median(x['wall_s'] for x in row['cpu'])/statistics.median(x['wall_s'] for x in row['gpu'])
            row['performance_passed']=row['speedup']>=2
        results.append(row);write_json(a.output,{'results':results,'requested_targets':a.targets,'complete':len(results)==len(a.targets),'target_speedup':2,'timing_scope':'matrix assembly, transfer, analysis, factorization, solve and raw result recovery; CPU input/output conversion included; mesh/report separate','precision':'FP64','all_performance_passed':len(results)==len(a.targets) and all(r.get('performance_passed',False) for r in results)})
        print('finished',target,row.get('speedup'),flush=True)

if __name__=='__main__':main()
