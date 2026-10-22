---
name: mlspace-launch-training-job
description: "Prepare and launch a Cloud.ru MLSpace training or binary job. Resolve workspace, allocation, queue, available instance type and compatible image before confirming submission."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Launch a training job

Use the connected `mlspace` MCP tools. If unavailable, explain what is missing;
do not invent resource choices or substitute a direct API write.

## Inputs

Reuse the user's explicit choices and established task context. Discover available
options before asking the user to choose. Ask only about missing decisions; group
related questions and offer actual API-backed options, not an exhaustive form.

Resolve workspace, region, allocation/queue or verified default placement,
instance type (GPU model/count/memory, CPU/RAM), worker count, image with exact tag,
job type and command/script. For training, also resolve relevant data/code paths,
arguments, environment and output/checkpoint locations. These are not all mandatory
API fields: a simple binary test does not require a training dataset or checkpoint.
Never ask the user to paste secrets into chat or echo secret environment values.

## Procedure

Apply these gates in order; resource discovery is not permission to skip a gate:

1. **Workspace gate:** multiple candidates without the user's choice/delegation →
   ask for the workspace and stop this turn. Do not pick the one with resources.
2. **Placement gate:** multiple suitable allocations/active queues without the
   user's choice/delegation → present those choices and stop this turn. A default
   flag can mark a recommendation, but does not authorize selecting it silently.
3. **Availability gate:** get the scoped available instance types before configs.
   Record their exact keys as the candidate set. A 403 or other failed scoped check
   means **no verified candidate set**: tell the user availability could not be
   checked and stop. Do not use configs as a fallback, produce a ready-to-run
   configuration, or ask to confirm a launch while this check is unresolved.
   After success, configs only supplies specs/images **for those candidate keys**.
   Never offer extra configs-only types, even as alternatives. If one candidate
   remains, don't ask the user to choose between it and unavailable catalog types.


### 1. Make the workspace visible

**Stop point before resource discovery:** call mlspace_contexts first. If it returns
multiple workspaces and the user has not selected one or explicitly delegated
workspace selection, ask which workspace to use and END THIS TURN. Do not inspect
allocations to choose on their behalf. An empty allocation list in one workspace
is not authorization to switch to another. A request for a region or GPU count
alone does not delegate workspace selection. Resume resource discovery only after
the user's answer. Example: "В каком воркспейсе готовим запуск: qa-alpha или qa-beta?"

Resolve `target` to a configured workspace UUID and use it
consistently for every discovery and submission call. Show workspace name, ID and
region to the user. If several workspaces are possible and neither the task nor a
user-delegated selection criterion resolves them, ask before further discovery.
A singleton needs no redundant choice question, but still show it. A previously
inspected workspace or an example is not an instruction to place a new job there.

### 2. Discover placement in that workspace

Call mlspace_workspaces action=allocations with the resolved `target` (workspace_id
is auto-filled). This is the workspace allocation list; do not substitute
mlspace_allocations action=list, whose endpoint is personal-account-only and is
not the source for this workflow. An empty or failed alternative does not prove
that the workspace has no allocations.

Present allocations from the response, matching the requested `region_key` and
node type. Preserve exact IDs/names/region_key/default_for_job. Do not derive the
region from a name suffix, collapse distinct IDs with similar names, or manufacture
rows. Label a region-filtered list as such; do not call a truncated list complete.
If the user names an absent allocation, resolve the discrepancy before submission.

**Stop point after the allocation list:** if more than one allocation matches the
requested region/node type and the user has not selected or delegated the choice,
your next answer must list those allocations and ask which to use. END THIS TURN.
Do not call allocation_queues, instance_types or configs yet. Mark the default in
the options, but do not select it. For example: "Доступны A (по умолчанию) и B.
В какой аллокации запускаем?" Do not replace this choice with a final launch preview.
Apply the same stop-and-ask rule to multiple active queues after the allocation is
chosen. Skip this question when the user's request already specifies the choice.

After selecting an allocation, call mlspace_workspaces action=allocation_queues
with `allocation_id` and the same `target`. Offer its active queues, preserve exact
names/IDs/default_for_job, and check returned allocation_id against the selection.
Ask about real alternatives unless the user already chose or delegated the choice.
A default is an option to display, not an invisible decision. Do not create queues,
assign allocations, or change workspace defaults as part of launching a job.

