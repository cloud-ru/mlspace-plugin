"""Identify plugin traffic at the outgoing HTTP boundary, including bootstrap."""
from __future__ import annotations

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings

from mlspace_mcp import __version__
from mlspace_mcp.init_cli import _fetch_workspaces, _verify
from mlspace_mcp.server import build_server

TOKEN_BODY = {"token": {"access_token": "test-token", "expires_in": 3600}}


@pytest.mark.parametrize("flow", ["discovery", "verification"])
@respx.mock
def test_onboarding_identifies_every_outgoing_request(flow):
    respx.post(AUTH_URL).respond(200, json=TOKEN_BODY)
    if flow == "discovery":
        respx.get(f"{BASE_URL}/public/v2/workspaces/v3/").respond(
            200, json={"workspaces": [{"id": "ws-1", "name": "test"}]},
        )
        assert _fetch_workspaces("cid", "csecret", base_url=BASE_URL)[0]["id"] == "ws-1"
    else:
        respx.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").respond(
            200, json={"x-api-key": "test-key"},
        )
        respx.get(f"{BASE_URL}/public/v2/workspaces/v3/ws-1").respond(
            200, json={"id": "ws-1"},
        )
        assert not _verify({
            "CLIENT_ID": "cid", "CLIENT_SECRET": "csecret", "WORKSPACE_ID": "ws-1",
        }, base_url=BASE_URL).errors
    assert respx.calls
    for call in respx.calls:
        assert call.request.headers["user-agent"] == f"mlspace-plugin/{__version__}"


@respx.mock
async def test_server_identifies_auth_key_lookup_api_and_retry_requests():
    respx.post(AUTH_URL).respond(200, json=TOKEN_BODY)
    respx.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").respond(
        200, json={"x-api-key": "test-key"},
    )
    jobs = respx.get(f"{BASE_URL}/public/v2/jobs").mock(side_effect=[
        httpx.Response(401, json={"detail": "expired"}),
        httpx.Response(200, json={"jobs": []}),
    ])
    server = build_server(make_settings(api_key=""))
    async with server.settings.lifespan(server) as app:
        client = await app.clients.resolve("ws-1")
        assert await client.request("GET", "/public/v2/jobs") == {"jobs": []}
    assert jobs.call_count == 2
    assert respx.post(AUTH_URL).call_count == 2
    for call in respx.calls:
        assert call.request.headers["user-agent"] == f"mlspace-plugin/{__version__}"
    assert jobs.calls.last.request.headers["x-api-key"] == "test-key"
    assert jobs.calls.last.request.headers["x-workspace-id"] == "ws-1"
