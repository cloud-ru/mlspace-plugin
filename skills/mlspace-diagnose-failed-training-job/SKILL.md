---
name: mlspace-diagnose-failed-training-job
description: "Diagnose a failed / stuck training job on Cloud.ru MLSpace. Why did my training job fail or why is it stuck Pending; or roll up recent failures. Uses the mlspace_jobs, mlspace_jobs_overview tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Diagnose a failed / stuck training job

Why did my training job fail or why is it stuck Pending; or roll up recent failures.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `job_name` — Job to diagnose (omit for a recent-failures roll-up).
- `region` — Region of the job, if known.
- `days` — Look-back window in days for the roll-up.
- `job_author` — Filter the roll-up by author email.
- `queue_id` — Filter the roll-up by queue id.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: explain why a training job FAILED or is STUCK PENDING, and/or roll up recent failures.
Use mlspace_jobs and relevant read-only resource/queue helpers; never run/restart/delete.

If a job name is given:
1. jobs get (job_name only — no region arg). Read status (lowercase) + timeline
   (created_at/pending_at/running_at/completed_at). NOTE error_code/error_message
   here are deprecated constants — ignore them.
2. jobs get_params (job_name) and record the authoritative script, region, image and
   instance_type. `/home/jovyan` is region-scoped; the same absolute path in another
   notebook region is a different NFS volume.
3. Classify from the timeline BEFORE logs: running_at>0 & failed -> runtime crash
   (expect traceback); pending with running_at=0 -> never scheduled (capacity/quota);
   terminated -> likely preempted.
4. jobs logs (job_name, region from get_params, tail=100, verbose=true; raise tail
   to ~300 if a traceback is cut off). This is the actual error.
   If the job failed before the first user-script line and the launcher cannot access
   the entrypoint: for type=binary check that the command/executable exists in the
   selected image and that its arguments are valid; for an NFS script, treat a wrong
   regional NFS as the leading cause even when the path begins with /home/jovyan.
5. jobs list_pods (job_name) -> per-pod status + `reason` (OOMKilled / Unschedulable /
   ImagePull...). This is the key "why" for both crash and pending.
6. If pending/terminated: jobs get_preemptors (job_name) -> who preempted; and check
   capacity (see the discover_launchable_resources / capacity playbook): instance
   availability + node load in the job's region.

For a recent-failures roll-up: mlspace_jobs_overview with status=["Failed","Terminated"]
and author=job_author when explicitly known, plus targets for an explicitly restricted
workspace scope. Regions are discovered dynamically and pages are collected by the
helper. Filter returned data by the requested days/queue/region, disclosing incomplete
coverage. If exact server-side date/queue/region filtering is needed, use jobs list
in each selected context and discovered MT region with start_date/end_date (RFC3339 Z),
queue_id and pagination; never treat one page as the full set. Group observed failures
by instance_type/author/queue, retaining each source/resource_ref.

Output: a one-line verdict (status + inferred cause), the phase timeline, the key
log excerpt, pod reason, and a concrete next step.
Caveats: no per-pod logs beyond the job log stream; capacity is inferred from node
load + preemptors (no quota/cost numbers exist in this API).
