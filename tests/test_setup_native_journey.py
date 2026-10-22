"""Terminal and agent journeys against a synthetic HTTPS MLSpace API."""
import json
import os
import select
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
import trustme

pytestmark = pytest.mark.usefixtures('isolated_client_environment')


@pytest.fixture
def setup_environment(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('MLSPACE_', 'CODEX_', 'CLAUDE_', 'OPENCODE_', 'SSL_CERT')) or key.lower().endswith('_proxy'):
            monkeypatch.delenv(key)
    home = tmp_path / 'home'
    home.mkdir()
    for key, suffix in [('HOME', ''), ('XDG_CONFIG_HOME', 'custom-config'), ('CODEX_HOME', '.codex'),
                        ('CLAUDE_CONFIG_DIR', '.claude'), ('XDG_DATA_HOME', '.local/share'),
                        ('XDG_STATE_HOME', '.local/state'), ('XDG_CACHE_HOME', '.cache')]:
        path = home / suffix
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv('TERM', 'xterm')
    monkeypatch.setenv('PROMPT_TOOLKIT_NO_CPR', '1')
    return home


@pytest.fixture
def local_api(tmp_path, request):
    denied_ids = getattr(request, 'param', set())
    rows = [{'id': f'workspace-{i:03}', 'name': f'workspace-{i:03}', 'project_name': f'project-{i // 100}'}
            for i in range(300)]
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            calls.append(('POST', self.path))
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            if self.path != '/public/v2/service_auth' or data != {'client_id': 'dummy-id', 'client_secret': 'dummy-secret'}:
                return self.reply(401, {})
            self.reply(200, {'token': {'access_token': 'dummy-token', 'expires_in': 3600}})

        def do_GET(self):
            path = urlsplit(self.path).path
            calls.append(('GET', path))
            if path == '/public/v2/workspaces/v3/':
                return self.reply(200, {'workspaces': rows})
            if path == '/public/v2/workspaces/v1/x_api_key':
                return self.reply(200, {'x-api-key': 'derived-not-persisted'})
            for row in rows:
                if path == '/public/v2/workspaces/v3/' + row['id']:
                    if row['id'] in denied_ids:
                        return self.reply(403, {'detail': 'Workspace access denied'})
                    return self.reply(200, row)
            self.reply(404, {})
    ca = trustme.CA()
    pem = tmp_path / 'source-ca.pem'
    ca.cert_pem.write_to_path(pem)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert('localhost').configure_cert(context)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'https://localhost:{server.server_port}', pem, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_human_terminal_then_agent_search_after_source_ca_removed(setup_environment, local_api):
    home = setup_environment
    endpoint, pem, calls = local_api
    command = [sys.executable, '-m', 'mlspace_mcp', 'setup']
    master, slave = os.openpty()
    process = subprocess.Popen(command + ['--base-url', endpoint, '--client-path', 'opencode=/usr/bin/true'],
                               stdin=slave, stdout=slave, stderr=slave, cwd=home)
    os.close(slave)
    transcript = b''
    steps = [(b'Cloud.ru Key ID:', b'dummy-id\r'),
             (b'Cloud.ru Key Secret:', b'\x1b[200~dummy-secret\x1b[201~\r'),
             ('Введите путь к этому файлу'.encode(), str(pem).encode() + b'\r'),
             ('Поиск по названию / проекту / ID:'.encode(), b'workspace-299 \x15workspace-001 \r')]
    sent = 0
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                transcript += chunk
                if sent < len(steps) and steps[sent][0] in transcript:
                    os.write(master, steps[sent][1])
                    sent += 1
            if process.poll() is not None:
                break
        assert process.wait(timeout=3) == 0, transcript.decode(errors='replace')[-2000:]
        assert b'dummy-secret' not in transcript and b'dummy-id' not in transcript
        assert sent == 4 and b'***' in transcript
        assert 'opencode: skills из релиза проверены'.encode() in transcript
        assert 'запуск процесса MCP проверен'.encode() in transcript
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
    target = home / 'custom-config/mlspace-plugin/.env'
    assert target.stat().st_mode & 0o777 == 0o600
    assert 'derived-not-persisted' not in target.read_text()
    pem.unlink()
    env = dict(os.environ)
    env.pop('XDG_CONFIG_HOME')
    result = subprocess.run(command + ['--non-interactive', '--list-workspaces', '--search', 'project-2',
                                      '--offset', '20', '--limit', '20'],
                            env=env, cwd=home, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    page = json.loads(result.stdout)
    assert page['total'] == 100 and len(page['workspaces']) == 20 and page['has_more']
    assert all(method == 'GET' or path == '/public/v2/service_auth' for method, path in calls)
    logs = list((home / '.config/mlspace-plugin/logs').glob('*.log'))
    assert any(path.name.startswith('setup-') for path in logs)
    assert any(path.name.startswith('server-') for path in logs)
    for path in logs:
        assert path.stat().st_mode & 0o777 == 0o600
        text = path.read_text()
        for secret in ('dummy-id', 'dummy-secret', 'dummy-token', 'derived-not-persisted'):
            assert secret not in text
        assert all(isinstance(json.loads(line), dict) for line in text.splitlines())
