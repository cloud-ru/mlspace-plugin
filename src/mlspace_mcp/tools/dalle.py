"""``mlspace_dalle`` — call the MLSpace DALL-E async inference API.

Pure declarative data only; the core (registry.py + client.py) owns all request
mechanics. Two operations: submit an async predict request, then poll for its
status/result (which may be a generated image).
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

ACTIONS: dict[str, Op] = {
    "predict": Op(
        "POST", "/public/v2/dalle/v1/predict/{model_name}",
        path_params={"model_name": "model_name"},
        body_field="body",
        required=("model_name",),
        side_effect=True,  # submits a (paid) inference job — not a read
        help="Send an async request to DALL-E inference; put the model's input in `body` (free-form). Returns a request_id.",
    ),
    "result": Op(
        "GET", "/public/v2/dalle/v1/result/{request_id}",
        path_params={"request_id": "request_id"},
        required=("request_id",),
        kind="binary",
        help="Get status or result (may be a generated image) for a prior predict request_id.",
    ),
}

PARAMS: list[Param] = [
    Param(
        "model_name",
        str,
        "DALL-E model name to run the prediction on (predict only). NOTE: no MLSpace "
        "endpoint lists DALL-E models — the ai_services catalog returned 403 on the "
        "tested workspace (RBAC role model access checking error) and the /inference "
        "list is empty — so this name is NOT discoverable via the API; take it from the "
        "platform docs/console. A wrong name yields an opaque 404 that is byte-identical "
        "for 'unknown model' and 'service not deployed'.",
    ),
    Param("request_id", str, "Request id returned by predict, used to poll for the result."),
    Param("body", dict, "Free-form JSON input payload forwarded to the DALL-E model (predict only)."),
]

DOMAIN = DomainTool(
    domain="dalle",
    name="mlspace_dalle",
    title="MLSpace DALL-E",
    summary="Call the MLSpace DALL-E async inference API: submit a predict request, then poll for the status/result.",
    actions=ACTIONS,
    params=PARAMS,
)
