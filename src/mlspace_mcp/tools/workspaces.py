"""``mlspace_workspaces`` — inspect MLSpace workspaces (read-only).

Pure declarative data following the ``inference`` exemplar: the core owns all
request mechanics (headers, path templating, query encoding, bodies, readonly,
confirm, formatting). This module only declares ``ACTIONS`` / ``PARAMS``.

SAFETY: workspace bootstrap (POST /workspaces/v3/) and debootstrap (DELETE
/workspaces/v3/) are DELIBERATELY NOT exposed — creating/deleting an entire
workspace is a catastrophic blast radius for a shared, company-wide server, so
those ops are intentionally omitted (and listed in the drift test's exclusion
set so the bijection check stays green). Do workspace lifecycle in the console.
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
ACTIONS: dict[str, Op] = {
    "get_api_key": Op(
        "GET", "/public/v2/workspaces/v1/x_api_key",
        query_params={"workspace_id": "workspace_id"},
        required=("workspace_id",),
        reveal_result_keys=("x-api-key",),
        help="Get the workspace X-API-KEY (masked by default). Only for an authorized "
             "credential task, use reveal_secret=true with confirm=true to receive "
             "the value. Ordinary MCP operations resolve their credentials automatically.",
    ),
    "status": Op(
        "GET", "/public/v2/workspaces/v1/{workspace_id}/status",
        path_params={"workspace_id": "workspace_id"},
        required=("workspace_id",),
        help="Get a workspace's status.",
    ),
    "users": Op(
        "GET", "/public/v2/workspaces/v3/{workspace_id}/users",
        path_params={"workspace_id": "workspace_id"},
        required=("workspace_id",),
        kind="list",
        help="List the users who have access to a workspace.",
    ),
    "list": Op(
        "GET", "/public/v2/workspaces/v3/",
        query_params={"customer_id": "customer_id"},
        kind="list",
        help="List user workspaces (optionally filtered by customer_id).",
    ),
    "get": Op(
        "GET", "/public/v2/workspaces/v3/{workspace_id}",
        path_params={"workspace_id": "workspace_id"},
        required=("workspace_id",),
        help="Get workspace details.",
    ),
    "allocations": Op(
        "GET", "/public/v2/workspaces/v3/{workspace_id}/allocations",
        path_params={"workspace_id": "workspace_id"},
        required=("workspace_id",),
        kind="list",
        help="Discover allocations for the selected workspace before job placement. "
        "Use target; workspace_id is auto-filled. Preserve IDs, exact names, "
        "region_key and default_for_job; do not infer region from the name. "
        "When multiple allocations match and the user has not chosen or delegated "
        "the choice, present the alternatives and ask before reading queues/resources. "
        "A default_for_job flag is not user approval to select it.",
    ),
    "allocation_queues": Op(
        "GET",
        "/public/v2/workspaces/v3/{workspace_id}/allocations/{allocation_id}/queues",
        path_params={"workspace_id": "workspace_id", "allocation_id": "allocation_id"},
        required=("workspace_id", "allocation_id"),
        kind="list",
        help="Discover queues for the selected workspace and allocation before job placement. "
        "Check allocation_id, active and default_for_job; an empty result "
        "does not establish PAYG or absence of default scheduling.",
    ),
    # NOTE: bootstrap (POST /workspaces/v3/) and debootstrap (DELETE /workspaces/v3/)
    # are intentionally NOT exposed — see the SAFETY note in the module docstring.
}

PARAMS: list[Param] = [
    Param(
        "workspace_id",
        str,
        "Workspace UUID (path/query param for get_api_key/status/get/"
        "allocations/allocation_queues). Distinct from the x-workspace-id header. "
        "Optional API parameter: auto-filled from the selected target when omitted. "
        "Use target to select the connected workspace.",
    ),
    Param("allocation_id", str, "Allocation UUID (allocation_queues only)."),
    Param("customer_id", str, "Filter workspaces by customer (list only)."),
]

DOMAIN = DomainTool(
    domain="workspaces",
    name="mlspace_workspaces",
    title="MLSpace Workspaces",
    summary="Inspect MLSpace workspaces (status, details, allocations, queues, "
    "api-key). Read-only: workspace create/delete is intentionally not exposed.",
    actions=ACTIONS,
    params=PARAMS,
    redact_result_keys=("x-api-key",),
)
