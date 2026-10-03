"""Typed, local stdio MCP tools for the existing Strength Studio API."""
from typing import Literal
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from strength_client import StudioClient, identifier

mcp = FastMCP('ThermoForge Studio', instructions=(
    '一段长指令包含多项分析及动画报告时，读取 studio_analysis_context，再用 studio_submit_analysis_plan 提交当前项目的计划。'
    '使用本地热、结构、疲劳工作台。多物理场研究使用 studio_study_schema 和 studio_submit_study；静力兼容工况使用原接口。先读取项目真实几何与类型化工况定义，'
    '缺少物理条件须补问；仅在用户授权软件试算时构造并说明假设。'
    '从当前 Codex 直接提交工况，不再嵌套调用 Codex CLI。所有数值须来自真实作业。'))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=READ)
def studio_status() -> dict:
    """读取本地工作台 CPU 配置、求解器及服务状态；不触发求解。"""
    return StudioClient().health()


@mcp.tool(annotations=IDEMPOTENT)
def studio_start() -> dict:
    """启动已安装的本地工作台；已运行时复用。不会重新安装依赖。"""
    return StudioClient().start()


@mcp.tool(annotations=READ)
def studio_projects() -> list[dict]:
    """列出本地项目编号、模型编号和已保存配置。"""
    return StudioClient().request('GET', '/api/v1/projects')


@mcp.tool(annotations=WRITE)
def studio_create_project(name: str) -> dict:
    """创建一个空项目；请求超时后先查项目列表，避免重复创建。"""
    return StudioClient().request('POST', '/api/v1/projects', {'name': name})


@mcp.tool(annotations=READ)
def studio_context(project_id: str) -> dict:
    """读取真实组件/面/命名选区证据、已有工况及作业编号；不得虚构面编号。"""
    return StudioClient().context(project_id)


@mcp.tool(annotations=READ)
def studio_schemas() -> dict:
    """获取后端 LoadCase、ResultQuery 及其引用定义；数值输入使用 SI 单位。"""
    return StudioClient().schemas()


@mcp.tool(annotations=READ)
def studio_materials() -> list[dict]:
    """读取带来源与适用范围的金属/塑料材料预设；预设不能替代批次材料证明。"""
    return StudioClient().request('GET', '/api/v1/materials')


@mcp.tool(annotations=WRITE)
def studio_demo(project_id: str, kind: Literal['bracket', 'bar', 'beam', 'components'] = 'bracket') -> dict:
    """为项目生成内置支架/拉杆/梁/双组件演示模型和明确标注的假设材料。会替换该项目的当前模型。"""
    return StudioClient().request('POST', f'/api/v1/projects/{identifier(project_id)}/demo?kind={kind}')


@mcp.tool(annotations=WRITE)
def studio_import_model(project_id: str, path: str, units: Literal['mm', 'cm', 'm', 'inch'] | None = None) -> dict:
    """导入用户指定绝对路径的 STEP/STP/STL（≤100 MiB）；STL 必须明确单位。会替换项目当前模型。"""
    return StudioClient().import_model(project_id, path, units)


@mcp.tool(annotations=READ)
def studio_validate_case(project_id: str, case: dict) -> dict:
    """按 studio_schemas 的 LoadCase 校验工况和选面，返回真实面映射；不会求解。"""
    return StudioClient().request('POST', f'/api/v1/projects/{identifier(project_id)}/validate', case)


@mcp.tool(annotations=IDEMPOTENT)
def studio_submit_case(project_id: str, case: dict, request_id: str) -> dict:
    """校验后提交真实 CPU 静力作业并立即返回 task id。同一次提交的重试必须复用 request_id；修改工况须使用新 id。"""
    return StudioClient().submit(project_id, case, request_id)


@mcp.tool(annotations=READ)
def studio_task(task_id: str) -> dict:
    """读取任务进度、失败原因和 job_ids；求解不在本调用内阻塞。"""
    return StudioClient().request('GET', f'/api/v1/agent/tasks/{identifier(task_id)}')


@mcp.tool(annotations=IDEMPOTENT)
def studio_cancel(task_id: str) -> dict:
    """用户要求取消时停止指定任务及其计算进程，保留已完成结果。"""
    return StudioClient().request('POST', f'/api/v1/agent/tasks/{identifier(task_id)}/cancel', {})


@mcp.tool(annotations=READ)
def studio_job(job_id: str) -> dict:
    """读取真实作业状态、积分点统计、平衡检查、网格收敛及结果摘要。"""
    return StudioClient().request('GET', f'/api/v1/jobs/{identifier(job_id)}')


@mcp.tool(annotations=READ)
def studio_results(job_id: str) -> dict:
    """读取作业字段目录、单位、数据位置、统计与已保存查询。"""
    return StudioClient().request('GET', f'/api/v1/jobs/{identifier(job_id)}/results')


@mcp.tool(annotations=READ)
def studio_query(job_id: str, query: dict) -> dict:
    """按 ResultQuery 查询探针、路径、相对位移、反力矩、区域统计或刚度。外部点返回无定义，不修改历史结果。"""
    return StudioClient().request('POST', f'/api/v1/jobs/{identifier(job_id)}/results/query', query)


@mcp.tool(annotations=READ)
def studio_compare(project_id: str) -> dict:
    """比较项目已完成工况；不同物理配置的曲线由后端隔离。"""
    return StudioClient().request('GET', f'/api/v1/projects/{identifier(project_id)}/results/comparison')


