"""Real stdio MCP -> local API -> CalculiX -> probe -> PDF acceptance.

Creates its own small demo project. Does not touch existing models or tasks.
Run using .runtime/plugin-env/Scripts/python.exe after configuring the plugin.
"""
import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main(launcher: Path, output: Path):
    params = StdioServerParameters(command='powershell.exe', args=[
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(launcher)])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=180)) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f'MCP connected: {len(tools.tools)} tools', flush=True)

            async def call(tool_name, **args):
                result = await session.call_tool(tool_name, args)
                if result.isError:
                    raise RuntimeError(str(result.content))
                return json.loads(next(c.text for c in result.content if c.type == 'text'))

            health = await call('studio_status')
            assert health['solver_available']
            assert health['execution']['gpu_diagnostic']['cuda_initialized'] is False
            project = await call('studio_create_project', name='Codex 插件 · 标准拉杆验收')
            pid = project['id']
            await call('studio_demo', project_id=pid, kind='bar')
            context = await call('studio_context', project_id=pid)
            schemas = await call('studio_schemas')
            assert 'ResultQuery' in schemas
            materials = await call('studio_materials')
            assert materials
            query = {'name': '端面轴心位移', 'kind': 'probe', 'field': 'displacement_x', 'point_m': [.1, .005, .005]}
            case = {'name': '插件拉杆 1000 N', 'material': context['project']['material'],
                    'supports': [{'name': '左端固定', 'selection': {'kind': 'direction', 'direction': '-x'}}],
                    'loads': [{'name': '轴向拉力', 'kind': 'force', 'selection': {'kind': 'direction', 'direction': '+x'}, 'vector': [1000, 0, 0]}],
                    'mesh': {'size_m': .006}, 'result_requests': [query]}
            await call('studio_validate_case', project_id=pid, case=case)
            request_id = 'plugin-smoke-' + uuid.uuid4().hex
            task = await call('studio_submit_case', project_id=pid, case=case, request_id=request_id)
            duplicate = await call('studio_submit_case', project_id=pid, case=case, request_id=request_id)
            assert duplicate['id'] == task['id']
            for attempt in range(100):
                task = await call('studio_task', task_id=task['id'])
                if task['status'] in ('completed', 'failed', 'cancelled', 'partial', 'interrupted'):
                    break
                if attempt % 6 == 0:
                    print('Task:', task['status'], flush=True)
                await asyncio.sleep(3)
            assert task['status'] == 'completed', task
            assert len(task['job_ids']) == 1
            job_id = task['job_ids'][0]
            job = await call('studio_job', job_id=job_id)
            fields = await call('studio_results', job_id=job_id)
            probe = await call('studio_query', job_id=job_id, query=query)
            assert probe['status'] == 'ok' and probe['unit'] == 'm'
            assert abs(probe['value'] / (1000 * .1 / (210e9 * .01 * .01)) - 1) < .01
            assert job['status'] == 'completed' and fields['fields']
            comparison = await call('studio_compare', project_id=pid)
            artifacts = await call('studio_artifacts', job_id=job_id)
            assert artifacts['available']
            output.mkdir(parents=True, exist_ok=True)
            pdf = output / ('插件拉杆验收-' + job_id + '.pdf')
            exported = await call('studio_export', kind='job', item_id=job_id, filename='report.pdf', destination=str(pdf.resolve()))
            assert pdf.read_bytes().startswith(b'%PDF-')
            record = {'tools': [t.name for t in tools.tools], 'health': health, 'project_id': pid,
                      'task_id': task['id'], 'job_id': job_id, 'status': task['status'],
                      'probe': probe, 'summary': job['summary'], 'comparison': comparison,
                      'pdf': exported, 'duplicate_submission_same_task': True}
            (output / 'acceptance.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'status': 'passed', 'project': pid, 'job': job_id, 'pdf': str(pdf)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--launcher', type=Path, default=ROOT / 'scripts/Start-Mcp.ps1')
    parser.add_argument('--output', type=Path, default=ROOT / '.runtime/plugin-acceptance')
    args = parser.parse_args()
    asyncio.run(main(args.launcher, args.output))
