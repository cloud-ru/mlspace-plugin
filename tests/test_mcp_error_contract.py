"""FIX-2 regression: refusals are protocol errors (isError=true), successes are not.

FastMCP maps ANY exception raised out of a tool to CallToolResult.isError=true
(mcp.server.lowlevel.server: ``except Exception: return _make_error_result(...)``),
and a normal return to isError=false. We drive the real registered tool through
FastMCP's ToolManager (the layer that makes that decision) with a synthesized
Context, so a refusal must raise ToolError and a success must return content.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.registry import register_domain
from mlspace_mcp.tools.jobs import DOMAIN

TOKEN_BODY = {"token": {"access_token": "AT-1", "expires_in": 3600}}


def _ctx(http: httpx.AsyncClient) -> SimpleNamespace:
    return runtime_ctx(http)

def _mcp():
    mcp = FastMCP("test")
    register_domain(mcp, make_settings(readonly=False), DOMAIN)
    return mcp


async def _call(mcp: FastMCP, ctx, **arguments):
    """Run the registered tool exactly as FastMCP does; convert_result mirrors the
    server. Raising == isError=true downstream; returning == isError=false."""
    return await mcp._tool_manager.call_tool(
        "mlspace_jobs", arguments, context=ctx, convert_result=True
    )


async def test_refusal_is_protocol_error():
    # jobs list without region is a deliberate refusal → must be isError=true
    mcp = _mcp()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:
            await _call(mcp, _ctx(http), action="list")
    assert "region" in str(exc.value)


@respx.mock
async def test_success_is_not_an_error():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/jobs/j1").mock(
        return_value=httpx.Response(200, json={"job_name": "j1", "status": "running"})
    )
    mcp = _mcp()
    async with httpx.AsyncClient() as http:
        result = await _call(mcp, _ctx(http), action="get", job_name="j1")
    # a normal (non-raising) return → FastMCP sets isError=false; content carries data
    assert result is not None
    text = str(result)
    assert "running" in text or "j1" in text


@respx.mock
async def test_http_4xx_is_protocol_error():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/jobs/missing").mock(
        return_value=httpx.Response(404, json={"detail": "nope"})
    )
    mcp = _mcp()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:
            await _call(mcp, _ctx(http), action="get", job_name="missing")
    assert "404" in str(exc.value)


@respx.mock
async def test_multi_workspace_references_over_mcp_protocol():
    """Exercise JSON-RPC CallToolResult, schema parsing and real server lifespan."""
    from mcp.shared.memory import create_connected_server_and_client_session
    from test_resource_refs import ref
    from test_workspace_context import multi_settings

    from mlspace_mcp.server import build_server

    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").respond(
        200, json={"x-api-key": "key-B"})
    get = respx.get(f"{BASE_URL}/public/v2/jobs/old").respond(
        200, json={"job_name": "old", "status": "Running"})
    server = build_server(multi_settings())
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        jobs = next(t for t in (await session.list_tools()).tools if t.name == "mlspace_jobs")
        assert {"target", "resource_ref"} <= set(jobs.inputSchema["properties"])
        refused = await session.call_tool("mlspace_jobs", {"action": "get", "job_name": "old"})
        assert refused.isError is True
        assert not respx.calls
        conflict = await session.call_tool("mlspace_jobs", {
            "action": "get", "target": "A", "resource_ref": ref()})
        assert conflict.isError is True
        assert not respx.calls
        success = await session.call_tool("mlspace_jobs", {"action": "get", "resource_ref": ref()})
        assert success.isError is False
        assert "Running" in str(success.content)
        assert get.calls[0].request.headers["x-workspace-id"] == "B"
