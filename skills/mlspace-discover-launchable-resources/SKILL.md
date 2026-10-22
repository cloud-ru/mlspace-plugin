---
name: mlspace-discover-launchable-resources
description: "Discover what you can launch on Cloud.ru MLSpace. Valid instance types, base images and free GPUs in a region before launching. Uses the mlspace_allocations, mlspace_docker_registry, mlspace_queue_inspect, mlspace_resources, mlspace_workspaces tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Discover what you can launch

Valid instance types, base images and free GPUs in a region before launching.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `region` — Target region.
- `cluster_type` — MT (jobs/notebooks) or INF (inference).
- `allocation_id` — Allocation id, if known; access varies by endpoint.
- `include_custom_images` — Also list registry images.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: list compatible instance types/images and observed capacity before launching.
1. Read mlspace_contexts and resolve the task's workspace. Use the same explicit
   target in every call; show its name/ID and the requested region. Clarify only
   genuine ambiguity; a singleton does not need an extra selection question.
2. Call mlspace_workspaces action=allocations for that target, then
   mlspace_workspaces action=allocation_queues for the chosen allocation_id.
   Use exact IDs/names, region_key, active queues and default_for_job. Do not use
   the personal-account mlspace_allocations action=list as this workspace inventory.
   Preserve distinct allocation IDs even when names look similar. An allocation's
   name is not evidence of its region or billing mode. Label filtered/partial lists.
3. Call mlspace_resources action=instance_types with region, target and the selected
   allocation_name/queue_name when applicable. Omitting these names uses defaults;
   it does not select PAYG. For allocation/queue detail, use mlspace_allocations
   action=list_instance_types or mlspace_queues action=instance_types with their IDs.
   Keep scopes distinct: allocation-wide data cannot override a queue restriction.
   Empty/error results leave availability unresolved, not permission to substitute
   the general catalog. Zero availability is not by itself proof of unsupported type.
4. Read mlspace_resources action=configs (cluster_type=MT for jobs/notebooks;
   INF for inference), with the same region and target. Join scoped candidate keys
   with actual instances_types specifications and datahub_images/custom_images.
   Compare GPU model/memory/count, CPU/RAM and workers, not GPU count alone.
   Include zero-GPU cpu.* types. An empty images list does not establish a compatible
   pairing; do not recommend an arbitrary image. Missing specifications stay unknown.
   This allocation/queue flow applies to jobs/notebooks; for inference establish
   its own service eligibility rather than treating MT availability as INF capacity.
5. If capacity inspection is requested, mlspace_resources nodes and
   mlspace_resources nodes_load provide regional node/load facts. For a known queue,
   mlspace_queue_inspect(queue_id, target) examines node facts, job/notebook/pod
   observations and pending priorities. Available GPU readings are point-in-time;
   they do not reserve resources or guarantee scheduling. Keep paused notebooks
   visible; their configured GPUs are not active consumption. Do not sum shared
   views or claim complete cross-workspace coverage. Missing readings remain unknown.
6. Optionally docker_registry current_registry -> list_repos -> list_tags for images.
   Registry membership alone does not prove compatibility with the selected type.
Output: scoped choices, compatible images, observed capacity when requested, and
unresolved eligibility. Present defaults explicitly and ask only for missing choices.
For job submission use the sibling mlspace-launch-training-job skill and its full
configuration preview; this discovery workflow performs no writes.
