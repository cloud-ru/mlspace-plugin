"""``mlspace_notebooks`` — manage MLSpace Jupyter Server notebooks.

Pure declarative ``DomainTool`` data; the core owns all request mechanics.
Mixes v1 and v2 API paths — paths are copied verbatim from the slice.
"""

from __future__ import annotations

from typing import Literal

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
ACTIONS: dict[str, Op] = {
    "list": Op(
        "GET", "/public/v2/notebooks/v2/notebooks",
        query_params={
            "notebook_type": "notebook_type",
            "order_by": "order_by",
            "desc": "desc",
            "limit": "limit",
            "offset": "offset",
            "search": "search",
            "status": "status",
            "access_mode": "access_mode",
        },
        kind="list",
        help="List one page of Jupyter servers in the selected target. For the connected-set "
        "inventory use mlspace_notebooks_overview. Retain paused notebooks with status; "
        "configured GPUs are not evidence of active use. These are workloads, not compute nodes.",
    ),
    "get": Op(
        "GET", "/public/v2/notebooks/v1/notebook/{notebook_uuid}",
        path_params={"notebook_uuid": "notebook_uuid"},
        required=("notebook_uuid",),
        help="Get one Jupyter server by uuid.",
    ),
    "autoshutdown_get": Op(
        "GET", "/public/v2/notebooks/v2/autoshutdown-rules/workspace/{workspace_id}",
        path_params={"workspace_id": "workspace_id"},
        required=("workspace_id",),
        help="Get the workspace-level autoshutdown rule.",
    ),
    "create": Op(
        "POST", "/public/v2/notebooks/v2/{namespace}/notebook",
        path_params={"namespace": "namespace"},
        body_field="body",
        required=("namespace", "body"),
        write=True,
        help="Create a Jupyter server; `body` = CreateNotebookPayload.",
    ),
    "delete": Op(
        "DELETE", "/public/v2/notebooks/v2/notebook/{notebook_uuid}",
        path_params={"notebook_uuid": "notebook_uuid"},
        required=("notebook_uuid",),
        write=True,
        confirm=True,
        help="Delete a Jupyter server asynchronously.",
    ),
    "users_list": Op(
        "GET", "/public/v2/notebooks/v2/{namespace}/{notebook_name}/users",
        path_params={"namespace": "namespace", "notebook_name": "notebook_name"},
        required=("notebook_name",),
        kind="list",
        help="List users a notebook is shared with. Keyed by notebook NAME (not uuid).",
    ),
    "users_set": Op(
        "PUT", "/public/v2/notebooks/v2/{namespace}/{notebook_name}/users",
        path_params={"namespace": "namespace", "notebook_name": "notebook_name"},
        body_field="body",
        required=("notebook_name", "body"),
        write=True,
        help="Grant notebook access to users; `body` = user access payload.",
    ),
    "users_revoke": Op(
        "DELETE", "/public/v2/notebooks/v2/{namespace}/{notebook_name}/users",
        path_params={"namespace": "namespace", "notebook_name": "notebook_name"},
        body_field="body",
        required=("notebook_name", "body"),
        write=True,
        confirm=True,
        help="Revoke notebook access from users; `body` = user access payload.",
    ),
    "pause": Op(
        "POST", "/public/v2/notebooks/v2/notebook/{notebook_uuid}/pause",
        path_params={"notebook_uuid": "notebook_uuid"},
        required=("notebook_uuid",),
        write=True,
        help="Pause a Jupyter server asynchronously.",
    ),
    "resume": Op(
        "POST", "/public/v2/notebooks/v1/{namespace}/notebook/{notebook_uuid}/resume",
        path_params={"namespace": "namespace", "notebook_uuid": "notebook_uuid"},
        body_field="body",
        required=("namespace", "notebook_uuid", "body"),
        write=True,
        help="Resume a Jupyter server; `body` = ResumeNotebookPayload.",
    ),
    "modify": Op(
        "POST", "/public/v2/notebooks/v2/notebook/{notebook_uuid}/modify",
        path_params={"notebook_uuid": "notebook_uuid"},
        body_field="body",
        required=("notebook_uuid", "body"),
        write=True,
        help="Modify a Jupyter server; `body` = ModifyNotebookV2Request.",
    ),
    "autoshutdown_set": Op(
        "POST", "/public/v2/notebooks/v2/autoshutdown-rules/workspace/{workspace_id}",
        path_params={"workspace_id": "workspace_id"},
        body_field="body",
        required=("workspace_id", "body"),
        write=True,
        help="Create the workspace-level autoshutdown rule; `body` = AutoShutdownV2Config.",
    ),
    "autoshutdown_delete": Op(
        "DELETE", "/public/v2/notebooks/v1/autoshutdown-rules/workspace/{workspace_id}",
        path_params={"workspace_id": "workspace_id"},
        required=("workspace_id",),
        write=True,
        confirm=True,
        help="Delete the workspace-level autoshutdown rule.",
    ),
}

PARAMS: list[Param] = [
    Param("notebook_uuid", str, "UUID of the Jupyter server (uid in list results), or use resource_ref."),
    Param("notebook_name", str, "Notebook NAME (users_* actions are keyed by name, "
          "not by uuid)."),
    Param("namespace", str, "Namespace the notebook belongs to (create/resume). "
          "Optional: auto-filled from the selected target if omitted."),
    Param("workspace_id", str, "Workspace UUID for autoshutdown rules (path parameter). "
          "Optional: auto-filled from the selected target if omitted."),
    Param(
        "notebook_type",
        Literal["Spark", "Default"],
        "List filter: notebook type.",
    ),
    Param(
        "order_by",
        Literal["name", "creationTimestamp"],
        "List ordering field.",
    ),
    Param("desc", bool, "List: sort descending."),
    Param("limit", int, "List: max results to return."),
    Param("offset", int, "List: pagination offset."),
    Param("search", str, "List: search by name."),
    Param("status", str, "List: filter by status (comma-separated)."),
    Param("access_mode", str, "List: filter by access mode (comma-separated)."),
    Param(
        "body",
        dict,
        "JSON body. create=CreateNotebookPayload {name, image, instance_type, ...} — "
        "`image` is an OBJECT {name, tag, type}, where type is datahub|custom; a bare "
        "\"repo:tag\" string is rejected with 422. "
        "resume=ResumeNotebookPayload {region, instance_type} — the body is REQUIRED "
        "even though both fields have defaults, and the API is known to ignore "
        "`instance_type`, resuming on the notebook's previous configuration while "
        "answering 200: check `instanceType` in the response before assuming the "
        "requested size, or a paid GPU may come back up instead of a free one. "
        "modify=ModifyNotebookV2Request; autoshutdown_set=AutoShutdownV2Config "
        "{by_timer | by_load | by_schedule}.",
    ),
]

DOMAIN = DomainTool(
    domain="notebooks",
    name="mlspace_notebooks",
    title="MLSpace Notebooks",
    summary="Manage MLSpace Jupyter Server notebooks (create, inspect, pause, resume, "
    "modify, delete, autoshutdown rules).",
    actions=ACTIONS,
    params=PARAMS,
)
