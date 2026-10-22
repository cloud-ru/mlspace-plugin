"""Optional real OpenCode CLI journeys; only local test credentials and API."""
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import test_setup_native_journey as journeys

from mlspace_mcp.client_setup import _read_config, _skill_sources, install_state_file

local_api = journeys.local_api
setup_environment = journeys.setup_environment


pytestmark = pytest.mark.usefixtures('isolated_client_environment')


@pytest.fixture
def opencode(setup_environment, monkeypatch):
    executable = shutil.which('opencode')
    if not executable:
        pytest.skip('OpenCode CLI is not installed')
    monkeypatch.setenv('OPENCODE_DISABLE_AUTOUPDATE', 'true')
    monkeypatch.setenv('OPENCODE_DISABLE_MODELS_FETCH', 'true')
    return executable


def cli(home, command, *, timeout=45):
    # The installed CLI truncates large `debug skill` JSON at 64 KiB when piped.
    # A regular file captures the complete inventory, including skill contents.
    with tempfile.TemporaryFile(mode='w+') as output:
        result = subprocess.run(command, cwd=home, text=True, stdout=output,
                                stderr=subprocess.PIPE, timeout=timeout)
        output.seek(0)
        result.stdout = output.read()
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def terminal(home, command, steps):
    master, slave = os.openpty()
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, cwd=home)
    os.close(slave)
    transcript = b''
    remaining = b''
    sent = 0
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                transcript += chunk
                remaining += chunk
                if sent < len(steps) and steps[sent][0] in remaining:
                    os.write(master, steps[sent][1])
                    remaining = b''
                    sent += 1
            if process.poll() is not None:
                break
        code = process.wait(timeout=3)
        assert sent == len(steps), transcript.decode(errors='replace')
        return code, transcript.decode(errors='replace')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def custom_skill(root, name, marker):
    path = root / 'skills' / name / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nname: {name}\ndescription: User workflow for MLSpace QA.\n---\n\n{marker}\n')
    return path


def prepare_legacy(home):
    root = home / 'custom-config/opencode'
    edited = custom_skill(root, 'mlspace-list-running-jobs', 'USER_MODIFIED_WORKFLOW')
    extra = custom_skill(root, 'mlspace-my-qa-workflow', 'USER_EXTRA_WORKFLOW')
    helper = edited.parent / 'helper.txt'
    helper.write_text('user supporting file')
    config = root / 'opencode.jsonc'
    config.write_text('{ // user comment\n "mcp": {"mlspace": {"type":"local",'
                      '"command":["/old/mcp-mlspace-wrapper.sh"]}}, "theme":"system"\n}')
    return config, edited, extra


