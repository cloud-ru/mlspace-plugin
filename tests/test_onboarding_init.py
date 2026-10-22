import json
import stat

import httpx
import pytest
import respx

from mlspace_mcp.config import read_dotenv_file
from mlspace_mcp.init_cli import run_init

BASE = 'https://setup.example'
pytestmark = pytest.mark.usefixtures('isolated_client_environment')


def conflicting_opencode(tmp_path, monkeypatch):
    import sys

    from mlspace_mcp import init_cli
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(init_cli, 'detect_clients', lambda: {'opencode': '/usr/bin/true'})
    monkeypatch.setattr(init_cli, 'server_command', lambda: [sys.executable, '-m', 'mlspace_mcp', '--transport', 'stdio'])
    root = tmp_path / '.config/opencode'
    skill = root / 'skills/mlspace-list-running-jobs/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('custom workflow')
    config = root / 'opencode.jsonc'
    config.write_text('{"mcp":{"mlspace":{"command":["old-wrapper"]}}}')
    return skill, config


@respx.mock
def test_interactive_setup_resolves_wrapper_and_keeps_custom_skill(tmp_path, monkeypatch, capsys):
    routes()
    skill, config = conflicting_opencode(tmp_path, monkeypatch)
    answers = iter(['r', 'k'])
    monkeypatch.setattr('builtins.input', lambda *a: next(answers))
    target = tmp_path / 'new.env'
    assert run_init(target, base_url=BASE, from_env=True, clients=['opencode'], workspace_ids=['ws2']) == 0
    assert skill.read_text() == 'custom workflow'
    assert 'old-wrapper' not in config.read_text()
    assert read_dotenv_file(target)['MLSPACE_WORKSPACE_ID'] == 'ws2'
    output = capsys.readouterr().err
    assert str(config) in output and str(skill) in output
    assert output.index(str(skill)) < output.index('[1/4]')
    assert 'соответствие релизу не проверяется' in output


def test_cancel_conflicts_preserves_configuration_and_credentials(tmp_path, monkeypatch, capsys):
    skill, config = conflicting_opencode(tmp_path, monkeypatch)
    before = config.read_bytes()
    monkeypatch.setattr('builtins.input', lambda *a: '')
    target = tmp_path / 'new.env'
    assert run_init(target, base_url=BASE, from_env=True, clients=['opencode'], workspace_ids=['ws2']) == 1
    assert not target.exists()
    assert config.read_bytes() == before and skill.read_text() == 'custom workflow'
    assert 'отменена' in capsys.readouterr().err.lower()


def test_noninteractive_conflicts_list_all_paths_without_prompt_even_with_force(tmp_path, monkeypatch, capsys):
    skill, config = conflicting_opencode(tmp_path, monkeypatch)
    monkeypatch.setattr('builtins.input', lambda *a: pytest.fail('unexpected prompt'))
    target = tmp_path / 'existing.env'
    target.write_text('MLSPACE_CLIENT_SECRET=old-secret\n')
    assert run_init(target, base_url=BASE, from_env=True, clients=['opencode'], workspace_ids=['ws2'],
                    non_interactive=True, force=True) == 1
    output = capsys.readouterr().err
    assert str(skill) in output and str(config) in output
    assert target.read_text() == 'MLSPACE_CLIENT_SECRET=old-secret\n'


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path, isolated_client_environment):
    import os
    for key in list(os.environ):
        if key.startswith('MLSPACE_') or key in ('SSL_CERT_FILE', 'SSL_CERT_DIR'):
            monkeypatch.delenv(key)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.setenv('MLSPACE_CLIENT_ID', 'id-value')
    monkeypatch.setenv('MLSPACE_CLIENT_SECRET', 'secret-value')


def routes():
    respx.post(BASE + '/public/v2/service_auth').respond(200, json={'token': {'access_token': 't', 'expires_in': 3600}})
    respx.get(BASE + '/public/v2/workspaces/v3/').respond(200, json={'workspaces': [
        {'id': 'ws1', 'name': 'one', 'project_name': 'project'},
        {'id': 'ws2', 'name': 'two', 'project_name': 'project'}]})
    respx.get(BASE + '/public/v2/workspaces/v1/x_api_key').respond(200, json={'x-api-key': 'derived-not-stored'})
    respx.get(BASE + '/public/v2/workspaces/v3/ws2').respond(200, json={'id': 'ws2'})


