"""Real client CLIs against a local Git marketplace; no account or network API."""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.usefixtures('isolated_client_environment')


@pytest.fixture
def git_marketplace(tmp_path):
    repo = tmp_path / 'market.git'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Test')
    git('config', 'http.receivepack', 'true')
    import build_plugin
    bundle, _ = build_plugin.build_release(tmp_path / 'release')
    shutil.copytree(bundle, repo, dirs_exist_ok=True)

    def release(version):
        for name in ('plugin.json', '.claude-plugin/plugin.json',
                     '.codex-plugin/plugin.json'):
            path = repo / name
            data = json.loads(path.read_text())
            data['version'] = version
            path.write_text(json.dumps(data))
        git('add', '.')
        git('commit', '-m', version)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.backend()

        def do_POST(self):
            self.backend()

        def backend(self):
            path, _, query = self.path.partition('?')
            env = dict(os.environ, GIT_PROJECT_ROOT=str(tmp_path), GIT_HTTP_EXPORT_ALL='1',
                       PATH_INFO=path, QUERY_STRING=query, REQUEST_METHOD=self.command,
                       CONTENT_TYPE=self.headers.get('Content-Type', ''))
            payload = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            result = subprocess.run(['git', 'http-backend'], env=env, input=payload, capture_output=True)
            headers, _, body = result.stdout.partition(b'\r\n\r\n')
            self.send_response(200)
            for line in headers.decode().splitlines():
                key, _, value = line.partition(':')
                self.send_header(key, value.strip())
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/market.git', release
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('client,binary', [('claude-code', 'claude'), ('codex', 'codex')])
def test_native_cli_installs_repeats_and_updates(tmp_path, monkeypatch, git_marketplace, client, binary):
    from mlspace_mcp import native_plugins as native
    executable = shutil.which(binary)
    if not executable:
        pytest.skip('optional real client CLI integration')
    for key in list(os.environ):
        if key.startswith(('MLSPACE_', 'CLAUDE_', 'CODEX_', 'OPENCODE_', 'OPENAI_', 'ANTHROPIC_')) or key.lower().endswith('_proxy'):
            monkeypatch.delenv(key)
    home = tmp_path / 'home'
    home.mkdir()
    for key, suffix in [('HOME', ''), ('CODEX_HOME', '.codex'), ('CLAUDE_CONFIG_DIR', '.claude'),
                        ('XDG_CONFIG_HOME', '.config'), ('XDG_DATA_HOME', '.local/share'),
                        ('XDG_STATE_HOME', '.local/state'), ('XDG_CACHE_HOME', '.cache')]:
        path = home / suffix
        if key != 'CODEX_HOME':
            path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(path))
    source, release = git_marketplace
    monkeypatch.setattr(native, 'MARKETPLACE_SOURCE', source)
    release('0.0.1')
    first = native.install_native(client, executable, '0.0.1')
    assert first.version == '0.0.1' and first.enabled
    assert native.install_native(client, executable, '0.0.1') == first
    release('0.0.2')
    updated = native.install_native(client, executable, '0.0.2')
    assert updated.version == '0.0.2' and updated.enabled
    assert updated.plugin_root != first.plugin_root
    assert len(list((updated.plugin_root / 'skills').glob('*/SKILL.md'))) == 18
    assert not (home / '.agents/skills').exists()


def test_codex_default_home_can_be_created_by_cli(tmp_path, monkeypatch):
    from mlspace_mcp.native_plugins import inspect_native
    executable = shutil.which('codex')
    if not executable:
        pytest.skip('optional real client CLI integration')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('CODEX_HOME', raising=False)
    assert not inspect_native('codex', executable).installed
