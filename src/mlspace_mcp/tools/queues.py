"""``mlspace_queues`` — manage MLSpace allocation queues and shared-cluster queues.

Pure declarative data following the EXEMPLAR (``inference.py``). The core owns
all request mechanics; this module only declares ``ACTIONS`` / ``PARAMS`` and the
``DomainTool`` metadata for the ``queues`` domain.
"""

from __future__ import annotations

from typing import Literal

from ..registry import DomainTool, Op, Param

# action -> Op. (method, path) pairs are the single source of truth for drift.
ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/queues/",
        query_params={"allocation_id": "allocation_id", "workspace_id": "workspace_id"},
        required=("allocation_id",),
        kind="list",
        help="List queues for an allocation.",
    ),
    "get": Op(
        "GET", "/public/v2/queues/{queue_id}",
        path_params={"queue_id": "queue_id"},
        required=("queue_id",),
        help="Get detailed information about one queue.",
    ),
    "defaults": Op(
        "GET", "/public/v2/queues/defaults",
        query_params={"allocation_id": "allocation_id"},
        required=("allocation_id",),
        kind="list",
        help="List default queues (with types) for an allocation.",
    ),
    "instance_types": Op(
        "GET", "/public/v2/queues/{queue_id}/instance-types",
        path_params={"queue_id": "queue_id"},
        required=("queue_id",),
        kind="list",
        help="List instance types available for a queue.",
    ),
    "jobs": Op(
        "GET", "/public/v2/queues/{queue_id}/jobs",
        path_params={"queue_id": "queue_id"},
        required=("queue_id",),
        kind="list",
        help="List jobs in a queue.",
    ),
    "notebooks": Op(
        "GET", "/public/v2/queues/{queue_id}/notebooks",
        path_params={"queue_id": "queue_id"},
        required=("queue_id",),
        kind="list",
        help="List notebooks in a queue.",
    ),
    "pods": Op(
        "GET", "/public/v2/queues/{queue_id}/pods",
        path_params={"queue_id": "queue_id"},
        required=("queue_id",),
        kind="list",
        help="List pods in a queue.",
    ),
    "awaiting_launch_resources": Op(
        "GET", "/public/v2/queues/{queue_id}/awaiting-launch-resources",
        path_params={"queue_id": "queue_id"},
        query_params={"priority": "priority", "limit": "limit", "offset": "offset"},
        required=("queue_id", "priority", "limit", "offset"),
        kind="list",
        help="List one priority page; priority, limit and offset are required. Use mlspace_queue_inspect for all-priority coverage. List the resources a queue is waiting on. THE way to answer 'why is my "
        "job still pending?' — `jobs get` only reports the bare status `pending` and "
        "`jobs list_nodes` answers 404 while the job waits.",
    ),
    "queue_defaults": Op(
        "GET", "/public/v2/queues/{queue_id}/defaults",
        path_params={"queue_id": "queue_id"},
        required=("queue_id",),
        kind="list",
        help="List default settings of ONE queue (the `defaults` action lists default "
        "queues of an allocation — different question, similar name).",
    ),
    "assign_workspace": Op(
        "POST", "/public/v2/queues/{queue_id}/workspaces/assign",
        path_params={"queue_id": "queue_id"},
        body_field="body",
        required=("queue_id", "body"),
        write=True,
        help="Assign workspaces to a queue; `body` = workspace assignment request.",
    ),
    "delete": Op(
        "DELETE", "/public/v2/queues/{queue_id}",
        path_params={"queue_id": "queue_id"},
        required=("queue_id",),
        write=True,
        confirm=True,
        help="Delete a queue. Irreversible; jobs and notebooks bound to it lose their "
        "scheduling target.",
    ),
    "create": Op(
        "POST", "/public/v2/queues/",
        body_field="body",
        required=("body",),
        write=True,
        help="Create a queue; `body` = CreateQueueRequest.",
    ),
    "update": Op(
        "PUT", "/public/v2/queues/{queue_id}",
        path_params={"queue_id": "queue_id"},
        body_field="body",
        required=("queue_id", "body"),
        write=True,
        help="Update a queue; `body` = UpdateQueueRequest.",
    ),
    "assign": Op(
        "POST", "/public/v2/queues/{queue_id}/assign",
        path_params={"queue_id": "queue_id"},
        body_field="body",
        required=("queue_id", "body"),
        write=True,
        help="Assign a queue to a workspace; `body` = AssignmentRequest.",
    ),
    "unassign": Op(
        "DELETE", "/public/v2/queues/{queue_id}/assign",
        path_params={"queue_id": "queue_id"},
        body_field="body",
        required=("queue_id", "body"),
        write=True,
        help="Unassign a queue from a workspace; `body` = AssignmentRequest.",
    ),
    "set_default": Op(
        "POST", "/public/v2/queues/{queue_id}/defaults",
        path_params={"queue_id": "queue_id"},
        body_field="body",
        required=("queue_id", "body"),
        write=True,
        help="Set the queue as default for a job/notebook; "
             "`body` = DefaultQueueRequest: {org_structure, default_for}.",
    ),
    "unset_default": Op(
        "DELETE", "/public/v2/queues/{queue_id}/defaults",
        path_params={"queue_id": "queue_id"},
        body_field="body",
        required=("queue_id", "body"),
        write=True,
        help="Unset the queue as default for a job/notebook; "
             "`body` = DefaultQueueRequest: {org_structure, default_for}.",
    ),
    "add_nodes": Op(
        "POST", "/public/v2/queues/{queue_id}/nodes",
        path_params={"queue_id": "queue_id"},
        body_field="body",
        required=("queue_id", "body"),
        write=True,
        help="Add compute nodes to the destination queue; `body` = AddNodesRequest "
        "{nodes, force_withdrawal}. Inspect source workload with mlspace_queue_inspect first. "
        "force_withdrawal=true forces withdrawal of all load; never escalate silently.",
    ),
    "assign_shared": Op(
        "POST", "/public/v2/shared-cluster-queues/assign",
        body_field="body",
        required=("body",),
        write=True,
        help="Assign a shared cluster queue to a workspace; "
             "`body` = AssignmentRequest: {region_key, org_structure}.",
    ),
    "unassign_shared": Op(
        "DELETE", "/public/v2/shared-cluster-queues/assign",
        body_field="body",
        required=("body",),
        write=True,
        help="Unassign a shared cluster queue from a workspace; "
             "`body` = AssignmentRequest: {region_key, org_structure}.",
    ),
}

