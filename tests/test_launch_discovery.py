"""Exercise the read path used to prepare a launch, not an LLM decision test."""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.tools.resources import DOMAIN as RESOURCES
from mlspace_mcp.tools.workspaces import DOMAIN as WORKSPACES


@pytest.mark.parametrize("availability_status", [200, 403])
async def test_workspace_placement_reads_preserve_inventory_scope_and_availability_failure(
    availability_status,
):
    settings = make_settings(workspace_id="workspace-b")
    allocations = [
        {"id": "alloc-payg", "name": "customer-gpu", "region_key": "SR008",
         "default_for_job": False},
        {"id": "alloc-priority", "name": "priority-sr008", "region_key": "SR008",
         "default_for_job": True},
        # Similar labels do not authorize the wrapper to merge IDs or infer regions.
        {"id": "alloc-other", "name": "priority-sr008", "region_key": "SR007",
         "default_for_job": False},
    ]
    queues = [{"id": "queue-1", "name": "training", "allocation_id": "alloc-priority",
               "active": True, "default_for_job": True}]
    availability = {"instance_types": [{"key": "h100.1gpu", "name": "H100", "count": 0}]}
    with respx.mock(assert_all_called=False) as mock:
        mock.post(AUTH_URL).respond(
            200, json={"token": {"access_token": "token", "expires_in": 3600}})
        personal = mock.get(f"{BASE_URL}/public/v2/allocations/").respond(
            200, json=[allocations[1]])
        inventory = mock.get(
            f"{BASE_URL}/public/v2/workspaces/v3/workspace-b/allocations"
        ).respond(200, json=allocations)
        queue_list = mock.get(
            f"{BASE_URL}/public/v2/workspaces/v3/workspace-b/allocations/alloc-priority/queues"
        ).respond(200, json=queues)
        available = mock.get(
            f"{BASE_URL}/public/v2/instance_types/SR008/available",
            params={"allocation_name": "priority-sr008", "queue_name": "training"},
        ).respond(availability_status, json=availability if availability_status == 200
                  else {"detail": "Forbidden"})
        async with httpx.AsyncClient() as http:
            ctx = runtime_ctx(http, settings)

            async def read(domain, action, **values):
                call = _build_handler(domain, available_actions(domain, True), settings)
                return json.loads(await call(action=action, target="workspace-b",
                                             response_format="json", ctx=ctx, **values))

            assert await read(WORKSPACES, "allocations") == allocations
            assert await read(WORKSPACES, "allocation_queues",
                              allocation_id="alloc-priority") == queues
            args = dict(region="SR008", allocation_name="priority-sr008", queue_name="training")
            if availability_status == 200:
                assert await read(RESOURCES, "instance_types", **args) == availability
            else:
                with pytest.raises(ToolError, match="Launch availability is unverified") as error:
                    await read(RESOURCES, "instance_types", **args)
                assert "403" in str(error.value)
        assert not personal.called
        for route in (inventory, queue_list, available):
            assert route.call_count == 1
            assert route.calls[0].request.headers["x-workspace-id"] == "workspace-b"
        assert all(call.request.method == "GET" or str(call.request.url) == AUTH_URL
                   for call in mock.calls)


@pytest.mark.parametrize("domain_name,action,argument,path", [
    ("allocations", "list_instance_types", "allocation_id",
     "/public/v2/allocations/placement-id/instance-types"),
    ("queues", "instance_types", "queue_id",
     "/public/v2/queues/placement-id/instance-types"),
])
async def test_failed_scoped_detail_does_not_suggest_catalog_fallback(
    domain_name, action, argument, path,
):
    from importlib import import_module

    domain = import_module(f"mlspace_mcp.tools.{domain_name}").DOMAIN
    settings = make_settings()
    call = _build_handler(domain, available_actions(domain, True), settings)
    with respx.mock() as mock:
        mock.post(AUTH_URL).respond(
            200, json={"token": {"access_token": "token", "expires_in": 3600}})
        route = mock.get(BASE_URL + path).respond(403, json={"detail": "Forbidden"})
        async with httpx.AsyncClient() as http:
            with pytest.raises(ToolError, match="Launch availability is unverified") as error:
                await call(action=action, **{argument: "placement-id"},
                           ctx=runtime_ctx(http, settings))
        assert "403" in str(error.value)
        assert route.call_count == 1
