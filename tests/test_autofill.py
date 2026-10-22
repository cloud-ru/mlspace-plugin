"""Server-side auto-fill of workspace_id (from config) and namespace (resolved
from workspace_id) for actions that need them, so the model needn't discover them."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx

from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.tools.notebooks import DOMAIN as NB
from mlspace_mcp.tools.workspaces import DOMAIN as WS

TOKEN_BODY = {"token": {"access_token": "AT-1", "expires_in": 3600}}
GOOD_NB_BODY = {
    "name": "nb",
    "image": {"name": "img", "tag": "1", "type": "datahub"},
    "instance_type": "free.0gpu",
}


def _ctx(http: httpx.AsyncClient, namespace: str = "") -> SimpleNamespace:
    return runtime_ctx(http, namespace=namespace)


def _handler(domain, settings):
    return _build_handler(domain, available_actions(domain, settings.readonly), settings)


@respx.mock
async def test_namespace_autofilled_from_workspace_lookup():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    ws = respx.get(f"{BASE_URL}/public/v2/workspaces/v3/ws-1").mock(
        return_value=httpx.Response(200, json={"namespace": "ns-resolved", "name": "w"})
    )
    create = respx.post(f"{BASE_URL}/public/v2/notebooks/v2/ns-resolved/notebook").mock(
        return_value=httpx.Response(200, json={"uid": "nb-1"})
    )
    handler = _handler(NB, make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        out = await handler(action="create", body=GOOD_NB_BODY, ctx=_ctx(http))
    assert "nb-1" in out
    assert ws.called and create.called  # namespace looked up, then used in the path


@respx.mock
async def test_explicit_namespace_is_respected_and_skips_lookup():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    ws = respx.get(f"{BASE_URL}/public/v2/workspaces/v3/ws-1").mock(
        return_value=httpx.Response(200, json={"namespace": "ns-resolved"})
    )
    create = respx.post(f"{BASE_URL}/public/v2/notebooks/v2/explicit-ns/notebook").mock(
        return_value=httpx.Response(200, json={"uid": "nb-2"})
    )
    handler = _handler(NB, make_settings(readonly=False))
    async with httpx.AsyncClient() as http:
        out = await handler(action="create", namespace="explicit-ns", body=GOOD_NB_BODY, ctx=_ctx(http))
    assert "nb-2" in out
    assert create.called and not ws.called  # override used; no workspace lookup needed


@respx.mock
async def test_workspace_id_autofilled_from_config():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    get = respx.get(f"{BASE_URL}/public/v2/workspaces/v3/ws-1").mock(
        return_value=httpx.Response(200, json={"id": "ws-1", "name": "w"})
    )
    handler = _handler(WS, make_settings(readonly=True))
    async with httpx.AsyncClient() as http:
        out = await handler(action="get", ctx=_ctx(http))  # no workspace_id passed
    assert "ws-1" in out
    assert get.called  # hit the configured workspace path
