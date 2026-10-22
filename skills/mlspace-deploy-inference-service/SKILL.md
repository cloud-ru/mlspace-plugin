---
name: mlspace-deploy-inference-service
description: "Deploy a model image as an inference service on Cloud.ru MLSpace. Deploy an already-built registry image as a new inference service. Write action. Uses the mlspace_docker_registry, mlspace_inference, mlspace_resources tools."
license: MIT
compatibility: Requires the `mlspace` MCP server from this plugin (Cloud.ru MLSpace credentials needed).
---

# Deploy a model image as an inference service

Deploy an already-built registry image as a new inference service. Write action.

This skill drives the `mlspace` MCP server shipped in the same plugin. If its
tools are not connected, say so instead of guessing — none of these steps can be
carried out any other way.

## Arguments

- `image` **(required)** — Registry image ref to deploy.
- `region` — Target region (default CCE-INF).
- `instance_type` — Inference instance type.
- `replicas_min` — Min replicas.
- `replicas_max` — Max replicas.
- `alias` — Service alias/name.
- `description` — Service description.
- `target` — Connected workspace ID or unambiguous name, if scoped.

## Procedure

Goal: deploy an already-built registry image as an inference service (write
action; refused under MLSPACE_READONLY=true).
1. INTENT GATE: if the user actually wants to BUILD an image, STOP — image build is not
   possible via this API (build-image is multipart-only; no create-repo). Hand off to
   the console/CLI for the build, then come back to deploy the resulting image ref.
2. (best-effort) verify the image: docker_registry current_registry -> list_repos /
   tags to confirm the ref exists.
3. mlspace_resources configs cluster_type=INF -> pick a valid inference instance_type.
4. Build CreateInferenceServiceRequestV2 {image, region (default CCE-INF), instance_type,
   replicas:{min,max}, optional alias/description}. Echo it.
5. mlspace_inference create (body=...). Capture the service name/status.
6. mlspace_inference get (inference_name, region) to confirm provisioning.
Output: the created service alias, image, region, instance_type, replicas, and status.
Caveat: you can deploy an existing image but cannot build one here.
