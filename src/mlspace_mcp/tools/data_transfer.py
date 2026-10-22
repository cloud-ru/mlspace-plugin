"""``mlspace_data_transfer`` — manage MLSpace data-transfer connectors, transfers, and history.

Pure declarative data (see ``inference.py`` exemplar). The core owns all request
mechanics. Spans API versions v2/v3/v4/v5; paths are copied verbatim from the
slice, placeholders included.
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

ACTIONS: dict[str, Op] = {
    # --- connectors (v3 collection + v2 delete) ---------------------------
    "list_connectors": Op(
        "GET", "/public/v2/data_transfer/v3/connectors",
        # page/page_size are the ONLY working shrink lever for this ~27KB list: the
        # API ignores limit/offset here (limit=5&offset=0 still returned all 65). No
        # auto-default page_size — the response has no total_count, so a silent clamp
        # would hide data (breaks principle #1). Pagination stays strictly opt-in.
        query_params={"page": "page", "page_size": "page_size"},
        kind="list",
        help="List connectors visible in the workspace. Use page/page_size for bounded reads; "
             "do not infer a complete inventory from one page.",
    ),
    "get_connector": Op(
        "GET", "/public/v2/data_transfer/v3/connectors/{connector_type}/{id_}",
        path_params={"connector_type": "connector_type", "id_": "id_"},
        required=("connector_type", "id_"),
        help="Get one connector by type and id.",
    ),
    "create_connector": Op(
        "POST", "/public/v2/data_transfer/v3/connectors",
        body_field="body",
        required=("body",),
        write=True,
        help="Create a user connector; `body` = ConnectorInputViewModelV3. "
             "The bundled creation schema lists postgresql, mssql, mysql, clickhouse, "
             "oracledb, s3amazon, s3custom, s3google. Source discovery also includes "
             "system types; it is not a list of types that can be created.",
    ),
    "update_connector": Op(
        "POST", "/public/v2/data_transfer/v3/connectors/{connector_type}/{id_}",
        path_params={"connector_type": "connector_type", "id_": "id_"},
        body_field="body",
        required=("connector_type", "id_", "body"),
        write=True,
        help="Update a connector; `body` = ConnectorUpdateViewModelV3.",
    ),
    "fav_connector": Op(
        "POST", "/public/v2/data_transfer/v3/connectors/{connector_type}/{id_}/fav",
        path_params={"connector_type": "connector_type", "id_": "id_"},
        body_field="body",
        required=("connector_type", "id_", "body"),
        write=True,
        help="Mark/unmark a connector favorite; `body` = FavoriteInputViewModel.",
    ),
    "halt_connector": Op(
        "POST", "/public/v2/data_transfer/v3/connectors/{connector_type}/{id_}/halt",
        path_params={"connector_type": "connector_type", "id_": "id_"},
        required=("connector_type", "id_"),
        write=True,
        help="Mark a connector inactive (also halts its transfers).",
    ),
    "try_connector": Op(
        "POST", "/public/v2/data_transfer/v3/connectors/{connector_type}/{id_}/try",
        path_params={"connector_type": "connector_type", "id_": "id_"},
        required=("connector_type", "id_"),
        write=True,
        help="Test connectivity and activate the connector on success (state-changing). "
             "Do not use or recommend it as a read-only diagnostic step; inspect existing "
             "connector details/logs first. Activation needs user authorization.",
    ),
    "get_connector_logs": Op(
        "GET", "/public/v2/data_transfer/v3/connectors/{connector_type}/{id_}/try/logs",
        path_params={"connector_type": "connector_type", "id_": "id_"},
        required=("connector_type", "id_"),
        kind="log",
        help="Get existing connector activation logs. Empty logs do not prove that a "
             "connector is missing or inactive; do not run try_connector just to read logs.",
    ),
    "delete_connectors": Op(
        "DELETE", "/public/v2/data_transfer/v2/connectors",
        query_params={"ids": "ids"},
        required=("ids",),
        write=True,
        confirm=True,
        help="Delete connectors by id (ids repeat).",
    ),
    "list_sources": Op(
        "GET", "/public/v2/data_transfer/v2/connectors/sources",
        kind="list",
        help="List source schemas, including system types. Use these to inspect parameters; "
             "use the create_connector contract to determine user-creatable types.",
    ),
    # --- transfers (v4 reads, v5 writes, v2 delete) -----------------------
    "list_transfers": Op(
        "GET", "/public/v2/data_transfer/v4/transfer",
        kind="list",
        help="List transfers in the workspace. This action accepts no pagination parameters. "
             "If output is shortened, use get_transfer for a known id; do not claim completeness.",
    ),
    "get_transfer": Op(
        "GET", "/public/v2/data_transfer/v4/transfer/{transfer_id}",
        path_params={"transfer_id": "transfer_id"},
        required=("transfer_id",),
        help="Get one transfer by id.",
    ),
    "create_transfer": Op(
        "POST", "/public/v2/data_transfer/v5/transfer",
        body_field="body",
        required=("body",),
        write=True,
        help="Create a transfer; `body` = TransferInputViewModelV5. Runs now unless crontab "
             "is set. crontab is a CronViewModel object, not a cron string. Verify schedule "
             "semantics. MCP rejects fractional period values and reads back the saved schedule. "
             "If verification fails, the write may already have happened; do not repeat it automatically. "
             "Use a verified cluster_name/route; do not invent cross-cluster route names.",
    ),
    "update_transfer": Op(
        "POST", "/public/v2/data_transfer/v5/transfer/{id_}",
        path_params={"id_": "id_"},
        body_field="body",
        required=("id_", "body"),
        write=True,
        help="Update a transfer by id; `body` = TransferInputViewModelV5. For scheduling, "
             "use a CronViewModel object with a whole-number period. MCP reads back the saved schedule; "
             "on verification failure inspect get_transfer before any further write.",
    ),
    "fav_transfer": Op(
        "POST", "/public/v2/data_transfer/v4/transfer/{id_}/fav",
        path_params={"id_": "id_"},
        body_field="body",
        required=("id_", "body"),
        write=True,
        help="Mark/unmark a transfer favorite; `body` = FavoriteInputViewModel.",
    ),
    "switch_transfer": Op(
        "POST", "/public/v2/data_transfer/v4/transfer/{id_}/switch",
        path_params={"id_": "id_"},
        body_field="body",
        required=("id_", "body"),
        write=True,
        help="Toggle a transfer active/inactive; `body` = StateInputViewModel.",
    ),
    "delete_transfers": Op(
        "DELETE", "/public/v2/data_transfer/v2/transfer",
        query_params={"ids": "ids"},
        required=("ids",),
        write=True,
        confirm=True,
        help="Delete transfers by id (ids repeat).",
    ),
    # --- history (v3 reads, v2/v3 ops, v2 delete) -------------------------
    "list_history": Op(
        "GET", "/public/v2/data_transfer/v3/history",
        query_params={
            "transfer_id": "transfer_id",
            "source_name": "source_name",
            "page": "page",
            "page_size": "page_size",
        },
        kind="list",
        help="List transfer history (paginated; filter by transfer_id/source_name). "
             "Zero bytes/rows/progress are reported counters only: they do not prove "
             "that data was unchanged or identify the failure phase.",
    ),
    "get_history_status": Op(
        "GET", "/public/v2/data_transfer/v3/history/status/stream",
        query_params={"ids": "ids"},
        required=("ids",),
        kind="list",
        help="Get history entries by id (ids repeat). Status/counters alone do not "
             "establish failure cause, execution phase or whether destination data changed.",
    ),
    "get_event_logs": Op(
        "GET", "/public/v2/data_transfer/v2/events/list",
        query_params={
            "offset": "offset",
            "limit": "limit",
            "history_id": "history_id",
            "transfer_id": "transfer_id",
            "event_type": "event_type",
            "event_category": "event_category",
            "as_enum_resolved": "as_enum_resolved",
        },
        required=("offset", "limit"),
        kind="list",
        help="Get transfer event logs (needs offset+limit and either history_id or transfer_id). "
             "In a diagnosis, report cause and data impact separately. Explicitly state "
             "that data impact is unverified unless source/destination contents were checked; "
             "AccessDenied or zero counters cannot prove that nothing was written. "
             "If empty, cross-check run status and connector logs; do not infer success "
             "or a specific cause from absent events. Rerunning changes state; never "
             "recommend a rerun as a read-only diagnostic check.",
    ),
    "cancel_history": Op(
        "POST", "/public/v2/data_transfer/v2/history/cancel",
        body_field="body",
        required=("body",),
        write=True,
        confirm=True,
        help="Cancel a transfer; `body` = CancelInput.",
    ),
    "rerun_history": Op(
        "POST", "/public/v2/data_transfer/v3/history/rerun",
        body_field="body",
        required=("body",),
        write=True,
        help="Restart a transfer; `body` = RerunInput.",
    ),
    "fav_history": Op(
        "POST", "/public/v2/data_transfer/v2/history/{id_}/fav",
        path_params={"id_": "id_"},
        body_field="favorite",
        required=("id_", "favorite"),
        write=True,
        help="Mark/unmark a history entry favorite; `favorite` is a bare boolean body.",
    ),
    "delete_history": Op(
        "DELETE", "/public/v2/data_transfer/v2/history",
        query_params={"ids": "ids"},
        required=("ids",),
        write=True,
        confirm=True,
        help="Delete history entries by id (ids repeat).",
    ),
}

PARAMS: list[Param] = [
    Param("id_", str, "Entity UUID (connector/transfer/history id, depending on action)."),
    Param("transfer_id", str, "Transfer UUID (get_transfer path; filter for list_history/event logs)."),
    Param(
        "connector_type",
        str,
        "Connector type. Known values: postgresql, mssql, mysql, clickhouse, "
        "oracledb, s3amazon, s3custom, s3google, hdfs, s3mlspace, s3datahub, nfs, "
        "nfsprivate, nfsshared, s3evolution. get_connector accepts the full set; "
        "try/halt/fav/update only the custom subset (the first 8). "
        "Discover live values via list_connectors.",
    ),
    Param("ids", list[str], "List of UUIDs (repeated query) for delete/status actions."),
    Param("source_name", str, "Filter history by source_name (list_history)."),
    Param(
        "page",
        int,
        "Page number (list_history, list_connectors only, 1-based). For list_connectors, "
        "track the pages checked; a short/empty page alone does not establish completeness. "
        "For list_history, use the returned total_count to track progress.",
    ),
    Param("page_size", int, "Page size (list_history, list_connectors)."),
    Param("offset", int, "Pagination offset (get_event_logs)."),
    Param("limit", int, "Pagination limit (get_event_logs)."),
    Param("history_id", str, "History UUID filter (get_event_logs)."),
    Param("event_type", int, "Event type filter (get_event_logs)."),
    Param("event_category", int, "Event category filter (get_event_logs)."),
    Param("as_enum_resolved", bool, "Resolve enum codes to names (get_event_logs)."),
    Param(
        "favorite",
        bool,
        "Favorite flag for fav_history (sent as a bare boolean body, not an object).",
    ),
    Param(
        "body",
        dict,
        "JSON body. create_connector=ConnectorInputViewModelV3; "
        "update_connector=ConnectorUpdateViewModelV3; "
        "fav_connector/fav_transfer=FavoriteInputViewModel {favorite}; "
        "create_transfer/update_transfer=TransferInputViewModelV5; "
        "switch_transfer=StateInputViewModel; cancel_history=CancelInput; "
        "rerun_history=RerunInput {history_id}.",
    ),
]

DOMAIN = DomainTool(
    domain="data_transfer",
    name="mlspace_data_transfer",
    title="MLSpace Data Transfer",
    summary=(
        "Inspect connectors, transfers, run history and logs; manage transfers and user connectors. "
        "Capability boundary: creating connectors is supported ONLY for postgresql, mssql, mysql, "
        "clickhouse, oracledb, s3amazon, s3custom, s3google. Existing system connectors "
        "(NFS, S3 MLSpace/Evolution/DataHub, HDFS) can be inspected and used by transfers, "
        "but this tool cannot create them. Do not describe source discovery as creation support. "
        "For a service overview, briefly describe connectors, transfer configurations, and "
        "run history/logs. Diagnostic caveats belong in an actual investigation. "
        "For failed-run diagnosis, use the mlspace-diagnose-data-transfer skill when available."
    ),
    actions=ACTIONS,
    params=PARAMS,
)