@mcp.tool(annotations=READ)
def studio_artifacts(job_id: str) -> dict:
    """获取带说明目录的中文 PDF、CSV、VTU、HDF5 与计算包的本机下载地址。"""
    return StudioClient().artifacts(job_id)


@mcp.tool(annotations=WRITE)
def studio_export(kind: Literal['job', 'task'], item_id: str, filename: str, destination: str) -> dict:
    """将指定结果下载到用户指定绝对文件路径；不会覆盖已有文件。PDF 文件名为 report.pdf。"""
    return StudioClient().export(kind, item_id, filename, destination)


@mcp.tool(annotations=READ)
def studio_study_schema() -> dict:
    """统一 AnalysisCase v3：热稳态/瞬态、高周疲劳、周期接触、磨损和热结构。所有输入使用 SI。"""
    return StudioClient().request('GET','/api/v1/studies/schema')


@mcp.tool(annotations=READ)
def studio_study_template(project_id: str, analysis_type: str) -> dict:
    """基于真实项目创建明确标注的演示模板；模板本身不执行计算。"""
    from urllib.parse import urlencode
    return StudioClient().request('GET',f'/api/v1/projects/{identifier(project_id)}/studies/template?'+urlencode({'analysis_type':analysis_type}))


@mcp.tool(annotations=READ)
def studio_validate_study(project_id: str, case: dict) -> dict:
    """使用与手动界面完全相同的类型、材料、几何和选区校验。"""
    return StudioClient().request('POST',f'/api/v1/projects/{identifier(project_id)}/studies/validate',case)


@mcp.tool(annotations=IDEMPOTENT)
def studio_submit_study(project_id: str, case: dict, request_id: str, results_from_study: str | None = None, resume_from_study: str | None = None) -> dict:
    """提交授权的多物理场工况；幂等 request_id 在超时重试时保持不变。返回研究 id。"""
    return StudioClient().request('POST',f'/api/v1/projects/{identifier(project_id)}/studies',{'case':case,'request_id':request_id,'results_from_study':results_from_study,'resume_from_study':resume_from_study})


@mcp.tool(annotations=READ)
def studio_study(study_id: str) -> dict:
    """读取研究状态、原始应力指标、材料缺失和数值校核状态。不得把未验证或演示参数说成验证完成。"""
    return StudioClient().request('GET',f'/api/v1/studies/{identifier(study_id)}')


@mcp.tool(annotations=IDEMPOTENT)
def studio_cancel_study(study_id: str) -> dict:
    """取消研究及其求解进程树，保留已经完成的阶段文件。"""
    return StudioClient().request('POST',f'/api/v1/studies/{identifier(study_id)}/cancel',{})


@mcp.tool(annotations=READ)
def studio_analysis_context(project_id: str) -> dict:
    """通用长指令的真实模型、材料、已保存研究和 AnalysisCase 定义；仅限当前项目。"""
    client=StudioClient()
    return {'context':client.request('GET',f'/api/v1/projects/{identifier(project_id)}/analysis-context'),
            'plan_schema':client.request('GET','/api/v1/analysis-plans/schema')}


@mcp.tool(annotations=IDEMPOTENT)
def studio_submit_analysis_plan(project_id: str, plan: dict, request_id: str, force: bool = False) -> dict:
    """直接提交按 schema 组织的多工况计划，宿主校验后自动求解、生成动画与报告；不嵌套调用模型。"""
    return StudioClient().request('POST',f'/api/v1/projects/{identifier(project_id)}/analysis-plans',
        {'request_id':request_id,'prompt':json.dumps(plan,ensure_ascii=False),'provider':'local','force':force,'execute':True})


@mcp.tool(annotations=READ)
def studio_analysis_plan(plan_id: str) -> dict:
    """读取计划校验、缺项或其自动提交的 run_id。"""
    return StudioClient().request('GET',f'/api/v1/analysis-plans/{identifier(plan_id)}')


@mcp.tool(annotations=READ)
def studio_analysis_run(run_id: str) -> dict:
    """读取通用分析批次、各工况状态及视频、报告、ZIP 产物清单。"""
    return StudioClient().request('GET',f'/api/v1/analysis-runs/{identifier(run_id)}')


@mcp.tool(annotations=IDEMPOTENT)
def studio_cancel_analysis_plan(plan_id: str) -> dict:
    """取消本计划的解析或已提交的批次；保留完整结果，不影响其他批次。"""
    return StudioClient().request('POST',f'/api/v1/analysis-plans/{identifier(plan_id)}/cancel',{})


@mcp.tool(annotations=IDEMPOTENT)
def studio_reexport_analysis(run_id: str, request_id: str, scope: Literal['report','video','all']='all') -> dict:
    """仅从已有原始场生成新报告/视频交付版本，不启动求解，不覆盖旧交付。"""
    return StudioClient().request('POST',f'/api/v1/analysis-runs/{identifier(run_id)}/exports',{'request_id':request_id,'scope':scope})


@mcp.tool(annotations=READ)
def studio_project_comparison(project_id: str) -> dict:
    """统一比较旧静力与新研究；不同几何、分析或时间轴明确标记不可直接相减。"""
    return StudioClient().request('GET',f'/api/v1/projects/{identifier(project_id)}/analysis-comparison')


if __name__ == '__main__':
    mcp.run(transport='stdio')
