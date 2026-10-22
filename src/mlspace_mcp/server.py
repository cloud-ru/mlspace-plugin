"""Assemble the FastMCP server: shared async client via lifespan, transport
security, and registration of the enabled domain umbrella tools."""

from __future__ import annotations

import importlib
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import USER_AGENT
from .auth import TokenManager
from .config import Settings
from .overview import register_overviews
from .registry import DomainTool, register_domain
from .tls import tls_context
from .workspace_context import (
    WorkspaceClients,
    environment_id,
    normalized_endpoint,
    selected_workspaces,
)

logger = logging.getLogger(__name__)

# Domain modules, each exposing a module-level ``DOMAIN: DomainTool``.
DOMAIN_MODULES: tuple[str, ...] = (
    "inference",
    "async_inference",
    "dalle",
    "jobs",
    "build_image",
    "notebooks",
    "tensorboards",
    "workspaces",
    "allocations",
    "queues",
    "docker_registry",
    "data_transfer",
    "resources",
)

# Shared rules every task needs. Step-by-step recipes ship as skills in the plugin
# (skills/); only these invariants live in the server, so a client sees them
# once whether or not a skill is active.
CONTEXT_INSTRUCTIONS = """\
Context and evidence rules (apply to every step):
- mlspace_contexts is the fixed selected catalogue, for one, three or ten workspaces.
  mlspace_workspaces list shows credential-visible workspaces and never expands it.
  No global default/current workspace exists. A standalone overview (including
  "now show jobs") covers the selected set even after inspecting an object in A.
- Address an existing object with its resource_ref, or exact target and resource
  address. Keep that address for diagnosis/restart; retain the new restart reference.
  An explicit task in A scopes its steps; an unrelated read in B does not change it.
  A singleton needs no placement question. Creation uses explicit placement, the
  established task, the singleton, or a delegated criterion (e.g. any suitable GPU
  in A/B). Evaluate that criterion and report the choice without asking again.
  A comparison/example alone does not choose placement. Ask only for real ambiguity;
  recover a lost object reference or clarify it rather than select the first match.
- Broad jobs/notebooks reads use mlspace_jobs_overview/mlspace_notebooks_overview.
  targets omitted means all selected contexts; explicit targets restrict observation.
  Inspect requested_scope, checked_scope, failures, observed_at, fetch_complete,
  output_truncated and complete. observed_count counts observations, not a complete
  inventory total or unique shared capacity. Partial search proves neither absence
  nor uniqueness. "All" after a shortened list needs a defined set (shown vs matching);
  an explicitly chosen object remains actionable despite unrelated failed reads.
- "My jobs" needs a confirmed author mapping or an explicitly supplied author filter.
  Reuse a user-specified saved author filter if available, label it as such; it is
  not verified identity/ownership. Membership, LLM login and a shared service account
  do not identify an author. If no usable mapping/filter exists, explain and obtain
  an author or offer the workspace overview; never silently label colleagues' jobs mine.
- Keep paused notebooks in inventory with their status. Configured GPU/instance size
  is not current usage; do not recommend pausing a paused notebook to free capacity.
  Compute nodes and Jupyter servers are different objects. Pending demand, running
  pods and node load are separate observations; do not sum repeated shared-resource
  views. Missing or inaccessible workload means unknown, not idle or safe to move.
- Use existing write/confirm rules. Context selection and resource_ref do not add a
  confirmation requirement or replace a required confirm=true. Read-only diagnosis
  never submits a paid canary merely to test quota.
"""

RESPONSE_INSTRUCTIONS = """\
User-facing responses:
- Answer the user's task in their language, with the useful result first. For a
  capabilities question, describe available information and operations briefly;
  do not fetch entire inventories just to explain what a service can do.
- Tool descriptions, schema notes and wrapper notes are operational guidance for
  you. Apply them while working; do not append an unsolicited API issues, caveats
  or limitations section to a normal overview or successful task. Use business
  terms rather than action names, JSON fields or pagination details unless asked.
- Recover using the documented action parameters where possible. If an error,
  incomplete result or uncertainty affects the answer or next decision, say what
  it means for the task in plain language. Never claim a partial list is complete,
  infer absence from a partial search, hide a failure, or claim an unverified success.
  Explain technical causes and workarounds when troubleshooting or asked for them;
  distinguish observed evidence from a suspected cause. Do not label normal optional
  fields or implementation details as defects.
- Request only pagination parameters supported by the specific action. A shortened
  nested array does not imply that the endpoint supports pagination.
- Credential reads are masked by default. For an explicitly requested task that
  needs the actual credential, use reveal_secret=true and confirm=true only on an
  action that supports them. Explain that this exposes the secret to the agent
  client/tool transcript; obtain permission if this was not already authorized.
  For registry login, reuse an existing authenticated session or an available
  authorized credential first. Login does not inherently require password rotation.
  generate_password rotates the registry password: explain that the old password
  stops working and obtain authorization before calling it. Do not rotate just to
  inspect credentials or retry a rotation merely because its result was masked.
  Rotation is disabled by default in server configuration. Do not enable it yourself
  or bypass the restriction via another tool or direct HTTP request.
  Use the revealed value only for the authorized task, not in ordinary replies,
  docs or committed files. For docker login prefer --password-stdin over command
  arguments. Never claim this keeps the secret out of the agent client's context.
"""