@respx.mock
def test_noninteractive_init_uses_explicit_ids_and_stores_only_two_keys(tmp_path):
    routes()
    target = tmp_path / 'config/.env'
    assert run_init(target, base_url=BASE, config_only=True, non_interactive=True,
                    from_env=True, workspace_ids=['ws2']) == 0
    data = read_dotenv_file(target)
    assert data['MLSPACE_WORKSPACE_ID'] == 'ws2'
    assert 'derived-not-stored' not in target.read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


@respx.mock
def test_failed_discovery_preserves_old_file_and_never_asks_for_workspace(tmp_path, capsys):
    respx.post(BASE + '/public/v2/service_auth').respond(401)
    target = tmp_path / '.env'
    original = 'MLSPACE_READONLY=true\n'
    target.write_text(original)
    assert run_init(target, base_url=BASE, config_only=True, non_interactive=True,
                    from_env=True, workspace_ids=['ws2'], force=True) == 1
    assert target.read_text() == original
    output = capsys.readouterr().err
    assert 'HTTP 401' in output and 'secret-value' not in output


@respx.mock
def test_verification_failure_never_saves_new_file(tmp_path):
    routes()
    respx.get(BASE + '/public/v2/workspaces/v3/ws2').respond(403)
    target = tmp_path / '.env'
    assert run_init(target, base_url=BASE, config_only=True, non_interactive=True,
                    from_env=True, workspace_ids=['ws2']) == 1
    assert not target.exists()


@pytest.mark.parametrize('failure_at', ['details', 'key'])
@respx.mock
def test_interactive_verification_reopens_picker_without_reasking_keys(
    tmp_path, monkeypatch, capsys, failure_at,
):
    from mlspace_mcp import init_cli

    routes()
    if failure_at == 'details':
        respx.get(BASE + '/public/v2/workspaces/v3/ws1').respond(403)
    else:
        respx.get(BASE + '/public/v2/workspaces/v1/x_api_key').mock(
            side_effect=lambda request: httpx.Response(
                403 if request.headers['x-workspace-id'] == 'ws1' else 200,
                json={'x-api-key': 'derived-not-stored'},
            ),
        )
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    prompted = []

    def ask(label, **kwargs):
        prompted.append(label)
        return 'typed-id' if 'ID' in label else 'typed-secret'

    monkeypatch.setattr(init_cli, '_ask', ask)
    target = tmp_path / 'new.env'
    selections = []

    def choose(rows, selected_ids=None):
        selections.append(selected_ids)
        assert not target.exists()
        assert len(selections) <= 2
        return rows if len(selections) == 1 else [row for row in rows if row['id'] == 'ws2']

    monkeypatch.setattr(init_cli, 'choose_workspaces', choose)
    assert run_init(target, base_url=BASE, config_only=True) == 0
    assert selections == [[], ['ws2']]
    assert len(prompted) == 2
    saved = read_dotenv_file(target)
    assert saved['MLSPACE_WORKSPACE_ID'] == 'ws2'
    assert [row['id'] for row in json.loads(saved['MLSPACE_WORKSPACES'])] == ['ws2']
    assert saved['MLSPACE_CLIENT_SECRET'] == 'typed-secret'
    output = capsys.readouterr().err
    assert 'HTTP 403' in output and 'повтор' in output.lower()
    assert 'typed-secret' not in output and 'typed-id' not in output


@respx.mock
def test_cancel_workspace_reselection_preserves_previous_file(tmp_path, monkeypatch):
    routes()
    respx.get(BASE + '/public/v2/workspaces/v3/ws1').respond(403)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    target = tmp_path / '.env'
    original = 'MLSPACE_READONLY=true\n'
    target.write_text(original)

    def cancel(rows, selected_ids=None):
        assert selected_ids == ['ws2']
        raise KeyboardInterrupt

    monkeypatch.setattr('mlspace_mcp.init_cli.choose_workspaces', cancel)
    assert run_init(target, force=True, base_url=BASE, config_only=True,
                    from_env=True, workspace_ids=['ws1', 'ws2']) == 130
    assert target.read_text() == original


