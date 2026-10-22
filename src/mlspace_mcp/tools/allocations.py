"""``mlspace_allocations`` — inspect MLSpace compute allocations and their defaults.

Pure declarative data: a single ``DOMAIN: DomainTool``. All request mechanics
(headers, path templating, query encoding, bodies, readonly, confirm,
formatting) live in the core. See ``inference.py`` for the canonical shape.
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/allocations/",
        kind="list",
        help="Personal-account allocation listing; not supported for service accounts. "
        "For job placement and workspace allocation discovery use "
        "mlspace_workspaces action=allocations with target instead.",
    ),
    "list_defaults": Op(
        "GET", "/public/v2/allocations/defaults",
        query_params={"region": "region"},
        required=("region",),
        kind="list",
        help="List default allocations in a region.",
    ),
    "get": Op(
        "GET", "/public/v2/allocations/{allocation_id}",
        path_params={"allocation_id": "allocation_id"},
        required=("allocation_id",),
        help="Get one allocation with its resource status.",
    ),
    "list_assignments": Op(
        "GET", "/public/v2/allocations/{allocation_id}/assignments",
        path_params={"allocation_id": "allocation_id"},
        required=("allocation_id",),
        kind="list",
        help="List an allocation's assignments by organization structure.",
    ),
    "list_instance_types": Op(
        "GET", "/public/v2/allocations/{allocation_id}/instance-types",
        path_params={"allocation_id": "allocation_id"},
        required=("allocation_id",),
        kind="list",
        help="List instance types available in an allocation.",
    ),
    "get_nodes": Op(
        "GET", "/public/v2/allocations/{allocation_id}/nodes",
        path_params={"allocation_id": "allocation_id"},
        required=("allocation_id",),
        help="Get the GPU nodes in an allocation.",
    ),
    "set_default": Op(
        "POST", "/public/v2/allocations/{allocation_id}/defaults",
        path_params={"allocation_id": "allocation_id"},
        body_field="body",
        required=("allocation_id", "body"),
        write=True,
        help="Set an allocation as default for job/notebook; `body` = DefaultRequest "
        "{org_structure, default_for}.",
    ),
    "unset_default": Op(
        "DELETE", "/public/v2/allocations/{allocation_id}/defaults",
        path_params={"allocation_id": "allocation_id"},
        body_field="body",
        required=("allocation_id", "body"),
        write=True,
        help="Unset an allocation as default for job/notebook; `body` = DefaultRequest "
        "{org_structure, default_for}.",
    ),
}

PARAMS: list[Param] = [
    Param("allocation_id", str, "Allocation ID (UUID)."),
    Param("region", str, "Region key (required for list_defaults)."),
    Param(
        "body",
        dict,
        "JSON body = DefaultRequest for set_default/unset_default: "
        "{org_structure: {id, type}, default_for: 'job'|'notebook'}.",
    ),
]

DOMAIN = DomainTool(
    domain="allocations",
    name="mlspace_allocations",
    title="MLSpace Allocations",
    summary="Inspect MLSpace compute allocations, their nodes, instance types, "
    "assignments, and manage default allocations.",
    actions=ACTIONS,
    params=PARAMS,
)
