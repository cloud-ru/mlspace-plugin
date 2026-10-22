---
name: mlspace-list-running-jobs
description: "List running / pending jobs on Cloud.ru MLSpace. What's the status of my jobs / what's running or pending. Uses the mlspace_jobs, mlspace_jobs_overview tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# List running / pending jobs

What's the status of my jobs / what's running or pending.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `region` — Region to scope to (omit to sweep all).
- `status_filter` — Comma statuses, default Running,Pending.
- `job_author` — Author email to filter by.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: show in-flight / recent jobs over the requested connected contexts.
1. Use mlspace_jobs_overview(targets=explicit_scope_or_omitted,
   status=["Running","Pending"], author=explicit_author_or_omitted). Parse status_filter
   if supplied; author is a user filter, not proof of identity. The helper discovers
   each workspace's actual MT regions and paginates them; no fixed region list.
2. If region was requested, filter observed rows by provenance.region and label that scope.
   The helper has no region argument. For a narrow provider-side region query use jobs
   list with target, region, status and job_author, advancing offset through all pages.
3. Read items as {source, data, provenance, resource_ref?}. Show workspace, region,
   job_name, status, job_author and time when returned; retain the exact resource_ref.
4. Sort Running before Pending. State actual checked scope, failures and truncation;
   observed counts from a partial read are not full totals or proof of a unique name.
5. Use jobs get/logs on a chosen object's reference for follow-up, without another sweep.
Example tool call (omit targets for the full connected set):
```json
{"tool":"mlspace_jobs_overview","arguments":{"status":["Running","Pending"]}}
```
Output: compact observed status board with source and completeness. States are
observations over time, not a transactional snapshot.
