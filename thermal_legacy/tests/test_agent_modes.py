import pytest
import json
import os
import subprocess
from types import SimpleNamespace

import thermal_legacy.agent as agent
import thermal_legacy.agent_skill as agent_skill

from thermal_legacy.agent import _cli_response_text, _json_from_text, _response_text, codex_plan_request
from thermal_legacy.schemas import AgentRequest, Simulation


@pytest.fixture
def prepared_cli(monkeypatch):
    def prepare(engine, model_id, prompt, current, conversation=None):
        original = Simulation(model_id=model_id, **{k:v for k,v in current.items() if k != 'model_id'}).model_dump()
        return dict(model_id=model_id, original=original, candidate=original, warnings=[], public={},
                    selectors={'top': {'faces':[0], 'face_count':1}})
    monkeypatch.setattr(agent_skill, 'prepare', prepare)


def test_agent_request_mode_defaults_to_local():
    assert AgentRequest(model_id='model_1', prompt='热源').mode == 'local'
    assert AgentRequest(model_id='model_1', prompt='热源', mode='codex').mode == 'codex'
    with pytest.raises(ValueError):
        AgentRequest(model_id='model_1', prompt='热源', mode='remote')


def test_codex_mode_reports_missing_key_without_local_fallback(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('THERMAL_CODEX_PROVIDER', 'api')
    result = codex_plan_request('missing-model', '铝件 10 W', {})
    assert result['ok'] is False
    assert result['mode'] == 'codex'
    assert 'OPENAI_API_KEY' in result['questions'][0]


def test_extract_structured_response_json():
    payload = {'output': [{'content': [{'type': 'output_text', 'text': '{"model_id":"x"}'}]}]}
    assert _json_from_text(_response_text(payload)) == {'model_id': 'x'}
    assert _json_from_text('```json\n{"ok": true}\n```') == {'ok': True}


def test_extract_cli_jsonl_agent_message():
    output = '\n'.join([
        '{"type":"thread.started","thread_id":"t"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"{\\"model_id\\":\\"x\\"}"}}',
    ])
    assert _cli_response_text(output) == '{"model_id":"x"}'


def test_cli_uses_only_last_completed_message():
    events = [[], {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Working'}},
              {'type': 'item.started', 'item': {'type': 'agent_message', 'text': 'Incomplete'}},
              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"model_id":"x"}'}}]
    assert _cli_response_text('\n'.join(json.dumps(event) for event in events)) == '{"model_id":"x"}'


def test_cli_inherits_system_proxy_only_in_child(monkeypatch):
    for key in list(os.environ):
        if key.lower().endswith('_proxy'):
            monkeypatch.delenv(key)
    monkeypatch.setenv('NO_PROXY', 'internal.example')
    monkeypatch.setattr(agent.urllib.request, 'getproxies_registry',
                        lambda: {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}, raising=False)
    child = agent._codex_cli_env()
    assert child['HTTPS_PROXY'] == 'http://127.0.0.1:7897'
    assert child['HTTP_PROXY'] == 'http://127.0.0.1:7897'
    assert set(child['NO_PROXY'].split(',')) == {'internal.example', 'localhost', '127.0.0.1', '::1'}
    assert 'HTTPS_PROXY' not in os.environ
    assert os.environ['NO_PROXY'] == 'internal.example'


def test_cli_preserves_explicit_proxy(monkeypatch):
    monkeypatch.setenv('https_proxy', 'http://configured:8000')
    monkeypatch.setattr(agent.urllib.request, 'getproxies_registry',
                        lambda: pytest.fail('Explicit proxy must take precedence'), raising=False)
    child = {key.lower(): value for key, value in agent._codex_cli_env().items()}
    assert child['https_proxy'] == 'http://configured:8000'


@pytest.mark.parametrize('raw', ['0', '601', 'nan', '10.5'])
def test_cli_rejects_invalid_timeout(monkeypatch, raw):
    monkeypatch.setenv('THERMAL_CODEX_TIMEOUT_S', raw)
    with pytest.raises(ValueError, match='THERMAL_CODEX_TIMEOUT_S'):
        agent._codex_cli_timeout()


def test_cli_timeout_reports_upstream_failure_and_keeps_config(monkeypatch, prepared_cli):
    monkeypatch.setattr(agent, '_codex_prompt', lambda *args: 'test prompt')
    monkeypatch.setenv('THERMAL_CODEX_TIMEOUT_S', '180')
    current = {'model_id': 'x', 'duration_s': 60}
    def timed_out(args, **kwargs):
        assert kwargs['timeout'] == 180
        assert 'localhost' in kwargs['env']['NO_PROXY']
        raise subprocess.TimeoutExpired(args, 180, output=b'{"type":"turn.started"}\n',
                                        stderr=b'WARN request timed out; stream disconnected - retrying')
    monkeypatch.setattr(agent.subprocess, 'run', timed_out)
    result = agent._codex_cli_plan_request('x', 'test', current, 'codex.exe')
    assert not result['ok']
    assert result['error_code'] == 'timeout'
    assert result['config'] == current
    assert '上游连接超时' in result['questions'][0]
    assert '无法启动' not in result['questions'][0]
    assert 'request timed out' in result['diagnostic']


def test_cli_startup_error_is_distinct(monkeypatch, prepared_cli):
    monkeypatch.setattr(agent, '_codex_prompt', lambda *args: 'test prompt')
    def missing(*args, **kwargs):
        raise FileNotFoundError('Executable not found')
    monkeypatch.setattr(agent.subprocess, 'run', missing)
    result = agent._codex_cli_plan_request('x', 'test', {}, 'missing.exe')
    assert result['error_code'] == 'startup'


def test_cli_success_still_returns_validated_config(monkeypatch, prepared_cli):
    monkeypatch.setattr(agent, '_codex_prompt', lambda *args: 'test prompt')
    monkeypatch.setattr(agent, 'read_json', lambda path: {'triangles': 12})
    review = dict(patch=[dict(path='/duration_s',value_json='600'),
                        dict(path='/heat_sources',value_json='[{"power_W":20,"surface_selection":"top"}]')], questions=[])
    event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(review)}}
    def run(args, **kwargs):
        assert '--output-schema' in args
        assert args[args.index('--sandbox')+1] == 'read-only'
        assert '--disable' in args and 'shell_tool' in args and 'apps' in args
        schema = json.loads(open(args[args.index('--output-schema')+1],encoding='utf-8').read())
        assert schema['additionalProperties'] is False
        return SimpleNamespace(returncode=0, stdout=json.dumps(event), stderr='')
    monkeypatch.setattr(agent.subprocess, 'run', run)
    result = agent._codex_cli_plan_request('x', 'test', {}, 'codex.exe')
    assert result['ok']
    assert result['config']['duration_s'] == 600
    assert result['workflow'] == 'thermal-config'
    assert result['metrics']['tool_calls'] == 0


def test_cli_diagnostics_redact_credentials():
    detail = agent._cli_error_detail(b'', b'error: Authorization: Bearer secret-value sk-test-secret')
    assert 'secret-value' not in detail
    assert 'sk-test-secret' not in detail
