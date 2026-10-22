import json
import subprocess

import pytest


@pytest.fixture(params=['claude-code', 'codex'])
def cli(request, monkeypatch, tmp_path):
    from mlspace_mcp import native_plugins as native

    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / '.codex'))
    client = request.param
    calls = []
    state = {'market': None, 'version': None, 'enabled': True}
    root = tmp_path / 'plugin'
    root.mkdir()

    def run(argv, **kwargs):
        calls.append(argv[1:])
        assert 'MLSPACE_CLIENT_SECRET' not in kwargs['env']
        assert kwargs['timeout'] > 0 and kwargs['capture_output']
        args = argv[1:]
        if client == 'claude-code' and args[1] in ('install', 'update') and '--json' in args:
            raise subprocess.CalledProcessError(1, argv, stderr="error: unknown option '--json'")
        if args[:3] == ['plugin', 'marketplace', 'list']:
            rows = [] if not state['market'] else ([{
                'name': 'mlspace', 'source': 'git', 'url': state['market'],
            }] if client == 'claude-code' else [{
                'name': 'mlspace', 'marketplaceSource': {'sourceType': 'git', 'source': state['market']},
            }])
            result = rows if client == 'claude-code' else {'marketplaces': rows}
        elif args[:2] == ['plugin', 'list']:
            rows = [] if not state['version'] else ([{
                'id': 'mlspace@mlspace', 'scope': 'user', 'enabled': state['enabled'],
                'version': state['version'], 'installPath': str(root),
            }] if client == 'claude-code' else [{
                'pluginId': 'mlspace@mlspace', 'installed': True, 'enabled': state['enabled'],
                'version': state['version'],
            }])
            result = rows if client == 'claude-code' else {'installed': rows, 'available': []}
        elif args[:3] == ['mcp', 'get', 'mlspace']:
            result = {'enabled': True, 'transport': {'env': {'PLUGIN_ROOT': str(root)}}}
        elif args[:3] == ['plugin', 'marketplace', 'add']:
            state['market'] = args[3]
            result = {}
        else:
            if args[1] in ('install', 'add', 'update'):
                state['version'] = '1.2.3'
            if args[1] == 'enable':
                state['enabled'] = True
            result = {}
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), '')

    monkeypatch.setenv('MLSPACE_CLIENT_SECRET', 'never-to-cli')
    monkeypatch.setattr(native.subprocess, 'run', run)
    return native, client, state, calls, root


def test_install_and_repeat_do_not_duplicate_native_plugin(cli):
    native, client, state, calls, root = cli
    status = native.install_native(client, '/client', '1.2.3')
    assert status.installed and status.enabled and status.version == '1.2.3'
    assert status.plugin_root == root and status.marketplace_source == native.MARKETPLACE_SOURCE
    calls.clear()
    native.install_native(client, '/client', '1.2.3')
    assert all('list' in args or args[:2] == ['mcp', 'get'] for args in calls)


def test_foreign_marketplace_stops_before_any_write(cli):
    native, client, state, calls, _ = cli
    state['market'] = 'https://example.org/someone-else.git'
    with pytest.raises(ValueError, match='источник'):
        native.install_native(client, '/client', '1.2.3')
    assert all('list' in args for args in calls)


def test_update_reads_back_target_version(cli):
    native, client, state, calls, _ = cli
    state.update(market=native.MARKETPLACE_SOURCE, version='1.0.0')
    assert native.install_native(client, '/client', '1.2.3').version == '1.2.3'
    assert ['plugin', 'marketplace', 'update' if client == 'claude-code' else 'upgrade',
            'mlspace', *([] if client == 'claude-code' else ['--json'])] in calls


def test_cli_error_and_invalid_json_never_expose_output(cli, monkeypatch):
    native, client, _, _, _ = cli
    def failure(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, output='private-token')
    monkeypatch.setattr(native.subprocess, 'run', failure)
    with pytest.raises(ValueError, match=client) as error:
        native.inspect_native(client, '/client')
    assert 'private-token' not in str(error.value)
    monkeypatch.setattr(native.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess([], 0, 'private-token', ''))
    with pytest.raises(ValueError, match=client) as error:
        native.inspect_native(client, '/client')
    assert 'private-token' not in str(error.value)


@pytest.mark.parametrize('failure,expected', [
    (subprocess.CalledProcessError(1, ['/client'], stderr="unknown option '--json' private-token"),
     'не поддерживает'),
    (subprocess.CalledProcessError(7, ['/client'], stderr='private-token'), 'код возврата 7'),
    (subprocess.CalledProcessError(1, ['/client'], stderr=
     "Marketplace source 'git:https://github.com/cloud-ru/mlspace-plugin.git' "
     '(github.com) is blocked by enterprise policy. Allowed sources: private-token'), 'политик'),
    (subprocess.TimeoutExpired(['/client'], 180, output='private-token'), '180'),
    (FileNotFoundError('private-token'), 'не найден'),
])
def test_native_failure_identifies_command_and_reason_without_output(monkeypatch, failure, expected):
    from mlspace_mcp import native_plugins as native

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(native.subprocess, 'run', fail)
    with pytest.raises(ValueError) as error:
        native._run('claude-code', '/client', ['plugin', 'list', '--json'], json_output=True)
    message = str(error.value)
    assert '/client plugin list --json' in message
    assert expected in message
    assert 'private-token' not in message


def test_invalid_native_json_is_distinct_from_cli_failure(monkeypatch):
    from mlspace_mcp import native_plugins as native

    monkeypatch.setattr(native.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess([], 0, 'private-token', ''))
    with pytest.raises(ValueError) as error:
        native._run('claude-code', '/client', ['plugin', 'list', '--json'], json_output=True)
    assert 'JSON' in str(error.value)
    assert '/client plugin list --json' in str(error.value)
    assert 'private-token' not in str(error.value)
