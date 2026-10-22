"""``mlspace_jobs`` — manage MLSpace training jobs.

Pure declarative data following the exemplar in ``inference.py``. All request
mechanics (headers, path templating, query encoding, bodies, readonly, confirm,
formatting) live in the core; this module only declares ``ACTIONS`` / ``PARAMS``.
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
# NOTE: most paths use {job_name}, but /params and /preemptors use {name}.
ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/jobs",
        query_params={
            "region": "region",
            "allocation_name": "allocation_name",
            "status": "status",
            "job_author": "job_author",
            "queue_id": "queue_id",
            "offset": "offset",
            "limit": "limit",
            "start_date": "start_date",
            "end_date": "end_date",
        },
        kind="list",
        help="List one page of training jobs in a target region (filter by status/author/queue/dates). "
        "For broad connected-workspace reads use mlspace_jobs_overview (dynamic regions/pages). "
        "Pass `region` (e.g. SR006): without it the API returns count=0 (empty), not all regions.",
    ),
    "get": Op(
        "GET", "/public/v2/jobs/{job_name}",
        path_params={"job_name": "job_name"},
        required=("job_name",),
        # kind='job' triggers _normalise_job: flags the created→'job not found' race
        # and the failed+empty-error trap, and adds a canonical status.
        kind="job",
        help="Get one job's status using resource_ref or its exact target/name.",
    ),
    "logs": Op(
        "GET", "/public/v2/jobs/{job_name}/logs",
        path_params={"job_name": "job_name"},
        query_params={"tail": "tail", "verbose": "verbose", "region": "region"},
        # API rejects logs without region (422) though the spec marks it optional —
        # both blind-model runs hit this, so require it.
        required=("job_name", "region"),
        kind="log",
        help="Get a job's logs by name (use `tail` to limit lines).",
    ),
    "list_nodes": Op(
        "GET", "/public/v2/jobs/{job_name}/nodes",
        path_params={"job_name": "job_name"},
        required=("job_name",),
        kind="list",
        help="List nodes that were used for a job's execution.",
    ),
    "list_pods": Op(
        "GET", "/public/v2/jobs/elastic/{job_name}/pods",
        path_params={"job_name": "job_name"},
        required=("job_name",),
        kind="list",
        help="List Elastic job pods by job name (pytorch_elastic jobs).",
    ),
    "get_params": Op(
        "GET", "/public/v2/jobs/{name}/params",
        path_params={"name": "job_name"},
        required=("job_name",),
        help="Get the parameters a job was run with, by job name.",
    ),
    "get_preemptors": Op(
        "GET", "/public/v2/jobs/{name}/preemptors",
        path_params={"name": "job_name"},
        required=("job_name",),
        kind="list",
        help="Get the preemptors that gained priority over a job, by job name.",
    ),
    "run": Op(
        "POST", "/public/v2/jobs",
        body_field="body",
        required=("body",),
        write=True,
        help="Run a training job; `body` = RunJobRequest: "
        "{script, base_image, instance_type, region, type, n_workers, ...}. "
        "Before submission, show and confirm workspace name/ID, region, allocation/queue "
        "or explained defaults, scoped available instance type, image:tag and command. "
        "Discover placement via mlspace_workspaces allocations/allocation_queues and "
        "mlspace_resources instance_types; configs alone is not eligibility evidence. "
        "NOTE: `region` must be explicit. For type=binary, `script` is a non-empty "
        "command or executable path and is forwarded unchanged; verify that it is "
        "available in the selected image. Other job types require an NFS script path "
        "under /home/jovyan/... in that same region. NFS volumes are region-scoped, "
        "and the Public API cannot stat the file.",
    ),
    "restart": Op(
        "POST", "/public/v2/jobs/restart",
        body_field="body",
        required=("body",),
        write=True,
        help="Restart a job by name (new job, same params); `body` = "
        "RestartJobRequest: {job_name}.",
    ),
    "delete": Op(
        "DELETE", "/public/v2/jobs/{job_name}",
        path_params={"job_name": "job_name"},
        query_params={"region": "region"},
        required=("job_name", "region"),
        write=True,
        confirm=True,
        help="Delete a job by name.",
    ),
}

PARAMS: list[Param] = [
    Param("job_name", str, "Name of the job (path param for get/logs/nodes/pods/"
          "params/preemptors/delete)."),
    Param(
        "region",
        str,
        "Model-training region, e.g. DGX2-MT, A100-MT, SR003-SR006, SR008 "
        "(configs may omit allocated regions; use jobs overview or workspace allocations "
        "to discover existing-job regions). Required for logs and delete, "
        "and effectively required for `list` (omitting it returns count=0, not all regions).",
    ),
    Param("allocation_name", str, "Filter list by allocation name."),
    Param(
        "status",
        list[str],
        "Filter list by job statuses (repeatable). Known values: Completed, "
        "Completing, Deleted, Failed, Pending, Running, Stopped, Succeeded, "
        "Terminated, Terminating, Restarting.",
    ),
    Param("job_author", list[str], "Filter list by job authors (repeatable)."),
    Param("queue_id", str, "Filter list by queue UUID."),
    Param("offset", int, "Number of list items to skip.", default=0),
    Param("limit", int, "Max number of list items to return."),
    Param("start_date", str, "List start-date filter (RFC3339, e.g. 2026-03-31T00:00:00Z)."),
    Param("end_date", str, "List end-date filter (RFC3339, e.g. 2026-03-31T23:00:00Z)."),
    Param("tail", int, "logs: number of recent log lines to display.", default=0),
    Param("verbose", bool, "logs: if true, include timestamps on each line.", default=True),
    Param(
        "body",
        dict,
        "JSON body. run=RunJobRequest (requires script, base_image, instance_type); "
        "for type=binary, script may be a command or executable path; "
        "restart=RestartJobRequest ({job_name}).",
    ),
]

DOMAIN = DomainTool(
    domain="jobs",
    name="mlspace_jobs",
    title="MLSpace Training Jobs",
    summary="Manage MLSpace training jobs (run, inspect, logs, nodes, restart, delete).",
    actions=ACTIONS,
    params=PARAMS,
)
