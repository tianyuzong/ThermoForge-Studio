"""Transactional staggered thermo-contact iteration with bounded time cutbacks."""
from pathlib import Path
import copy
import numpy as np
from .mechanics import solve_static
from .interfaces import interface_map
from .contact import recover_contact
from .thermal import next_step
from strength.store import write_json
from strength.result_catalog import finite_json
from .errors import NonlinearConvergenceError
from .schemas import coupling_mode
from .initial_state import load_initial_temperature
from .checkpoints import append_step,JournalSeries


class CouplingFailure(RuntimeError):
    def __init__(self,message,partial=None):
        super().__init__(message);self.partial=partial


def coupled_step(system,case,evidence,folder,previous_T,time,dt,progress=lambda *a:None,committed_temperatures=None):
    cfg=case.coupling;mesh=system.mesh;guess=previous_T.copy();old_u=getattr(system,'committed_u',np.zeros_like(mesh['points']));old_flow=None;log=[]
    if hasattr(system,'committed_maps'):system.interface_records=copy.deepcopy(system.committed_maps)
    state=copy.deepcopy(getattr(system,'committed_contact_state',None))
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    # With no mechanical interfaces the thermal equation has no displacement
    # feedback. Solving this exact triangular system once avoids false iterations.
    if coupling_mode(case)=='one_way_thermal_structure':
        progress('solving',f'热结构 t={time:g}s · 理想连接热场与热应力')
        temperature,thermal=system.solve(previous_T,time,dt,commit=False)
        result=solve_static(mesh,case,evidence,folder/'direct',temperature=temperature,temperature_history=committed_temperatures,
                            restart_from=getattr(system,'committed_native_restart',None),write_restart=bool(case.contacts))
        maps={p.name:interface_map(mesh,p,evidence,result['displacements'],allow_empty=True) for p in case.contacts}
        state=recover_contact(mesh,result,maps,case,True) if case.contacts else {}
        for i,value in system.control_values.items():system.controls[i]=value==system.boundaries[i][0].thermostat.maximum_W
        system.committed_contact_state=copy.deepcopy(state);system.committed_u=result['displacements'].copy()
        system.committed_native_restart=result.get('native_restart')
        return temperature,result,{'thermal':thermal,'mechanical':result.get('balance'),'iterations':[],'contact':state,'feedback':'one_way_thermal_structure'}
    for iteration in range(cfg.max_iterations):
        progress('solving',f'热结构 t={time:g}s · 界面迭代 {iteration+1}/{cfg.max_iterations}')
        # Every thermal trial starts from previous_T, never from a trial time-step state.
        temperature,thermal=system.solve(previous_T,time,dt,state,guess,commit=False)
        trial=guess+cfg.relaxation*(temperature-guess)
        # Every trial reads the same committed native state, preserving friction
        # without replaying all physical time steps or accumulating trial history.
        result=solve_static(mesh,case,evidence,Path(folder)/f'i{iteration:02d}',temperature=trial,temperature_history=committed_temperatures,
                            restart_from=getattr(system,'committed_native_restart',None),write_restart=True)
        maps={p.name:interface_map(mesh,p,evidence,result['displacements'],allow_empty=True) for p in case.contacts}
        new_state=recover_contact(mesh,result,maps,case,True)
        for pair in case.contacts:
            if pair.thermal:system.interface_records[pair.name]=maps[pair.name]
        flow=np.array([a['heatflow_W'] for a in thermal['interfaces']]) if thermal['interfaces'] else np.zeros(1)
        dT=float(np.max(np.abs(temperature-guess)))
        du=float(np.linalg.norm(result['displacements']-old_u)/max(np.linalg.norm(result['displacements']),1e-8))
        dq=float(np.linalg.norm(flow-old_flow)/max(np.linalg.norm(flow),1e-6)) if old_flow is not None else float('inf')
        log.append({'iteration':iteration+1,'temperature_change_K':dT,'displacement_relative':du,'heatflow_relative':None if not np.isfinite(dq) else dq})
        write_json(folder/'iterations.json',finite_json({'time_s':time,'dt_s':dt,'iterations':log}))
        guess=trial;old_u=result['displacements'].copy();old_flow=flow;state=new_state
        if iteration and dT<=cfg.temperature_tolerance_K and du<=cfg.displacement_relative_tolerance and dq<=cfg.heatflow_relative_tolerance:
            # Validate the final mechanical feedback without advancing physical time.
            check,final_audit=system.solve(previous_T,time,dt,state,guess,commit=False)
            mismatch=float(np.max(np.abs(check-guess)))
            if mismatch>cfg.temperature_tolerance_K:continue
            final_audit['mechanical_temperature_consistency_K']=mismatch
            final_audit['saved_temperature']='final thermal solve; mechanical temperature differs by at most the configured coupling tolerance'
            committed_restart=getattr(system,'committed_native_restart',None)
            final_audit['mechanical_history_mode']='native_restart' if committed_restart else 'initial_static_state'
            if committed_restart:final_audit['mechanical_restart_source_sha256']=committed_restart['sha256']
            for i,value in system.control_values.items():system.controls[i]=value==system.boundaries[i][0].thermostat.maximum_W
            system.committed_maps=copy.deepcopy(system.interface_records);system.committed_contact_state=copy.deepcopy(state);system.committed_u=result['displacements'].copy()
            system.committed_native_restart=result['native_restart']
            return check,result,{'thermal':final_audit,'mechanical':result.get('balance'),'iterations':log,'contact':state,'feedback':'two_way_contact'}
    raise CouplingFailure(f't={time:g}s 热结构双向迭代未收敛')


