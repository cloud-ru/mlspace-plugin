"""Selected contexts are fixed; requests and lazy lookups never share workspace state."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.auth import TokenManager
from mlspace_mcp.errors import MLSpaceError
from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.tools.notebooks import DOMAIN as NB


def multi_settings(**kwargs):
    return make_settings(workspaces=[
        {"id": "A", "name": "alpha", "project_name": "p1"},
        {"id": "B", "name": "beta", "project_name": "p2"},
    ], namespace="legacy-ns", **kwargs)


def runtime(settings, http):
    from mlspace_mcp.workspace_context import WorkspaceClients
    return WorkspaceClients(settings, http, TokenManager(http, BASE_URL, "cid", "csecret"))


def context(clients):
    return SimpleNamespace(request_context=SimpleNamespace(
        lifespan_context=SimpleNamespace(clients=clients)))


def handler(domain, settings):
    return _build_handler(domain, available_actions(domain, settings.readonly), settings)


@respx.mock
async def test_interleaved_notebook_creates_keep_keys_headers_namespaces():
    settings = multi_settings(readonly=False)
    respx.post(AUTH_URL).respond(200, json={"token": {"access_token": "token", "expires_in": 3600}})
    def key(request):
        ws = request.headers["x-workspace-id"]
        assert ws in {"A", "B"}
        assert request.headers["x-api-key"] == ""
        return httpx.Response(200, json={"x-api-key": f"key-{ws}"})
    respx.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").mock(side_effect=key)
    for ws in ("A", "B"):
        respx.get(f"{BASE_URL}/public/v2/workspaces/v3/{ws}").respond(
            200, json={"namespace": f"ns-{ws}"})
        respx.post(f"{BASE_URL}/public/v2/notebooks/v2/ns-{ws}/notebook").respond(
            200, json={"uid": f"nb-{ws}"})
    body = {"name": "nb", "image": {"name": "img", "tag": "1", "type": "datahub"},
            "instance_type": "free.0gpu"}
    async with httpx.AsyncClient() as http:
        clients = runtime(settings, http)
        call = handler(NB, settings)
        outputs = await asyncio.gather(*(
            call(action="create", target=ws, body=body, ctx=context(clients))
            for ws in ("A", "B", "A", "B")))
        assert (await clients.resolve("alpha")) is (await clients.resolve("A"))
    assert all("request_context" in json.loads(out) for out in outputs)
    for call in respx.calls:
        request = call.request
        if request.url.path.endswith("service_auth") or request.url.path.endswith("x_api_key"):
            continue
        ws = request.headers["x-workspace-id"]
        assert request.headers["x-api-key"] == f"key-{ws}"
        assert f"ns-{ws}" in request.url.path or request.url.path.endswith(f"/{ws}")


@respx.mock
@pytest.mark.parametrize("target", [None, "missing", "https://other.example/A"])
async def test_invalid_target_refused_without_http(target):
    async with httpx.AsyncClient() as http:
        with pytest.raises(MLSpaceError, match="target|configured"):
            await runtime(multi_settings(), http).resolve(target)
    assert not respx.calls


@respx.mock
async def test_ambiguous_names_require_id_and_catalogue_stays_fixed():
    settings = multi_settings()
    settings.workspaces[1].name = "alpha"
    async with httpx.AsyncClient() as http:
        clients = runtime(settings, http)
        settings.workspaces.clear()
        with pytest.raises(MLSpaceError, match="ambiguous"):
            await clients.resolve("alpha")
    assert not respx.calls


@respx.mock
async def test_singleton_fallback_preserves_legacy_key_namespace():
    async with httpx.AsyncClient() as http:
        client = await runtime(make_settings(namespace="pinned"), http).resolve(None)
        assert client.workspace_id == "ws-1"
        assert await client.workspace_namespace() == "pinned"
    assert not respx.calls


@respx.mock
async def test_singleton_without_stored_key_resolves_it_before_the_operation():
    """init no longer writes MLSPACE_API_KEY; a manual single-workspace file must work."""
    settings = make_settings(api_key="")
    settings.require_credentials()
    respx.post(AUTH_URL).respond(200, json={"token": {"access_token": "t", "expires_in": 3600}})
    key = respx.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").respond(
        200, json={"x-api-key": "fetched-key"})
    detail = respx.get(f"{BASE_URL}/public/v2/workspaces/v3/ws-1").respond(200, json={"id": "ws-1"})
    async with httpx.AsyncClient() as http:
        client = await runtime(settings, http).resolve(None)
        await client.request("GET", "/public/v2/workspaces/v3/{workspace_id}",
                             path_params={"workspace_id": "ws-1"})
    assert key.call_count == 1
    assert detail.calls.last.request.headers["x-api-key"] == "fetched-key"


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(403, json={"detail": "revoked"}),
                                      httpx.Response(200, json={})])
async def test_unavailable_lazy_key_does_not_send_operation(response):
    settings = multi_settings()
    respx.post(AUTH_URL).respond(200, json={"token": {"access_token": "t", "expires_in": 3600}})
    respx.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").mock(return_value=response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError):
            await handler(NB, settings)(action="list", target="B", ctx=context(runtime(settings, http)))
    assert len(respx.calls) == 2


@respx.mock
async def test_catalogue_lists_only_selected_without_credentials_or_http():
    from mlspace_mcp.server import build_server
    settings = multi_settings()
    mcp = build_server(settings)
    catalog = await mcp._tool_manager.call_tool("mlspace_contexts", {}, convert_result=False)
    assert BASE_URL in catalog
    assert 'alpha' in catalog and 'beta' in catalog
    assert 'csecret' not in catalog and 'akey' not in catalog and 'api_key' not in catalog
    assert not respx.calls


@respx.mock
async def test_multi_lifespan_has_no_first_workspace_fallback():
    from mlspace_mcp.server import build_server
    mcp = build_server(multi_settings())
    async with mcp.settings.lifespan(mcp) as app:
        assert [item.id for item in app.clients.selected] == ["A", "B"]
    assert not respx.calls


@respx.mock
async def test_cached_key_revocation_does_not_fall_back_to_other_context():
    settings = multi_settings()
    settings.workspaces[0].api_key = settings.api_key
    respx.post(AUTH_URL).respond(200, json={"token": {"access_token": "t", "expires_in": 3600}})
    respx.get(f"{BASE_URL}/public/v2/notebooks/v2/notebooks").respond(403, json={"detail": "revoked"})
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError, match="403"):
            await handler(NB, settings)(action="list", target="A", ctx=context(runtime(settings, http)))
    assert respx.calls[-1].request.headers["x-workspace-id"] == "A"
    assert len(respx.calls) == 2


@pytest.mark.respx(assert_all_called=False)
@pytest.mark.parametrize("legacy_id,selected_ids,expected_ns,profile_needed", [
    ("A", ["A"], "pinned-a", False),
    ("other", ["A"], "resolved-a", True),
    ("A", ["A", "B"], "resolved-a", True),
])
async def test_catalogue_namespace_pin_only_applies_to_matching_singleton(
    legacy_id, selected_ids, expected_ns, profile_needed, respx_mock,
):
    settings = make_settings(readonly=False, workspace_id=legacy_id, namespace="pinned-a",
        workspaces=[{"id": ws, "name": ws, "project_name": "project", "api_key": f"key-{ws}"}
                    for ws in selected_ids])
    respx_mock.post(AUTH_URL).respond(200, json={"token": {"access_token": "t", "expires_in": 3600}})
    profile = respx_mock.get(f"{BASE_URL}/public/v2/workspaces/v3/A").respond(
        200 if profile_needed else 403,
        json={"namespace": "resolved-a"} if profile_needed else {"detail": "unavailable"})
    create = respx_mock.post(f"{BASE_URL}/public/v2/notebooks/v2/{expected_ns}/notebook").respond(
        200, json={"uid": "nb-a"})
    body = {"name": "nb", "image": {"name": "img", "tag": "1", "type": "datahub"},
            "instance_type": "free.0gpu"}
    async with httpx.AsyncClient() as http:
        out = await handler(NB, settings)(action="create", target="A", body=body,
                                         ctx=context(runtime(settings, http)))
    assert "nb-a" in out
    assert profile.called == profile_needed
    assert create.calls[0].request.headers["x-workspace-id"] == "A"
    assert create.calls[0].request.headers["x-api-key"] == "key-A"
