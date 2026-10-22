---
name: mlspace-resume-notebook-and-set-autoshutdown
description: "Resume a notebook + set autoshutdown on Cloud.ru MLSpace. Wake a paused Jupyter notebook and set idle autoshutdown to control cost. Write action. Uses the mlspace_notebooks, mlspace_notebooks_overview tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Resume a notebook + set autoshutdown

Wake a paused Jupyter notebook and set idle autoshutdown to control cost. Write action.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `notebook_name` **(required)** — Notebook to resume.
- `idle_minutes` — Idle minutes before autoshutdown.
- `workspace_id` — Workspace for the autoshutdown rule.
- `instance_type` — Override instance type on resume.
- `region` — Override region on resume.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: resume a paused Jupyter notebook and set workspace autoshutdown (write
actions; refused under MLSPACE_READONLY=true).
1. For an unlocated notebook use mlspace_notebooks_overview and match data.name;
   retain source/resource_ref and data.uid (not id). For a known target use
   mlspace_notebooks list with search=notebook_name and paginate; resolve ambiguity.
2. If already running/resuming/creating -> skip resume, report state.
3. mlspace_notebooks resume using resource_ref (or target, namespace, notebook_uuid=uid)
   and body={region, instance_type} from the chosen notebook unless overridden.
   Check the returned instanceType: the API may retain the previous size on resume.
4. Use the chosen notebook's context for workspace_id (auto-filled from target).
   If an explicit workspace_id disagrees, resolve that conflict before changing rules.
   autoshutdown is WORKSPACE-WIDE; tell the user it affects all notebooks there.
5. mlspace_notebooks autoshutdown_get (workspace_id) to show the current rule.
6. mlspace_notebooks autoshutdown_set (workspace_id, body = AutoShutdownV2Config:
   nested {by_timer:{...}, by_load:{...}, by_schedule:{...}}; for an idle timer set
   by_timer with the timeout — read the action's body hint for the exact fields).
Output: resume result + the autoshutdown rule set + the workspace-wide warning.
Caveat: autoshutdown is per-workspace, not per-notebook.
