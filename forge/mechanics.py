"""CalculiX adapter for thermal loads and incremental surface contact.

Original meshes and input decks remain in the job folder. No ADJUST, weak springs,
automatic load reduction or geometry welding is used.
"""
import re,hashlib,json,shutil
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from scipy.spatial.transform import Rotation
from strength.fem import build_constraints
from .loads import assemble_loads
from strength.materials import selection_mask
from strength.solver import run_ccx
from strength.bonding import prepare_bonds,equation_lines
from .schemas import material_for
from .errors import NonlinearConvergenceError


def run_native(folder,**kwargs):
    try:return run_ccx(folder,**kwargs)
    except RuntimeError as exc:
        if re.search(r'too many cutbacks|too many iterations|increment size[^\n]*(?:small|minimum)|solution seems to diverge',str(exc),re.I):
            raise NonlinearConvergenceError(str(exc)) from exc
        raise

FACE_CORNERS=((0,1,2),(0,3,1),(1,3,2),(2,3,0))


def stage_reusable(folder,deck):
    inp=folder/'analysis.inp';log=folder/'solver.log'
    if not inp.is_file() or not log.is_file() or not (folder/'analysis.dat').is_file():return False
    if inp.read_text(encoding='ascii')!=deck:return False
    provenance=folder/'completed-stage.json'
    if provenance.is_file():
        saved=json.loads(provenance.read_text(encoding='utf-8'))
        with (folder/'analysis.dat').open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        if saved.get('raw_dat_sha256')!=checksum or saved.get('input_sha256')!=hashlib.sha256(deck.encode('ascii')).hexdigest():return False
        if saved.get('restart_sha256'):
            path=folder/'analysis.rout'
            if not path.is_file():return False
            with path.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=saved['restart_sha256']:return False
    output=log.read_text(errors='replace')
    return 'Job finished' in output and '*ERROR' not in output.upper() and bool(re.search(r'CalculiX Version\s+2\.23(?:\D|$)',output))