PARAMS: list[Param] = [
    Param("priority", Literal["shared-low", "shared-medium", "shared-cluster", "low", "medium", "high"], "Required priority for awaiting_launch_resources; one priority is not complete demand."),
    Param("limit", int, "Required pending page size."),
    Param("offset", int, "Required pending page offset (start at zero)."),
    Param("queue_id", str, "Queue unique ID (UUID); required for queue-scoped actions."),
    Param("allocation_id", str, "Allocation unique ID (UUID); required for list/defaults."),
    Param("workspace_id", str, "Optional workspace UUID to filter the queue list by."),
    Param(
        "body",
        dict,
        "JSON body. create=CreateQueueRequest; update=UpdateQueueRequest; "
        "assign/unassign=AssignmentRequest {org_structure}; "
        "set_default/unset_default=DefaultQueueRequest {org_structure, default_for}; "
        "add_nodes=AddNodesRequest {nodes, force_withdrawal}; "
        "assign_shared/unassign_shared=AssignmentRequest {region_key, org_structure}.",
    ),
]

DOMAIN = DomainTool(
    domain="queues",
    name="mlspace_queues",
    title="MLSpace Queues",
    summary="Manage MLSpace allocation queues and shared-cluster queues "
            "(list, inspect, create, update, assign, set defaults, add nodes).",
    actions=ACTIONS,
    params=PARAMS,
)