INSTRUCTIONS = (
    "Tools for the Cloud.ru MLSpace platform (ML training, inference, notebooks, "
    "data transfer, registry, resources). Each tool is an umbrella over one domain; "
    "pass an `action` plus that action's parameters. Read the tool description for "
    "the per-action parameter index. Destructive actions require confirm=true; all "
    "write actions disappear when the server runs with MLSPACE_READONLY=true."
) + "\n\n" + CONTEXT_INSTRUCTIONS + "\n\n" + RESPONSE_INSTRUCTIONS


@dataclass
class AppContext:
    """What lives in the FastMCP lifespan context (read by tool handlers)."""

    clients: WorkspaceClients


def _load_domain_tools(settings: Settings) -> list[DomainTool]:
    enabled = settings.enabled_domain_set
    if enabled is not None:
        unknown = enabled - set(DOMAIN_MODULES)
        if unknown:
            logger.warning(
                "MLSPACE_ENABLED_DOMAINS lists unknown domain(s) %s; known domains: %s",
                ", ".join(sorted(unknown)),
                ", ".join(DOMAIN_MODULES),
            )
    tools: list[DomainTool] = []
    for mod_name in DOMAIN_MODULES:
        if enabled is not None and mod_name not in enabled:
            continue
        full = f"{__package__}.tools.{mod_name}"
        try:
            module = importlib.import_module(full)
        except ModuleNotFoundError as exc:
            # Not-yet-implemented domain module: skip. A genuine missing dependency
            # inside an existing module (different name) is re-raised.
            if exc.name == full:
                continue
            raise
        tools.append(module.DOMAIN)
    return tools


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    def forms(host: str) -> set[str]:
        # IPv6 literals appear bracketed in the Host header ([::1], [::1]:8000)
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return {host, f"{host}:{settings.port}"}

    hosts = forms(settings.host) | forms("127.0.0.1") | forms("localhost")
    origins = {f"http://{h}" for h in hosts} | {f"https://{h}" for h in hosts}
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=sorted(origins),
    )


def build_server(settings: Settings) -> FastMCP:
    """Build (but do not run) the FastMCP server. Does not require credentials —
    credentials are only needed to serve requests."""

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        async with httpx.AsyncClient(
            timeout=settings.timeout, verify=tls_context(settings.ca_file),
            headers={"User-Agent": USER_AGENT},
        ) as http:
            tokens = TokenManager(
                http,
                settings.base_url,
                settings.client_id,
                settings.client_secret.get_secret_value(),
            )
            clients = WorkspaceClients(settings, http, tokens)
            yield AppContext(clients=clients)

    mcp = FastMCP(
        "mlspace_mcp",
        instructions=INSTRUCTIONS,
        host=settings.host,
        port=settings.port,
        lifespan=lifespan,
        transport_security=_transport_security(settings),
    )

    for dt in _load_domain_tools(settings):
        register_domain(mcp, settings, dt)

    catalogue = {
        "environment": environment_id(settings.base_url),
        "endpoint": normalized_endpoint(settings.base_url),
        "workspaces": [{"id": item.id, "name": item.name, "project_name": item.project_name}
                       for item in selected_workspaces(settings)],
    }

    async def mlspace_contexts() -> str:
        return json.dumps(catalogue, ensure_ascii=False)

    mcp.add_tool(mlspace_contexts, description="Saved connected workspaces available as target. "
                 "This fixed catalogue differs from credential-visible mlspace_workspaces list.",
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))

    register_overviews(mcp, settings)
    return mcp
