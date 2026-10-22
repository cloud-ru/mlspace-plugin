import json
import stat
import subprocess

import httpx
import pytest
import respx

from mlspace_mcp import diagnostics
from mlspace_mcp.errors import MLSpaceError


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    path = diagnostics.start('test')
    assert path is not None
    yield path
    diagnostics.close()


def test_private_file_and_exception_chain_without_values(log):
    try:
        try:
            raise ValueError('client_secret=SHOULD-NOT-LEAK')
        except ValueError as exc:
            raise RuntimeError('Bearer SHOULD-NOT-LEAK') from exc
    except RuntimeError as exc:
        diagnostics.failure('setup', exc)
    text = log.read_text()
    assert 'SHOULD-NOT-LEAK' not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert events[0]['event'] == 'session.start'
    assert events[-1]['error_chain'][0]['type'] == 'RuntimeError'
    assert events[-1]['error_chain'][1]['type'] == 'ValueError'
    assert events[-1]['error_chain'][0]['frames']
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_rotation_is_bounded_private_and_keeps_current_events(log, monkeypatch):
    monkeypatch.setattr(diagnostics._handler, 'maxBytes', 600)
    for _ in range(70):
        diagnostics.event('step', phase='test')
    assert log.with_name(log.name + '.1').exists()
    assert log.with_name(log.name + '.2').exists()
    assert not log.with_name(log.name + '.3').exists()
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in log.parent.iterdir())
    assert json.loads(log.read_text().splitlines()[-1])['event'] == 'step'


def test_unwritable_log_does_not_prevent_work(tmp_path, monkeypatch, capsys):
    diagnostics.close()
    monkeypatch.setenv('HOME', str(tmp_path))
    def fail(*a, **kw):
        raise PermissionError('private-value')
    monkeypatch.setattr(diagnostics, '_open_handler', fail)
    assert diagnostics.start('test') is None
    diagnostics.event('step')
    err = capsys.readouterr().err
    assert 'не удалось' in err
    assert 'private-value' not in err


def test_native_failure_logs_command_and_policy_without_raw_output(log, monkeypatch):
    from mlspace_mcp import native_plugins
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr=
            "blocked by your organization's policy; private-token")
    monkeypatch.setattr(native_plugins.subprocess, 'run', fail)
    with pytest.raises(ValueError, match='политик'):
        native_plugins._run('claude-code', '/client', ['plugin', 'install', 'mlspace@mlspace'])
    text = log.read_text()
    assert 'private-token' not in text
    assert 'plugin install mlspace@mlspace' in text
    assert 'policy' in text


async def test_api_log_has_status_without_headers_body_query_or_ids(log):
    from mlspace_mcp.auth import TokenManager
    from mlspace_mcp.client import MLSpaceClient
    with respx.mock as mock:
        mock.post('https://api.test/public/v2/service_auth').respond(200, json={
            'token': {'access_token': 'secret-token'}})
        mock.get('https://api.test/public/v2/workspaces/v3/secret-id').respond(
            403, json={'detail': 'secret-response'})
        async with httpx.AsyncClient() as http:
            client = MLSpaceClient(http, TokenManager(http, 'https://api.test', 'secret-id',
                'secret-client-key'), base_url='https://api.test', api_key='secret-api-key',
                workspace_id='secret-id')
            with pytest.raises(MLSpaceError):
                await client.request('GET', '/public/v2/workspaces/v3/{workspace_id}',
                    path_params={'workspace_id': 'secret-id'}, params={'password': 'secret-query'})
    text = log.read_text()
    for value in ('secret-token', 'secret-id', 'secret-response', 'secret-query',
                  'secret-client-key', 'secret-api-key'):
        assert value not in text
    assert '403' in text and '/public/v2/service_auth' in text
    assert '/public/v2/workspaces/v3/{workspace_id}' in text


def test_no_diagnostic_messages_on_stdout(log, capsys):
    diagnostics.event('step')
    assert capsys.readouterr().out == ''


def test_log_flush_failure_does_not_escape_close(log, monkeypatch):
    handler = diagnostics._handler
    assert handler is not None
    original_close = handler.close
    def fail():
        original_close()
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr(handler, 'close', fail)
    diagnostics.close()
    assert diagnostics.current_path() is None


def test_no_symlink_following(tmp_path, monkeypatch):
    diagnostics.close()
    monkeypatch.setenv('HOME', str(tmp_path))
    target = tmp_path / 'elsewhere'
    target.mkdir()
    folder = tmp_path / '.config/mlspace-plugin'
    folder.mkdir(parents=True)
    (folder / 'logs').symlink_to(target, target_is_directory=True)
    assert diagnostics.start('test') is None
    assert not list(target.iterdir())


def test_setup_failure_prints_log_location_and_preserves_exit_code(tmp_path, monkeypatch, capsys):
    from mlspace_mcp.__main__ import main
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.delenv('MLSPACE_ENV_FILE', raising=False)
    result = main(['setup', '--config-only', '--non-interactive', '--no-verify'])
    assert result == 1
    logs = list((tmp_path / '.config/mlspace-plugin/logs').glob('*.log'))
    assert len(logs) == 1
    stderr = capsys.readouterr().err
    assert str(logs[0]) in stderr
    assert 'failure' in logs[0].read_text()