@respx.mock
def test_retry_single_workspace_can_select_multiple_without_stale_default(tmp_path, monkeypatch):
    routes()
    respx.get(BASE + '/public/v2/workspaces/v3/ws1').respond(200, json={'id': 'ws1'})
    respx.get(BASE + '/public/v2/workspaces/v3/ws2').mock(side_effect=[
        httpx.Response(403),
        httpx.Response(200, json={'id': 'ws2'}),
    ])
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)

    def choose(rows, selected_ids=None):
        assert selected_ids == []
        return rows

    monkeypatch.setattr('mlspace_mcp.init_cli.choose_workspaces', choose)
    target = tmp_path / '.env'
    assert run_init(target, base_url=BASE, config_only=True, from_env=True,
                    workspace_ids=['ws2']) == 0
    saved = read_dotenv_file(target)
    assert 'MLSPACE_WORKSPACE_ID' not in saved
    assert len(json.loads(saved['MLSPACE_WORKSPACES'])) == 2


@respx.mock
def test_reuses_saved_keys_without_prompt_and_preserves_readonly(tmp_path, monkeypatch):
    routes()
    target = tmp_path / '.env'
    target.write_text(f'MLSPACE_BASE_URL={BASE}\nMLSPACE_CLIENT_ID=old-id\n'
                      'MLSPACE_CLIENT_SECRET=old-secret\nMLSPACE_READONLY=true\n')
    def fail(*a, **k):
        pytest.fail('unexpected secret prompt')
    monkeypatch.setattr('mlspace_mcp.init_cli._ask', fail)
    assert run_init(target, config_only=True, non_interactive=True, workspace_ids=['ws2'], force=True) == 0
    data = read_dotenv_file(target)
    assert data['MLSPACE_CLIENT_SECRET'] == 'old-secret'
    assert data['MLSPACE_READONLY'] == 'true'


def test_changed_endpoint_requires_explicit_credentials(tmp_path):
    target = tmp_path / '.env'
    original = 'MLSPACE_BASE_URL=https://old.example\nMLSPACE_CLIENT_ID=old\nMLSPACE_CLIENT_SECRET=old\n'
    target.write_text(original)
    assert run_init(target, base_url=BASE, config_only=True, non_interactive=True,
                    workspace_ids=['ws2'], force=True) == 1
    assert target.read_text() == original


@respx.mock
def test_agent_workspace_search_is_bounded_json_and_does_not_write(tmp_path, capsys):
    routes()
    target = tmp_path / '.env'
    assert run_init(target, base_url=BASE, non_interactive=True, from_env=True,
                    list_workspaces=True, search='project', offset=1, limit=1) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['total'] == 2 and result['workspaces'][0]['id'] == 'ws2'
    assert not target.exists()


@respx.mock
def test_noninteractive_requires_explicit_workspace_even_when_only_one(tmp_path):
    routes()
    respx.get(BASE + '/public/v2/workspaces/v3/').respond(200, json={'workspaces': [
        {'id': 'ws2', 'name': 'only', 'project_name': 'project'}]})
    target = tmp_path / '.env'
    assert run_init(target, base_url=BASE, config_only=True, non_interactive=True,
                    from_env=True) == 1
    assert not target.exists()


@respx.mock
def test_ca_copy_survives_source_removal_and_shell_change(tmp_path, monkeypatch):
    import trustme

    from mlspace_mcp.config import load_settings
    from mlspace_mcp.tls import tls_context
    ca_source = tmp_path / 'provided.pem'
    trustme.CA().cert_pem.write_to_path(ca_source)
    routes()
    target = tmp_path / 'app/.env'
    assert run_init(target, base_url=BASE, config_only=True, non_interactive=True,
                    from_env=True, workspace_ids=['ws2'], ca_file=ca_source) == 0
    ca_source.unlink()
    monkeypatch.setenv('MLSPACE_ENV_FILE', str(target))
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    settings = load_settings()
    assert settings.ca_file != str(ca_source)
    assert tls_context(settings.ca_file).check_hostname


def test_no_tty_never_reads_or_echoes_piped_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    target = tmp_path / '.env'
    assert run_init(target, config_only=True) == 1
    assert not target.exists()
    assert 'терминал' in capsys.readouterr().err


