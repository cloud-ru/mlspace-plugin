"""Full-handler tests for the jobs-delete existence guard (respx + _build_handler).

The guard issues one status GET just before the destructive DELETE so the wrapper
never reports a false 'deleted' for a job that does not exist. Style mirrors
test_integration: a synthesized request Context runs the real dynamic handler.
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
from mlspace_mcp.tools.jobs import DOMAIN

TOKEN_BODY = {"token": {"access_token": "AT-1", "expires_in": 3600}}


def _ctx(http: httpx.AsyncClient) -> SimpleNamespace:
    return runtime_ctx(http)

def _handler():
    settings = make_settings(readonly=False)
    return _build_handler(DOMAIN, available_actions(DOMAIN, settings.readonly), settings)


@respx.mock
async def test_delete_ghost_job_is_refused_before_delete():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    # verbatim live 05#1
    respx.get(f"{BASE_URL}/public/v2/jobs/ghost").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_name": "ghost",
                "status": "job not found",
                "error_code": 0,
                "error_message": "job not found",
            },
        )
    )
    delete_route = respx.delete(f"{BASE_URL}/public/v2/jobs/ghost").mock(
        return_value=httpx.Response(
            200, json={"job_name": "ghost", "status": "deleted", "deleted_at": "1"}
        )
    )
    handler = _handler()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:  # refusal is now a protocol error
            await handler(
                action="delete", job_name="ghost", region="SR006", confirm=True, ctx=_ctx(http)
            )
    assert "does not exist" in str(exc.value)
    assert "false" in str(exc.value).lower()  # names the false 'deleted' it prevented
    assert delete_route.call_count == 0  # the false DELETE never went out


@respx.mock
async def test_delete_real_job_proceeds():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/jobs/real").mock(
        return_value=httpx.Response(200, json={"status": "failed", "job_name": "real"})
    )
    delete_route = respx.delete(f"{BASE_URL}/public/v2/jobs/real").mock(
        return_value=httpx.Response(
            200, json={"job_name": "real", "status": "deleted", "deleted_at": "1785823100"}
        )
    )
    handler = _handler()
    async with httpx.AsyncClient() as http:
        out = await handler(
            action="delete", job_name="real", region="SR006", confirm=True, ctx=_ctx(http)
        )
    assert delete_route.call_count == 1
    assert "deleted" in out
    # honest note: MLSpace delete is logical, the job can still be listed afterward
    assert "LOGICAL" in out and "purge" in out.lower()


@respx.mock
async def test_guard_precheck_failure_fails_closed_and_does_not_delete():
    # FIX-3: an UNCONFIRMED precheck (500/timeout/odd shape) must NOT let a blind
    # DELETE report a false 'deleted' — the guard now refuses fail-closed.
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/jobs/x").mock(return_value=httpx.Response(500))
    delete_route = respx.delete(f"{BASE_URL}/public/v2/jobs/x").mock(
        return_value=httpx.Response(200, json={"job_name": "x", "status": "deleted"})
    )
    handler = _handler()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError) as exc:
            await handler(
                action="delete", job_name="x", region="SR006", confirm=True, ctx=_ctx(http)
            )
    assert delete_route.call_count == 0  # fail-closed: the DELETE never went out
    assert "could not confirm" in str(exc.value).lower()


@respx.mock
async def test_job_get_not_found_is_explained_not_read_as_failure():
    # 'job not found' under 200 is ambiguous (fresh job vs wrong name); the reply must say so.
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/jobs/ghost").mock(
        return_value=httpx.Response(
            200, json={"job_name": "ghost", "status": "job not found", "error_code": 0}
        )
    )
    handler = _handler()
    async with httpx.AsyncClient() as http:
        out = await handler(action="get", job_name="ghost", ctx=_ctx(http))
    data = json.loads(out)
    assert data["status"] == "job not found"  # original field preserved
    assert "retry" in data["_wrapper_note"] and "do not create a duplicate" in data["_wrapper_note"]


@pytest.mark.parametrize("precheck", [[], {}, {"status": None}, {"status": {"phase": "x"}},
                                      {"status": ""}, {"status": "   "}])
@respx.mock
async def test_guard_precheck_unexpected_shape_fails_closed(precheck):
    # 200 without a string status is unconfirmed; str(None) must not read as a status
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    respx.get(f"{BASE_URL}/public/v2/jobs/y").mock(return_value=httpx.Response(200, json=precheck))
    delete_route = respx.delete(f"{BASE_URL}/public/v2/jobs/y").mock(
        return_value=httpx.Response(200, json={"job_name": "y", "status": "deleted"})
    )
    handler = _handler()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError):
            await handler(
                action="delete", job_name="y", region="SR006", confirm=True, ctx=_ctx(http)
            )
    assert delete_route.call_count == 0