An empty queue list or 403/409 is not proof that the allocation is unusable or that
PAYG is selected. Keep queue support/defaults unresolved until verified; a confirmed
allocation without custom queues may use its documented default scheduling.
Omitting allocation_name/queue_name invokes API defaults; it does not explicitly
select PAYG. Do not infer billing mode from an allocation's name. Explain any
intentional omission and its known effect in the final preview; if the placement
cannot be established, stop and ask for the missing information.

### 3. Check available resources, then the image

Call mlspace_resources action=instance_types with `target`, explicit `region` and
the selected `allocation_name` and `queue_name` when applicable. Match these names
exactly to the discovery responses. Allocation/queue-specific detail can also be
read through mlspace_allocations action=list_instance_types and mlspace_queues
action=instance_types using their IDs. Keep the scope of each result explicit;
an allocation-wide result does not override a queue restriction or failed check.

Use mlspace_resources action=configs with cluster_type=MT, `region` and `target`
for resource specifications and image compatibility. The general catalog alone is
not proof of availability in the selected allocation/queue. Intersect keys with
the scoped resource results. If results conflict or are inaccessible, stop preparation and explain which
placement could not be checked. Do not continue with an unverified catalog choice. Do not replace a
user-selected type silently. Availability is a point-in-time count, not a capacity
reservation; zero availability does not by itself mean a type is unsupported.

Compare GPU model, GPU memory where reported, GPU count **per worker**, CPU/RAM and
`n_workers`. One GPU does not distinguish A100/H100 or memory variants. Ask the user
to choose among materially different candidates, or apply their explicit criterion.
For `gpus=0`, include compatible zero-GPU cpu.* types; do not require a GPU.
Show unknown specifications as unknown rather than deducing them from a type name.

Offer compatible `base_image` choices with exact tags from the region's
`datahub_images`/`custom_images`; retain a compatible user-specified image.
Use `instances_types[].images` only as compatibility references: the MCP compacts
these into strings such as name:tag1,tag2, not a runnable combined image tag.
An empty compatible images list is not permission to select an arbitrary image.
Ask about framework/environment needs if needed to choose an image. Do not choose
the first image or tag merely because it appears in the catalog. Custom registry
images need the same compatibility check; being listed in a registry is insufficient.

### 4. Resolve the command and show the complete launch

Set job `type` explicitly. For type=binary, `script` is the unchanged command or
executable path, not necessarily an NFS path. `sleep infinity` is a long-running
idle test, not training: explain that it holds resources until stopped. If intent
is unclear, clarify it; do not replace an explicitly requested command or claim
that training is running. For a temporary test, establish how it will be stopped
without inventing an API timeout field or terminating someone else's job.

For other job types, the script must already exist under /home/jovyan on that
region's NFS. The same path in another region may have different contents. The
Public API cannot stat that file or execute an image check: report command/NFS
availability as unverified unless execution-environment evidence is available.
Keep checkpoint_dir/logs_dir on the same regional NFS. Include relevant flags,
env_variables, internet, temporary storage or retry settings only when needed or
requested and supported by RunJobRequest; max_retry is allocation-only, not PAYG.

Before jobs run, show a concise human-readable preview: workspace name/ID, region,
allocation and queue (or explained defaults), instance key and specs, worker count,
image:tag, job type, command and relevant input/output paths and optional settings.
Do not expose secrets. Obtain confirmation of this resolved configuration; an
answer to an earlier GPU or region question is not confirmation of the full launch.
If a subsequent change affects placement, image, command or resources, revise the
preview and confirmation. Never submit while a material choice remains ambiguous.

Build RunJobRequest with script, base_image, instance_type, region, type, n_workers
and the agreed optional fields. allocation_name/queue_name are exact names, not
UUIDs; `target` is a tool argument, not part of the request body.

### 5. Submit once and verify

Call mlspace_jobs action=run with `target` and the agreed body. Capture job_name
and resource_ref. Read mlspace_jobs action=get and action=logs (region, tail=50)
for that job. Complete both status and log reads before the final report; empty
logs from an idle command are expected, but do not fabricate a log result. Report the observed status; acceptance/queued is not running and a
running sleep command is not successful training. After a timeout or uncertain
write result, inspect for the submitted job before retrying to avoid duplicates.

For an explicitly requested restart, inspect the existing job's parameters and
scope, show the configuration being reused, then use mlspace_jobs action=restart.
A restart creates a new job; retain its new reference. A requested configuration
change requires a fresh resolved launch rather than assuming restart applies it.
