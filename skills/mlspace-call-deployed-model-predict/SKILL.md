---
name: mlspace-call-deployed-model-predict
description: "Call a deployed model (sync predict) on Cloud.ru MLSpace. Run a live prediction against a deployed inference service. Uses the mlspace_contexts, mlspace_inference tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Call a deployed model (sync predict)

Run a live prediction against a deployed inference service.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `service_name` **(required)** — Deployed inference service name.
- `input_payload` **(required)** — JSON input forwarded to the model.
- `model_name` — Model name within the service (default = service).
- `region` — Service region (default DGX2-INF-001).
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: run a synchronous prediction against a deployed inference service.
NOTE: inference.predict is a side effect — it runs the deployed (billed) model.
1. Resolve the service target from its address/task or mlspace_contexts selected catalogue.
2. mlspace_inference list -> find the service whose manifest name matches service_name.
3. Resolve model_name (often == service_name) and region (arg, else default
   DGX2-INF-001). mlspace_inference get (inference_name, region) to confirm it's Ready.
4. Echo back service/model/region and that this is a billed call; then
   mlspace_inference predict (inference_name=service_name, model_name, body=input_payload
   passed through unchanged).
Output: the model's raw prediction JSON + a one-line note of what was called.
Caveat: the predict response shape is model-defined (no fixed schema).
