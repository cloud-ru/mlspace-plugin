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


def _ctx() -> SimpleNamespace:
    """Production-shaped runtime with no mocked routes: any HTTP fails the test."""
    return runtime_ctx(httpx.AsyncClient(transport=_NO_NETWORK))


def _refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"dry-run must not issue an HTTP request: {request.method} {request.url}")


_NO_NETWORK = httpx.MockTransport(_refuse)


def _handler(settings):
    return _build_handler(DOMAIN, available_actions(DOMAIN, settings.readonly), settings)


async def test_dry_run_write_returns_preview_without_calling_api():
    settings = make_settings(readonly=False, dry_run=True)
    handler = _handler(settings)
    out = await handler(
        action="create",
        body={
            "image": "cr.ai.cloud.ru/x/img:1",
            "region": "CCE-INF",
            "instance_type": "v100.1gpu",
        },
        ctx=_ctx(),
    )
    data = json.loads(out)
    assert data["dry_run"] is True
    assert data["would_send"]["method"] == "POST"
    assert data["would_send"]["path"].endswith("/inference/v2/")
    assert data["would_send"]["body"]["instance_type"] == "v100.1gpu"


async def test_dry_run_still_enforces_body_lint():
    # an invalid body must be rejected BEFORE the preview (validation, not blind echo)
    settings = make_settings(readonly=False, dry_run=True)
    handler = _handler(settings)
    with pytest.raises(ToolError) as exc:  # lint caught it; no preview
        await handler(action="create", body={}, ctx=_ctx())  # missing required keys
    assert "invalid" in str(exc.value).lower()


async def test_dry_run_off_sends_the_write():
    """Positive control for the previews above: without dry-run the POST is sent."""
    settings = make_settings(readonly=False, dry_run=False)
    with respx.mock:
        respx.post(AUTH_URL).respond(200, json={"token": {"access_token": "t", "expires_in": 3600}})
        create = respx.post(f"{BASE_URL}/public/v2/inference/v2/").respond(200, json={"name": "svc"})
        async with httpx.AsyncClient() as http:
            await _handler(settings)(
                action="create",
                body={"image": "cr.ai.cloud.ru/x/img:1", "region": "CCE-INF",
                      "instance_type": "v100.1gpu"},
                ctx=runtime_ctx(http, settings),
            )
    assert create.call_count == 1
    assert json.loads(create.calls.last.request.content)["instance_type"] == "v100.1gpu"
