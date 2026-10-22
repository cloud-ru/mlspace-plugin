"""End-to-end dispatch through the dynamically-built tool handler.

Unit tests cover each layer separately; these exercise the assembled handler
(action -> resolve_request -> client -> formatting), which only runs inside a
FastMCP request. We synthesize the request Context so the full execution path
of the dynamic handler is actually run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.tools.inference import DOMAIN

TOKEN_BODY = {"token": {"access_token": "AT-1", "expires_in": 3600}}


def _ctx(http: httpx.AsyncClient) -> SimpleNamespace:
    return runtime_ctx(http)

def _handler(settings):
    return _build_handler(DOMAIN, available_actions(DOMAIN, settings.readonly), settings)


@respx.mock
async def test_full_dispatch_list_formats_result():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/inference/v2/").mock(
        return_value=httpx.Response(200, json=[{"name": "svc-a"}, {"name": "svc-b"}])
    )
    handler = _handler(make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        out = await handler(action="list", ctx=_ctx(http))
    assert "svc-a" in out and "svc-b" in out


@respx.mock
async def test_full_dispatch_get_builds_query_and_headers():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.get(f"{BASE_URL}/public/v2/inference/v2/svc-a").mock(
        return_value=httpx.Response(200, json={"name": "svc-a", "status": "Running"})
    )
    handler = _handler(make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        out = await handler(action="get", inference_name="svc-a", region="CCE-INF", ctx=_ctx(http))
    assert "Running" in out
    req = route.calls.last.request
    assert req.headers["x-workspace-id"] == "ws-1"
    assert req.headers["authorization"] == "Bearer AT-1"
    assert "region=CCE-INF" in str(req.url.query)


@respx.mock
async def test_full_dispatch_predict_posts_free_form_body():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.post(f"{BASE_URL}/public/v2/inference/v2/predict/svc-a/m1/").mock(
        return_value=httpx.Response(200, json={"prediction": [0.9]})
    )
    handler = _handler(make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        out = await handler(
            action="predict", inference_name="svc-a", model_name="m1", body={"x": 1}, ctx=_ctx(http)
        )
    assert json.loads(route.calls.last.request.content) == {"x": 1}
    assert "prediction" in out


async def test_delete_without_confirm_is_blocked_before_any_call():
    # confirm gating raises in resolve_request before the client is touched; a refusal
    # is now a protocol error (isError=true), i.e. the handler raises ToolError.
    handler = _handler(make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:
            await handler(action="delete", inference_name="svc-a", region="CCE-INF", ctx=_ctx(http))
    assert "confirm=true" in str(exc.value)


async def test_readonly_hides_write_action_at_runtime():
    handler = _handler(make_settings(readonly=True))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:
            await handler(action="create", body={"x": 1}, ctx=_ctx(http))
    assert "read-only" in str(exc.value).lower()


async def test_dispatch_lints_body_before_any_api_call():
    # a structurally-invalid create body must be blocked locally (no HTTP) by the
    # spec-driven lint — proven without respx, so any network call would error out.
    handler = _handler(make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:
            await handler(action="create", body={}, ctx=_ctx(http))
    assert "Request body is invalid" in str(exc.value)


@respx.mock
async def test_http_error_surfaced_as_tool_error_not_success():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/inference/v2/missing").mock(
        return_value=httpx.Response(404, json={"detail": "nope"})
    )
    handler = _handler(make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:
            await handler(action="get", inference_name="missing", region="CCE-INF", ctx=_ctx(http))
    assert "404" in str(exc.value)
