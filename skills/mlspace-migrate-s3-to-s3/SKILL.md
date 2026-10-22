---
name: mlspace-migrate-s3-to-s3
description: "Copy or sync between S3 buckets through Cloud.ru MLSpace Data Transfer, including external S3 to Evolution/Advanced and cross-project buckets. Uses mlspace_data_transfer; checks endpoint/bucket identity and destination-specific prerequisites."
license: MIT
---

# Migrate data between S3 buckets

Read [the shared transfer procedure](../mlspace-transfer-data/SKILL.md). Use this
for object-storage → object-storage, without silently introducing NFS as a staging hop.

## Resolve two independent S3 ends

1. Establish source and destination endpoint, bucket, prefix and owning project/account.
   Equal bucket names on different endpoints do not identify the same storage.
   Resolve identical or overlapping prefixes on the same bucket before synchronizing.
2. Use `mlspace_data_transfer` actions `list_connectors` and `get_connector` to reuse
   matching connectors. Confirm IDs and actual types separately. Cross-project
   migration can require a custom connector for the other project's bucket;
   availability of one project's system connector does not establish access to another.
3. Check source and destination enums separately. The current target schema includes
   `s3custom` and `s3evolution`, but not every S3 source type. Select a verified target
   connector; do not relabel an existing connector to force a match.
4. Evolution's documented migration path can use a Custom S3 destination. Obtain
   its precise endpoint and S3-compatible authentication through the approved secret
   workflow; do not guess credential formatting from an IAM Key ID alone. Advanced
   uses its bucket endpoint and OBS AK/SK. Reuse existing connectors without reading
   or displaying their secret values.
5. The Advanced migration guide requires a destination compatible with its SSE-KMS
   restriction. Do not automatically disable bucket encryption. Resolve this with
   the user or choose an agreed compatible destination. Recommended chunking in
   that tutorial is preparation guidance, not a universal API object-size limit.

## Configure a copy, not an implicit cutover

- Use the actual endpoint's transfer schema. Older tutorials include `legacy` and
  a fixed cluster: do not copy those into `TransferInputViewModelV5` blindly. Even an
  S3 → S3 rule needs a verified execution `cluster_name`; no NFS end is implied.
- Preserve exact object-prefix semantics. Multiple source prefixes, where supported,
  go into the API's `query.source` list; the CLI's comma syntax is not proof that the
  API wants a comma-joined string.
- Confirm copy/append/sync intent and apply shared file-count checks. Neither a
  scheduled copy nor `append_new` proves bidirectional replication or CDC.
- Create/update once, then read back and track the matching run. On an uncertain
  write result, locate the existing rule/run before retrying. Report any unverified
  destination-content checks explicitly.
- Do not delete the source bucket, rotate keys, switch application endpoints or
  perform a cutover as an implicit part of migration. Those are separate operations.

## Sources

- [External S3 to Evolution](https://cloud.ru/docs/aicloud/mlspace/concepts/tutorials/data-use/data-use-migration-from-s3-to-evo)
- [External S3 to Advanced](https://cloud.ru/docs/aicloud/mlspace/concepts/tutorials/data-use/data-use-migration-from-s3-to-adv)
- [Cross-project S3 transfers](https://cloud.ru/docs/aicloud/mlspace/concepts/faq__data-catalog)
