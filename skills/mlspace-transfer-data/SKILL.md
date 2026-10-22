---
name: mlspace-transfer-data
description: "Plan Cloud.ru MLSpace data movement when the route is not yet chosen, or apply the shared create/update/schedule/verification procedure for mlspace_data_transfer. Route-specific skills cover NFS to NFS, NFS to S3, S3 to NFS, S3 to S3 and database exports."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Transfer data between supported sources and destinations

Use `mlspace_data_transfer` to prepare and, when requested, create/update a transfer.
An explanation or route feasibility question needs reads only. Creation without a
schedule starts a transfer immediately; it is not a dry run. Respect read-only mode.

For a known route, read only the matching procedure:

| Requested movement | Procedure |
| --- | --- |
| NFS → NFS, including other regions/workspaces | [NFS transfer](../mlspace-transfer-nfs-to-nfs/SKILL.md) |
| NFS → S3: results, checkpoints, datasets | [S3 export](../mlspace-export-nfs-to-s3/SKILL.md) |
| S3 → NFS: staging data for jobs | [NFS staging](../mlspace-stage-s3-to-nfs/SKILL.md) |
| S3 → S3, including Evolution/Advanced | [Bucket migration](../mlspace-migrate-s3-to-s3/SKILL.md) |
| Database → supported storage | [Database export](../mlspace-export-database-data/SKILL.md) |

If arriving from that procedure, continue below without reloading it. Do not read
all sibling skills for one request.

## Resolve both ends

1. Identify the workspace where the rule will live (`target`), source and destination
   connectors, both paths, and the user's copy/append/sync intent. For each NFS end,
   identify its workspace and exact region/storage variant separately. The same path
   in two regions need not refer to the same files. Do not silently use the current
   workspace for a destination in another workspace.
2. Find connectors with `list_connectors(page=1, page_size=20)`, paging as needed,
   and confirm selected IDs/types with `get_connector(connector_type, id_)` in the
   relevant connected workspace. Keep `target` explicit for multi-workspace work.
   A system NFS connector can be absent from the list: the intended workspace ID
   can be checked as a candidate with `get_connector(connector_type="nfs", id_=...)`.
   Use it only after confirming its identity. Do not treat every workspace ID as
   a connector ID or probe unrelated workspaces.
3. Reuse system connectors (`nfs`, `nfsprivate`, `nfsshared`, etc.). `list_sources`
   describes types/parameters; it does not authorize creating every listed type.
   Only create a custom connector when requested, using the create_connector schema.
   Do not request connector passwords in chat or expose them in the answer.

## Establish the route before writing

- Read the configured endpoint's current transfer schema: `TransferInputViewModelV5`,
  `TransferClusterEnum`, `TransferMultiClusterEnum`, `ConnectorTypes` and
  `ConnectorTypesTarget`. These are in `/public/v2/openapi.json`; use schemas already
  exposed by the connected tools or retrieve that public document if available.
  If unavailable, use verified task context or ask for the missing route details.
  A bundled schema is a snapshot, not evidence of current access or reachability.
- A source type is not automatically allowed as a destination. Check both enums.
- For NFS → NFS, regions can exchange data except SR008: its NFS transfers stay
  within SR008 (`nfs2`, `nfs3`, `fs2`). This service-team clarification applies only
  to NFS → NFS, not S3 or database routes. See the NFS procedure for examples.
- For cross-cluster NFS → NFS, establish the exact `cluster_name` from the current
  `TransferMultiClusterEnum`, a verified existing rule for that direction, or the
  service team. Do not concatenate labels or infer reverse encodings. Missing
  enum entries alone do not establish that a direction is unsupported. Two NFS
  variants within one region may also need a directed route. A shared connector ID
  does not make two locations identical; check the route and paths.
- Enum membership documents a value, not permission or a working data path.
  Verify both connectors and available access information. If a route
  cannot be established, explain what is missing before creating anything.
  Do not substitute another region or start a two-hop copy via S3 without an agreed
  intermediate destination and verification of both legs.
- `try_connector` is for supported custom connectors and changes state. Do not
  apply it to system NFS connectors or use it as a read-only route check.

## Relevant NFS limits

The official documentation linked below was checked on 2026-10-13. Recheck it
when the requested scale, storage type or deployment makes these limits relevant.

- The documented per-transfer threshold is 400,000 files. For append-new and full
  synchronization, count source and destination files together. The prose and
  example disagree at exactly 400,000: do not promise that boundary succeeds;
  confirm it with the service team or propose smaller batches with the user.
- NFS also has namespace file quotas. Byte capacity alone does not establish room
  for the copy. The MCP does not provide a general NFS directory walk: do not invent
  file counts or available capacity. Use available evidence, or explain which size
  check remains unverified when it affects the requested transfer.
- For a Personal Directory destination, documentation requires updating workspaces
  created before 2024-11-01. Check whether it is already updated; do not update a
  workspace automatically as part of copying data.

These are task-specific checks, not a general warning block for every answer.

## Create, update and verify

1. For an existing rule, use `get_transfer(transfer_id)` or locate it with
   `list_transfers`. A shortened list does not establish that no matching rule
   exists. Do not create a duplicate to work around an unknown ID.
2. Build the write schema explicitly; do not POST the GET response unchanged:
   `name`, `cluster_name`, `connector_id`, `destination_connector_id`,
   `connector_type`, `destination_connector_type`, `strategy`, `favorite`,
   `query={source, destination}`, and optional `description`/`crontab`.
   Use the actual connector type returned for each end, not a guessed generic NFS type.
3. Choose `write_all`, `append_new` or `sync_all` from the requested behavior and
   verified service semantics. A recurring schedule alone does not determine the
   strategy. For scheduling use a `CronViewModel` object, not a cron string; establish
   period units and timezone semantics before converting a human recurrence.
4. Summarize source → destination, region/storage variants, strategy and schedule.
   Obtain missing intent before writing, particularly destructive sync behavior.
   Use `create_transfer(body=...)` or `update_transfer(id_, body=...)` as requested.
   If a write times out, check whether the rule/run already exists before retrying.
5. Read back with `get_transfer`, comparing both ends, route, strategy and schedule.
   Use `list_history(transfer_id, page=1, page_size=20)` and
   `get_history_status(ids=[history_id])` for the matching run. A saved rule or future
   scheduled run is not a completed copy. Do not assume list ordering proves which
   run belongs to this request; compare IDs and times.
6. On failure, use `get_event_logs(history_id, offset=0, limit=50,
   as_enum_resolved=true)` or the `mlspace-diagnose-data-transfer` skill. Zero counters
   or AccessDenied do not prove that the destination was unchanged. Do not rerun
   merely to diagnose a failure.

Answer in the user's language: what was configured, where data goes, rule/run ID,
and observed status. Keep tool-specific cautions internal unless they block this
task or the user asks for diagnosis. For the narrower S3 → NFS staging workflow,
the `mlspace-stage-s3-to-nfs` skill is also available.

## Official references

- [Cross-region and cross-workspace NFS transfers](https://cloud.ru/docs/aicloud/mlspace/concepts/faq__data-catalog)
- [Creating a rule and Personal Directory prerequisites](https://cloud.ru/docs/aicloud/mlspace/concepts/guides/guides__dc/data-catalog__data-processing__create-transfer-rule)
- [File-count limits](https://cloud.ru/docs/aicloud/mlspace/concepts/overview__limitations)
