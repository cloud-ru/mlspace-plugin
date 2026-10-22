import json

import pytest

pytestmark = pytest.mark.usefixtures('isolated_client_environment')


def test_jsonc_preserves_unrelated_comments_and_values():
    from mlspace_mcp.client_setup import set_jsonc_member
    original = '{\n // keep me\n "mcp": {"other": {"command": ["x"],},},\n "model": "foo",\n}\n'
    result = set_jsonc_member(original, ['mcp', 'mlspace'], {'type': 'local', 'command': ['mlspace-plugin']})
    assert '// keep me' in result
    assert '"other": {"command": ["x"],}' in result
    assert '"model": "foo"' in result
    assert set_jsonc_member(result, ['mcp', 'mlspace'], {'type': 'local', 'command': ['mlspace-plugin']}) == result


def test_jsonc_rejects_duplicate_keys():
    from mlspace_mcp.client_setup import set_jsonc_member
    with pytest.raises(ValueError):
        set_jsonc_member('{"mcp":{},"mcp":{}}', ['mcp', 'mlspace'], {})


def test_detect_clients_only_uses_executables(monkeypatch):
    from mlspace_mcp.client_setup import detect_clients
    monkeypatch.setattr('shutil.which', lambda name: '/bin/codex' if name == 'codex' else None)
    assert detect_clients() == {'codex': '/bin/codex'}


def test_install_opencode_preserves_config_installs_skills_and_is_idempotent(tmp_path, monkeypatch):
    from mlspace_mcp.client_setup import install_clients
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    config = tmp_path / '.config/opencode/opencode.jsonc'
    config.parent.mkdir(parents=True)
    config.write_text('{ // personal comment\n "mcp": {"other": {"type":"local","command":["other"]}}, "model":"keep"\n}')
    env_file = tmp_path / 'credentials.env'
    args = ({'opencode': '/usr/bin/true'}, env_file, ['/opt/tools/mlspace-plugin', '--transport', 'stdio'])
    install_clients(*args)
    content = config.read_text()
    assert 'personal comment' in content and '"model":"keep"' in content
    assert str(env_file) in content
    assert len(list((tmp_path / '.config/opencode/skills').glob('mlspace-*/SKILL.md'))) == 18
    install_clients(*args)
    assert config.read_text() == content


def test_modified_skill_fails_before_client_configuration(tmp_path, monkeypatch):
    from mlspace_mcp.client_setup import install_clients
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    skill = tmp_path / '.config/opencode/skills/mlspace-list-running-jobs/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('my custom skill')
    with pytest.raises(ValueError, match='skill'):
        install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['mlspace-plugin'])
    assert skill.read_text() == 'my custom skill'
    assert not (tmp_path / '.config/opencode/opencode.json').exists()


@pytest.fixture(autouse=True)
def native_cli(monkeypatch, tmp_path):
    from mlspace_mcp import __version__, client_setup
    from mlspace_mcp.native_plugins import MARKETPLACE_SOURCE, NativePluginStatus
    states = {}
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / '.claude'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex'))
    def inspect(client, executable):
        return states.get(client, NativePluginStatus(client))
    def install(client, executable, version):
        states[client] = NativePluginStatus(client, True, True, __version__, MARKETPLACE_SOURCE, tmp_path / client)
        return states[client]
    monkeypatch.setattr(client_setup, 'inspect_native', inspect)
    monkeypatch.setattr(client_setup, 'install_native', install)
    return states


def test_native_install_has_no_direct_registration_or_shared_skills(tmp_path, monkeypatch, native_cli):
    from mlspace_mcp.client_setup import install_clients
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    install_clients({'codex': '/usr/bin/true'}, tmp_path / '.env', [])
    assert native_cli['codex'].installed
    assert not (tmp_path / '.codex/config.toml').exists()
    assert not (tmp_path / '.config/opencode/skills').exists()
    state = json.loads((tmp_path / '.config/mlspace-plugin/install-state.json').read_text())
    assert state['clients']['codex']['mode'] == 'native'


