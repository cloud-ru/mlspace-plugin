from __future__ import annotations

import re

import pytest

from mlspace_mcp import registry
from mlspace_mcp.errors import MLSpaceError
from mlspace_mcp.server import build_server
from mlspace_mcp.tools.inference import DOMAIN as INF


def test_the_default_mode_is_read_write():
    """Pinned because it decides what a fresh install can do to a live platform.

    Every other test states the mode explicitly, so a change here would otherwise
    pass unnoticed in either direction. Flipping it is a deliberate act; this test
    is the place it has to be acknowledged.
    """
    from mlspace_mcp.config import Settings

    assert Settings(_env_file=None).readonly is False


# Written out independently of the domain modules: dropping confirm from any of
# these (or adding one silently) must fail here, not pass through a derived list.
DESTRUCTIVE = {
    ("mlspace_inference", "delete"), ("mlspace_jobs", "delete"),
    ("mlspace_build_image", "delete"), ("mlspace_notebooks", "delete"),
    ("mlspace_notebooks", "users_revoke"), ("mlspace_notebooks", "autoshutdown_delete"),
    ("mlspace_tensorboards", "delete"), ("mlspace_queues", "delete"),
    ("mlspace_docker_registry", "delete_repos"), ("mlspace_docker_registry", "delete_images"),
    ("mlspace_docker_registry", "delete_tags"),
    ("mlspace_docker_registry", "generate_password"),  # a GET that rotates a password
    ("mlspace_data_transfer", "delete_connectors"), ("mlspace_data_transfer", "delete_transfers"),
    ("mlspace_data_transfer", "cancel_history"), ("mlspace_data_transfer", "delete_history"),
}


def _domains():
    import importlib

    from mlspace_mcp.server import DOMAIN_MODULES

    return [importlib.import_module(f"mlspace_mcp.tools.{d}").DOMAIN for d in DOMAIN_MODULES]


def test_confirm_gates_exactly_the_destructive_actions():
    gated = {(dt.name, a) for dt in _domains() for a, op in dt.actions.items() if op.confirm}
    assert gated == DESTRUCTIVE
    assert all(dt.actions[a].write for dt in _domains() for (n, a) in DESTRUCTIVE if n == dt.name)


@pytest.mark.parametrize(("tool", "action"), sorted(DESTRUCTIVE))
async def test_destructive_action_without_confirm_sends_nothing(tool, action):
    """Refused because of confirm, before any HTTP (incl. lazy key/namespace lookups);
    with confirm=true the same call gets past the gate."""
    import httpx
    import respx
    from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx
    from mcp.server.fastmcp.exceptions import ToolError

    dt = next(d for d in _domains() if d.name == tool)
    op = dt.actions[action]
    settings = make_settings(readonly=False, api_key="",
                             allow_registry_password_rotation=True)  # a key lookup would be HTTP
    handler = registry._build_handler(dt, registry.available_actions(dt, False), settings)
    # namespace is left out on purpose: its lazy lookup is HTTP and must not
    # happen before the confirm refusal either
    args = {name: "x" for name in (*op.required, *op.path_params.values()) if name != "namespace"}
    if op.body_field:
        args[op.body_field] = {}
    with respx.mock(assert_all_called=False) as mock:
        mock.route().respond(200, json={"status": "running"})
        async with httpx.AsyncClient() as http:
            with pytest.raises(ToolError, match="confirm=true"):
                await handler(action=action, ctx=runtime_ctx(http, settings), **args)
            assert not mock.calls
    # positive control: with confirm=true (and a lint-valid body) the declared
    # endpoint is actually reached
    from test_bodyspec import _minimal_valid

    from mlspace_mcp import bodyspec

    if op.body_field:
        schema = bodyspec.schema_for(op.method, op.path) or {}
        args[op.body_field] = _minimal_valid(schema, schema.get("$defs", {})) or {}
    if "namespace" in op.path_params.values():
        args["namespace"] = "x"
    pattern = "[^/]+".join(re.escape(part) for part in re.split(r"\{[a-z_]+\}", op.path))
    with respx.mock(assert_all_called=False) as mock:
        mock.post(AUTH_URL).respond(200, json={"token": {"access_token": "t", "expires_in": 3600}})
        mock.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").respond(200, json={"x-api-key": "k"})
        target = mock.request(op.method, url__regex=rf"^{re.escape(BASE_URL)}{pattern}(\?.*)?$")
        target.respond(200, json={"status": "running"})
        mock.route().respond(200, json={"status": "running"})  # e.g. the jobs delete precheck
        async with httpx.AsyncClient() as http:
            await handler(action=action, confirm=True, ctx=runtime_ctx(http, settings), **args)
    assert target.called, f"{tool}.{action} did not reach {op.method} {op.path}"


