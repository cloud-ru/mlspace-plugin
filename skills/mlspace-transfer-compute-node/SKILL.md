---
name: mlspace-transfer-compute-node
description: "Inspect and transfer a compute node between queues on Cloud.ru MLSpace. Check shared workload before recommending or moving a compute node. Write only on request. Uses the mlspace_contexts, mlspace_notebooks, mlspace_queue_inspect, mlspace_queues tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Inspect and transfer a compute node between queues

Check shared workload before recommending or moving a compute node. Write only on request.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `source_queue_id` **(required)** — Source queue ID.
- `destination_queue_id` — Destination queue ID.
- `destination_target` — Destination connected workspace ID or unambiguous name.
- `node_name` — Compute node name; omit to inspect source candidates.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: inspect and, when requested, move a COMPUTE NODE between queues. A Jupyter
server is a workload, not the node to move. Never substitute notebooks pause/modify.
1. Resolve the source queue_id/target and destination_queue_id/destination_target
   within mlspace_contexts. If placement is delegated, compare eligible destinations
   and report the choice without another workspace question.
2. mlspace_queue_inspect(queue_id=source_queue_id, target=target,
   candidate_nodes=[node_name]) (omit candidate_nodes to inspect source queue nodes).
   Leave targets omitted to inspect the fixed selected set, unless the user explicitly
   restricts observation. Examine checked_scope/failures/complete, queue/allocation
   association, candidate membership, allocation node facts and node loads alongside
   jobs/notebooks/pods and all pending priorities. Pending is demand, not running load;
   paused notebook configuration alone is not capacity that can be freed.
3. mlspace_queues get (queue_id=destination_queue_id, target=destination_target) and
   mlspace_queues instance_types (queue_id=destination_queue_id,
   target=destination_target) to check the destination/allocation compatibility.
   Match concrete identities; a friendly workspace name or request context does not
   establish owner identity. Repeated shared views cannot be summed.
4. Report candidates, observed competing workload, freshness, failed/unread scopes
   and unknown cross_workspace_coverage. Even complete=true does not prove all shared
   workload is visible. If visibility is incomplete, do not call the move impact-free;
   explain the remaining check before recommending a candidate as idle.
5. If the user requested execution and placement/node are resolved, use supported
   mlspace_queues add_nodes (target=destination_target, queue_id=destination_queue_id,
   body={"nodes":[node_name], "force_withdrawal":false}). Follow the action's existing
   write/confirm rules; never add a new mandatory workspace confirmation. Do not
   silently escalate force_withdrawal on refusal; explain its consequences and use it
   only when forced withdrawal was explicitly authorized. There is no remove_nodes
   action to invent. Provider acceptance remains subject to queue/allocation policy.
Example calls (replace these illustrative IDs with resolved addresses):
```json
{"tool":"mlspace_queue_inspect","arguments":{"queue_id":"source-queue","target":"workspace-a","candidate_nodes":["node-a"]}}
```
```json
{"tool":"mlspace_queues","arguments":{"action":"add_nodes","target":"workspace-b","queue_id":"destination-queue","body":{"nodes":["node-a"],"force_withdrawal":false}}}
```
6. Read source and destination queue membership and relevant node loads again. Report
   actual results and unresolved impact; HTTP success alone does not prove idle load
   or uninterrupted workloads. For an inspection-only request, stop before add_nodes.
