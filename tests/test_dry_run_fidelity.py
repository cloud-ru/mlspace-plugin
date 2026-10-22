"""FIX-4 regression: dry-run preview is faithful (all ids) and safe (no secrets).

Two bugs from A-HIGH-4: ``dict(query)`` collapsed a multi-delete's repeated ids to
the last one, and the raw ``body`` (possibly a connector password) was echoed into
the preview. Both are proven through the real handler with dry-run on and an
exploding client, so no HTTP is issued.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
from conftest import make_settings, runtime_ctx

from mlspace_mcp import formatting
from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.tools.data_transfer import DOMAIN as DT


def _ctx() -> SimpleNamespace:
    """Production-shaped runtime with no mocked routes: any HTTP fails the test."""
    return runtime_ctx(httpx.AsyncClient(transport=_NO_NETWORK))


def _refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"dry-run must not issue an HTTP request: {request.method} {request.url}")


_NO_NETWORK = httpx.MockTransport(_refuse)


def _handler():
    settings = make_settings(readonly=False, dry_run=True)
    return _build_handler(DT, available_actions(DT, settings.readonly), settings)


async def test_dry_run_multi_delete_shows_all_ids():
    handler = _handler()
    ids = ["id-a", "id-b", "id-c"]
    out = await handler(action="delete_connectors", ids=ids, confirm=True, ctx=_ctx())
    data = json.loads(out)
    query = data["would_send"]["query"]
    # repeated ids preserved as ordered pairs — NOT collapsed to the last one
    got = [v for k, v in query if k == "ids"]
    assert got == ids


async def test_dry_run_create_masks_secret_body_field():
    handler = _handler()
    body = {
        "name": "c",
        "source_type": "s3",
        "parameters": {"password": "HUNTER2_SECRET", "bucket": "b"},
    }
    out = await handler(action="create_connector", body=body, confirm=True, ctx=_ctx())
    assert "HUNTER2_SECRET" not in out  # the credential is not echoed in cleartext
    data = json.loads(out)
    params = data["would_send"]["body"]["parameters"]
    assert params["bucket"] == "b"  # non-secret fields still visible
    assert "redacted" in params["password"].lower()  # secret masked, shape preserved


# --- the pure redactor used by the preview (nested + list, fail-safe) ---------


def test_redact_secrets_masks_nested_and_leaves_the_rest():
    obj = {
        "user": "alice",
        "token": "abc123",
        "nested": {"api_key": "K", "note": "keep"},
        "list": [{"secret": "s"}, {"ok": 1}],
    }
    out = formatting.redact_secrets(obj)
    assert out["user"] == "alice"
    assert "redacted" in out["token"].lower()
    assert "redacted" in out["nested"]["api_key"].lower()
    assert out["nested"]["note"] == "keep"
    assert "redacted" in out["list"][0]["secret"].lower()
    assert out["list"][1] == {"ok": 1}


def test_redact_secrets_fail_safe_on_scalars():
    assert formatting.redact_secrets("x") == "x"
    assert formatting.redact_secrets(None) is None
    assert formatting.redact_secrets(5) == 5