@pytest.mark.parametrize('client', ['claude-code', 'codex'])
def test_direct_entry_blocks_native_install(tmp_path, monkeypatch, client):
    from mlspace_mcp.client_setup import _config, prepare_clients
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    config = _config(client)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('[mcp_servers.mlspace]\ncommand="old"\n' if client == 'codex' else
                      json.dumps({'mcpServers': {'mlspace': {'command': 'old'}}}))
    with pytest.raises(ValueError, match='прямое'):
        prepare_clients({client: '/usr/bin/true'}, tmp_path / '.env', [])


def test_skill_edited_after_preflight_is_not_overwritten(tmp_path, monkeypatch):
    from mlspace_mcp.client_setup import install_clients, prepare_clients
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    args = ({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['/bin/echo'])
    prepare_clients(*args)
    target = tmp_path / '.config/opencode/skills/mlspace-list-running-jobs/SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text('edited while entering keys')
    with pytest.raises(ValueError, match='skill'):
        install_clients(*args)
    assert target.read_text() == 'edited while entering keys'


def test_opencode_skills_do_not_leak_into_native_clients(tmp_path, monkeypatch):
    from mlspace_mcp.client_setup import install_clients
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['/bin/echo'])
    assert len(list((tmp_path / '.config/opencode/skills').glob('*/SKILL.md'))) == 18
    assert not (tmp_path / '.agents/skills').exists()
    assert not (tmp_path / '.claude/skills').exists()


def test_disabled_native_plugin_does_not_block_setup(tmp_path, monkeypatch):
    from mlspace_mcp.client_setup import prepare_clients
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex'))
    config = tmp_path / '.codex/config.toml'
    config.parent.mkdir()
    config.write_text('[plugins."mlspace@marketplace"]\nenabled = false\n')
    prepare_clients({'codex': '/usr/bin/true'}, tmp_path / '.env', ['/bin/echo'])


