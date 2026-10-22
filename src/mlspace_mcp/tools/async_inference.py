"""``mlspace_async_inference`` — submit and track asynchronous inference requests.

Pure declarative data following the ``inference`` exemplar; all request
mechanics live in the core (registry.py + client.py).
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/async_inferences/v1/",
        query_params={"inference_name": "inference_name"},
        required=("inference_name",),
        kind="list",
        help="List asynchronous requests for an inference service.",
    ),
    "predict": Op(
        "POST",
        "/public/v2/async_inferences/v1/{region}/{inference_name}/{predict_path}",
        path_params={
            "region": "region",
            "inference_name": "inference_name",
            "predict_path": "predict_path",
        },
        # predict_path is a deliberate multi-segment tail (e.g. v1/models/svc:predict):
        # it keeps its "/" separators; region and inference_name stay opaque segments.
        path_tail=frozenset({"predict_path"}),
        body_field="body",
        required=("region", "inference_name", "predict_path"),
        side_effect=True,  # submits a (paid) inference job — not a read
        help="Send an async prediction request; put the model's input in `body` (free-form).",
    ),
    "get_result": Op(
        "GET", "/public/v2/async_inferences/v1/{request_id}/result",
        path_params={"request_id": "request_id"},
        query_params={"inference_name": "inference_name"},
        required=("request_id", "inference_name"),
        help="Get the result of an asynchronous request.",
    ),
    "get_status": Op(
        "GET", "/public/v2/async_inferences/v1/{request_id}/status",
        path_params={"request_id": "request_id"},
        query_params={"inference_name": "inference_name"},
        required=("request_id", "inference_name"),
        help="Get the status of an asynchronous request.",
    ),
}

PARAMS: list[Param] = [
    Param("inference_name", str, "Name of the inference service."),
    Param(
        "region",
        str,
        "Inference region, e.g. DGX2-INF-001, CCE-INF, INF-002 "
        "(authoritative list via mlspace_resources configs cluster_type=INF). "
        "Required for predict.",
    ),
    Param(
        "predict_path",
        str,
        "Predict path, usually `v1/models/{inference_name}:predict` (predict only).",
    ),
    Param("request_id", str, "ID of the async request (required for get_result/get_status)."),
    Param("body", dict, "Free-form model input payload for predict."),
]

DOMAIN = DomainTool(
    domain="async_inference",
    name="mlspace_async_inference",
    title="MLSpace Async Inference",
    summary="Submit and track asynchronous MLSpace inference requests (predict, status, result).",
    actions=ACTIONS,
    params=PARAMS,
    redact_result_keys=("inference_key",),
)
