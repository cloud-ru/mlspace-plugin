---
name: mlspace-transfer-nfs-to-nfs
description: "Copy or sync NFS data on Cloud.ru MLSpace between regions, storage variants or workspaces, including Personal Directory and shared NFS. Uses mlspace_data_transfer; verifies both locations and the directed route before writing."
license: MIT
---

# Copy NFS data between storage locations

Use this for NFS → NFS, including regional migration and copying a dataset to
another workspace. Read [the shared transfer procedure](../mlspace-transfer-data/SKILL.md)
for discovery, write authorization, limits, scheduling and verification, then apply
the checks below. Tools must be connected before carrying out the operation.

## Resolve the two locations

- Ask only for missing source/destination workspace, region/storage variant and path.
  Confirm which workspace owns the transfer rule (`target`). Never change `target`
  implicitly halfway through a multi-workspace operation.
- Inspect both connectors with `mlspace_data_transfer` actions `list_connectors`
  and `get_connector`. Use their actual `nfs`, `nfsprivate` or `nfsshared` type.
  System connectors are reused, not created as custom connectors.
- A notebook's `/home/jovyan/...` mount is not automatically the transfer-service
  path. Establish the connector-relative path from the selected storage's metadata,
  a verified existing rule or the user's copied NFS object path; do not strip or
  prepend path segments by guesswork.
- The same workspace/connector ID can address different regional NFS locations.
  Compare workspace, storage variant, route and path together. Conversely, copying
  onto the exact same storage/path is not a migration; resolve that ambiguity before
  creating a rule. Check overlapping source/destination folders when synchronizing.

## Match the exact direction

Service-team clarification (2026-10-19): NFS → NFS transfers are supported between
regions except SR008, which is isolated. SR008 NFS can transfer only within SR008,
including its `nfs2`, `nfs3` and `fs2` storage variants. This restriction concerns
NFS → NFS only; do not apply it to S3 or database transfers.

For cross-cluster or cross-storage transfer, establish the exact directed
`cluster_name` value using the configured endpoint's current `TransferMultiClusterEnum`,
a verified existing rule for the same direction, or confirmation from the service
team. Never build a route by joining UI labels with `->` or reversing a known value.
For a same-storage copy, use the applicable single-cluster value. Missing enum
entries do not prove that a non-SR008 direction is unsupported; the schema can lag
behind the service. Schema membership does not establish access or reachability.

Examples: `sr008.nfs2->sr008.fs2` stays inside the isolated region;
`sr008.nfs2->sr009` crosses its boundary and must not be submitted for NFS → NFS.
An absent `sr009->christofari-1` entry needs route confirmation, not a claim that
the service cannot do the copy.

If the exact value remains unknown, keep the task pending at route selection and
state what needs confirmation. A two-leg copy via S3 requires an agreed intermediate
location and verification of both legs; it is not an automatic fallback.

Before writing, apply the shared file-count and namespace quota checks. For an
individual notebook's Personal Directory, establish whether the documented
pre-2024-11-01 workspace update prerequisite is satisfied. Do not update the
workspace, change quota or create a notebook to make the copy work automatically.

## Complete the requested copy

Use `create_transfer` or `update_transfer` with the confirmed connector IDs,
types, direction, paths and strategy. Follow the shared readback/history checks.
Report both storage locations and the matching run status. A successful copy does
not authorize deleting the original data, and a scheduled rule is not completed
data movement.

## Sources

- [NFS transfers across regions and workspaces](https://cloud.ru/docs/aicloud/mlspace/concepts/faq__data-catalog)
- [Rule creation and Personal Directory prerequisites](https://cloud.ru/docs/aicloud/mlspace/concepts/guides/guides__dc/data-catalog__data-processing__create-transfer-rule)
- [Transfer limits](https://cloud.ru/docs/aicloud/mlspace/concepts/overview__limitations)