def test_installed_opencode_can_handshake_with_mlspace_in_isolated_home(tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess
    import sys

    from mlspace_mcp.client_setup import install_clients
    executable = shutil.which('opencode')
    if not executable:
        pytest.skip('optional native CLI smoke test')
    for name in list(os.environ):
        if name.startswith(('MLSPACE_', 'OPENCODE_')):
            monkeypatch.delenv(name)
    monkeypatch.setenv('HOME', str(tmp_path))
    for key, directory in [('XDG_CONFIG_HOME', '.config'), ('XDG_DATA_HOME', '.local/share'),
                           ('XDG_STATE_HOME', '.local/state'), ('XDG_CACHE_HOME', '.cache')]:
        monkeypatch.setenv(key, str(tmp_path / directory))
    env_file = tmp_path / 'fake.env'
    env_file.write_text('MLSPACE_CLIENT_ID=dummy\nMLSPACE_CLIENT_SECRET=dummy\n'
                        'MLSPACE_WORKSPACE_ID=dummy\nMLSPACE_BASE_URL=https://127.0.0.1:9\n')
    env_file.chmod(0o600)
    command = [sys.executable, '-m', 'mlspace_mcp', '--transport', 'stdio']
    install_clients({'opencode': executable}, env_file, command)
    result = subprocess.run([executable, 'mcp', 'list'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert 'mlspace' in result.stdout + result.stderr
    assert 'connected' in (result.stdout + result.stderr).lower()


def test_skill_write_failure_reports_partial_registration(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    original = client_setup.atomic_write
    def fail_skill(path, text, mode=0o600):
        if path.name == 'SKILL.md':
            raise PermissionError('not writable')
        original(path, text, mode)
    monkeypatch.setattr(client_setup, 'atomic_write', fail_skill)
    with pytest.raises(ValueError, match='MCP подключён: opencode'):
        client_setup.install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['/bin/echo'])




def test_local_wheel_in_uvx_cache_is_not_substituted_with_pypi(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    site = tmp_path / 'archive-v0/env/lib/site-packages'
    source = site / 'mlspace_mcp/client_setup.py'
    source.parent.mkdir(parents=True)
    source.touch()
    metadata = site / 'mlspace_plugin-0.2.1.dist-info'
    metadata.mkdir()
    (metadata / 'direct_url.json').write_text('{"url":"file:///private/local.whl"}')
    monkeypatch.setattr(client_setup, '__file__', str(source))
    monkeypatch.setattr('shutil.which', lambda name: '/opt/bin/uvx' if name == 'uvx' else None)
    with pytest.raises(ValueError, match='локального wheel'):
        client_setup.server_command()


def test_shared_skill_update_includes_previously_connected_clients(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex'))
    monkeypatch.setattr(client_setup, '_skill_sources', lambda: {'mlspace-example': 'version one'})
    client_setup.install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v1'])
    monkeypatch.setattr(client_setup, '_skill_sources', lambda: {'mlspace-example': 'version two'})
    plan = client_setup.prepare_clients({'codex': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v2'])
    registrations = {row[0]: row[3] for row in plan['registrations']}
    assert registrations['opencode']['command'] == ['runtime-v2']
    assert registrations['codex'] is None
    assert plan['writes'][tmp_path / '.config/opencode/skills/mlspace-example/SKILL.md'] == 'version two'


def test_update_does_not_recreate_removed_client_registration(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex'))
    client_setup.install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v1'])
    (tmp_path / '.config/opencode/opencode.json').write_text('{}')
    plan = client_setup.prepare_clients({'codex': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v2'])
    assert [row[0] for row in plan['registrations']] == ['codex']


def test_modified_previously_connected_client_blocks_shared_update(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex'))
    client_setup.install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v1'])
    config = tmp_path / '.config/opencode/opencode.json'
    config.write_text(config.read_text().replace('runtime-v1', 'user-edited'))
    with pytest.raises(ValueError, match='файлы ещё не перезаписаны'):
        client_setup.prepare_clients({'codex': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v2'])


def test_custom_client_config_cannot_be_silently_forgotten(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setenv('OPENCODE_CONFIG', str(tmp_path / 'custom.json'))
    client_setup.install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v1'])
    monkeypatch.delenv('OPENCODE_CONFIG')
    with pytest.raises(ValueError, match='OPENCODE_CONFIG'):
        client_setup.prepare_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v2'])
    assert not (tmp_path / '.config/opencode/opencode.json').exists()


def test_missing_managed_native_cli_blocks_before_new_registration(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex'))
    monkeypatch.setattr(client_setup, 'detect_clients', lambda: {})
    client_setup.atomic_write(tmp_path / '.codex/config.toml', '[mcp_servers.mlspace]\ncommand="old"\n')
    client_setup.atomic_write(tmp_path / '.config/mlspace-plugin/install-state.json',
                              json.dumps({'clients': {'codex': {'mode': 'native'}}}))
    with pytest.raises(ValueError, match='codex.*недоступен'):
        client_setup.install_clients({'opencode': '/usr/bin/true'}, tmp_path / '.env', ['runtime-v2'])
    assert not (tmp_path / '.config/opencode/opencode.json').exists()


def test_partial_native_install_can_resume_without_duplicate(tmp_path, monkeypatch, native_cli):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setattr(client_setup, 'detect_clients', lambda: {})
    install = client_setup.install_native
    def fail_second(client, *args):
        if client == 'codex':
            raise ValueError('codex unavailable')
        return install(client, *args)
    monkeypatch.setattr(client_setup, 'install_native', fail_second)
    args = ({'claude-code': '/usr/bin/true', 'codex': '/usr/bin/true'}, tmp_path / '.env', [])
    with pytest.raises(ValueError, match='Уже подключены: claude-code') as error:
        client_setup.install_clients(*args)
    assert 'Повторите команду:' in str(error.value)
    assert '--client-path codex=/usr/bin/true' in str(error.value)
    state = json.loads((tmp_path / '.config/mlspace-plugin/install-state.json').read_text())
    assert list(state['clients']) == ['claude-code']
    monkeypatch.setattr(client_setup, 'install_native', install)
    assert client_setup.install_clients(*args) == ['claude-code', 'codex']


def test_install_state_survives_lost_xdg_setting(tmp_path, monkeypatch, native_cli):
    from mlspace_mcp import client_setup
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'custom'))
    client_setup.install_clients({'codex': '/usr/bin/true'}, tmp_path / '.env', [])
    monkeypatch.delenv('XDG_CONFIG_HOME')
    assert client_setup.managed_clients() == {'codex': '/usr/bin/true'}
