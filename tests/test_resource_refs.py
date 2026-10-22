"""References carry addresses, never confirmation or arbitrary API parameters."""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL
from mcp.server.fastmcp.exceptions import ToolError
from test_workspace_context import context, handler, multi_settings, runtime

from mlspace_mcp.tools.jobs import DOMAIN as JOBS
from mlspace_mcp.tools.notebooks import DOMAIN as NB


def ref(**overrides):
    from mlspace_mcp.workspace_context import environment_id
    result = {"version": 1, "environment": environment_id(BASE_URL), "target": "B",
              "kind": "job", "address": {"job_name": "old", "region": "SR006"}}
    result.update(overrides)
    return result


def auth():
    respx.post(AUTH_URL).respond(200, json={"token": {"access_token": "t", "expires_in": 3600}})
    respx.get(f"{BASE_URL}/public/v2/workspaces/v1/x_api_key").respond(
        200, json={"x-api-key": "key-B"})


@respx.mock
@pytest.mark.parametrize("kwargs", [
    {"target": "A"}, {"job_name": "other"}, {"region": "SR003"},
    {"resource_ref": {"environment": "another"}},
    {"resource_ref": {"target": "unselected"}},
    {"resource_ref": {"version": 2}},
    {"resource_ref": {"address": {"job_name": "../escape"}}},
    {"resource_ref": {"address": {"job_name": "old", "secret": "not-allowed"}}},
])
async def test_conflicts_and_invalid_refs_fail_before_lazy_http(kwargs):
    settings = multi_settings(readonly=False)
    kwargs = dict(kwargs)
    address = ref(**kwargs.pop("resource_ref", {}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError, match="resource_ref|target|conflict"):
            await handler(JOBS, settings)(action="delete", resource_ref=address, confirm=True,
                                         ctx=context(runtime(settings, http)), **kwargs)
    assert not respx.calls


@respx.mock
async def test_ref_delete_guard_and_delete_both_address_b():
    auth()
    respx.get(f"{BASE_URL}/public/v2/jobs/old").respond(200, json={"job_name": "old", "status": "Running"})
    respx.delete(f"{BASE_URL}/public/v2/jobs/old").respond(204)
    settings = multi_settings(readonly=False)
    async with httpx.AsyncClient() as http:
        result = await handler(JOBS, settings)(action="delete", resource_ref=ref(), confirm=True,
                                               ctx=context(runtime(settings, http)))
    assert json.loads(result)["request_context"]["target"] == "B"
    for call in respx.calls[-2:]:
        assert call.request.headers["x-workspace-id"] == "B"
        assert call.request.headers["x-api-key"] == "key-B"
    assert respx.calls[-1].request.url.params["region"] == "SR006"


@respx.mock
async def test_reference_does_not_replace_confirm():
    settings = multi_settings(readonly=False)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError, match="confirm"):
            await handler(JOBS, settings)(action="delete", resource_ref=ref(),
                                         ctx=context(runtime(settings, http)))
    assert not respx.calls


@respx.mock
@pytest.mark.parametrize("payload,new_name", [({"job_name": "new"}, "new"), ({}, None),
                                             ({"job_name": {"unexpected": "shape"}}, None)])
async def test_reference_only_restart_builds_body_and_reports_only_new_identity(payload, new_name):
    auth()
    restart = respx.post(f"{BASE_URL}/public/v2/jobs/restart").respond(200, json=payload)
    settings = multi_settings(readonly=False)
    async with httpx.AsyncClient() as http:
        result = json.loads(await handler(JOBS, settings)(action="restart", resource_ref=ref(),
                                                         ctx=context(runtime(settings, http))))
    assert json.loads(restart.calls[0].request.content) == {"job_name": "old"}
    if new_name:
        assert result["resource_refs"][0]["address"]["job_name"] == new_name
        assert result["resource_refs"][0]["address"]["region"] == "SR006"
    else:
        assert not result["resource_refs"]
        assert "unknown" in result["address_note"]


@respx.mock
@pytest.mark.parametrize("domain,action", [(JOBS, "run"), (JOBS, "list"), (NB, "create")])
async def test_existing_ref_not_accepted_for_create_or_list(domain, action):
    settings = multi_settings(readonly=False)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError, match="resource_ref"):
            await handler(domain, settings)(action=action, resource_ref=ref(), body={},
                                           ctx=context(runtime(settings, http)))
    assert not respx.calls


@respx.mock
async def test_notebook_name_actions_require_name_and_namespace_address():
    settings = multi_settings()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError, match="resource_ref"):
            await handler(NB, settings)(action="users_list", resource_ref=ref(
                kind="notebook", address={"notebook_uuid": "nb-id"}),
                ctx=context(runtime(settings, http)))
    assert not respx.calls


