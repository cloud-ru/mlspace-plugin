"""``mlspace_build_image`` — manage MLSpace build-image jobs.

Pure declarative data; the core (registry.py + client.py) owns all request
mechanics. Mirrors the exemplar ``inference.py`` in shape.
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

# action -> Op. (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec.
ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/build-image/jobs",
        kind="list",
        help="List build-image jobs in the workspace.",
    ),
    "run": Op(
        "POST", "/public/v2/build-image/jobs",
        body_field="body",
        required=("body",),
        write=True,
        help="Run a build-image job. NOTE: this endpoint requires a "
        "multipart/form-data file upload (a requirements.txt), which this tool "
        "does NOT support — a JSON body will be rejected by the API. Build images "
        "via the MLSpace console/CLI instead.",
    ),
    "get": Op(
        "GET", "/public/v2/build-image/jobs/{job_name}",
        path_params={"job_name": "job_name"},
        required=("job_name",),
        help="Get a build-image job's status by name.",
    ),
    "get_logs": Op(
        "GET", "/public/v2/build-image/jobs/{job_name}/logs",
        path_params={"job_name": "job_name"},
        required=("job_name",),
        kind="log",
        help="Get a build-image job's logs by name.",
    ),
    "get_pods": Op(
        "GET", "/public/v2/build-image/jobs/{job_name}/pods",
        path_params={"job_name": "job_name"},
        required=("job_name",),
        kind="list",
        help="List a build-image job's pods by name.",
    ),
    "delete": Op(
        "DELETE", "/public/v2/build-image/jobs/{job_name}",
        path_params={"job_name": "job_name"},
        required=("job_name",),
        write=True,
        confirm=True,
        help="Delete a build-image job by name.",
    ),
}

PARAMS: list[Param] = [
    Param("job_name", str, "Name of the build-image job."),
    Param(
        "body",
        dict,
        "Unused: the run endpoint needs a multipart file upload, not a JSON body "
        "(not supported by this tool — use the console/CLI to build images).",
    ),
]

DOMAIN = DomainTool(
    domain="build_image",
    name="mlspace_build_image",
    title="MLSpace Build Image",
    summary="Manage MLSpace build-image jobs (run, inspect status/logs/pods, delete).",
    actions=ACTIONS,
    params=PARAMS,
)