@respx.mock
def test_keyboard_interrupt_during_verification_preserves_previous_file(tmp_path, monkeypatch):
    routes()
    target = tmp_path / '.env'
    original = 'MLSPACE_READONLY=true\n'
    target.write_text(original)
    def interrupt(*a, **kw):
        raise KeyboardInterrupt
    monkeypatch.setattr('mlspace_mcp.init_cli._verify', interrupt)
    assert run_init(target, force=True, base_url=BASE, config_only=True,
                    non_interactive=True, from_env=True, workspace_ids=['ws2']) == 130
    assert target.read_text() == original


@respx.mock
def test_client_install_failure_keeps_verified_credentials_for_retry(tmp_path, monkeypatch, capsys):
    routes()
    target = tmp_path / '.env'
    original = 'MLSPACE_READONLY=true\n'
    target.write_text(original)
    monkeypatch.setattr('mlspace_mcp.init_cli._clients', lambda *a: {'opencode': '/bin/true'})
    monkeypatch.setattr('mlspace_mcp.init_cli.server_command', lambda: ['/bin/echo'])
    monkeypatch.setattr('mlspace_mcp.init_cli.prepare_clients', lambda *a, **kw: {'registrations': [('opencode',)]})
    def fail(*a, **kw):
        raise ValueError('Client installation failed')
    monkeypatch.setattr('mlspace_mcp.init_cli.install_clients', fail)
    assert run_init(target, force=True, base_url=BASE, non_interactive=True,
                    from_env=True, workspace_ids=['ws2']) == 1
    assert read_dotenv_file(target)["MLSPACE_CLIENT_SECRET"] == "secret-value"
    assert read_dotenv_file(target)["MLSPACE_READONLY"] == "true"
    assert "Ключи сохранены; настройка клиентов не завершена" in capsys.readouterr().err


@respx.mock
def test_init_refuses_unpersisted_ca_directory_dependency(tmp_path, monkeypatch, capsys):
    routes()
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path))
    target = tmp_path / '.env'
    assert run_init(target, base_url=BASE, config_only=True, non_interactive=True,
                    from_env=True, workspace_ids=['ws2']) == 1
    assert not target.exists()
    assert '--ca-file' in capsys.readouterr().err


@respx.mock
def test_human_init_connects_detected_client_and_skills_after_two_masked_prompts(tmp_path, monkeypatch, capsys):
    routes()
    respx.get(BASE + '/public/v2/workspaces/v3/').respond(200, json={'workspaces': [
        {'id': 'ws2', 'name': 'only', 'project_name': 'project'}]})
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('mlspace_mcp.init_cli.detect_clients', lambda: {'opencode': '/bin/true'})
    labels = []
    values = iter(['human-id', 'human-secret'])
    def prompt(label):
        labels.append(label)
        return next(values)
    monkeypatch.setattr('mlspace_mcp.init_cli.masked_prompt', prompt)
    monkeypatch.setattr('mlspace_mcp.init_cli.server_command',
                        lambda: ['/opt/tools/mlspace-plugin', '--transport', 'stdio'])
    monkeypatch.setattr('mlspace_mcp.init_cli.verify_client', lambda *a: None)
    target = tmp_path / '.config/mlspace-plugin/.env'
    assert run_init(target, base_url=BASE) == 0
    assert [label.strip() for label in labels] == ['Cloud.ru Key ID:', 'Cloud.ru Key Secret:']
    assert read_dotenv_file(target)['MLSPACE_CLIENT_SECRET'] == 'human-secret'
    config = json.loads((tmp_path / '.config/opencode/opencode.json').read_text())
    assert config['mcp']['mlspace']['environment'] == {'MLSPACE_ENV_FILE': str(target)}
    assert len(list((tmp_path / '.config/opencode/skills').glob('*/SKILL.md'))) == 18
    output = capsys.readouterr()
    assert 'human-secret' not in output.err + output.out
    assert 'human-id' not in output.err + output.out


