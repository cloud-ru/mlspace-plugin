---
name: mlspace-export-nfs-to-s3
description: "Export datasets, model checkpoints or job results from NFS to S3 on Cloud.ru MLSpace, including Evolution, Advanced and compatible external buckets. Uses mlspace_data_transfer; preserves source files and verifies the destination connector and prefix."
license: MIT
---

# Export NFS files to an S3 bucket

Read [the shared transfer procedure](../mlspace-transfer-data/SKILL.md). This skill
handles NFS → S3; for the reverse direction use `mlspace-stage-s3-to-nfs`.

## Establish the export destination

1. Resolve the NFS workspace, region/storage variant and connector-relative path.
   A job's region identifies relevant storage, but does not prove its output files
   exist. Obtain a verified output path instead of assuming the script directory
   contains the checkpoint. The MCP is not a general NFS file browser.
2. With `mlspace_data_transfer` actions `list_connectors` and `get_connector`,
   identify the destination by endpoint, bucket and connector ID, not name alone.
   Confirm the object prefix and intended overwrite/append/sync behavior. A local
   directory path and an S3 object prefix are different namespaces.
3. Select a destination type accepted by the endpoint's `ConnectorTypesTarget`.
   An Amazon/Google source connector type is not automatically accepted as a target;
   use a verified destination-compatible connector (often `s3custom`). Reuse an
   existing system Evolution connector where available. For a new custom connector,
   use the current create_connector schema and an authorized credential workflow;
   do not ask for keys in chat or derive S3 credentials from unrelated Registry secrets.
4. For Advanced, check the migration guide's SSE-KMS prerequisite if relevant.
   Never disable encryption on an existing bucket automatically. Ask for a destination
   compatible with the documented transfer path or resolve the prerequisite first.
5. Confirm `cluster_name` for the selected NFS storage using the endpoint schema and
   verified context. Do not hardcode a tutorial's cluster or infer it from the bucket
   endpoint. Apply the shared transfer file-count limits.

## Run and verify

Create/update the requested rule using the actual connector types and
`query.source`/`query.destination` paths. For a recurring export, retain the shared
schedule checks; recurrence does not imply full synchronization or deduplication.

Read back the rule and inspect its matching history/status. Report API-reported
bytes/objects separately from an independent destination-content check. Do not claim
that a backup is restorable, checksums match, or the model artifact is valid solely
because the transfer status is successful. Keep the NFS originals unless deletion
was separately requested and verified as safe.

## Sources

- [Transfers between S3 and NFS](https://cloud.ru/docs/aicloud/mlspace/concepts/guides/guides__dc/data-catalog__data-processing__move-data-s3-nfs)
- [Destination connector types](https://cloud.ru/docs/aicloud/mlspace-cli/topics/references__transfer-create)
- [Advanced destination prerequisites](https://cloud.ru/docs/aicloud/mlspace/concepts/tutorials/data-use/data-use-migration-from-s3-to-adv)
