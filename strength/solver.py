import json, os, re, subprocess, time, hashlib
from pathlib import Path
import h5py, numpy as np, meshio
from .config import CCX, DATA
from .schemas import LoadCase
from .geometry import build_mesh, validate_case, validate_execution, resolve_selection
from .materials import bodies,material_for,property_array,selection_mask
from .fem import assemble_loads, build_constraints, stress_invariants, nodal_stress_for_display, IP_BARY, tet_shape
from .store import write_json
from .backends import resolve_backend
from .postprocess import fields_for, summarize, execute_requests
from .result_catalog import finite_json, FIELDS

def write_deck(folder,mesh,case,force,prescribed,backend='calculix_cpu',bonding=None):
    lines=['*HEADING','Strength Studio - linear static C3D10, SI units','*NODE,NSET=NALL']
    # CalculiX numeric fields are limited to 20 characters. Keep exponents intact.
    lines += [f'{i+1},'+','.join(f'{v:.12E}' for v in p) for i,p in enumerate(mesh['points'])]
    lines += ['*ELEMENT,TYPE=C3D10,ELSET=EALL']
    lines += [f'{i+1},'+','.join(str(int(v)+1) for v in t) for i,t in enumerate(mesh['tets'])]
    for body in np.unique(bodies(mesh)):
        m=material_for(case,body);elements=np.flatnonzero(bodies(mesh)==body)+1
        lines += [f'*ELSET,ELSET=BODY{body}']
        lines += [','.join(str(e) for e in elements[i:i+16]) for i in range(0,len(elements),16)]
        lines += [f'*MATERIAL,NAME=MAT{body}','*ELASTIC',f'{m.young_modulus_Pa:.15g},{m.poisson_ratio:.15g}',
            '*DENSITY',str(m.density_kg_m3),f'*SOLID SECTION,ELSET=BODY{body},MATERIAL=MAT{body}']
    if bonding is not None:
        from .bonding import equation_lines
        lines += equation_lines(bonding)
    lines += ['*BOUNDARY']
    lines += [f'{d//3+1},{d%3+1},{d%3+1},{v:.12E}' for d,v in sorted(prescribed.items())]
    lines += ['*STEP','*STATIC,SOLVER=PASTIX' if backend=='calculix_cuda' else '*STATIC','*CLOAD']
    lines += [f'{n+1},{a+1},{force[n,a]:.12E}' for n,a in np.argwhere(np.abs(force)>1e-30)]
    lines += ['*NODE PRINT,NSET=NALL,GLOBAL=YES','U,RF','*EL PRINT,ELSET=EALL,GLOBAL=YES','S,E',
        '*NODE FILE,GLOBAL=YES','U','*EL FILE,GLOBAL=YES','S,E','*END STEP']
    (folder/'analysis.inp').write_text('\n'.join(lines)+'\n',encoding='ascii')

def parse_dat(path,nnode,nelem):
    arrays={'displacements':np.full((nnode,3),np.nan),'forces':np.full((nnode,3),np.nan),
        'stresses':np.full((nelem,4,6),np.nan),'strains':np.full((nelem,4,6),np.nan)}
    active=None
    for line in path.read_text(errors='replace').splitlines():
        text=line.strip().lower()
        found=next((k for k in arrays if text.startswith(k+' ')),None)
        if found: active=found; continue
        if not active or not re.match(r'^\s*\d+\s+',line): continue
        fields=line.split()
        try:
            if active in ('stresses','strains'):
                if len(fields)!=8: continue
                e,ip=int(fields[0])-1,int(fields[1])-1
                arrays[active][e,ip]=[float(x.replace('D','E')) for x in fields[2:]]
            else:
                if len(fields)!=4: continue
                arrays[active][int(fields[0])-1]=[float(x.replace('D','E')) for x in fields[1:]]
        except (IndexError,ValueError): raise ValueError('CalculiX 输出包含非法节点或单元编号')
    for name,a in arrays.items():
        if not np.isfinite(a).all(): raise ValueError(f'CalculiX {name} 输出不完整；查看 solver.log 与 analysis.dat')
    return arrays