@pytest.mark.parametrize('kind', ['symlink', 'directory'])
@respx.mock
def test_invalid_credentials_target_fails_before_client_setup(tmp_path, monkeypatch, capsys, kind):
    target = tmp_path / 'invalid.env'
    if kind == 'symlink':
        target.symlink_to(tmp_path / 'missing.env')
    else:
        target.mkdir()
    calls = []
    monkeypatch.setattr('mlspace_mcp.init_cli._clients', lambda *a: calls.append('clients') or {})
    assert run_init(target, force=True, non_interactive=True, from_env=True) == 1
    assert calls == []
    assert 'обычный файл' in capsys.readouterr().err


@respx.mock
def test_save_failure_happens_before_connecting_clients(tmp_path, monkeypatch, capsys):
    routes()
    target = tmp_path / '.env'
    original = 'MLSPACE_READONLY=true\n'
    target.write_text(original)
    monkeypatch.setattr('mlspace_mcp.init_cli._clients', lambda *a: {'opencode': '/bin/true'})
    monkeypatch.setattr('mlspace_mcp.init_cli.server_command', lambda: ['/bin/echo'])
    def fail_save(*a):
        raise PermissionError('sensitive detail should not be printed')
    monkeypatch.setattr('mlspace_mcp.init_cli._write', fail_save)
    assert run_init(target, force=True, base_url=BASE, non_interactive=True,
                    from_env=True, workspace_ids=['ws2']) == 1
    assert target.read_text() == original
    assert not (tmp_path / '.config/opencode/opencode.json').exists()
    error = capsys.readouterr().err
    assert 'Не удалось сохранить ключи' in error
    assert str(target) in error
    assert 'Клиенты ещё не подключены' in error
    assert 'sensitive detail' not in error


@respx.mock
def test_existing_opencode_can_update_without_cli_or_client_selection(tmp_path, monkeypatch):
    from mlspace_mcp import client_setup
    routes()
    target = tmp_path / '.env'
    client_setup.install_clients({'opencode': '/missing/opencode'}, target, ['runtime-v1'])
    monkeypatch.setattr(client_setup, 'detect_clients', lambda: {})
    monkeypatch.setattr('mlspace_mcp.init_cli.detect_clients', lambda: {})
    monkeypatch.setattr('mlspace_mcp.init_cli.server_command', lambda: ['runtime-v2'])
    monkeypatch.setattr('mlspace_mcp.init_cli.verify_client', lambda *a: None)
    assert run_init(target, force=True, base_url=BASE, non_interactive=True,
                    from_env=True, workspace_ids=['ws2']) == 0
    config = json.loads((tmp_path / '.config/opencode/opencode.json').read_text())
    assert config['mcp']['mlspace']['command'] == ['runtime-v2']


@respx.mock
def test_setup_handshake_failure_reports_saved_and_installed_but_not_ready(tmp_path, monkeypatch, capsys):
    routes()
    monkeypatch.setattr('mlspace_mcp.init_cli._clients', lambda *a: {'opencode': '/bin/true'})
    monkeypatch.setattr('mlspace_mcp.init_cli.server_command', lambda: ['/bin/echo'])
    def fail(client, executable, target, command):
        assert target.exists()
        assert (tmp_path / '.config/opencode/opencode.json').exists()
        raise ValueError('MCP verification failed')
    monkeypatch.setattr('mlspace_mcp.init_cli.verify_client', fail)
    assert run_init(tmp_path / '.env', base_url=BASE, non_interactive=True,
                    from_env=True, workspace_ids=['ws2']) == 1
    output = capsys.readouterr().err
    assert 'Ключи сохранены' in output and 'opencode' in output
    assert 'Done.' not in output


def test_deleted_connections_are_not_automatically_recreated(tmp_path, monkeypatch):
    from mlspace_mcp.client_setup import install_clients
    from mlspace_mcp.init_cli import _clients
    install_clients({'opencode': '/bin/true'}, tmp_path / '.env', ['runtime-v1'])
    (tmp_path / '.config/opencode/opencode.json').write_text('{}')
    monkeypatch.setattr('mlspace_mcp.init_cli.detect_clients', lambda: {'opencode': '/bin/true'})
    with pytest.raises(ValueError, match='Явно выберите новый клиент'):
        _clients([], [], True)
    assert _clients(['opencode'], [], True) == {'opencode': '/bin/true'}
