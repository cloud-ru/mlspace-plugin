---
name: mlspace-diagnose-data-transfer
description: "Diagnose a failed / partial data transfer on Cloud.ru MLSpace. Did my data transfer actually fail / land incompletely, and why. Uses the mlspace_data_transfer tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Diagnose a failed / partial data transfer

Did my data transfer actually fail / land incompletely, and why.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `transfer_id` — Transfer id (or give a name).
- `transfer_name` — Transfer name to look up.
- `expected_rows` — Rows you expected, to judge completeness.
- `act` — If true, may rerun a failed run (after confirm).
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Final-answer contract:
- **Status and reported counters**: observations returned by the API.
- **Failure cause and phase**: use explicit matching log evidence; otherwise unknown.
- **Data impact**: unknown until an independent source/destination contents check
  establishes it. Even a known cause such as AccessDenied at PutObject does not
  establish whether other writes in the run already happened. A known cause and
  unknown data impact can both be true. Zero counters are not that contents check.

The tool's `_mcp_evidence` describes the limits of those observations, not an API
defect or a separate user-facing warning. Never translate zero counters into
"the run failed before transfer started" or "nothing was read/written".

Goal: determine whether a data transfer truly failed / was partial, and why.
1. Resolve the transfer: if transfer_id given use it, else data_transfer list_transfers
   and match transfer_name.
2. data_transfer list_history (transfer_id, page=1, page_size=20 — this endpoint uses
   page/page_size). Take the latest run's history_id + metrics (size_bytes, rows_count,
   progress, status).
3. data_transfer get_history_status (ids=[history_id]) for the authoritative status
   (in_progress|success|failed|cancelled).
4. If failed/cancelled, data_transfer get_event_logs (history_id, offset=0, limit=50,
   as_enum_resolved=true) for the decoded reason.
   If events are empty, retain the failed/cancelled status and inspect the matching
   history entry and existing connector logs. Do not call try_connector merely to
   read logs: it can activate the connector. Treat a value such as not_found as an
   observation, not proof of a missing SQL table; that cause needs supporting logs
   or a separate authorized check. Never invent the reason for failure.
5. Compare rows_count/size_bytes vs the user's expectation to judge "partial".
   These are reported counters, not an audit of source/destination contents.
   Even zero bytes/rows/progress does not prove the run never started, identify
   its failure phase, or prove data was unchanged. Do not claim any of those
   without independent evidence. Distinguish "this diagnostic made no changes"
   from "the earlier transfer made no changes". A connector's saved success
   status is not a fresh connection test and does not establish current health.
Output: status + hard numbers (rows/bytes/progress) + completeness verdict + decoded
event reasons; separate "genuinely failed" from "clean exit / fewer rows than expected".
When failure logs are empty, conclude that the cause and data impact are unknown.
Report the counters as observations; do not turn them into a narrative about which
execution phase failed. Suggest the next read-only check instead of inventing a cause.
If no evidence identifies a failure phase, do not list speculative phases as the
diagnosis. Rerunning is a write that may change destination data: never include it
in a list of read-only checks or propose it when the user asked for read-only diagnosis.
Only if act=true: rerun_history (body={history_id}) for a failed run, after confirming.
For reasoning: status is only a 4-value enum; "finished but data looks wrong" is
inferred from the numbers, not a dedicated field. In the answer, explain only the
evidence and remaining uncertainty relevant to this run, not a general API audit.
