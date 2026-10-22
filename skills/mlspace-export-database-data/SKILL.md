---
name: mlspace-export-database-data
description: "Export data from PostgreSQL, MySQL, MS SQL, Oracle DB or ClickHouse into an allowed storage destination through Cloud.ru MLSpace. Uses mlspace_data_transfer; resolves the extraction query and format without promising database replication or database-to-database support."
license: MIT
---

# Export database data to storage

Read [the shared transfer procedure](../mlspace-transfer-data/SKILL.md). This is
database → supported storage export. Database source support does not establish
database destination support, CDC, transaction-consistent snapshots or live replication.

## Establish the extraction contract

1. Identify the DB connector, database/schema/table or approved extraction query,
   destination connector/path, intended file format and schedule. Resolve whether
   the user wants a full extract or a bounded incremental selection. Do not invent
   table names, watermarks, partitions or output formats.
2. Use `mlspace_data_transfer` actions `list_sources`, `list_connectors` and
   `get_connector`. Match the actual `postgresql`, `mysql`, `mssql`, `oracledb` or
   `clickhouse` connector. Saved success is not proof of current connectivity.
   Do not activate a connector with `try_connector` just to perform a read-only check.
3. Inspect the configured endpoint's source schema and a verified existing rule
   where available. Older client_lib documentation shows SQL `query`/`schema` under
   connector parameters; that alone does not establish their placement or semantics
   in the current transfer API. Establish how `query.source` represents this source
   before creating the rule. If the contract or format is unclear, request a known
   working rule or service clarification instead of trying arbitrary SQL/body fields.
4. Prefer an existing least-privilege DB connector. If a new connector is requested,
   use its current schema and secure credential workflow; do not ask for a password
   in chat, broaden DB grants or edit a shared connector's query automatically.
   Restrict the extraction to the requested read-only data selection.
5. Check destination support independently (`ConnectorTypesTarget`): select an
   accepted S3/NFS/storage connector, not another database merely because it appears
   in source discovery. Establish the execution cluster and any NFS variant/path.

## Apply source-specific prerequisites only when relevant

- For Advanced RDS SQL Server, Cloud.ru documents network configuration and allowing
  the connector service's source IPs. Obtain the current IP list from support;
  do not substitute the developer machine's public IP or open access to all networks.
  Creating RDS instances or editing security groups/NAT is not part of an export
  unless separately requested.
- For ClickHouse, the service documentation recommends `readonly=2`, or an appropriate
  `changeable_in_readonly` configuration when using `readonly=1`. Treat this as a
  prerequisite to resolve with the DB owner; never change server settings automatically.
- Apply current driver/version prerequisites from the source documentation if a
  compatibility question arises. Do not turn an old supported-version list into
  a blanket claim that every later DB version fails.

## Run and assess the export

Use the shared write/readback/history procedure after the extraction contract is
established. `append_new` is a service transfer strategy, not evidence that SQL rows
are deduplicated or that a changing database is captured consistently. A recurring
full extract is not automatically incremental extraction.

Report the rule/run ID, destination, observed status and reported rows/bytes.
Distinguish those counters from source-to-destination reconciliation. For failure,
use matching event evidence; do not infer a missing table or unchanged destination
from a generic error or zero counters.

## Sources

- [Supported DB sources and ClickHouse prerequisites](https://cloud.ru/docs/aicloud/mlspace/concepts/data-catalog__data-processing__data-migration)
- [Connector configuration](https://cloud.ru/docs/aicloud/mlspace/concepts/guides/guides__dc/data-catalog__data-processing__data-migration__create-connector)
- [Advanced SQL Server connectivity](https://cloud.ru/docs/aicloud/mlspace/concepts/guides/guides__advanced/guides__sql_adv__connect)
- [Older client_lib SQL parameter reference](https://cloud.ru/docs/aicloud/mlspace/concepts/client-lib__copy-to-nfs)