def test_annotations_reflect_available_actions():
    # inference read-only still includes the side-effecting `predict`, so it is
    # NOT advertised as read-only; read-write adds the destructive `delete`.
    ann_ro = registry.build_annotations(INF, registry.available_actions(INF, True))
    ann_rw = registry.build_annotations(INF, registry.available_actions(INF, False))
    assert ann_ro.readOnlyHint is False  # predict has a side effect
    assert ann_ro.destructiveHint is False
    assert ann_rw.readOnlyHint is False
    assert ann_rw.destructiveHint is True  # delete has confirm=True

    # a genuinely read-only domain (resources: only GET catalog reads) is read-only
    from mlspace_mcp.tools.resources import DOMAIN as RES
    ann_res = registry.build_annotations(RES, registry.available_actions(RES, True))
    assert ann_res.readOnlyHint is True
    assert ann_res.destructiveHint is False


def test_predict_has_side_effect_so_tool_not_readonly():
    # dalle/async_inference contain only reads + a side-effecting predict:
    # they must NOT advertise readOnlyHint=true even though no write action exists.
    from mlspace_mcp.tools.dalle import DOMAIN as DALLE
    ann = registry.build_annotations(DALLE, registry.available_actions(DALLE, readonly=True))
    assert ann.readOnlyHint is False


def test_resolve_request_requires_region_for_get():
    with pytest.raises(MLSpaceError):
        registry.resolve_request(INF.actions["get"], {"inference_name": "svc"})


def test_resolve_request_get_builds_path_and_query():
    method, path, path_params, query, body = registry.resolve_request(
        INF.actions["get"], {"inference_name": "svc", "region": "CCE-INF"}
    )
    assert method == "GET"
    assert path_params == {"inference_name": "svc"}
    assert query == [("region", "CCE-INF")]
    assert body is None


def test_resolve_request_delete_needs_confirm():
    with pytest.raises(MLSpaceError):
        registry.resolve_request(
            INF.actions["delete"], {"inference_name": "svc", "region": "CCE-INF"}
        )
    # with confirm it resolves
    method, *_ = registry.resolve_request(
        INF.actions["delete"],
        {"inference_name": "svc", "region": "CCE-INF", "confirm": True},
    )
    assert method == "DELETE"


def test_resolve_request_predict_passes_free_form_body():
    _, _, path_params, _, body = registry.resolve_request(
        INF.actions["predict"],
        {"inference_name": "svc", "model_name": "m", "body": {"prompt": "hi"}},
    )
    assert path_params == {"inference_name": "svc", "model_name": "m"}
    assert body == {"prompt": "hi"}


def test_description_has_action_index():
    desc = registry.build_description(INF, registry.available_actions(INF, False))
    assert "Actions:" in desc
    assert "- delete:" in desc
    assert "needs confirm=true" in desc


async def test_built_tool_schema_is_flat_and_readonly_filters_enum(settings_ro, settings_rw):
    # read-only: action enum exposes only read actions
    mcp_ro = build_server(settings_ro)
    tools_ro = {t.name: t for t in await mcp_ro.list_tools()}
    inf = tools_ro["mlspace_inference"]
    props = inf.inputSchema["properties"]
    assert "action" in props  # flat, not nested under "params"
    assert set(props["action"]["enum"]) == {"list", "get", "predict"}
    assert inf.inputSchema["required"] == ["action"]

    # read-write: all actions exposed
    mcp_rw = build_server(settings_rw)
    tools_rw = {t.name: t for t in await mcp_rw.list_tools()}
    enum_rw = set(tools_rw["mlspace_inference"].inputSchema["properties"]["action"]["enum"])
    assert enum_rw == {"list", "get", "predict", "create", "update", "delete"}