def save_stage_provenance(folder,deck,reused):
    from strength.store import write_json
    with (folder/'analysis.dat').open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    record={'input_sha256':hashlib.sha256(deck.encode('ascii')).hexdigest(),'raw_dat_sha256':checksum,'reused_complete_stage':reused,'solver':'CalculiX 2.23'}
    if (folder/'analysis.rout').is_file():
        with (folder/'analysis.rout').open('rb') as stream:record['restart_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
    write_json(folder/'completed-stage.json',record)


def surface_lines(mesh,selection,evidence,name):
    mask=selection_mask(mesh,selection,evidence);lines=[f'*SURFACE,NAME={name},TYPE=ELEMENT']
    for face,e in zip(mesh['faces'][mask],mesh['owners'][mask]):
        key=set(face[:3]);tet=mesh['tets'][e]
        local=next((i+1 for i,c in enumerate(FACE_CORNERS) if set(tet[list(c)])==key),None)
        if local is None:raise ValueError('接触面无法绑定体单元')
        lines.append(f'{e+1},S{local}')
    return lines


def motion_displacements(mesh,case,evidence,index):
    fixed={}
    for motion in case.motions:
        axis=np.asarray(motion.axis,dtype=float);axis/=np.linalg.norm(axis)
        R=Rotation.from_rotvec(axis*motion.angles_rad[index]).as_matrix()
        center=np.array(motion.center_m)
        translation=np.array(motion.translations_m[index] if motion.translations_m else [0,0,0])
        nodes=np.unique(mesh['faces'][selection_mask(mesh,motion.selection,evidence)])
        u=(mesh['points'][nodes]-center)@R.T+center+translation-mesh['points'][nodes]
        for node,displacement in zip(nodes,u):
            for a,value in enumerate(displacement):
                if 'xyz'[a] in motion.components:fixed[int(node)*3+a]=float(value)
    return fixed


def base_deck(mesh,case,evidence):
    lines=['*HEADING','ThermoForge Studio / SI / C3D10','*NODE,NSET=NALL']
    lines += [f'{i+1},'+','.join(f'{v:.12E}' for v in p) for i,p in enumerate(mesh['points'])]
    lines+=['*ELEMENT,TYPE=C3D10,ELSET=EALL']
    lines += [f'{i+1},'+','.join(str(int(n)+1) for n in t) for i,t in enumerate(mesh['tets'])]
    for body in np.unique(mesh['body_ids']):
        m=material_for(case,body);elements=np.flatnonzero(mesh['body_ids']==body)+1
        lines += [f'*ELSET,ELSET=B{body}']+ [','.join(map(str,elements[i:i+16])) for i in range(0,len(elements),16)]
        lines += [f'*MATERIAL,NAME=M{body}','*ELASTIC',f'{m.young_modulus_Pa:.12E},{m.poisson_ratio:.12E}','*DENSITY',f'{m.density_kg_m3:.12E}']
        if m.thermal_expansion_per_K is not None:lines += [f'*EXPANSION,ZERO={case.thermal.reference_C:.12E}',f'{m.thermal_expansion_per_K:.12E}']
        lines += [f'*SOLID SECTION,ELSET=B{body},MATERIAL=M{body}']
    from .axles import deck
    lines+=deck(mesh,case,evidence)
    for i,pair in enumerate(case.contacts):
        lines+=surface_lines(mesh,pair.slave,evidence,f'S{i}')+surface_lines(mesh,pair.master,evidence,f'M{i}')
        if pair.initial_offset_m!=0:raise ValueError('初始过盈／间隙必须体现在导入几何中；不允许未验证的接触偏移')
        lines += [f'*SURFACE INTERACTION,NAME=I{i}','*SURFACE BEHAVIOR,PRESSURE-OVERCLOSURE=LINEAR',f'{pair.penalty_Pa_m:.12E}']
        if pair.friction:lines += ['*FRICTION',f'{pair.friction:.12E},{pair.tangential_stiffness_Pa_m:.12E}']
        lines += [f'*CONTACT PAIR,INTERACTION=I{i},TYPE=SURFACE TO SURFACE',f'S{i},M{i}']
    # Thermal reference is independent of the initial thermal problem temperature.
    lines += ['*INITIAL CONDITIONS,TYPE=TEMPERATURE',f'NALL,{case.thermal.reference_C:.12E}']
    return lines


def constraints(mesh,case,evidence,index=None):
    if case.supports:
        prescribed,groups=build_constraints(mesh,case,evidence,check_components=not(case.contacts or case.bonded_connections or case.motions or case.axles))
    else:prescribed,groups={},[]
    if index is not None:
        for dof,value in motion_displacements(mesh,case,evidence,index).items():
            if dof in prescribed and abs(prescribed[dof]-value)>1e-10:raise ValueError('运动与固定边界冲突')
            prescribed[dof]=value
    from .axles import prescribed as axle_constraints
    prescribed.update(axle_constraints(mesh,case,index))
    if not prescribed:raise ValueError('结构计算缺少实际支撑或运动边界')
    return prescribed,groups


def step_lines(mesh,case,evidence,force,prescribed,temperature,duration=1.,contact=False):
    lines=['*STEP,NLGEOM,INC=1000' if contact or case.axles else '*STEP','*STATIC',f'{duration:.12E},{duration:.12E},{duration/1024:.12E},{duration:.12E}','*BOUNDARY,OP=NEW']
    lines += [f'{d//3+1},{d%3+1},{d%3+1},{v:.12E}' for d,v in sorted(prescribed.items())]
    lines += ['*CLOAD,OP=NEW']+[f'{n+1},{a+1},{force[n,a]:.12E}' for n,a in np.argwhere(np.abs(force)>1e-30)]
    from .axles import references
    lines += [f'{rot},{axis+1},{a.torque_Nm:.12E}' for a,ref,rot,axis in references(mesh,case) if a.torque_Nm]
    if temperature is not None:
        lines += ['*TEMPERATURE']+[f'{i+1},{v:.12E}' for i,v in enumerate(temperature)]
    lines += ['*NODE PRINT,NSET=NALL,GLOBAL=YES','U,RF','*EL PRINT,ELSET=EALL,GLOBAL=YES','S,E','*NODE FILE,GLOBAL=YES','U','*EL FILE,GLOBAL=YES','S,E']
    if contact:
        lines += ['*CONTACT PRINT','CDIS,CSTR','*CONTACT FILE','CDIS,CSTR']
        for i,pair in enumerate(case.contacts):lines += [f'*CONTACT PRINT,SLAVE=S{i},MASTER=M{i}','CF']
    if case.axles:lines+=['*NODE PRINT,NSET=AXLES_NODES,GLOBAL=YES','U,RF']
    lines += ['*END STEP']
    return lines


def text_lines(path):
    with Path(path).open(encoding='ascii',errors='replace') as stream:yield from stream


def parse_history(path,nnode,nelem,time_range=None):
    frames={};active=None;frame=None
    keys={'displacements':'displacements','forces':'forces','stresses':'stresses','strains':'strains'}
    contact_key=None;cf_key=None;cf_mode=None
    for line in text_lines(path):
        low=line.strip().lower()
        match=re.search(r'\btime\s+([+\-\d.EeDd]+)',low) if 'time' in low else None
        if match:
            time=float(match[1].replace('d','e'))
            if time_range and not time_range[0]<=time<=time_range[1]:frame=None;continue
            frame=frames.setdefault(time,{})
            cf=re.search(r'statistics for slave set s(\d+), master set m\d+',low)
            cf_key=int(cf[1]) if cf else None;cf_mode=None
            key=next((k for k in keys if low.startswith(k+' ')),None)
            if key:
                active=keys[key];contact_key=None
                if 'axles_nodes' in low:active='axle_'+active;frame[active]={}
                else:frame[active]=np.full((nelem,4,6) if active in ('stresses','strains') else (nnode,3),np.nan)
            elif 'contact stress' in low:active=None;contact_key='contact_stresses';frame.setdefault(contact_key,[])
            elif 'relative contact displacement' in low:active=None;contact_key='contact_displacements';frame.setdefault(contact_key,[])
            else:active=None;contact_key=None
            continue
        if frame is None:continue
        if cf_key is not None:
            if low.startswith('total surface force'):cf_mode='force';continue
            if low.startswith('area,'):cf_mode='area';continue
            if not low:continue
            if cf_mode:
                try:values=[float(v.replace('d','e')) for v in low.split()]
                except ValueError:cf_mode=None;continue
                stats=frame.setdefault('contact_totals',{}).setdefault(cf_key,{})
                if cf_mode=='force' and len(values)==6:stats.update(force_N=values[:3],moment_Nm=values[3:])
                if cf_mode=='area' and len(values)==3:stats.update(area_m2=values[0],normal_force_N=values[1] if values[0] else 0.,shear_force_N=values[2] if values[0] else 0.)
                cf_mode=None
            continue
        if not low or not low[0].isdigit():continue
        parts=line.split()
        try:values=[float(x.replace('D','E').replace('d','e')) for x in parts]
        except ValueError:continue
        if active in ('stresses','strains') and len(values)==8:
            e,ip=int(values[0])-1,int(values[1])-1
            if 0<=e<nelem and 0<=ip<4:frame[active][e,ip]=values[2:]
        elif active in ('displacements','forces') and len(values)==4:
            n=int(values[0])-1
            if 0<=n<nnode:frame[active][n]=values[1:]
        elif active in ('axle_displacements','axle_forces') and len(values)==4:frame[active][int(values[0])]=values[1:]
        elif contact_key:frame[contact_key].append(values)
    # CF uses the solver's actual contact integration areas. Summing whole
    # surface faces overestimates a contact patch cut by a face boundary.
    result=[]
    for time,frame in sorted(frames.items()):
        if not all(k in frame for k in keys):continue
        if any(not np.isfinite(frame[k]).all() for k in keys):raise ValueError('CalculiX 时间帧存在缺失的原始体结果')
        result.append({'time_s':time,'contact_totals':{},**frame})
    if not result:raise ValueError('CalculiX 没有完整位移及积分点应力时间帧')
    return result


def parse_contact_statistics(path):
    result={};current=None;mode=None
    for line in text_lines(path):
        match=re.search(r'statistics for slave set S(\d+), master set M\d+ and time\s+([+\-\d.EeDd]+)',line,re.I)
        if match:
            current=result.setdefault(float(match[2].replace('D','E')),{}).setdefault(int(match[1]),{});mode=None;continue
        if current is None:continue
        low=line.strip().lower()
        if low.startswith('total surface force'):mode='force_moment';continue
        if low.startswith('area,'):mode='area';continue
        if not low:continue
        if mode:
            try:values=[float(s.replace('D','E')) for s in low.split()]
            except ValueError:mode=None;continue
            if mode=='force_moment' and len(values)==6:current.update(force_N=values[:3],moment_Nm=values[3:])
            if mode=='area' and len(values)==3:current.update(area_m2=values[0],normal_force_N=values[1] if values[0] else 0.,shear_force_N=values[2] if values[0] else 0.)
            mode=None
    return result


def solve_static(mesh,case,evidence,folder,temperature=None,force=None,temperature_history=None,restart_from=None,write_restart=False):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    prescribed,groups=constraints(mesh,case,evidence)
    if force is None:force,audit=assemble_loads(mesh,case,evidence)
    else:audit=[]
    lines=base_deck(mesh,case,evidence);bonding=None
    if case.bonded_connections:
        bonding=prepare_bonds(mesh,case,evidence,prescribed,check_anchoring=not(case.contacts or case.axles));lines+=equation_lines(bonding)
    model_hash=hashlib.sha256('\n'.join(lines).encode('ascii')).hexdigest()
    end=len(temperature_history or [])+1
    if restart_from:
        if restart_from['model_sha256']!=model_hash:raise ValueError('重启动状态与当前网格、材料或连接不匹配')
        path=Path(restart_from['path'])
        if path.resolve()==(folder/'analysis.rout').resolve():raise ValueError('机械试算必须使用独立目录，不能覆盖已提交重启动状态')
        with path.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        if checksum!=restart_from['sha256']:raise ValueError('机械重启动状态校验失败')
        lines=['** ThermoForge committed-state SHA256: '+checksum,'*RESTART,READ']
        end=restart_from['time_s']+1
    else:
        for committed in temperature_history or []:
            lines+=step_lines(mesh,case,evidence,force,prescribed,committed,contact=bool(case.contacts))
    last=step_lines(mesh,case,evidence,force,prescribed,temperature,contact=bool(case.contacts))
    if write_restart:last.insert(-1,'*RESTART,WRITE,FREQUENCY=1')
    lines+=last
    deck='\n'.join(lines)+'\n';reused=stage_reusable(folder,deck) and (not write_restart or (folder/'analysis.rout').is_file())
    if not reused:
        if restart_from:shutil.copyfile(restart_from['path'],folder/'analysis.rin')
        (folder/'analysis.inp').write_text(deck,encoding='ascii')
        run_native(folder,timeout_s=case.resources.time_limit_s)
    frame=parse_history(folder/'analysis.dat',len(mesh['points']),len(mesh['tets']),(end-1e-6,end+1e-6))[-1]
    frame['stage_reused']=reused;save_stage_provenance(folder,deck,reused)
    if write_restart:
        path=folder/'analysis.rout'
        with path.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        frame['native_restart']={'path':str(path.resolve()),'sha256':checksum,'time_s':float(frame['time_s']),'model_sha256':model_hash}
    frame['applied_force']=force;frame['prescribed']=prescribed;frame['load_audit']=audit
    from .axles import annotate
    annotate(mesh,case,frame)
    if bonding:
        from strength.bonding import recover_bonds
        frame['bonding']=recover_bonds(mesh,frame,force,prescribed,bonding)
    frame['balance']=balance(mesh,frame,force,prescribed)
    return frame


def balance(mesh,frame,force,prescribed):
    reaction=np.zeros_like(force);dofs=np.array(sorted(d for d in prescribed if d<force.size),int)
    reaction.ravel()[dofs]=frame.get('reactions',frame['forces']-force).ravel()[dofs]
    p=mesh['points']+frame['displacements'];origin=p.mean(0);p=p-origin;total=reaction+force
    from .axles import balance_terms
    af,am,fs,ms=balance_terms(frame,origin)
    scale=max(np.linalg.norm(reaction,axis=1).sum(),np.linalg.norm(force,axis=1).sum(),1e-12)
    moment_scale=max(np.linalg.norm(np.cross(p,force),axis=1).sum(),np.linalg.norm(np.cross(p,reaction),axis=1).sum(),scale*np.ptp(p,axis=0).max()*1e-3,1e-15)
    fr=float(np.linalg.norm(total.sum(0)+af)/max(scale,fs));mr=float(np.linalg.norm(np.cross(p,total).sum(0)+am)/max(moment_scale,ms))
    return {'force_relative':fr,'moment_relative':mr,'passed':max(fr,mr)<=.01}


def solve_cycle(mesh,case,evidence,folder,progress=lambda *args:None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    lines=base_deck(mesh,case,evidence);history=case.history
    prescribed,_=constraints(mesh,case,evidence,0)
    if case.bonded_connections:lines+=equation_lines(prepare_bonds(mesh,case,evidence,prescribed,check_anchoring=not(case.contacts or case.axles)))
    p=mesh['points'];size=float(mesh.get('size_m',case.mesh.size_m or .001));last=None;load_states=[]
    size=min([size,*[r.size_m for r in case.mesh.regions],*([case.mesh.local_size_m] if case.mesh.local_size_m else [])])
    indices=list(range(len(history.times_s)))+list(range(1,len(history.times_s)))*history.stabilization_cycles
    for step_index,i in enumerate(indices):
        time=history.times_s[i]
        prescribed,_=constraints(mesh,case,evidence,i)
        # Continuous axle rotation is unwrapped over repeated periods. Resetting
        # 2*pi to the first increment would otherwise reverse a complete turn.
        repeat=max(0,(step_index-1)//(len(history.times_s)-1))
        if repeat:
            from .axles import references
            for axle,ref,rot,axis in references(mesh,case):
                if axle.angles_rad is not None:prescribed[3*(rot-1)+axis]+=repeat*(axle.angles_rad[-1]-axle.angles_rad[0])
        if last is not None and case.motions:
            delta=max((abs(v-last.get(d,0)) for d,v in prescribed.items() if d<3*len(p)),default=0)
            if delta>size*.2:raise ValueError('转角／位置步长过大：边界增量位移超过局部尺度的 20%，请加密运动时间轴')
        if last is not None:
            from .axles import references
            for axle,ref,rot,axis in references(mesh,case):
                if axle.angles_rad is None:continue
                nodes=np.unique(mesh['tets'][mesh['body_ids']==axle.body_id]);delta_xyz=p[nodes]-axle.center_m;delta_xyz[:,axis]=0
                angle_change=abs(prescribed[3*(rot-1)+axis]-last[3*(rot-1)+axis])
                if angle_change*np.linalg.norm(delta_xyz,axis=1).max()>size*.2:raise ValueError('轴转角步长过大，请加密运动时间轴')
        last=prescribed
        loads=[]
        for load in case.loads:
            factor=history.factors.get(load.name,[1.]*len(history.times_s))[i]
            copy=load.model_copy(deep=True);copy.vector=[v*factor for v in load.vector];copy.pressure_Pa*=factor;loads.append(copy)
            copy.torque_Nm=[v*factor for v in load.torque_Nm]
        snapshot=case.model_copy(update={'loads':loads});force,_=assemble_loads(mesh,snapshot,evidence)
        load_states.append((force,prescribed))
        duration=history.times_s[i]-history.times_s[i-1] if i else max((history.times_s[-1]-history.times_s[0])*1e-6,1e-6)
        lines+=step_lines(mesh,case,evidence,force,prescribed,None,duration,True)
    deck='\n'.join(lines)+'\n';reused=stage_reusable(folder,deck)
    if not reused:(folder/'analysis.inp').write_text(deck,encoding='ascii')
    progress('solving','CalculiX 增量接触：保持摩擦历史并更新接触配对')
    last_progress=[None]
    def monitor():
        path=folder/'analysis.sta'
        if not path.exists():return
        lines=path.read_text(errors='replace').splitlines()
        rows=[line.split() for line in lines[-3:] if re.match(r'^\s*\d+\s+\d+',line)]
        if not rows:return
        step=int(rows[-1][0])
        if step!=last_progress[0]:progress('solving',f'CalculiX 接触周期：第 {step}/{len(indices)} 个运动增量（含预循环）');last_progress[0]=step
    if not reused:run_native(folder,timeout_s=case.resources.time_limit_s,progress=monitor)
    else:progress('analyzing','恢复已完成的 CalculiX 周期，校验原始帧后继续评估')
    progress('analyzing','读取原始接触周期，恢复材料点应力、接触场与反力')
    offset=max((history.times_s[-1]-history.times_s[0])*1e-6,1e-6)
    targets=np.asarray(history.times_s)-history.times_s[0]+offset+history.stabilization_cycles*(history.times_s[-1]-history.times_s[0])
    frames=parse_history(folder/'analysis.dat',len(p),len(mesh['tets']),(targets.min()-1e-7,targets.max()+max(targets.max()*2e-6,1e-7)))
    selected=[]
    for target,(force,prescribed) in zip(targets,load_states[-len(history.times_s):]):
        frame=min(frames,key=lambda f:abs(f['time_s']-target))
        if abs(frame['time_s']-target)>max(target*2e-6,1e-7):raise ValueError('接触周期缺少请求的时间帧')
        from .axles import annotate
        annotate(mesh,case,frame)
        frame['balance']=balance(mesh,frame,force,prescribed);selected.append(frame)
        frame['stage_reused']=reused
    save_stage_provenance(folder,deck,reused)
    return selected
