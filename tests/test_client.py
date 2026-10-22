from __future__ import annotations

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL

from mlspace_mcp.auth import TokenManager
from mlspace_mcp.client import MLSpaceClient
from mlspace_mcp.errors import MLSpaceError

TOKEN_BODY = {"token": {"access_token": "AT-1", "expires_in": 3600}}


def _client(http: httpx.AsyncClient) -> MLSpaceClient:
    tokens = TokenManager(http, BASE_URL, "cid", "csecret")
    return MLSpaceClient(http, tokens, base_url=BASE_URL, api_key="akey", workspace_id="ws-1")


@respx.mock
async def test_injects_all_three_headers():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.get(f"{BASE_URL}/public/v2/inference/v2/").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with httpx.AsyncClient() as http:
        await _client(http).request("GET", "/public/v2/inference/v2/")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer AT-1"
    assert req.headers["x-api-key"] == "akey"
    assert req.headers["x-workspace-id"] == "ws-1"


@respx.mock
async def test_no_auth_headers_on_service_auth():
    auth = respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/inference/v2/").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        await _client(http).request("GET", "/public/v2/inference/v2/")
    auth_req = auth.calls.last.request
    assert "authorization" not in auth_req.headers
    assert "x-api-key" not in auth_req.headers
    assert "x-workspace-id" not in auth_req.headers


@respx.mock
async def test_path_templating():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.get(f"{BASE_URL}/public/v2/inference/v2/svc-1").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as http:
        await _client(http).request(
            "GET", "/public/v2/inference/v2/{inference_name}",
            path_params={"inference_name": "svc-1"},
        )
    assert route.called


@respx.mock
async def test_repeated_query_keys():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.delete(f"{BASE_URL}/public/v2/data_transfer/v2/connectors").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with httpx.AsyncClient() as http:
        await _client(http).request(
            "DELETE", "/public/v2/data_transfer/v2/connectors",
            params=[("ids", "a"), ("ids", "b")],
        )
    query = str(route.calls.last.request.url.query)
    assert "ids=a" in query and "ids=b" in query


@respx.mock
async def test_delete_with_body():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.delete(f"{BASE_URL}/public/v2/docker_registry/v2/tags/").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as http:
        await _client(http).request(
            "DELETE", "/public/v2/docker_registry/v2/tags/", json_body={"ids": [1, 2]}
        )
    import json as _json

    assert _json.loads(route.calls.last.request.content) == {"ids": [1, 2]}


@respx.mock
async def test_401_triggers_single_retry_after_refresh():
    auth = respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.get(f"{BASE_URL}/public/v2/inference/v2/").mock(
        side_effect=[
            httpx.Response(401, json={"detail": "expired"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with httpx.AsyncClient() as http:
        result = await _client(http).request("GET", "/public/v2/inference/v2/")
    assert result == {"ok": True}
    assert route.call_count == 2
    assert auth.call_count == 2  # initial token + one forced refresh


@respx.mock
async def test_maps_404_to_mlspace_error():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/inference/v2/x").mock(
        return_value=httpx.Response(404, json={"detail": "nope"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(MLSpaceError) as ei:
            await _client(http).request(
                "GET", "/public/v2/inference/v2/{inference_name}",
                path_params={"inference_name": "x"},
            )
    assert ei.value.status == 404
