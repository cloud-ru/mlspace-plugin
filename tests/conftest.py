from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mlspace_mcp.config import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


@pytest.fixture
def isolated_client_environment(tmp_path, monkeypatch):
    """Opt-in for tests which register/inspect real CLI clients, including units."""
    for key in list(os.environ):
        if key.startswith(('MLSPACE_', 'CODEX_', 'CLAUDE_', 'OPENCODE_', 'XDG_', 'OPENAI_', 'ANTHROPIC_')):
            monkeypatch.delenv(key)
    home = tmp_path / 'isolated-client-home'
    for key, suffix in [('HOME', ''), ('XDG_CONFIG_HOME', '.config'),
                        ('XDG_DATA_HOME', '.local/share'), ('XDG_STATE_HOME', '.local/state'),
                        ('XDG_CACHE_HOME', '.cache'), ('CODEX_HOME', '.codex'),
                        ('CLAUDE_CONFIG_DIR', '.claude')]:
        target = home / suffix
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(target))

BASE_URL = "https://api.test.local"
AUTH_URL = f"{BASE_URL}/public/v2/service_auth"


def make_settings(**overrides) -> Settings:
    base = dict(
        client_id="cid",
        client_secret="csecret",
        api_key="akey",
        workspace_id="ws-1",
        workspaces=[],
        base_url=BASE_URL,
        transport="stdio",
        readonly=True,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings_ro() -> Settings:
    return make_settings(readonly=True)


@pytest.fixture
def settings_rw() -> Settings:
    return make_settings(readonly=False)


def runtime_ctx(http, settings: Settings | None = None, **overrides):
    """A tool ``ctx`` wired exactly like the production lifespan: WorkspaceClients.

    With the default settings the single workspace ``ws-1`` already has ``akey``,
    so no key lookup is issued; pass ``api_key=""`` to exercise the lazy lookup.
    Wrap calls in ``respx.mock`` — an unmocked request then fails the test.
    """
    from types import SimpleNamespace

    from mlspace_mcp.auth import TokenManager
    from mlspace_mcp.workspace_context import WorkspaceClients

    settings = settings or make_settings(**overrides)
    clients = WorkspaceClients(settings, http, TokenManager(http, BASE_URL, "cid", "csecret"))
    return SimpleNamespace(request_context=SimpleNamespace(
        lifespan_context=SimpleNamespace(clients=clients)))