@pytest.mark.parametrize('choice', ['k', 'r'], ids=['keep-user-skill', 'backup-and-replace'])
def test_real_opencode_loads_mcp_and_user_skills_after_setup_and_retry(
        setup_environment, local_api, opencode, choice):
    home = setup_environment
    endpoint, pem, calls = local_api
    config, edited, extra = prepare_legacy(home)
    original = edited.read_bytes()
    extra_content = extra.read_bytes()
    command = [sys.executable, '-m', 'mlspace_mcp', 'setup', '--client-path', f'opencode={opencode}',
               '--base-url', endpoint, '--ca-file', str(pem), '--workspace', 'workspace-001',
               '--workspace', 'workspace-299']
    code, output = terminal(home, command, [
        ('[r] Создать резервную копию конфига'.encode(), b'r\r'),
        ('[k] Сохранить мой skill'.encode(), choice.encode() + b'\r'),
        (b'Cloud.ru Key ID:', b'dummy-id\r'),
        (b'Cloud.ru Key Secret:', b'dummy-secret\r'),
    ])
    assert code == 0, output
    assert output.index(str(edited)) < output.index('Cloud.ru Key ID:')
    assert 'dummy-id' not in output and 'dummy-secret' not in output
    assert '// user comment' in config.read_text()
    assert _read_config(config)['theme'] == 'system'
    assert extra.read_bytes() == extra_content
    if choice == 'k':
        assert edited.read_bytes() == original
    else:
        assert edited.read_text() == _skill_sources()[edited.parent.name]
        backup = next((install_state_file().parent / 'backups').glob('setup-*'))
        assert (backup / 'skills' / edited.parent.name / 'SKILL.md').read_bytes() == original
        assert (backup / 'skills' / edited.parent.name / 'helper.txt').read_text() == 'user supporting file'
    # New processes, no CA export and the original CA file no longer available.
    pem.unlink()
    mcp = cli(home, [opencode, 'mcp', 'list'])
    assert 'connected' in (mcp.stdout + mcp.stderr).lower()
    skills = json.loads(cli(home, [opencode, 'debug', 'skill']).stdout)
    names = [item['name'] for item in skills]
    assert set(_skill_sources()) | {'mlspace-my-qa-workflow'} <= set(names)
    assert len(names) == len(set(names))
    assert len([name for name in names if name.startswith('mlspace-')]) == len(_skill_sources()) + 1
    cli(home, [sys.executable, '-m', 'mlspace_mcp', 'setup', '--force', '--non-interactive',
               '--client', 'opencode', '--workspace', 'workspace-001'])
    assert extra.read_bytes() == extra_content
    expected = original if choice == 'k' else _skill_sources()[edited.parent.name].encode()
    assert edited.read_bytes() == expected
    assert 'connected' in cli(home, [opencode, 'mcp', 'list']).stdout.lower()
    again = json.loads(cli(home, [opencode, 'debug', 'skill']).stdout)
    assert sorted(item['name'] for item in again) == sorted(names)
    assert all(method == 'GET' or path == '/public/v2/service_auth' for method, path in calls)


