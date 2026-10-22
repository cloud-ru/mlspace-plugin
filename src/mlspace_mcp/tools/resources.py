"""``mlspace_resources`` — read MLSpace compute catalog and node load.

A merged read-only catalog over configs, available instance types, nodes, and
node load. Pure declarative data; the core owns all request mechanics.
"""

from __future__ import annotations

from typing import Literal

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
ACTIONS: dict[str, Op] = {
    "configs": Op(
        "GET", "/public/v2/configs",
        query_params={"cluster_type": "cluster_type"},
        required=("cluster_type",),
        kind="catalog",
        help="Get regional instance specifications and compatible images for Jobs/Notebooks. "
        "Catalog membership does not establish availability in a workspace allocation "
        "or queue. For a job, first get a successful scoped instance_types result, then "
        "use configs only for those returned keys. Never use configs as a fallback "
        "after a failed availability check or offer configs-only keys as launch options.",
    ),
    "instance_types": Op(
        "GET", "/public/v2/instance_types/{region}/available",
        path_params={"region": "region"},
        query_params={"allocation_name": "allocation_name", "queue_name": "queue_name"},
        required=("region",),
        kind="list",
        help="Check instance types for the selected target and region. Pass the selected "
        "allocation_name and queue_name when applicable; omitted names use defaults. "
        "Use this scoped result with configs for job placement. Counts are a snapshot, "
        "not a reservation. Only returned keys are launch candidates; look up their "
        "specifications/images in configs. If this call fails, stop launch preparation "
        "and report unverified availability; never replace it with catalog choices.",
    ),
    "nodes": Op(
        "GET", "/public/v2/nodes/{region}/nodes",
        path_params={"region": "region"},
        required=("region",),
        kind="list",
        help="List available nodes to allocation in a region.",
    ),
    "nodes_load": Op(
        "GET", "/public/v2/nodes/load",
        query_params={
            "queue_id": "queue_id",
            "allocation_id": "allocation_id",
            "node_names": "node_names",
        },
        required=("node_names",),
        kind="list",
        help="Get load on nodes; pass node_names (and optionally queue_id/allocation_id).",
    ),
    "rate_limits": Op(
        "GET", "/public/v2/limiter/",
        kind="list",
        help="List this account's API rate limits and current usage, per endpoint and "
        "period. Call this BEFORE a burst of writes: the contract documents 429 on "
        "only ~45% of operations, so this is the only reliable way to know the budget.",
    ),
}

PARAMS: list[Param] = [
    Param(
        "cluster_type",
        Literal["MT", "INF"],
        "Cluster type to fetch configs for (required for configs).",
    ),
    Param(
        "region",
        str,
        "Model-training region, e.g. DGX2-MT, A100-MT, SR003-SR006, SR008 "
        "(authoritative list via the configs action). Required for instance_types/nodes. "
        "Optional for configs: filters the catalog client-side to this one region "
        "(case-insensitive); omit to get all regions.",
    ),
    Param("allocation_name", str, "Filter instance_types by allocation name."),
    Param("queue_name", str, "Filter instance_types by queue name."),
    Param("queue_id", str, "Filter node load by queue UUID (nodes_load)."),
    Param("allocation_id", str, "Filter node load by allocation UUID (nodes_load)."),
    Param("node_names", list[str], "Node names to query load for (nodes_load)."),
]

DOMAIN = DomainTool(
    domain="resources",
    name="mlspace_resources",
    title="MLSpace Resources",
    summary="Read MLSpace compute catalog: configs, available instance types, nodes, node load.",
    actions=ACTIONS,
    params=PARAMS,
)