@respx.mock
async def test_multi_logs_preserve_tail_and_no_refs_for_arbitrary_node_names():
    auth()
    respx.get(f"{BASE_URL}/public/v2/jobs/old/logs").respond(200, text="one\ntwo\nthree")
    respx.get(f"{BASE_URL}/public/v2/jobs/old/nodes").respond(200, json=[{"id": "node", "name": "node"}])
    settings = multi_settings(log_tail_lines=2)
    async with httpx.AsyncClient() as http:
        ctx = context(runtime(settings, http))
        call = handler(JOBS, settings)
        logs = json.loads(await call(action="logs", resource_ref=ref(), ctx=ctx))
        nodes = json.loads(await call(action="list_nodes", resource_ref=ref(), ctx=ctx))
    assert "one" not in logs["data"] and "two\nthree" in logs["data"]
    assert not nodes["resource_refs"]


@respx.mock
async def test_cross_workspace_queue_is_not_relabelled():
    from mlspace_mcp.tools.queues import DOMAIN as QUEUES
    settings = multi_settings()
    auth()
    respx.get(f"{BASE_URL}/public/v2/queues/").respond(
        200, json={"items": [{"id": "q", "workspace_id": "A", "name": "queue"}]})
    async with httpx.AsyncClient() as http:
        out = json.loads(await handler(QUEUES, settings)(action="list", target="B", allocation_id="a",
                                                        ctx=context(runtime(settings, http))))
    assert out["request_context"]["target"] == "B"
    assert out["data"]["items"][0]["workspace_id"] == "A"
    assert not out["resource_refs"]
    assert "owner" not in out["request_context"]


@respx.mock
async def test_dry_run_does_not_mint_resource_refs():
    settings = multi_settings(readonly=False, dry_run=True)
    async with httpx.AsyncClient() as http:
        out = json.loads(await handler(JOBS, settings)(action="restart", resource_ref=ref(),
                                                       ctx=context(runtime(settings, http))))
    assert out["data"]["dry_run"] is True
    assert not out["resource_refs"]
    assert not respx.calls


@respx.mock
async def test_list_refs_match_visible_objects_and_explicit_owner():
    auth()
    respx.get(f"{BASE_URL}/public/v2/jobs").respond(200, json={"items": [
        {"job_name": "first", "workspace_id": "A", "region": "SR006"},
        {"job_name": "hidden", "workspace_id": "B", "region": "SR006"},
    ]})
    settings = multi_settings(list_max_limit=1)
    async with httpx.AsyncClient() as http:
        out = json.loads(await handler(JOBS, settings)(action="list", target="B", region="SR006",
                                                       ctx=context(runtime(settings, http))))
    assert out["request_context"]["workspace_name"] == "beta"
    assert len(out["resource_refs"]) == 1
    assert out["resource_refs"][0]["target"] == "A"
    assert out["resource_refs"][0]["address"]["job_name"] == "first"


@respx.mock
async def test_restart_conflicting_body_rejected_before_http():
    settings = multi_settings(readonly=False)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError, match="conflict"):
            await handler(JOBS, settings)(action="restart", resource_ref=ref(),
                body={"job_name": "another"}, ctx=context(runtime(settings, http)))
    assert not respx.calls


@respx.mock
async def test_notebook_resume_body_region_override_is_preserved():
    auth()
    resume = respx.post(f"{BASE_URL}/public/v2/notebooks/v1/ns-B/notebook/nb-id/resume").respond(
        200, json={"uid": "nb-id", "region": "SR003"})
    settings = multi_settings(readonly=False)
    nb_ref = ref(kind="notebook", address={"notebook_uuid": "nb-id", "namespace": "ns-B", "region": "SR006"})
    async with httpx.AsyncClient() as http:
        await handler(NB, settings)(action="resume", resource_ref=nb_ref,
            body={"region": "SR003", "instance_type": "free.0gpu"}, ctx=context(runtime(settings, http)))
    assert json.loads(resume.calls[0].request.content)["region"] == "SR003"


@respx.mock
async def test_reference_delete_guard_is_fail_closed_on_unverifiable_job():
    auth()
    respx.get(f"{BASE_URL}/public/v2/jobs/old").respond(503, json={"detail": "unavailable"})
    settings = multi_settings(readonly=False)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ToolError):
            await handler(JOBS, settings)(action="delete", resource_ref=ref(), confirm=True,
                                         ctx=context(runtime(settings, http)))
    assert all(call.request.method != "DELETE" for call in respx.calls)


@respx.mock
@pytest.mark.parametrize("response_name,new_address", [("old", False), ("new", True)])
async def test_restart_body_identity_is_not_minted_as_new_address(response_name, new_address):
    auth()
    restart = respx.post(f"{BASE_URL}/public/v2/jobs/restart").respond(
        200, json={"job_name": response_name})
    settings = multi_settings(readonly=False)
    async with httpx.AsyncClient() as http:
        out = json.loads(await handler(JOBS, settings)(action="restart", target="B",
            body={"job_name": "old"}, ctx=context(runtime(settings, http))))
    assert json.loads(restart.calls[0].request.content) == {"job_name": "old"}
    assert out["data"]["job_name"] == response_name
    if new_address:
        assert out["resource_refs"][0]["address"]["job_name"] == "new"
    else:
        assert out["resource_refs"] == []
        assert "unknown" in out["address_note"]