def test_real_terminal_cancel_and_agent_noninteractive_preserve_legacy_files(setup_environment, opencode):
    home = setup_environment
    config, edited, extra = prepare_legacy(home)
    before = {p: p.read_bytes() for p in [config, edited, extra]}
    command = [sys.executable, '-m', 'mlspace_mcp', 'setup', '--client-path', f'opencode={opencode}']
    code, output = terminal(home, command, [('[r] Создать резервную копию конфига'.encode(), b'\r')])
    assert code == 1 and 'отменена' in output.lower()
    assert 'Cloud.ru Key ID:' not in output
    result = subprocess.run(command + ['--non-interactive', '--force'], cwd=home,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert str(config) in result.stderr and str(edited) in result.stderr
    assert all(p.read_bytes() == content for p, content in before.items())
    assert not (home / 'custom-config/mlspace-plugin/.env').exists()


@pytest.fixture
def scripted_provider():
    """Deterministic tool requests, not a real LLM or a test of model reasoning."""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            names = [tool['function']['name'] for tool in body.get('tools', [])]
            results = [message for message in body.get('messages', []) if message['role'] == 'tool']
            delta = {'role': 'assistant', 'content': 'LOCAL_QA_COMPLETE'}
            finish = 'stop'
            if names:
                requests.append(body)
                if len(results) == 0:
                    name, arguments = 'skill', {'name': 'mlspace-my-qa-workflow'}
                elif len(results) == 1:
                    name = next(name for name in names if name.endswith('mlspace_contexts'))
                    arguments = {}
                elif len(results) == 2:
                    name = next(name for name in names if name.endswith('mlspace_workspaces'))
                    arguments = {'action': 'get', 'workspace_id': 'workspace-001', 'target': 'workspace-001'}
                else:
                    name, arguments = '', {}
                if name:
                    delta = {'role': 'assistant', 'tool_calls': [{
                        'index': 0, 'id': f'qa_call_{len(results)}', 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(arguments)},
                    }]}
                    finish = 'tool_calls'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for part, reason in [(delta, None), ({}, finish)]:
                chunk = {'id': 'qa-completion', 'object': 'chat.completion.chunk', 'created': 1,
                         'model': 'qa-model', 'choices': [{'index': 0, 'delta': part, 'finish_reason': reason}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_opencode_session_executes_user_skill_and_mcp_read_over_tls(
        setup_environment, local_api, opencode, scripted_provider):
    home = setup_environment
    endpoint, pem, calls = local_api
    provider_url, requests = scripted_provider
    root = home / 'custom-config/opencode'
    custom_skill(root, 'mlspace-my-qa-workflow', 'USER_EXTRA_WORKFLOW')
    config = {
        'enabled_providers': ['local-qa'], 'model': 'local-qa/qa-model',
        'small_model': 'local-qa/qa-model', 'share': 'disabled',
        'permission': {'*': 'deny', 'skill': 'allow', 'mlspace_*': 'allow'},
        'provider': {'local-qa': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'Local QA fixture',
            'options': {'baseURL': provider_url, 'apiKey': 'dummy'},
            'models': {'qa-model': {'name': 'QA model', 'limit': {'context': 200000, 'output': 4096}}},
        }},
    }
    (root / 'opencode.json').write_text(json.dumps(config))
    source = home / 'test-credentials.env'
    source.write_text('MLSPACE_CLIENT_ID=dummy-id\nMLSPACE_CLIENT_SECRET=dummy-secret\n')
    source.chmod(0o600)
    cli(home, [sys.executable, '-m', 'mlspace_mcp', 'setup', '--non-interactive',
               '--client-path', f'opencode={opencode}', '--credentials-file', str(source),
               '--base-url', endpoint, '--ca-file', str(pem), '--workspace', 'workspace-001',
               '--workspace', 'workspace-299'])
    pem.unlink()
    calls.clear()
    result = cli(home, [opencode, 'run', '--format', 'json', '--model', 'local-qa/qa-model',
                        'Use my QA skill, show selected contexts, then read workspace-001. Do not write anything.'],
                 timeout=60)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    assert not [event for event in events if event['type'] == 'error'], result.stdout
    tool_events = [event for event in events if event['type'] == 'tool_use']
    assert len(tool_events) == 3, result.stdout
    assert all(event['part']['state']['status'] == 'completed' for event in tool_events), result.stdout
    transcript = json.dumps(requests[-1]['messages'])
    assert 'USER_EXTRA_WORKFLOW' in transcript
    assert 'workspace-001' in transcript and 'workspace-299' in transcript
    assert 'LOCAL_QA_COMPLETE' in result.stdout
    assert ('GET', '/public/v2/workspaces/v3/workspace-001') in calls
    assert all(method == 'GET' or path == '/public/v2/service_auth' for method, path in calls)


@pytest.mark.parametrize('local_api', [{'workspace-299'}], indirect=True)
def test_real_terminal_reselects_after_403_and_connects_opencode(
        setup_environment, local_api, opencode):
    from mlspace_mcp.config import read_dotenv_file

    home = setup_environment
    endpoint, pem, calls = local_api
    command = [sys.executable, '-m', 'mlspace_mcp', 'setup',
               '--client-path', f'opencode={opencode}', '--base-url', endpoint,
               '--ca-file', str(pem)]
    code, output = terminal(home, command, [
        (b'Cloud.ru Key ID:', b'dummy-id\r'),
        (b'Cloud.ru Key Secret:', b'dummy-secret\r'),
        ('Поиск по названию / проекту / ID:'.encode(), b'workspace-001 \x15workspace-299 \r'),
        # The new picker retains workspace-001; Enter confirms that reduced selection.
        ('Выберите воркспейсы повторно.'.encode(), b'\r'),
    ])
    assert code == 0, output
    assert 'HTTP 403' in output
    assert 'Проверено выбранных воркспейсов: 1/2' in output
    assert 'Проверено выбранных воркспейсов: 1/1' in output
    assert 'dummy-secret' not in output and 'dummy-id' not in output
    target = home / 'custom-config/mlspace-plugin/.env'
    saved = read_dotenv_file(target)
    assert saved['MLSPACE_WORKSPACE_ID'] == 'workspace-001'
    assert [row['id'] for row in json.loads(saved['MLSPACE_WORKSPACES'])] == ['workspace-001']
    mcp = cli(home, [opencode, 'mcp', 'list'])
    assert 'connected' in (mcp.stdout + mcp.stderr).lower()
    skills = json.loads(cli(home, [opencode, 'debug', 'skill']).stdout)
    assert 'mlspace-transfer-data' in {item['name'] for item in skills}
    assert all(method == 'GET' or path == '/public/v2/service_auth' for method, path in calls)