def solve_coupled(system,case,evidence,folder,progress=lambda *a:None):
    settings=case.thermal;previous,initial_provenance=load_initial_temperature(system.mesh,case);times=[];temperatures=[];structures=[];audits=[]
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    write_json(folder/'initial-state.json',initial_provenance)
    system.committed_maps=copy.deepcopy(system.interface_records)
    if not case.coupling.transient:
        T,S,A=coupled_step(system,case,evidence,folder/'steady',previous,0,None,progress)
        A['initial_state']=initial_provenance;append_step(folder,0,0.,T,S,A)
        return [0.],JournalSeries(folder,[0],'temperature_C'),JournalSeries(folder,[0]),[A]
    initial=solve_static(system.mesh,case,evidence,folder/'initial',temperature=previous,write_restart=bool(case.contacts))
    system.committed_native_restart=initial.get('native_restart')
    maps={p.name:interface_map(system.mesh,p,evidence,initial['displacements'],allow_empty=True) for p in case.contacts}
    state=recover_contact(system.mesh,initial,maps,case,True)
    system.committed_contact_state=copy.deepcopy(state);system.committed_u=initial['displacements'].copy()
    for pair in case.contacts:
        if pair.thermal:system.committed_maps[pair.name]=maps[pair.name]
    times=[0.];saved_indices=[0];temperatures=JournalSeries(folder,saved_indices,'temperature_C');structures=JournalSeries(folder,saved_indices)
    audits=[{'time_s':0.,'initial':True,'initial_state':initial_provenance,'saved_frame':0,'contact':state,'mechanical':initial.get('balance')}]
    append_step(folder,0,0.,previous,initial,audits[0])
    time=0.;next_save=min(settings.save_s,settings.duration_s);index=0;committed_indices=[];committed=JournalSeries(folder,committed_indices,'temperature_C')
    while time<settings.duration_s-1e-9:
        dt=next_step(settings,time,next_save)
        for cutback in range(case.coupling.max_cutbacks+1):
            try:
                T,S,A=coupled_step(system,case,evidence,folder/f's{index:04d}c{cutback}',previous,time+dt,dt,progress,committed)
                break
            except (CouplingFailure,NonlinearConvergenceError) as exc:
                if cutback==case.coupling.max_cutbacks:
                    failure={'time_s':time+dt,'last_converged_time_s':time,'dt_s':dt,'cutbacks':cutback,'detail':str(exc)}
                    write_json(folder/'failure.json',failure)
                    # Include a converged unsaved frame when termination falls
                    # between requested output times.
                    if time>times[-1]+1e-9:
                        times.append(time);saved_indices.append(index)
                        audits[-1]['saved_frame']=len(times)-1
                    raise CouplingFailure(str(exc), (times,temperatures,structures,audits,failure)) from exc
                dt/=2
        # Commit only converged states. Mechanical trials never accumulate wear.
        previous=T;time+=dt;index+=1;A['time_s']=time;A['dt_s']=dt;A['cutbacks']=cutback
        append_step(folder,index,time,T,S,A)
        committed_indices.append(index)
        audits.append(A)
        if time>=next_save-1e-9:
            times.append(time);saved_indices.append(index)
            A['saved_frame']=len(times)-1
            if time>=next_save-1e-9:next_save=min(next_save+settings.save_s,settings.duration_s)
        np.savez_compressed(folder/'last-converged.npz',time_s=time,temperature_C=T,displacements=S['displacements'],stresses=S['stresses'],strains=S['strains'],forces=S['forces'])
        write_json(folder/'committed-audits.json',finite_json(audits))
    return times,temperatures,structures,audits
