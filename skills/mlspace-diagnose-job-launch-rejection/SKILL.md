---
name: mlspace-diagnose-job-launch-rejection
description: "Diagnose a rejected job launch on Cloud.ru MLSpace. Why was my job submit refused (no job created): no quota vs no queue vs PAYG vs wrong name. Uses the mlspace_allocations, mlspace_queue_inspect, mlspace_resources, mlspace_workspaces tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Diagnose a rejected job launch

Why was my job submit refused (no job created): no quota vs no queue vs PAYG vs wrong name.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `region` — Region the submit targeted.
- `instance_type` — Instance type that was rejected.
- `allocation_name` — Allocation named in the error, if any.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: explain a rejected POST /jobs when no job was created. Read actions only.
1. Preserve the submitted target, region, instance_type and provider error. Distinguish
   regional type mismatch, allocation eligibility, queue configuration and visibility;
   do not cycle through types or submit a paid probe to infer quota.
2. workspaces allocations in that target -> observed allocation IDs and region_key.
   workspaces allocation_queues for the relevant allocation. Empty queues are ambiguous
   (feature disabled, wrong ID, no visible queues); they do not prove GPU unavailable.
3. allocations get and allocations list_instance_types where accessible. Report actual
   resources_status/instance-type observations. Zero visible nodes or a 403/405 is not
   complete evidence about PAYG eligibility, all hardware or all workspace capacity.
4. mlspace_resources configs (cluster_type=MT) -> compare the requested region key and
   instance_type against the actual regional catalogue and compatible images.
5. For a known queue, mlspace_queue_inspect(queue_id, target) -> queue membership,
   pending demand across priorities, observable workloads and candidate node load.
   Keep failures and incomplete cross-workspace visibility explicit.
Output: the provider rejection, supporting observations, unresolved checks and next
step. A missing allocation/queue association may require the workspace owner/admin;
name the exact association to verify. Catalog presence does not prove launchability,
and a notebook is not an established GPU-job workaround without its own eligibility
and capacity evidence. Verify physical hardware from observed device facts, not labels.
