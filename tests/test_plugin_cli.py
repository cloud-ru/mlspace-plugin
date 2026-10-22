"""Public installer command, without contacting a tenant or configuring real clients."""

import pytest

from mlspace_mcp.__main__ import main
from mlspace_mcp.config import read_dotenv_file


def test_setup_writes_explicit_offline_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv("MLSPACE_CLIENT_ID", "test-id")
    monkeypatch.setenv("MLSPACE_CLIENT_SECRET", "test-secret")
    target = tmp_path / "config" / ".env"
    assert main([
        "setup", "--path", str(target), "--non-interactive", "--from-env",
        "--config-only", "--no-verify", "--workspace", "test-workspace",
        "--base-url", "https://example.invalid",
    ]) == 0
    saved = read_dotenv_file(target)
    assert saved["MLSPACE_CLIENT_ID"] == "test-id"
    assert saved["MLSPACE_CLIENT_SECRET"] == "test-secret"
    assert "test-workspace" in saved["MLSPACE_WORKSPACES"]


def test_old_init_command_is_not_an_alias():
    with pytest.raises(SystemExit) as error:
        main(["init"])
    assert error.value.code == 2


def test_version_identifies_the_public_product(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0
    assert capsys.readouterr().out.startswith("mlspace-plugin ")
