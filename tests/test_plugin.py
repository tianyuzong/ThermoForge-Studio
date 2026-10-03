"""Adapter boundaries and source package privacy, without a running desktop."""
import json
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from strength_client import StudioClient, StudioError, identifier, local_url
from package_plugin import release_files, package
from configure_plugin import configure


@pytest.mark.parametrize('url', ['https://127.0.0.1:8876', 'http://example.com:80',
    'http://127.0.0.1:8876@evil.com:80', 'http://127.0.0.1:8876/x',
    'http://localhost:8876?host=evil', 'http://user@localhost:8876', 'http://127.0.0.1'])
def test_client_rejects_nonlocal_or_ambiguous_destinations(url):
    with pytest.raises(StudioError):
        local_url(url)


def test_client_refuses_redirect_and_ignores_proxy(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith('redirect'):
                self.send_response(302)
                self.send_header('Location', 'https://example.com/private-data')
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true,"app":"Strength Studio"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
        client = StudioClient({'workspace': str(tmp_path), 'url': f'http://127.0.0.1:{server.server_port}'})
        assert client.health()['ok']
        with pytest.raises(StudioError, match='重定向'):
            client.request('GET', '/api/v1/redirect')
        with pytest.raises(StudioError):
            identifier('../../private')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_model_and_export_guardrails(tmp_path):
    client = StudioClient({'workspace': str(tmp_path), 'url': 'http://127.0.0.1:8876'})
    source = tmp_path / '中文模型.stl'
    source.write_text('solid test\nendsolid test', encoding='utf-8')
    with pytest.raises(StudioError, match='单位'):
        client.import_model('project', str(source))
    target = tmp_path / '已有报告.pdf'
    target.write_bytes(b'original report')
    with pytest.raises(StudioError, match='已存在'):
        client.export('job', 'job', 'report.pdf', str(target))
    assert target.read_bytes() == b'original report'
    with pytest.raises(StudioError):
        client.export('job', 'job', '../../auth.json', str(tmp_path / 'new'))


def test_package_and_install_exclude_private_data_and_preserve_catalog(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    public = {'.codex-plugin/plugin.json': json.dumps({'name': 'strength-studio', 'version': '0.5.0'}),
              'strength/server.py': '# public', 'frontend/dist/index.html': '<html/>',
              'skills/structural-simulation/SKILL.md': 'public skill',
              'README.md': '中文', 'README.en.md': 'English'}
    private = ['data/models/secret.step', '.runtime/server.log', '.venv/auth.json',
               'local-settings.json', 'frontend/.visual-backups/private.tsx',
               'docs/GEAR_CONTACT_GEOMETRY.json', 'docs/secret.key',
               'scripts/__pycache__/private.pyc', 'strength/.env', 'vendor/ccx.exe']
    for name, content in {**public, **dict.fromkeys(private, 'private')}.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    assert {p.relative_to(source).as_posix() for p in release_files(source)} == set(public)
    artifact = package(source, tmp_path / 'release.zip')
    assert artifact['files'] == len(public)
    user = tmp_path / 'user'
    market = user / '.agents/plugins/marketplace.json'
    market.parent.mkdir(parents=True)
    other = {'name': 'existing', 'source': {'source': 'local', 'path': './plugins/existing'}}
    market.write_text(json.dumps({'name': 'personal', 'plugins': [other]}), encoding='utf-8')
    settings = tmp_path / 'plugin-settings.json'
    monkeypatch.setenv('STRENGTH_PLUGIN_CONFIG', str(settings))
    result = configure(source, 'http://127.0.0.1:8876', Path(sys.executable), user)
    assert result['selector'] == 'strength-studio@personal'
    assert json.loads(market.read_text(encoding='utf-8'))['plugins'][0] == other
    assert json.loads(settings.read_text(encoding='utf-8'))['workspace'] == str(source.resolve())
    assert not (Path(result['plugin']) / 'data').exists()
