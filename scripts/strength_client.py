"""Local-only API adapter shared by the Codex MCP tools and diagnostic CLI.

This module deliberately uses only the standard library and never imports the
solver, starts Codex, or reads Codex login credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import uuid


class StudioError(RuntimeError):
    pass


def config_path() -> Path:
    return Path(os.environ.get('STRENGTH_PLUGIN_CONFIG', str(
        Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.config'))) /
        'StrengthStudio' / 'plugin.json'))).expanduser()


def read_config() -> dict:
    root=Path(__file__).resolve().parents[1]
    if (root/'SOURCE_VERSIONS.json').is_file() and not os.environ.get('STRENGTH_PLUGIN_CONFIG'):
        return {'workspace':str(root),'url':os.environ.get('THERMOFORGE_URL','http://127.0.0.1:8877')}
    path = config_path()
    if not path.is_file():
        raise StudioError('尚未配置插件。请从源码目录运行 Install-CodexPlugin.ps1。')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def local_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost')
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ('', '/') or not parts.port):
        raise StudioError('工作台地址必须为 http://127.0.0.1:端口 或 http://localhost:端口。')
    return value.rstrip('/')


def identifier(value: str) -> str:
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value):
        raise StudioError('项目、模型、任务或作业编号非法。')
    return quote(value, safe='')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StudioError('本地工作台不应重定向到其他地址。')


class StudioClient:
    def __init__(self, config: dict | None = None):
        self.config = read_config() if config is None else config
        self.url = local_url(self.config.get('url', 'http://127.0.0.1:8877'))
        self.workspace = Path(self.config['workspace']).expanduser().resolve()
        # CAD and numerical results must never pass through the system proxy.
        self.http = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method: str, path: str, payload=None, *, data=None,
                content_type='application/json', binary=False, timeout=150):
        if not (path.startswith('/api/v1/') or path == '/openapi.json') or '..' in path:
            raise StudioError('仅允许工作台的固定 API 路径。')
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        req = Request(self.url + path, data=data, method=method,
                      headers={'Content-Type': content_type, 'Accept': '*/*' if binary else 'application/json'})
        try:
            with self.http.open(req, timeout=timeout) as response:
                raw = response.read()
                return raw if binary else json.loads(raw)
        except HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:8000]
            raise StudioError(f'工作台返回 HTTP {exc.code}: {detail}') from exc
        except (URLError, TimeoutError) as exc:
            raise StudioError('无法连接本地工作台或请求超时。先检查状态；写入请求不要盲目重复，提交工况须复用 request_id。') from exc

    def health(self):
        result = self.request('GET', '/api/v1/health', timeout=5)
        if result.get('app') not in ('Strength Studio','ThermoForge Studio'):
            raise StudioError('指定端口不是 Strength Studio 服务。')
        return result

    def start(self):
        try:
            return self.health()
        except StudioError:
            pass
        script = self.workspace / 'Start.ps1'
        if not script.is_file():
            raise StudioError('配置的源码目录缺少 Start.ps1，请重新运行插件安装脚本。')
        if not (self.workspace / '.venv' / 'Scripts' / 'python.exe').is_file():
            raise StudioError('工作台依赖尚未安装，请先运行 Install.ps1。')
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(script), '-Port', str(urlsplit(self.url).port), '-NoBrowser'],
            cwd=self.workspace, capture_output=True, timeout=120,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise StudioError('工作台启动失败，请查看项目 .runtime/server-error.log 并运行 Diagnose-CPU.ps1。')
        return self.health()

    def context(self, project_id: str):
        project_id = identifier(project_id)
        project = self.request('GET', f'/api/v1/projects/{project_id}')
        geometry = self.request('GET', f'/api/v1/models/{identifier(project["model_id"])}') if project.get('model_id') else None
        jobs = self.request('GET', '/api/v1/jobs?' + urlencode({'project_id': project_id}))
        return {'project': project, 'geometry': geometry,
                'saved_cases': self.request('GET', f'/api/v1/projects/{project_id}/cases'),
                'jobs': [{k: j.get(k) for k in ('id', 'task_id', 'name', 'model_id', 'status', 'detail')} for j in jobs],
                'workbench_url': self.url + '/?' + urlencode({'project': project_id})}

    def schemas(self):
        schemas = self.request('GET', '/openapi.json')['components']['schemas']
        # Preserve reference targets; the API remains the single schema authority.
        return {'LoadCase': schemas['LoadCase'], 'ResultQuery': schemas['ResultQuery'],
                'components': {'schemas': schemas},
                'units': 'SI：坐标 m、力 N、压力/应力 Pa、弹性模量 Pa、密度 kg/m³；导入 STL 必须指定单位。'}

    def import_model(self, project_id: str, path: str, units: str | None = None):
        source = Path(path).expanduser()
        if not source.is_absolute() or not source.is_file():
            raise StudioError('模型须为用户指定的本机文件绝对路径。')
        if source.suffix.lower() not in ('.step', '.stp', '.stl'):
            raise StudioError('仅支持 STEP、STP、STL 文件。')
        if source.stat().st_size > 100 * 1024 * 1024:
            raise StudioError('模型文件超过 100 MiB 上限。')
        if units is not None and units not in ('mm', 'cm', 'm', 'inch'):
            raise StudioError('单位须为 mm、cm、m 或 inch。')
        if source.suffix.lower() == '.stl' and units is None:
            raise StudioError('STL 没有单位信息，必须先明确单位。')
        boundary = 'strength-' + uuid.uuid4().hex
        # Fixed ASCII multipart filename avoids newline/quote injection; user path is never sent.
        chunks = []
        if units is not None:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="units"\r\n\r\n{units}\r\n'.encode())
        chunks.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="model{source.suffix.lower()}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
            source.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()])
        return self.request('POST', f'/api/v1/projects/{identifier(project_id)}/models',
                            data=b''.join(chunks), content_type=f'multipart/form-data; boundary={boundary}')

    def submit(self, project_id: str, case: dict, request_id: str):
        if not 1 <= len(request_id) <= 160:
            raise StudioError('request_id 必须为 1–160 字符；同一次提交重试时保持不变。')
        project_id = identifier(project_id)
        if case.get('assembly_mode') == 'gear_contact':
            raise StudioError('本插件仅提交 CPU 静力工况，不支持 CPU 齿轮接触。')
        self.request('POST', f'/api/v1/projects/{project_id}/validate', case)
        # The CPU plugin explicitly selects CPU, including projects previously set to GPU.
        self.request('PATCH', f'/api/v1/projects/{project_id}', {'compute_backend': 'calculix_cpu'})
        return self.request('POST', f'/api/v1/projects/{project_id}/jobs', {'case': case, 'request_id': request_id})

    def artifacts(self, job_id: str):
        job_id = identifier(job_id)
        job = self.request('GET', f'/api/v1/jobs/{job_id}')
        if job.get('status') != 'completed':
            return {'available': False, 'status': job.get('status'), 'detail': job.get('detail')}
        names = ('report.pdf', 'result.zip', 'metrics.csv', 'field-statistics.csv', 'result.vtu', 'fields.h5', 'summary.json')
        return {'available': True, 'job_id': job_id,
                'files': [{'name': n, 'url': f'{self.url}/api/v1/jobs/{job_id}/files/{n}'} for n in names]}

    def export(self, kind: str, item_id: str, filename: str, destination: str):
        allowed = {'job': {'report.pdf', 'result.zip', 'metrics.csv', 'field-statistics.csv', 'result.vtu', 'fields.h5', 'summary.json'},
                   'task': {'report.pdf', 'report.md', 'comparison.csv', 'comparison.json', 'feedback.json'}}
        if kind not in allowed or filename not in allowed[kind]:
            raise StudioError('不支持的导出文件类型。')
        target = Path(destination).expanduser()
        if not target.is_absolute():
            raise StudioError('导出目的地必须为绝对文件路径。')
        if target.exists():
            raise StudioError('目的文件已存在，请选择新文件名以保留已有报告。')
        prefix = 'jobs' if kind == 'job' else 'agent/tasks'
        raw = self.request('GET', f'/api/v1/{prefix}/{identifier(item_id)}/files/{filename}', binary=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(raw)
        return {'path': str(target), 'bytes': len(raw), 'source_id': item_id}


def main():
    parser = argparse.ArgumentParser(description='Strength Studio 插件诊断/备用接口')
    parser.add_argument('action', choices=['health', 'start', 'projects', 'context', 'schemas'])
    parser.add_argument('--project-id')
    args = parser.parse_args()
    client = StudioClient()
    if args.action == 'context' and not args.project_id:
        parser.error('context 需要 --project-id')
    actions = {'health': client.health, 'start': client.start, 'schemas': client.schemas,
               'projects': lambda: client.request('GET', '/api/v1/projects'),
               'context': lambda: client.context(args.project_id)}
    print(json.dumps(actions[args.action](), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
