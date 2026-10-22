---
name: mlspace-diagnose-unhealthy-inference
description: "Diagnose an unhealthy inference service on Cloud.ru MLSpace. Why is my deployed model returning 5xx / unhealthy (status-level only). Uses the mlspace_inference tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Diagnose an unhealthy inference service

Why is my deployed model returning 5xx / unhealthy (status-level only).

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `service_name` **(required)** — Inference service name.
- `region` — Service region, if known.
- `stop_billing` — If true, suggest scaling replicas to 0.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: diagnose an unhealthy / 5xx inference service — to the level the API allows.
1. Resolve service+region: if region empty, mlspace_inference list and match
   metadata.name == service_name.
2. mlspace_inference get (inference_name, region) -> K8S manifest.
3. Read status.conditions[]: the Ready condition + *Ready (Predictor/Routes/Ingress/
   LatestDeployment). Report which are False and their messages.
4. Read spec replicas.min/max (defaults min=0, max=3); if min=0 and 0 ready, the service
   may be scaled to zero (cold) — that explains 503s on first hit.
5. (optional, opt-in, PAID) only if the user wants to reproduce: inference predict with a
   minimal payload to capture the live error.
Output: identity/placement, the failing conditions + messages, replica readiness, and the
most likely cause at status level.
HARD CEILING — say this plainly: inference exposes NO pod logs, NO events, NO container
status. Crash-loop / OOM / image-pull / app-level 5xx CANNOT be seen here; hand off to the
MLSpace console for pod logs. (Only jobs have logs/pods.)
If stop_billing=true and min>0: suggest inference update to scale replicas to 0 (a write).
