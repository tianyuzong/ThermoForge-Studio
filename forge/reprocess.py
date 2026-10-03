"""New immutable postprocessing studies that reuse verified identical raw physics."""
import json,shutil,hashlib
import h5py,numpy as np
from strength.config import DATA
from strength.store import write_json
from strength.result_catalog import finite_json
from .schemas import AnalysisCase
from .results import write_report,frame_display,finalize_archive


def physics(case):
    value=case.model_dump()
    for key in ('name','assumptions','clearance_probes','fatigue','resources'):value.pop(key,None)
    for m in [value['material'],*value['component_materials'].values()]:
        for key in ('name','source','sn_curve','findley','yield_strength_Pa','tensile_strength_Pa'):m.pop(key,None)
    return value


def validate_source(case,parent):
    old=AnalysisCase.model_validate(parent['case'])
    if physics(case)!=physics(old):raise ValueError('复用原始场只允许修改疲劳评估、间隙探针、来源说明或资源；几何、网格、材料物性及边界必须完全一致')


def run(folder,config,case,geo):
    source=DATA/'studies'/config['reprocess_from']
    parent=json.loads((source/'config.json').read_text(encoding='utf-8'));validate_source(case,parent)
    if parent.get('sha256') and hashlib.sha256(json.dumps({k:v for k,v in parent.items() if k!='sha256'},sort_keys=True).encode()).hexdigest()!=parent['sha256']:raise ValueError('来源工况快照校验失败')
    with (source/'fields.h5').open('rb') as stream:source_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    manifest=source/'raw-fields.sha256.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))['sha256']!=source_hash:raise ValueError('来源原始场文件校验失败')
    summary=json.loads((source/'summary.json').read_text(encoding='utf-8'))
    summary.update(name=case.name,derived_from=config['reprocess_from'],postprocess_only=True,assumptions=case.assumptions)
    shutil.copy2(source/'fields.h5',folder/'fields.h5');shutil.copy2(source/'mesh.npz',folder/'mesh.npz')
    mesh=dict(np.load(folder/'mesh.npz'));fatigue=None
    write_json(folder/'status.json',{'status':'analyzing','detail':'复用不可变原始场，重新计算疲劳／间隙探针'})
    with h5py.File(folder/'fields.h5','a') as f:
        times=f['times_s'][:].tolist();structures=[{'displacements':f['displacements'][i]} for i in range(len(times))] if 'displacements' in f else []
        if case.analysis_type in ('fatigue','contact_fatigue'):
            if case.wear_blocks>1:raise ValueError('改变磨损几何的多循环块请重新提交完整计算')
            from .worker import evaluate_fatigue,accumulate_fatigue_block
            from .contact import corotate_stresses
            stress=[]
            for i in range(len(times)):
                frame={'axles':json.loads(f['native_contact'][str(i)].attrs.get('axles_json','{}'))}
                stress.append(corotate_stresses(f['stresses'][i],mesh,case,i,frame) if case.analysis_type=='contact_fatigue' else f['stresses'][i])
            fatigue=evaluate_fatigue(mesh,case,np.asarray(stress))
            if case.analysis_type=='contact_fatigue':fatigue=accumulate_fatigue_block(mesh,case,None,fatigue,0)
            if 'fatigue' in f:del f['fatigue']
            g=f.create_group('fatigue')
            for key in ('life_cycles','damage','evaluated'):g.create_dataset(key,data=fatigue[key],compression='gzip')
            if 'accumulated_block_damage' in fatigue:g.create_dataset('accumulated_block_damage',data=fatigue['accumulated_block_damage'],compression='gzip')
            g.attrs.update(location='integration_point',life_definition=fatigue['life_definition'])
            summary['fatigue']={k:v for k,v in fatigue.items() if not isinstance(v,np.ndarray)}
            summary['convergence']={'status':'not_verified','note':'疲劳评估参数或实现更新；需要重新核对寿命收敛'}
        if 'clearance_probes' in f:del f['clearance_probes']
        summary.pop('clearance_probes',None)
        if case.clearance_probes:
            if not structures:raise ValueError('间隙探针需要结构位移场')
            from .probes import clearance_history
            probes=clearance_history(mesh,case,geo,structures,times);group=f.create_group('clearance_probes')
            for name,values in probes.items():
                g=group.create_group(name)
                for key,value in values.items():
                    if isinstance(value,str):g.attrs[key]=value
                    else:g.create_dataset(key,data=value)
            summary['clearance_probes']={name:{k:v for k,v in values.items() if not isinstance(v,np.ndarray)} for name,values in probes.items()}
        f.attrs['derived_from_study']=config['reprocess_from']
    finalize_archive(folder)
    write_json(folder/'summary.json',finite_json(summary));write_json(folder/'audit.json',{'source_study':config['reprocess_from'],'source_snapshot_sha256':parent.get('sha256'),'source_fields_sha256':source_hash,'source_manifest_available':manifest.exists(),'raw_physics_reused':True})
    write_json(folder/'display.json',frame_display(folder,-1));write_report(folder,case,summary)
    partial=bool(fatigue and fatigue['status']!='evaluated') or bool(summary.get('solve_failure')) or any(summary.get(key,{}).get('passed') is False for key in ('thermal_balance','mechanical_balance'))
    write_json(folder/'status.json',{'status':'partial' if partial else 'completed','detail':'原始场复用与新评估已保存；源结果未修改'})
