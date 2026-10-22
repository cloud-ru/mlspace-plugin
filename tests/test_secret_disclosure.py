"""Explicit credential disclosure must not become a global redaction bypass."""

import json

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.server import build_server
from mlspace_mcp.tools.docker_registry import DOMAIN as REGISTRY
from mlspace_mcp.tools.workspaces import DOMAIN as WORKSPACES


async def test_registry_rotation_is_disabled_by_default_even_with_agent_confirmation():
    settings = make_settings(readonly=False)
    handler = _build_handler(REGISTRY, available_actions(REGISTRY, False), settings)
    with respx.mock as mock:
        async with httpx.AsyncClient() as http:
            with pytest.raises(ToolError, match="MLSPACE_ALLOW_REGISTRY_PASSWORD_ROTATION"):
                await handler(action="generate_password", confirm=True, reveal_secret=True,
                              ctx=runtime_ctx(http, settings))
        assert not mock.calls


@pytest.mark.parametrize("domain,action,key", [
    (REGISTRY, "generate_password", "password"),
    (WORKSPACES, "get_api_key", "x-api-key"),
])
@pytest.mark.parametrize("reveal", [None, False, True])
async def test_credential_disclosure_is_opt_in(domain, action, key, reveal):
    settings = make_settings(readonly=False, allow_registry_password_rotation=True)
    handler = _build_handler(domain, available_actions(domain, False), settings)
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={"token": {"access_token": "t"}})
        route = mock.get(BASE_URL + domain.actions[action].path).respond(
            200, json={key: "synthetic-secret", "username": "alice"})
        async with httpx.AsyncClient() as http:
            options = {} if reveal is None else {"reveal_secret": reveal}
            result = await handler(action=action, confirm=True,
                                   ctx=runtime_ctx(http, settings), **options)
    value = json.loads(result)[key]
    assert (value == "synthetic-secret") is bool(reveal)
    if not reveal:
        assert "redacted" in value
    request = route.calls[0].request
    assert "reveal_secret" not in str(request.url)
    assert "confirm" not in str(request.url)


@pytest.mark.parametrize("domain,action,kwargs", [
    (REGISTRY, "generate_password", {}),
    (WORKSPACES, "get_api_key", {}),
    (REGISTRY, "current_registry", {"confirm": True}),
    (WORKSPACES, "list", {"confirm": True}),
])
async def test_unconfirmed_or_unrelated_disclosure_is_refused_before_http(domain, action, kwargs):
    settings = make_settings(readonly=False)
    handler = _build_handler(domain, available_actions(domain, False), settings)
    with respx.mock as mock:
        async with httpx.AsyncClient() as http:
            with pytest.raises(ToolError, match="confirm=true|not supported"):
                await handler(action=action, reveal_secret=True,
                              ctx=runtime_ctx(http, settings), **kwargs)
        assert not mock.calls


async def test_disclosure_does_not_bypass_readonly_or_dry_run():
    with respx.mock as mock:
        async with httpx.AsyncClient() as http:
            settings = make_settings(readonly=True)
            handler = _build_handler(REGISTRY, available_actions(REGISTRY, True), settings)
            with pytest.raises(ToolError, match="read-only"):
                await handler(action="generate_password", confirm=True, reveal_secret=True,
                              ctx=runtime_ctx(http, settings))
            settings = make_settings(readonly=False, dry_run=True)
            handler = _build_handler(REGISTRY, available_actions(REGISTRY, False), settings)
            result = json.loads(await handler(
                action="generate_password", confirm=True, reveal_secret=True,
                ctx=runtime_ctx(http, settings)))
            assert result["dry_run"] is True
        assert not mock.calls


async def test_only_credential_tools_advertise_disclosure(settings_rw):
    tools = await build_server(settings_rw).list_tools()
    exposed = {tool.name for tool in tools
               if "reveal_secret" in tool.inputSchema["properties"]}
    assert exposed == {"mlspace_docker_registry", "mlspace_workspaces"}
