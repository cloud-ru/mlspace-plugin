---
name: mlspace-stage-s3-to-nfs
description: "Stage S3 data to NFS (one-off or nightly) on Cloud.ru MLSpace. Copy/sync S3 -> NFS for a GPU job, optionally on a schedule. Write action. Uses the mlspace_data_transfer tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Stage S3 data to NFS (one-off or nightly)

Copy/sync S3 -> NFS for a GPU job, optionally on a schedule. Write action.

Read [the shared transfer checks](../mlspace-transfer-data/SKILL.md) for file-count
limits, connector permissions and destination prerequisites, then follow this
S3-specific procedure. Do not reload this file when following the shared router.

For NFS -> NFS, cross-cluster/cross-workspace transfers, other connector routes,
or checking NFS file-count limits, use `mlspace-transfer-data` from this plugin.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `source_hint` — Which S3 connector/bucket.
- `destination_hint` — Which NFS/cluster connector.
- `source_path` — Path within the source.
- `destination_path` — Path within the NFS share.
- `schedule` — Desired recurrence, start time and timezone, or a verified CronViewModel object.
- `transfer_name` — Name for the transfer.
- `cluster_name` — Target cluster name (required, not auto-derivable).
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: copy/sync S3 -> NFS, optionally on a nightly schedule (write actions;
refused under MLSPACE_READONLY=true).
1. data_transfer list_connectors with page=1, page_size=20; continue as needed to
   locate the requested connectors. SOURCE: a verified object-storage connector
   (e.g. s3amazon/s3custom/s3google/s3evolution, as supported by the endpoint).
   DEST: an NFS/cluster connector with its actual nfs/nfsprivate/nfsshared type.
   Capture both connector ids. Do not create a custom s3evolution or NFS connector;
   reuse system connectors or use an authorized custom S3 connector where appropriate.
   Reuse existing system connectors rather than attempting to create them. A system
   NFS connector may be absent from list output. For the selected destination
   workspace, its workspace ID can be tested as a candidate via get_connector
   (connector_type="nfs", id_=workspace ID); use it only after verifying the returned
   connector belongs to the intended destination. Do not probe unrelated workspaces.
2. cluster_name is required. Use a verified value from task context or consult the
   configured endpoint's /public/v2/openapi.json, components.schemas.TransferClusterEnum
   and TransferMultiClusterEnum. These describe accepted names, not current reachability
   or access rights. Do not infer API names from UI labels or construct routes by
   concatenating cluster names. If the requested route is absent, explain that the
   direct route is not established; do not launch a multi-hop transfer without an
   agreed intermediate destination and confirmation of both supported legs.
   If names or destination remain ambiguous, ask the user.
3. Read existing connector details/logs first. Only for a supported custom S3
   connector, and when activation is authorized, use try_connector to check
   connectivity and activate it. Never use it for a system s3evolution connector
   or merely to read logs; get_connector_logs reads the existing logs.
4. Ensure source_path + destination_path are known (ask if absent). Choose the
   strategy from the user's overwrite/append/sync intent; recurrence alone does
   not determine it. For a schedule, confirm the time and timezone and build
   crontab as a CronViewModel JSON object (start_at, time, weekdays, monthdays,
   period as applicable), never a cron-expression string. Verify the service's
   field semantics before converting the requested recurrence; the schema alone
   does not establish period units or timezone behavior. Do not guess fractional
   periods or silently substitute a different interval. Read back the saved
   schedule and compare it with the requested one before claiming success.
   For a one-off run omit crontab; creation starts the transfer immediately.
5. list_transfers; if one matches this source->dest and the user means "edit", capture
   its id for update instead of create.
6. create_transfer (body=TransferInputViewModelV5: name, cluster_name, connector_id,
   destination_connector_id, connector_type, destination_connector_type, strategy,
   favorite, query={source, destination}, optional crontab/description)
   OR update_transfer(id_, full body).
   To just enable/disable: switch_transfer(id_, body={"active": true|false}).
7. Verify: get_transfer(transfer_id) to read back the rule; list_history(transfer_id,
   page=1, page_size=20) to identify the matching run by IDs and times rather than
   list order; get_history_status(ids=[history_id]) to check that run's status.
Output: source and destination, the created/updated transfer id, schedule with
timezone, and verified run status. A future scheduled run has not succeeded yet.
Before writing, summarize the destination, strategy and schedule for the user;
do not dump the request body or connector credentials. Report any unverified
schedule or incomplete result plainly; include API details only for diagnosis.