def run_ccx(folder,backend_name='calculix_cpu',timeout_s=1800,progress=None):
    backend=resolve_backend(backend_name)
    env=backend.environment()
    for attempt in range(2):
        try:
            with (folder/'solver.log').open('w',encoding='utf-8') as log:
                if progress is None:
                    p=subprocess.run([str(backend.executable),'-i','analysis'],cwd=folder,stdout=log,stderr=subprocess.STDOUT,env=env,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=timeout_s)
                else:
                    p=subprocess.Popen([str(backend.executable),'-i','analysis'],cwd=folder,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    started=time.monotonic()
                    try:
                        while p.poll() is None:
                            if time.monotonic()-started>timeout_s:raise TimeoutError('CalculiX 超过工况指定时间上限')
                            progress();time.sleep(.5)
                    finally:
                        if p.poll() is None:p.kill();p.wait()
            break
        except OSError:
            if attempt: raise
            time.sleep(.2)
    output=(folder/'solver.log').read_text(errors='replace')
    if not re.search(r'CalculiX Version\s+2\.23(?:\D|$)',output): raise RuntimeError('求解器未报告已支持的 CalculiX 2.23 版本；请核对所配置的可执行文件')
    if p.returncode or '*ERROR' in output.upper() or not (folder/'analysis.dat').exists():
        raise RuntimeError('CalculiX 求解失败：'+output[-2500:])
    if backend_name=='calculix_cuda':
        gpu=re.search(r'Number of GPUs:\s*(\d+)',output)
        runtime=bool(re.search(r'(PaRSEC|StarPU):\s*(Started|Enabled)',output,re.I))
        if not gpu or int(gpu[1])<1 or not runtime:
            raise RuntimeError('GPU 后端未报告可用 GPU 运行时；不能将 CPU 结果当作 GPU 计算。原始文件已保留，请检查 solver.log 和构建参数。')
    return backend.metadata()

def assess(mesh,case,data,force,prescribed,groups,load_audit,mesh_info):
    p=mesh['points']; t=mesh['tets']; u=data['displacements']; stress=data['stresses']; strain=data['strains']
    vm,principal=stress_invariants(stress)
    disp=np.linalg.norm(u,axis=1); ei,ip=np.unravel_index(np.argmax(vm),vm.shape)
    N,_=tet_shape(IP_BARY[ip,1:]); peak_position=N@p[t[ei]]
    reaction=np.zeros_like(force); dofs=np.array(sorted(prescribed),int)
    # CalculiX RF is total external nodal force, including prescribed loads.
    reaction.ravel()[dofs]=data['reactions'].ravel()[dofs] if 'reactions' in data else (data['forces']-force).ravel()[dofs]
    residual=(reaction+force).sum(0); origin=np.asarray(case.moment_origin_m) if case.moment_origin_m is not None else p.mean(0); moment=np.cross(p-origin,reaction+force).sum(0)
    fscale=max(np.linalg.norm(force,axis=1).sum(),np.linalg.norm(reaction,axis=1).sum(),1e-12)
    mscale=max(np.linalg.norm(np.cross(p-origin,force),axis=1).sum(),np.linalg.norm(np.cross(p-origin,reaction),axis=1).sum(),fscale*max(np.ptp(p,axis=0))*1e-3,1e-15)
    fr=float(np.linalg.norm(residual)/fscale); mr=float(np.linalg.norm(moment)/mscale)
    free=np.ones(p.size,bool); free[dofs]=False
    free_error=float(np.linalg.norm(data.get('reduced_residual',data['forces']-force).ravel()[free])/max(fscale,1e-12))
    balance_ok=max(fr,mr,free_error)<=1e-4
    peak=float(vm.max()); yields=property_array(mesh,case,'yield_strength_Pa')
    all_strength=bool(np.isfinite(yields).all())
    measure=vm if case.strength_criterion=='von_mises' else principal[:,:,-1]-principal[:,:,0]
    ratios=np.divide(yields[:,None],measure,out=np.full_like(measure,np.nan),where=measure>1e-12)
    factor=float(np.nanmin(ratios)) if all_strength and np.isfinite(ratios).any() else None
    # The weakest material can control even when another component has peak stress.
    critical_index=np.unravel_index(np.nanargmin(ratios),ratios.shape) if all_strength and np.isfinite(ratios).any() else np.unravel_index(np.argmax(measure),measure.shape)
    critical_e,critical_ip=critical_index
    sy=float(yields[critical_e]) if np.isfinite(yields[critical_e]) else None
    max_principal=float(principal.max()); min_principal=float(principal.min())
    maxstrain=float(np.max(np.abs(strain[:,:,:3])))
    checks={'strength':{'status':'not_evaluated' if not all_strength else ('within_limit' if np.all(measure<=yields[:,None]/case.target_safety_factor) else 'exceeded'),
            'value_Pa':float(measure[critical_index]),'criterion':case.strength_criterion,'limit_Pa':sy/case.target_safety_factor if sy else None,'safety_factor':factor,'target':case.target_safety_factor,
            'critical_element':int(critical_e)+1,'critical_integration_point':int(critical_ip)+1,'critical_body_id':int(bodies(mesh)[critical_e])},
        'displacement':{'status':'not_evaluated' if not case.displacement_limit_m else ('within_limit' if max(disp)<=case.displacement_limit_m else 'exceeded'),
            'value_m':float(max(disp)),'limit_m':case.displacement_limit_m}}
    warnings=[]
    if not balance_ok: warnings.append('力学平衡检查未通过；不得据此给出合格结论')
    if np.any(measure>yields[:,None]): warnings.append('局部所选判据应力超过所属组件材料屈服强度，超出线弹性材料适用范围；不能推算塑性承载能力')
    component_results=[]
    for body in np.unique(bodies(mesh)):
        mask=bodies(mesh)==body; nodes=np.unique(t[mask]);m=material_for(case,body);pv=float(vm[mask].max())
        assessed=float(measure[mask].max())
        sf=m.yield_strength_Pa/assessed if m.yield_strength_Pa and assessed>1e-12 else None
        component_results.append({'body_id':int(body),'name':f'组件 {body}','material':m.model_dump(),
            'maximum_von_mises_Pa':pv,'maximum_displacement_m':float(disp[nodes].max()),'minimum_safety_factor':sf,
            'strength_status':'not_evaluated' if sf is None else 'within_limit' if sf>=case.target_safety_factor else 'exceeded'})
    if len(component_results)>1 and case.assembly_mode=='independent': warnings.append('组件按独立结构计算，互不传力；不代表粘结、接触或螺栓连接的组合件整体响应')
    if case.assembly_mode=='bonded':warnings.append('整体停机理想粘结模型；接口不分离、不滑移、不相对转动。未模拟真实轴承滚动、螺栓预紧、摩擦或连接柔度。')
    if case.assembly_mode=='gear_contact':
        warnings.extend(['教学假设工况；固定啮合位置、无摩擦、小滑移接触，轴心及轴倾斜锁定，输出轴转角锁止；不含滚动啮合动力学和轴承柔度',
            '接触积分点离散尚未完成 Hertz 基准及接触网格收敛验证；显示压力为积分点力/面积的平均值，不作接触疲劳寿命结论',
            '总位移包含原始齿隙闭合所需轴端小转角；弹性变形场单独保存；屈服安全系数只采用线弹性体内 von Mises 应力'])
    if maxstrain>.01: warnings.append('应变超过 1%，超出本版本小应变验证范围')
    # Use deformation gradients to catch rotations that small strain alone misses.
    q=np.array([.25,.25,.25]); _,d=tet_shape(q); J=np.einsum('eic,ij->ecj',p[t],d)
    grad=np.einsum('eic,eij->ecj',u[t],np.einsum('ij,ejk->eik',d,np.linalg.inv(J)))
    maxgrad=float(np.max(np.linalg.norm(grad,axis=(1,2))))
    if maxgrad>.1: warnings.append('位移梯度较大，需使用几何非线性模型复核')
    used=set(); reactions=[]
    for group in groups:
        gd={n*3+'xyz'.index(a) for n in group['nodes'] for a in group['components']}; unique=gd-used; used|=gd
        rg=np.zeros_like(force); ids=np.array(sorted(unique),int); rg.ravel()[ids]=reaction.ravel()[ids]
        reactions.append({k:v for k,v in group.items() if k!='nodes'}|{'reaction_N':rg.sum(0).tolist(),'moment_Nm':np.cross(p-origin,rg).sum(0).tolist(),
            'moment_origin_m':origin.tolist(),'shared_dofs_assigned_to_earlier_supports':len(gd)-len(unique)})
    return {'maximum_displacement_m':float(disp.max()),'maximum_von_mises_Pa':peak,
        'minimum_safety_factor':factor,'components':component_results,'principal_max_Pa':max_principal,'principal_min_Pa':min_principal,
        'stress_peak':{'element':int(ei)+1,'integration_point':int(ip)+1,'position_m':peak_position.tolist()},
        'displacement_peak':{'node':int(disp.argmax())+1,'position_m':p[disp.argmax()].tolist()},
        'mesh':mesh_info,'checks':checks,'warnings':warnings,'loads':load_audit,'supports':reactions,
        'balance':{'passed':balance_ok,'force_relative':fr,'moment_relative':mr,'free_dof_relative':free_error,
            'force_residual_N':residual.tolist(),'moment_residual_Nm':moment.tolist(),'reaction_N':reaction.sum(0).tolist(),'moment_origin_m':origin.tolist()},
        'maximum_normal_strain':maxstrain,'maximum_displacement_gradient':maxgrad,
        'convergence':{'status':'not_verified'},'stress_basis':'C3D10 四个积分点原始应力；显示云图为外推后的体积加权节点平均',
        'solver':'CalculiX 2.23 / C3D10 / linear static','units':'SI'},reaction

def export_fields(folder,mesh,data,reaction,summary,case):
    p=mesh['points']; t=mesh['tets']; stress=data['stresses']; vm,principal=stress_invariants(stress)
    nodal=nodal_stress_for_display(mesh,stress); nodal_strain=nodal_stress_for_display(mesh,data['strains']); nvm,nprincipal=stress_invariants(nodal)
    faces=mesh['faces']; split=np.array([[0,3,5],[3,1,4],[5,4,2],[3,4,5]])
    tri=faces[:,split].reshape(-1,3); ids=np.repeat(mesh['surface_ids'],4)
    # Outward display winding, using the owning tetrahedron centre.
    pp=p[tri]; n=np.cross(pp[:,1]-pp[:,0],pp[:,2]-pp[:,0]); owners=np.repeat(mesh['owners'],4)
    invert=np.sum(n*(pp.mean(1)-p[t[owners,:4]].mean(1)),axis=1)<0
    tri[invert]=tri[invert][:,[0,2,1]]
    used=np.unique(tri); lut=np.full(len(p),-1,int); lut[used]=np.arange(len(used))
    raw,values=fields_for(mesh,case,data)
    summary.update(summarize(mesh,case,data,raw))
    critical=summary['field_statistics']['safety_factor']
    summary['safety_factor_minimum']=critical.get('minimum')
    if 'contact_pressure_display' in data:
        values['contact_pressure']=data['contact_pressure_display']
        values['elastic_displacement']=np.linalg.norm(data['elastic_displacements'],axis=1)
    display={'points':p[used].tolist(),'triangles':lut[tri].tolist(),'surface_ids':ids.tolist(),
        'displacements':data['displacements'][used].tolist(),'fields':{k:v[used].tolist() for k,v in values.items()},'node_ids':(used+1).tolist()}
    display=finite_json(display)
    display['field_catalog']=list(FIELDS.values())
    write_json(folder/'display.json',display)
    with h5py.File(folder/'fields.h5','w') as f:
        for name,a in {**{k:mesh[k] for k in ('points','tets','faces','surface_ids','owners')},**data,'reaction':reaction,'von_mises':vm,'principal':principal,'nodal_stress':nodal,**{'display_'+k:v for k,v in values.items()}}.items(): f.create_dataset(name,data=a,compression='gzip')
        f.attrs['units']='m,N,Pa'; f.attrs['stress_order']='xx,yy,zz,xy,xz,yz'; f.attrs['stress_location']='integration_point'
        f.attrs['result_schema_version']=2;f.attrs['strain_shear']='tensor';f.attrs['field_catalog']=json.dumps(list(FIELDS.values()),ensure_ascii=False)
        f.attrs['strain_energy_J']=summary['strain_energy_J'];f.attrs['reaction_moment_origin_m']=summary['balance']['moment_origin_m']
        for name,values_ip in raw.items():f.create_dataset('ip_'+name,data=values_ip,compression='gzip')
        f.create_dataset('body_ids',data=bodies(mesh))
    meshio.write(folder/'result.vtu',meshio.Mesh(p,[('tetra10',t)],point_data={'displacement_m':data['displacements'],'stress_smoothed_Pa':nodal,'reaction_N':reaction,**values},
        cell_data={'body_id':[bodies(mesh)],'von_mises_max_ip_Pa':[vm.max(axis=1)],'stress_mean_ip_Pa':[stress.mean(axis=1)],**{k+'_ip_mean':[v.mean(axis=1)] for k,v in raw.items()}}))
    return display

def solve_level(model_folder,case,folder,factor,progress,backend='calculix_cpu'):
    folder.mkdir(parents=True,exist_ok=True)
    evidence=json.loads((model_folder/'geometry.json').read_text(encoding='utf-8'))
    validate_execution(case,evidence)
    progress('meshing',f'生成 C3D10 网格（加密倍率 {factor:g}）')
    mesh_started=time.perf_counter()
    info=build_mesh(model_folder,case,folder/'mesh.npz',factor,max_dofs=1200000 if backend=='cudss_cuda' else None)
    with np.load(folder/'mesh.npz') as packed: mesh={k:packed[k] for k in packed.files}
    mesh_elapsed=time.perf_counter()-mesh_started; preparation_started=time.perf_counter()
    contact_summary=None;bonding=None
    if case.assembly_mode=='bonded' and backend!='calculix_cpu':raise ValueError('整体粘结当前仅支持 CalculiX CPU 后端')
    if case.assembly_mode=='gear_contact':
        if backend!='cudss_cuda':raise ValueError('当前齿轮接触实现要求 cudss_cuda；未切换计算后端')
        from .gear_contact import solve_gears
        preparation_elapsed=time.perf_counter()-preparation_started
        data,execution,force,fixed,groups,audit,contact_summary=solve_gears(mesh,case,evidence,folder,progress)
        # This is the elastic reference problem for independent CPU field checks;
        # contact.json/config.json define the actual coupled contact calculation.
        write_deck(folder,mesh,case,force,fixed,'calculix_cpu')
    else:
        force,audit=assemble_loads(mesh,case,evidence); fixed,groups=build_constraints(mesh,case,evidence,check_components=case.assembly_mode!='bonded')
        if case.assembly_mode=='bonded':
            from .bonding import prepare_bonds
            progress('validating','映射整体粘结接口并检查装配传力链')
            bonding=prepare_bonds(mesh,case,evidence,fixed)
        write_deck(folder,mesh,case,force,fixed,backend,bonding)
        preparation_elapsed=time.perf_counter()-preparation_started
    if backend=='cudss_cuda' and contact_summary is None:
        from .gpu_solver import solve_gpu
        data,execution=solve_gpu(mesh,case,force,fixed,progress)
        write_json(folder/'gpu-execution.json',execution)
        (folder/'solver.log').write_text(json.dumps(execution,ensure_ascii=False,indent=2),encoding='utf-8')
    elif backend!='cudss_cuda':
        progress('solving',f'CalculiX 正在求解：{info["nodes"]:,} 节点 / {info["elements"]:,} 单元')
        start=time.perf_counter();execution=run_ccx(folder,backend); data=parse_dat(folder/'analysis.dat',len(mesh['points']),len(mesh['tets']))
        execution['compute_s']=time.perf_counter()-start
    progress('verifying','校核原始积分点应力、支撑反力与力矩平衡')
    if bonding is not None:
        from .bonding import recover_bonds
        bonding_summary=recover_bonds(mesh,data,force,fixed,bonding)
    summary,reaction=assess(mesh,case,data,force,fixed,groups,audit,info)
    if bonding is not None:
        summary['bonding']=bonding_summary
        write_json(folder/'bonding.json',bonding_summary)
    summary['execution']=execution
    if contact_summary:
        summary['contact']=contact_summary
        write_json(folder/'gpu-execution.json',execution)
        (folder/'solver.log').write_text(json.dumps(execution,ensure_ascii=False,indent=2),encoding='utf-8')
    execution.update(mesh_s=mesh_elapsed,host_preparation_s=preparation_elapsed)
    if backend=='cudss_cuda':summary['solver']='Native CUDA + NVIDIA cuDSS 0.8 / C3D10 / FP64 linear static'
    if contact_summary:summary['solver']+=' / small-slip unilateral gear contact (experimental)'
    if execution.get('cudss_ir_status')==7:summary['warnings'].append('cuDSS 内部迭代修正未达到其容差；已通过独立单元内力残差 1e-8 及整体平衡校核，原始状态保存在计算记录')
    if backend=='calculix_cuda': summary['warnings'].append('本次请求了外部 PaStiX/CUDA 后端；GPU 实际工作量未自动核验，需检查求解日志；该构建不属于随附 CPU 回归验证范围')
    summary['material']=case.material.model_dump(); summary['name']=case.name
    if case.mesh.evaluation_selection:
        owners=np.unique(mesh['owners'][selection_mask(mesh,case.mesh.evaluation_selection,evidence)])
        summary['region_von_mises_Pa']=float(stress_invariants(data['stresses'][owners])[0].max())
    summary['geometry_sha256']=evidence['source_sha256']; summary['mesh_sha256']=hashlib.sha256((folder/'mesh.npz').read_bytes()).hexdigest()
    display=export_fields(folder,mesh,data,reaction,summary,case)
    write_json(folder/'summary.json',summary)
    if not summary['balance']['passed']: raise ValueError('求解结果未通过力和力矩平衡检查；已保留原始结果供诊断')
    return summary,display

def run_job(folder):
    config=json.loads((folder/'config.json').read_text(encoding='utf-8')); case=LoadCase.model_validate(config['case'])
    def progress(stage,text): write_json(folder/'status.json',{'status':stage,'detail':text,'updated_at':time.time()})
    start=time.perf_counter(); model_folder=DATA/'models'/config['model_id']
    source=json.loads((model_folder/'source.json').read_text(encoding='utf-8'))
    actual=hashlib.sha256((model_folder/source['file']).read_bytes()).hexdigest()
    expected=config.get('geometry_sha256',json.loads((model_folder/'geometry.json').read_text(encoding='utf-8'))['source_sha256'])
    if actual!=expected: raise ValueError('几何文件已改变，与不可变配置快照不匹配；请重新导入模型')
    backend=config.get('compute_backend','calculix_cpu'); resolve_backend(backend)
    summaries=[]; display=None
    factors=[1.,1.5,2.25] if case.mesh.convergence else [1.]
    for i,factor in enumerate(factors):
        target=folder if i==len(factors)-1 else folder/f'convergence-{i+1}'
        summary,display=solve_level(model_folder,case,target,factor,progress,backend); summaries.append(summary)
    summary=summaries[-1]
    if len(summaries)>1:
        a,b=summaries[-2:]; region=bool(case.mesh.evaluation_selection); field='region_von_mises_Pa' if region else 'maximum_von_mises_Pa'
        du=abs(a['maximum_displacement_m']-b['maximum_displacement_m'])/max(b['maximum_displacement_m'],1e-20)
        ds=abs(a[field]-b[field])/max(b[field],1e-20)
        summary['convergence']={'status':'converged' if du<=.02 and ds<=.05 else 'needs_review', 'displacement_change':du,'stress_change':ds,
            'stress_scope':'指定表面相邻单元积分点' if region else '全局积分点峰值',
            'levels':[{'mesh':s['mesh'],'displacement_m':s['maximum_displacement_m'],'displacement_peak':s['displacement_peak'],
                       'strain_energy_J':s.get('strain_energy_J'),'stress_Pa':s[field],'global_peak_Pa':s['maximum_von_mises_Pa'],'peak':s['stress_peak']} for s in summaries]}
        if ds>.05: summary['warnings'].append('应力峰值对网格敏感；请检查固定边缘或尖角，并指定关键区域复核')
    summary.update(elapsed_s=time.perf_counter()-start,job_id=folder.name,created_at=time.time(),configuration=config)
    write_json(folder/'summary.json',summary)
    summary['result_queries']=execute_requests(folder,case)
    write_json(folder/'summary.json',summary)
    progress('reporting','生成中文 PDF、CSV 与完整结果包')
    from .report import make_report
    reporting_started=time.perf_counter()
    make_report(folder,summary,display,model_folder)
    summary['report_and_archive_s']=time.perf_counter()-reporting_started
    summary['end_to_end_s']=time.perf_counter()-start
    timing={'end_to_end_s':summary['end_to_end_s'],'report_and_archive_s':summary['report_and_archive_s'],
        'levels':[s['execution'] for s in summaries],'note':'端到端从工作进程接受作业到结果包生成，不含 Agent 规划和排队；归档内 summary 为报告生成时快照，本文件记录最终耗时。'}
    write_json(folder/'timings.json',timing);write_json(folder/'summary.json',summary)
    import zipfile
    with zipfile.ZipFile(folder/'result.zip','a',compression=zipfile.ZIP_DEFLATED) as z:z.write(folder/'timings.json','timings.json')
    progress('completed','求解、结果校核与报告已完成')
    return summary
