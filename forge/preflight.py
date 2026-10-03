"""One geometry-aware preflight contract for UI, natural language and MCP."""
import os
from strength.geometry import resolve_selection
from .schemas import coupling_mode


def selections_for(case):
    values=[s.selection for s in case.supports]+[l.selection for l in case.loads if l.selection]+case.mesh.local_selections
    if case.mesh.evaluation_selection:values.append(case.mesh.evaluation_selection)
    values += [s.selection for s in case.thermal.boundaries if s.selection]+[m.selection for m in case.motions]+[a.selection for a in case.axles]
    for p in case.clearance_probes:values += [p.inner,p.outer]
    for pair in [*case.contacts,*case.bonded_connections,*case.thermal_connections]:values += [pair.slave,pair.master]
    values += [s.thermostat.sensor for s in case.thermal.boundaries if s.thermostat]
    return values


def validate_case(case,geometry):
    if case.resources.cpu_threads>(os.cpu_count() or 1):raise ValueError('CPU 线程数超过本机逻辑核心数')
    if case.geometry_sha256 and case.geometry_sha256!=geometry['source_sha256']:raise ValueError('工况选区绑定了另一版几何')
    bodies={int(c['id']) for c in geometry.get('components',[])}
    if not bodies:raise ValueError('几何中没有可分析组件')
    if set(case.component_materials)-{str(b) for b in bodies}:raise ValueError('材料引用了不存在的组件')
    if len(bodies)>1 and {str(b) for b in bodies}-set(case.component_materials):raise ValueError('装配体每个组件都需要明确材料')
    references=[('疲劳评估',b) for b in case.fatigue.body_ids]
    references += [(b.name,b.body_id) for b in case.thermal.boundaries if b.body_id is not None]
    references += [(l.name,l.body_id) for l in case.loads if l.body_id is not None]
    references += [(m.name,m.body_id) for m in [*case.motions,*case.axles]]
    for name,body in references:
        if body not in bodies:raise ValueError(f'{name}：引用了不存在的组件 {body}')
    selections=selections_for(case)
    for selection in selections:
        if selection.body_id is not None and selection.body_id not in bodies:raise ValueError(f'选区引用了不存在的组件 {selection.body_id}')
        resolve_selection(selection,geometry)
    for load in case.loads:
        if load.kind!='gravity' and load.selection is None:raise ValueError(f'{load.name}：表面载荷须指定真实选区')
        if load.selection and load.body_id is not None and load.selection.body_id not in (None,load.body_id):raise ValueError(f'{load.name}：载荷组件与选区组件不一致')
    thermal=case.analysis_type in ('thermal_steady','thermal_transient','thermo_mechanical')
    if not thermal or case.analysis_type=='thermo_mechanical':
        if not (case.supports or case.axles or case.motions):raise ValueError('缺少机械支撑或运动约束')
    if thermal and not case.thermal.boundaries:raise ValueError('缺少热边界，请明确给出，不推测散热条件')
    if case.mesh.convergence and case.thermal.initial_from_study:raise ValueError('温度续算要求节点身份一致；自动网格加密需先提供各档匹配网格的来源温度场，不能跨网格推测初始场')
    return {'ok':True,'selections':len(selections),'geometry_sha256':geometry['source_sha256'],'analysis_type':case.analysis_type,'coupling_mode':coupling_mode(case)}


def validate_initial_dependency(case,parent,model_id,project_id=None,allow_pending=False):
    if parent.get('model_id')!=model_id:raise ValueError('温度续算来源属于另一个模型')
    if project_id is not None and parent.get('project_id')!=project_id:raise ValueError('温度续算须引用当前项目的结果')
    allowed={'completed'}|({'queued','waiting','meshing','solving','analyzing','reporting'} if allow_pending else set())
    if parent.get('status') not in allowed:raise ValueError('温度续算须引用完整热结果；未完成依赖须先成功完成')
    if parent.get('case',{}).get('analysis_type') not in ('thermal_steady','thermal_transient','thermo_mechanical'):raise ValueError('温度续算来源不是热结果')
    if case.analysis_type not in ('thermal_steady','thermal_transient','thermo_mechanical'):raise ValueError('仅热分析和热结构分析可使用温度续算来源')
    return True
