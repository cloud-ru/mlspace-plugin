"""Where the credentials come from, and which source wins.

MCP hands a stdio server nothing here — the spec says such servers "retrieve
credentials from the environment" and stops — so the search order is entirely ours
to define and therefore entirely ours to pin. It runs from general to specific:
the user config written by ``setup``, then a file named
outright by ``MLSPACE_ENV_FILE`` (what the Agent Plugins bundle sets, since a path
may travel in a package where a secret may not), and finally real environment
variables, which beat every file.
"""

from __future__ import annotations

import json

import pytest

from mlspace_mcp.config import (
    CREDENTIALS,
    ENV_FILE_VAR,
    LITERAL_DOTENV_MARKER,
    Settings,
    load_settings,
    user_config_file,
)

FULL = """\
MLSPACE_CLIENT_ID=cid-from-file
MLSPACE_CLIENT_SECRET=secret-from-file
MLSPACE_API_KEY=key-from-file
MLSPACE_WORKSPACE_ID=ws-from-file
"""


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch, tmp_path):
    """A developer's own MLSPACE_* would mask exactly what these tests assert."""
    for name in (*CREDENTIALS, "API_KEY", "BASE_URL", "ENV_FILE", "NAMESPACE", "READONLY",
                 "WORKSPACES"):
        monkeypatch.delenv(f"MLSPACE_{name}", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))  # not the dev's own
    monkeypatch.chdir(tmp_path)  # and no ./.env to fall back on


def test_credentials_are_read_from_the_named_file(monkeypatch, tmp_path):
    env_file = tmp_path / "data" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(FULL)
    monkeypatch.setenv(ENV_FILE_VAR, str(env_file))

    settings = load_settings()
    settings.require_credentials()  # must not raise
    assert settings.client_id == "cid-from-file"
    assert settings.api_key.get_secret_value() == "key-from-file"


def test_a_real_environment_variable_still_wins_over_the_file(monkeypatch, tmp_path):
    """Precedence is unchanged, so an existing ambient setup keeps working."""
    env_file = tmp_path / ".env"
    env_file.write_text(FULL)
    monkeypatch.setenv(ENV_FILE_VAR, str(env_file))
    monkeypatch.setenv("MLSPACE_CLIENT_ID", "cid-from-environment")

    assert load_settings().client_id == "cid-from-environment"


def test_cwd_dotenv_is_ignored_when_no_file_is_named(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(FULL)
    assert load_settings().client_id == ""


def test_a_directory_is_refused_with_the_fix_named(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path))
    with pytest.raises(RuntimeError) as exc:
        load_settings()
    assert "directory" in str(exc.value) and ".env" in str(exc.value)


# --- the error a user actually hits -------------------------------------------


def test_missing_credentials_quote_the_absolute_path_to_create(monkeypatch, tmp_path):
    """"Set these four variables" is useless inside a plugin: the data directory is
    client-chosen and the user has no way to find it. The path must be in the error."""
    env_file = tmp_path / "plugin-data" / ".env"
    env_file.parent.mkdir()
    monkeypatch.setenv(ENV_FILE_VAR, str(env_file))

    with pytest.raises(RuntimeError) as exc:
        load_settings().require_credentials()
    text = str(exc.value)
    assert str(env_file) in text
    assert "does not exist yet" in text
    for name in CREDENTIALS:
        assert f"MLSPACE_{name}" in text


