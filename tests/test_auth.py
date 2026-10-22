from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL

from mlspace_mcp.auth import TokenManager
from mlspace_mcp.errors import MLSpaceError

TOKEN_BODY = {
    "token": {"access_token": "AT-1", "token_type": "Bearer", "expires_in": 3600},
    # deprecated siblings that must be ignored:
    "status": "ok",
    "error_code": 0,
    "error_message": "",
}


def _mgr(http: httpx.AsyncClient) -> TokenManager:
    return TokenManager(http, BASE_URL, "cid", "csecret")


@respx.mock
async def test_parses_nested_token_shape():
    route = respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    async with httpx.AsyncClient() as http:
        token, version = await _mgr(http).get_token()
    assert token == "AT-1"
    assert version == 1
    assert route.called


@respx.mock
async def test_invalid_credentials_raise_no_loop():
    route = respx.post(AUTH_URL).mock(return_value=httpx.Response(401, json={"detail": "bad"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(MLSpaceError) as ei:
            await _mgr(http).get_token()
    assert ei.value.status == 401
    assert route.call_count == 1  # no retry storm


@respx.mock
async def test_concurrent_get_token_is_single_flight():
    route = respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    async with httpx.AsyncClient() as http:
        mgr = _mgr(http)
        results = await asyncio.gather(*[mgr.get_token() for _ in range(8)])
    assert route.call_count == 1
    assert {t for t, _ in results} == {"AT-1"}


@respx.mock
async def test_concurrent_401_refresh_collapses_to_one_call():
    route = respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    async with httpx.AsyncClient() as http:
        mgr = _mgr(http)
        _, v = await mgr.get_token()  # 1 call, version 1
        assert route.call_count == 1
        await asyncio.gather(*[mgr.refresh_after_401(v) for _ in range(8)])
    assert route.call_count == 2  # exactly one extra refresh for all 8 stale callers
