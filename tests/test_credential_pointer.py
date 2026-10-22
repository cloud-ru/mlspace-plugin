import json
import stat

import pytest

from mlspace_mcp.config import load_settings
from mlspace_mcp.init_cli import _write, run_init


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith('MLSPACE_'):
            monkeypatch.delenv(key)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))


def test_setup_pointer_survives_lost_shell_and_does_not_merge_another_account(tmp_path, monkeypatch):
    monkeypatch.setenv('MLSPACE_CLIENT_ID', 'chosen-id')
    monkeypatch.setenv('MLSPACE_CLIENT_SECRET', 'chosen-secret')
    target = tmp_path / 'custom/credentials.env'
    assert run_init(target, config_only=True, verify=False, non_interactive=True,
                    from_env=True, workspace_ids=['chosen-workspace']) == 0
    _write(tmp_path / '.config/mlspace-plugin/.env',
           {'CLIENT_ID': 'stale', 'CLIENT_SECRET': 'stale', 'WORKSPACE_ID': 'stale', 'CA_FILE': '/stale.pem'})
    for key in ['XDG_CONFIG_HOME', 'MLSPACE_CLIENT_ID', 'MLSPACE_CLIENT_SECRET']:
        monkeypatch.delenv(key)
    settings = load_settings()
    assert settings.client_id == 'chosen-id'
    assert settings.client_secret.get_secret_value() == 'chosen-secret'
    assert settings.workspaces[0].id == 'chosen-workspace'
    assert settings.ca_file == ''
    pointer = tmp_path / '.config/mlspace-plugin/credentials-path.json'
    assert json.loads(pointer.read_text()) == {'env_file': str(target)}
    assert stat.S_IMODE(pointer.stat().st_mode) == 0o600
    assert stat.S_IMODE(pointer.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize('content', ['not-json', '[]', '{"env_file":"relative.env"}', '{"env_file":12}'])
def test_invalid_pointer_stops_before_loading_another_account(tmp_path, content):
    pointer = tmp_path / '.config/mlspace-plugin/credentials-path.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_text(content)
    with pytest.raises(ValueError, match='credentials-path'):
        load_settings()


def test_missing_pointer_target_does_not_fall_back_to_saved_keys(tmp_path):
    pointer = tmp_path / '.config/mlspace-plugin/credentials-path.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({'env_file': str(tmp_path / 'missing.env')}))
    _write(tmp_path / 'xdg/mlspace-plugin/.env', {'CLIENT_ID': 'wrong-account'})
    with pytest.raises(ValueError, match='credentials'):
        load_settings()
