"""``mlspace_inference`` — manage and call MLSpace inference services.

EXEMPLAR module: every other domain module follows this exact shape — a single
``DOMAIN: DomainTool`` made of pure data. No request mechanics live here; the
core (registry.py + client.py) owns headers, path templating, query encoding,
bodies, readonly, confirm, formatting. To add a domain, copy this structure and
fill ``ACTIONS`` / ``PARAMS`` from the domain's slice in ``_planning/slices``.
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/inference/v2/",
        kind="list",
        help="List inference services in the workspace.",
    ),
    "get": Op(
        "GET", "/public/v2/inference/v2/{inference_name}",
        path_params={"inference_name": "inference_name"},
        query_params={"region": "region"},
        required=("inference_name", "region"),
        help="Get one inference service.",
    ),
    "predict": Op(
        "POST", "/public/v2/inference/v2/predict/{inference_name}/{model_name}/",
        path_params={"inference_name": "inference_name", "model_name": "model_name"},
        body_field="body",
        required=("inference_name", "model_name"),
        side_effect=True,  # runs a deployed (paid) model — not a read
        help="Call a deployed model; put the model's input in `body` (free-form).",
    ),
    "create": Op(
        "POST", "/public/v2/inference/v2/",
        body_field="body",
        required=("body",),
        write=True,
        help="Create an inference service; `body` = CreateInferenceServiceRequestV2.",
    ),
    "update": Op(
        "PATCH", "/public/v2/inference/v2/{inference_name}",
        path_params={"inference_name": "inference_name"},
        body_field="body",
        required=("inference_name", "body"),
        write=True,
        help="Update an inference service; `body` = PatchInferenceServiceRequestV2.",
    ),
    "delete": Op(
        "DELETE", "/public/v2/inference/v2/{inference_name}",
        path_params={"inference_name": "inference_name"},
        query_params={"region": "region"},
        required=("inference_name", "region"),
        write=True,
        confirm=True,
        help="Delete an inference service.",
    ),
}

PARAMS: list[Param] = [
    Param("inference_name", str, "Name of the inference service."),
    Param("model_name", str, "Model name within the service (predict only)."),
    Param(
        "region",
        str,
        "Inference region, e.g. DGX2-INF-001, CCE-INF, INF-002 "
        "(authoritative list via mlspace_resources configs cluster_type=INF). "
        "Required for get/delete.",
    ),
    Param(
        "body",
        dict,
        "JSON body. create=CreateInferenceServiceRequestV2; update="
        "PatchInferenceServiceRequestV2; predict=the model's own input payload.",
    ),
]

DOMAIN = DomainTool(
    domain="inference",
    name="mlspace_inference",
    title="MLSpace Inference",
    summary="Manage and call MLSpace inference services (deploy, inspect, predict, delete).",
    actions=ACTIONS,
    params=PARAMS,
)