def test_an_existing_but_incomplete_file_is_reported_as_such(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MLSPACE_CLIENT_ID=only-this-one\n")
    monkeypatch.setenv(ENV_FILE_VAR, str(env_file))

    with pytest.raises(RuntimeError) as exc:
        load_settings().require_credentials()
    text = str(exc.value)
    assert "is empty or lacks them" in text
    assert "MLSPACE_CLIENT_SECRET" in text
    assert "MLSPACE_CLIENT_ID," not in text  # the one that IS set is not listed


def test_the_error_never_echoes_a_credential(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MLSPACE_CLIENT_ID=cid-abc\nMLSPACE_API_KEY=key-xyz\n")
    monkeypatch.setenv(ENV_FILE_VAR, str(env_file))

    with pytest.raises(RuntimeError) as exc:
        load_settings().require_credentials()
    assert "cid-abc" not in str(exc.value) and "key-xyz" not in str(exc.value)


def test_without_a_named_file_the_error_points_at_init_and_the_user_config():
    """Nothing configured is the state a newcomer is in; the way out must be one line."""
    with pytest.raises(RuntimeError) as exc:
        Settings(client_id="", client_secret="", api_key="", workspace_id="").require_credentials()
    text = str(exc.value)
    assert "mlspace-plugin setup" in text
    assert str(user_config_file()) in text
    assert "Looked in:" in text and "environment" in text


# --- the search order ---------------------------------------------------------


def test_the_user_config_is_used_when_nothing_else_is_set(monkeypatch, tmp_path):
    """`init` writes here, so this is the path that makes `init` worth having."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(FULL)

    settings = load_settings()
    settings.require_credentials()
    assert settings.client_id == "cid-from-file"


def test_explicit_dotenv_beats_the_user_config(monkeypatch, tmp_path):
    """Explicitly selecting a file overrides user defaults."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text("MLSPACE_CLIENT_ID=cid-from-user-config\n")
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_CLIENT_ID=cid-from-project\n")

    assert load_settings().client_id == "cid-from-project"


def test_a_named_file_beats_both(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text("MLSPACE_CLIENT_ID=cid-from-user-config\n")
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_CLIENT_ID=cid-from-project\n")
    named = tmp_path / "named.env"
    named.write_text("MLSPACE_CLIENT_ID=cid-from-named-file\n")
    monkeypatch.setenv(ENV_FILE_VAR, str(named))

    assert load_settings().client_id == "cid-from-named-file"


def test_files_lower_in_the_order_still_fill_gaps(monkeypatch, tmp_path):
    """Overriding one value must not blank the other three."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(FULL)
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_CLIENT_ID=cid-from-project\n")

    settings = load_settings()
    settings.require_credentials()  # the other three came from the user config
    assert settings.client_id == "cid-from-project"
    assert settings.api_key.get_secret_value() == "key-from-file"


def test_catalogue_and_service_account_are_sufficient_credentials():
    settings = Settings(
        client_id="cid",
        client_secret="secret",
        api_key="",
        workspace_id="",
        workspaces=[
            {"id": "ws-one", "name": "One", "project_name": "Project", "api_key": None}
        ],
    )
    settings.require_credentials()


def test_higher_legacy_selector_defeats_lower_catalogue_without_reusing_its_key(
    monkeypatch, tmp_path
):
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    catalogue = json.dumps(
        [{"id": "ws-low", "name": "Low", "project_name": "P", "api_key": "key-low"}]
    )
    cfg.write_text(
        FULL + f"MLSPACE_WORKSPACES='{catalogue}'\nMLSPACE_NAMESPACE=ns-low\n"
    )
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text(
        "MLSPACE_WORKSPACE_ID=ws-high\nMLSPACE_API_KEY=key-high\n"
    )

    settings = load_settings()
    assert settings.workspace_id == "ws-high"
    assert settings.workspaces == []
    assert settings.api_key.get_secret_value() == "key-high"
    assert settings.namespace == ""


def test_repeated_higher_legacy_selector_keeps_the_same_workspaces_lower_key(
    monkeypatch, tmp_path
):
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(FULL + "MLSPACE_NAMESPACE=ns-same\n")
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_WORKSPACE_ID=ws-from-file\n")

    settings = load_settings()
    assert settings.workspace_id == "ws-from-file"
    assert settings.api_key.get_secret_value() == "key-from-file"
    assert settings.namespace == "ns-same"


def test_empty_higher_catalogue_uses_legacy_context_and_clears_different_workspace_data(
    monkeypatch, tmp_path
):
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    catalogue = json.dumps(
        [{"id": "ws-a", "name": "A", "project_name": "P", "api_key": "key-a"}]
    )
    cfg.write_text(
        FULL.replace("ws-from-file", "ws-a").replace("key-from-file", "key-a")
        + f"MLSPACE_WORKSPACES='{catalogue}'\nMLSPACE_NAMESPACE=ns-a\n"
    )
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_WORKSPACE_ID=ws-b\nMLSPACE_WORKSPACES=[]\n")

    settings = load_settings()
    assert settings.workspace_id == "ws-b"
    assert settings.workspaces == []
    assert settings.api_key.get_secret_value() == ""
    assert settings.namespace == ""


def test_higher_scalar_key_updates_the_matching_singleton_catalogue(monkeypatch):
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    catalogue = json.dumps(
        [{"id": "ws-a", "name": "A", "project_name": "P", "api_key": "key-old"}]
    )
    cfg.write_text(
        FULL.replace("ws-from-file", "ws-a").replace("key-from-file", "key-old")
        + f"MLSPACE_WORKSPACES='{catalogue}'\n"
    )
    monkeypatch.setenv("MLSPACE_API_KEY", "key-new")

    settings = load_settings()
    assert settings.workspaces[0].api_key is not None
    assert settings.workspaces[0].api_key.get_secret_value() == "key-new"


def test_higher_legacy_selector_reuses_matching_key_from_lower_catalogue(monkeypatch, tmp_path):
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    catalogue = json.dumps(
        [
            {"id": "ws-a", "name": "A", "project_name": "P", "api_key": "key-a"},
            {"id": "ws-b", "name": "B", "project_name": "P", "api_key": "key-b"},
        ]
    )
    cfg.write_text(
        "MLSPACE_CLIENT_ID=cid\n"
        "MLSPACE_CLIENT_SECRET=secret\n"
        f"MLSPACE_WORKSPACES='{catalogue}'\n"
    )
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_WORKSPACE_ID=ws-a\n")

    settings = load_settings()
    assert settings.workspaces == []
    assert settings.workspace_id == "ws-a"
    assert settings.api_key.get_secret_value() == "key-a"
    settings.require_credentials()


def test_legacy_dotenv_keeps_environment_interpolation(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGACY_CLIENT_ID", "cid-expanded")
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_CLIENT_ID=${LEGACY_CLIENT_ID}\n")
    assert load_settings().client_id == "cid-expanded"


def test_marked_cwd_dotenv_is_not_parsed_again_by_base_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_NAME", 'has"quote')
    catalogue = json.dumps(
        [
            {
                "id": "ws-literal",
                "name": "literal-${REVIEW_NAME}",
                "project_name": "P",
                "api_key": "key",
            }
        ]
    )
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text(
        f"{LITERAL_DOTENV_MARKER}\n"
        "MLSPACE_CLIENT_ID=cid\n"
        "MLSPACE_CLIENT_SECRET=secret\n"
        f"MLSPACE_WORKSPACES='{catalogue}'\n"
    )

    settings = load_settings()
    assert settings.workspaces[0].name == "literal-${REVIEW_NAME}"


def test_overridden_malformed_lower_catalogue_is_not_parsed(monkeypatch, tmp_path):
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text("MLSPACE_WORKSPACES=obsolete-invalid-json\n")
    catalogue = json.dumps(
        [{"id": "ws-b", "name": "B", "project_name": "P", "api_key": "key-b"}]
    )
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text(
        "MLSPACE_CLIENT_ID=cid\n"
        "MLSPACE_CLIENT_SECRET=secret\n"
        f"MLSPACE_WORKSPACES='{catalogue}'\n"
    )

    settings = load_settings()
    assert [workspace.id for workspace in settings.workspaces] == ["ws-b"]


def test_effective_malformed_catalogue_still_fails(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("MLSPACE_WORKSPACES=invalid-json\n")
    with pytest.raises(ValueError):
        load_settings()


def test_catalogue_wins_over_legacy_selector_at_the_same_higher_source(monkeypatch, tmp_path):
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(FULL)
    catalogue = json.dumps(
        [{"id": "ws-high", "name": "High", "project_name": "P", "api_key": "key-high"}]
    )
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / ".env"))
    (tmp_path / ".env").write_text(
        f"MLSPACE_WORKSPACE_ID=ignored-legacy\nMLSPACE_WORKSPACES='{catalogue}'\n"
    )

    settings = load_settings()
    assert [workspace.id for workspace in settings.workspaces] == ["ws-high"]
    assert settings.workspaces[0].api_key is not None
    assert settings.workspaces[0].api_key.get_secret_value() == "key-high"


@pytest.mark.parametrize("workspace_id", ["", " ", "\t\n"])
def test_catalogue_rejects_blank_workspace_ids(monkeypatch, workspace_id):
    monkeypatch.setenv("MLSPACE_WORKSPACES", json.dumps([
        {"id": workspace_id, "name": "Selected", "project_name": "Project"},
    ]))
    with pytest.raises(ValueError, match="Workspace id must not be blank"):
        load_settings()


def test_catalogue_preserves_opaque_nonblank_workspace_id(monkeypatch):
    workspace_id = " opaque-ID "
    monkeypatch.setenv("MLSPACE_WORKSPACES", json.dumps([
        {"id": workspace_id, "name": "Selected", "project_name": "Project"},
    ]))
    assert load_settings().workspaces[0].id == workspace_id


@pytest.mark.parametrize("via_settings", [False, True])
def test_invalid_catalogue_error_hides_raw_key(via_settings):
    from mlspace_mcp.config import ConnectedWorkspace

    raw = {"api_key": "SYNTHKEY", "id": "A", "name": "A"}
    with pytest.raises(ValueError) as caught:
        if via_settings:
            Settings(_env_file=None, workspaces=[raw])
        else:
            ConnectedWorkspace.model_validate(raw)
    assert raw["api_key"] not in str(caught.value)
    assert "project_name" in str(caught.value)


def test_untrusted_cwd_cannot_redirect_saved_credentials(tmp_path):
    from mlspace_mcp.config import DEFAULT_BASE_URL
    cfg = user_config_file()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(FULL)
    (tmp_path / '.env').write_text('MLSPACE_BASE_URL=https://untrusted.example\n')
    settings = load_settings()
    assert settings.base_url == DEFAULT_BASE_URL
    assert settings.client_secret.get_secret_value() == 'secret-from-file'
    assert Settings().base_url == DEFAULT_BASE_URL
