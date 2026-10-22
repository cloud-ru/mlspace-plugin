"""``mlspace_tensorboards`` — manage MLSpace TensorBoard instances.

Pure declarative data following the inference.py exemplar. No request
mechanics here; the core (registry.py + client.py) owns headers, path
templating, query encoding, bodies, readonly, confirm, and formatting.
"""

from __future__ import annotations

from typing import Literal

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/tensorboards/v2/tensorboards",
        query_params={
            "order_by": "order_by",
            "desc": "desc",
            "limit": "limit",
            "offset": "offset",
            "search": "search",
            "status": "status",
        },
        kind="list",
        help="List TensorBoards in the workspace (filters: order_by, desc, "
        "limit, offset, search, status).",
    ),
    "get": Op(
        "GET", "/public/v2/tensorboards/v2/tensorboard/{tensorboard_uuid}",
        path_params={"tensorboard_uuid": "tensorboard_uuid"},
        required=("tensorboard_uuid",),
        help="Get one TensorBoard by uuid.",
    ),
    "create": Op(
        "POST", "/public/v2/tensorboards/v2/{namespace}/tensorboard",
        path_params={"namespace": "namespace"},
        body_field="body",
        required=("namespace", "body"),
        write=True,
        help="Create a TensorBoard; `body` = CreateTensorboardPayloadV2 "
        "({name, image, instance_type, logdir, ...}).",
    ),
    "modify": Op(
        "POST", "/public/v2/tensorboards/v2/tensorboard/{tensorboard_uuid}/modify",
        path_params={"tensorboard_uuid": "tensorboard_uuid"},
        body_field="body",
        required=("tensorboard_uuid", "body"),
        write=True,
        help="Modify a TensorBoard; `body` = ModifyTensorboardRequest "
        "({autoshutdown_config, description, s3_credentials, s3_buckets}).",
    ),
    "pause": Op(
        "POST", "/public/v2/tensorboards/v2/tensorboard/{tensorboard_uuid}/pause",
        path_params={"tensorboard_uuid": "tensorboard_uuid"},
        required=("tensorboard_uuid",),
        write=True,
        help="Pause a TensorBoard.",
    ),
    "resume": Op(
        "POST",
        "/public/v2/tensorboards/v2/{namespace}/tensorboard/{tensorboard_uuid}/resume",
        path_params={"namespace": "namespace", "tensorboard_uuid": "tensorboard_uuid"},
        body_field="body",
        required=("namespace", "tensorboard_uuid", "body"),
        write=True,
        help="Resume a TensorBoard; `body` = ResumeTensorboardPayload "
        "({region, instance_type}).",
    ),
    "delete": Op(
        "DELETE", "/public/v2/tensorboards/v2/tensorboard/{tensorboard_uuid}",
        path_params={"tensorboard_uuid": "tensorboard_uuid"},
        required=("tensorboard_uuid",),
        write=True,
        confirm=True,
        help="Delete a TensorBoard.",
    ),
}

PARAMS: list[Param] = [
    Param("tensorboard_uuid", str, "TensorBoard UUID (get/modify/pause/resume/delete)."),
    Param("namespace", str, "Namespace the TensorBoard belongs to (create/resume). "
          "Optional: auto-filled from the server's workspace if omitted."),
    Param(
        "order_by",
        Literal["name", "creationTimestamp"],
        "List ordering field (default creationTimestamp).",
    ),
    Param("desc", bool, "Sort descending (list)."),
    Param("limit", int, "Max results to return (list)."),
    Param("offset", int, "Pagination offset (list)."),
    Param("search", str, "Filter by name substring (list)."),
    Param("status", str, "Filter by comma-separated statuses, e.g. 'creating,running' (list)."),
    Param(
        "body",
        dict,
        "JSON body. create=CreateTensorboardPayloadV2; modify="
        "ModifyTensorboardRequest; resume=ResumeTensorboardPayload.",
    ),
]

DOMAIN = DomainTool(
    domain="tensorboards",
    name="mlspace_tensorboards",
    title="MLSpace TensorBoards",
    summary="Manage MLSpace TensorBoard instances (list, inspect, create, "
    "modify, pause, resume, delete).",
    actions=ACTIONS,
    params=PARAMS,
)
