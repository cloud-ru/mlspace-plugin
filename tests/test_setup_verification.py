import json
import sys

import pytest

pytestmark = pytest.mark.usefixtures('isolated_client_environment')


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    from mlspace_mcp.init_cli import save_credential_pointer
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'custom'))
    path = tmp_path / 'credentials.env'
    path.write_text('MLSPACE_CLIENT_ID=dummy\nMLSPACE_CLIENT_SECRET=dummy\n'
                    'MLSPACE_WORKSPACE_ID=dummy\nMLSPACE_BASE_URL=https://127.0.0.1:9\n')
    save_credential_pointer(path)
    return path


def test_real_stdio_handshake_finds_credentials_without_inherited_paths(credentials):
    from mlspace_mcp.setup_verification import verify_runtime
    verify_runtime([sys.executable, '-m', 'mlspace_mcp', '--transport', 'stdio'], credentials)


def test_wrong_process_is_not_reported_as_ready(credentials, caplog):
    from mlspace_mcp.setup_verification import verify_runtime
    with pytest.raises(ValueError, match='Не удалось проверить запуск MCP'):
        verify_runtime([sys.executable, '-c', 'print("private-secret-in-bad-output")'], credentials)
    assert 'private-secret-in-bad-output' not in caplog.text


def test_installed_plugin_with_changed_skill_or_version_fails_before_launch(tmp_path, monkeypatch, credentials):

    from mlspace_mcp import __version__
    from mlspace_mcp import setup_verification as verification
    from mlspace_mcp.native_plugins import NativePluginStatus
    root = tmp_path / 'installed'
    import build_plugin
    root, _ = build_plugin.build_release(tmp_path / 'bundle')
    status = NativePluginStatus('codex', True, True, __version__, 'source', root)
    monkeypatch.setattr(verification, 'inspect_native', lambda *a: status)
    monkeypatch.setattr(verification, 'verify_runtime', lambda *a, **kw: pytest.fail('must not launch'))
    skill = next((root / 'skills').glob('*/SKILL.md'))
    original = skill.read_text()
    skill.write_text('local change')
    with pytest.raises(ValueError, match='skill'):
        verification.verify_client('codex', '/client', credentials, [])
    skill.write_text(original)
    path = root / 'mcp.json'
    data = json.loads(path.read_text())
    data['mcpServers']['mlspace']['args'][3] = 'mlspace-plugin==999.0'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='команда запуска'):
        verification.verify_client('codex', '/client', credentials, [])
