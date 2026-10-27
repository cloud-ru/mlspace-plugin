"""Declarative umbrella-tool model + the generic dispatcher.

Each domain module declares pure DATA: a ``DomainTool`` whose ``actions`` map
(action name → :class:`Op`) is the single source of truth for action→endpoint.
ALL mechanics — readonly enum-stripping, path templating, repeated-query
encoding, body attachment, required-param validation, confirm gating,
pagination clamp, response formatting, annotations — live here, once. Domain
modules cannot drift on mechanics because they contain none.
"""

from __future__ import annotations

import inspect
import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import bodyspec, diagnostics, formatting
from .config import Settings
from .data_transfer_hooks import history_evidence, log_evidence, preflight_transfer_write
from .errors import MLSpaceError
from .job_hooks import (
    guard_job_delete,
    postprocess_job_delete,
    preflight_job_run,
    preflight_jobs_list,
)
from .resource_refs import (
    ResourceRef,
    apply_reference,
    parse_reference,
    response_references,
    with_context,
)
from .workspace_context import select_target
from .write_verification import verify_repository_labels, verify_transfer_schedule

logger = logging.getLogger(__name__)

# ---- declarative data model ------------------------------------------------


@dataclass(frozen=True)
class Op:
    """One MLSpace API operation reachable through an umbrella ``action``."""

    method: str  # GET / POST / PUT / PATCH / DELETE
    path: str  # may contain {placeholders}
    # placeholder-in-path -> input field name supplying its value
    path_params: dict[str, str] = field(default_factory=dict)
    # placeholders whose value is a deliberate multi-segment path TAIL (may contain
    # "/"), e.g. async_inference `predict_path`. Every OTHER placeholder is a single
    # opaque SEGMENT: its value is percent-encoded and forbidden from carrying "/",
    # "..", "?", "#" or control chars, so a name can never re-route the request to a
    # different endpoint (route-confusion / path traversal). See `_encode_path_param`.
    path_tail: frozenset[str] = frozenset()
    # query-key -> input field name (list values become repeated keys)
    query_params: dict[str, str] = field(default_factory=dict)
    body_field: str | None = None  # input field carrying the JSON body
    required: tuple[str, ...] = ()  # input fields that must be present for this action
    write: bool = False  # mutates state → stripped/blocked in readonly mode
    confirm: bool = False  # irreversible → needs confirm=true
    # external side effect / cost (e.g. predict runs a paid model) but NOT a state
    # mutation: allowed in readonly mode, yet the tool is not "read-only" for hints.
    side_effect: bool = False
    kind: str = "item"  # item | list | log | binary
    help: str = ""  # one-line description for the tool's action index
    reveal_result_keys: tuple[str, ...] = ()  # explicit credential-export actions only


@dataclass(frozen=True)
class Param:
    """An input field of an umbrella tool (optional at the schema level;
    per-action requiredness is enforced at dispatch via :attr:`Op.required`)."""

    name: str
    type: Any
    description: str = ""
    default: Any = None


@dataclass(frozen=True)
class DomainTool:
    domain: str  # "inference"
    name: str  # "mlspace_inference"
    title: str  # human-readable tool title
    summary: str  # base description (action index is appended)
    actions: dict[str, Op]
    params: list[Param]
    redact_result_keys: tuple[str, ...] = ()  # result keys whose values are secrets


# universal params injected into every tool
CONFIRM_PARAM = Param(
    "confirm", bool,
    "Set true to authorize an irreversible action or explicit credential disclosure.",
    default=False,
)
RESPONSE_FORMAT_PARAM = Param(
    "response_format", Literal["json", "markdown"],
    "Output format: 'json' (default, compact structured) or 'markdown' (fenced JSON).",
    default="json",
)

# Some clients expose tools but omit initialize.instructions. Keep the essential
# response contract at the surface those clients actually deliver to the model.
RESPONSE_GUIDANCE = (
    "Agent response guidance: For capability questions, give a short plain-language "
    "overview without fetching inventories. Treat technical notes as execution guidance, "
    "not an unsolicited API-problems section. Report errors or partial results when "
    "they affect the task. Empty logs do not establish a cause; a partial list does "
    "not prove absence. Never invent a diagnosis or claim unverified success. "
    "Explain technical details when troubleshooting or explicitly asked."
)


# ---- pure helpers (unit-tested without FastMCP) ----------------------------


def available_actions(dt: DomainTool, readonly: bool) -> list[str]:
    return [a for a, op in dt.actions.items() if not (readonly and op.write)]


def build_annotations(dt: DomainTool, actions: list[str]) -> ToolAnnotations:
    ops = [dt.actions[a] for a in actions]
    has_write = any(o.write for o in ops)
    has_destructive = any(o.confirm for o in ops)
    has_side_effect = any(o.side_effect for o in ops)
    return ToolAnnotations(
        title=dt.title,
        # a side-effecting predict (paid/async) is not a state mutation but is not
        # "read-only" either — don't advertise readOnlyHint for such tools.
        readOnlyHint=not (has_write or has_side_effect),
        destructiveHint=has_destructive,
        idempotentHint=False,
        openWorldHint=True,
    )


def build_description(dt: DomainTool, actions: list[str]) -> str:
    lines = [dt.summary, "", RESPONSE_GUIDANCE, "", "Actions:"]
    for a in actions:
        op = dt.actions[a]
        flags = []
        if op.write:
            flags.append("write")
        if op.confirm:
            flags.append("destructive, needs confirm=true")
        flag = f" [{', '.join(flags)}]" if flags else ""
        req = f" (requires: {', '.join(op.required)})" if op.required else ""
        body = ""
        if op.body_field:
            schema = bodyspec.schema_for(op.method, op.path)
            hint = bodyspec.summarize(schema) if schema else ""
            if hint:
                body = f" — {op.body_field}: {{{hint}}}"
        lines.append(f"- {a}: {op.help}{req}{flag}{body}")
    return "\n".join(lines)


# ---- path-parameter encoding (route-confusion / traversal defence) ----------
#
# A path parameter is user-controlled text spliced into the request path. Without
# encoding, a value like ``../docker_registry/v1/users/generate_password`` passed
# as a jobs `job_name` makes the client normalise the ``..`` and land on a totally
# different (mutating) endpoint, bypassing the domain's readonly/redaction policy.
# This is a SAFETY validator, so it is fail-closed: a value that could alter the
# route is refused with an honest MLSpaceError rather than silently sanitised away.

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
# A path segment that would traverse or inject a route boundary. ``..``/``.`` are
# traversal; the reserved chars start a new segment / query / fragment.
_SEGMENT_FORBIDDEN = ("/", "\\", "?", "#")
# Extra characters kept unescaped in a path TAIL beyond the "/" separators, so a
# legitimate predict path like ``v1/models/svc:predict`` is not mangled (":" and
# the RFC-3986 unreserved set are valid in a path and need no escaping).
_TAIL_SAFE = "/:@-._~"


def _reject_path_value(fname: str, value: str, *, detail: str) -> None:
    raise MLSpaceError(
        f"Path parameter '{fname}'={value!r} {detail}.",
        hint="Path parameters address a single resource; they may not contain path "
        "separators, '..', '?', '#' or control characters. Pass the bare "
        "name/id only.",
    )


def _encode_path_param(fname: str, value: str, *, is_tail: bool) -> str:
    """Percent-encode ONE path-parameter value, fail-closed on anything that could
    re-route the request. A normal SEGMENT may not contain "/" (encoded away here
    would still be refused, to keep the failure honest); a TAIL keeps its "/"
    separators but every segment is still traversal-checked."""
    if _CTRL_RE.search(value):
        _reject_path_value(fname, value, detail="contains control characters")
    if is_tail:
        for seg in value.split("/"):
            if seg in ("..", "."):
                _reject_path_value(fname, value, detail="contains a '..'/'.' path segment")
        for bad in ("\\", "?", "#"):
            if bad in value:
                _reject_path_value(fname, value, detail=f"contains {bad!r}")
        return urllib.parse.quote(value, safe=_TAIL_SAFE)
    # single opaque segment
    if value in ("..", "."):
        _reject_path_value(fname, value, detail="is a '..'/'.' path traversal segment")
    for bad in _SEGMENT_FORBIDDEN:
        if bad in value:
            _reject_path_value(fname, value, detail=f"contains {bad!r}")
    return urllib.parse.quote(value, safe="")


def _assert_route_shape(op: Op, encoded: dict[str, str]) -> None:
    """Allowlist check: the resolved path must have the SAME static skeleton as the
    declared ``op.path``. After per-segment encoding this is already guaranteed, but
    asserting it explicitly turns any future encoding gap into a hard failure instead
    of a silent re-route."""
    template_segs = op.path.split("/")
    resolved_segs = op.path.format(**encoded).split("/")
    has_tail = bool(op.path_tail)
    if not has_tail and len(resolved_segs) != len(template_segs):
        raise MLSpaceError(
            f"Resolved path {op.path.format(**encoded)!r} does not match the declared "
            f"route {op.path!r} (segment count differs)."
        )
    for i, seg in enumerate(template_segs):
        if seg.startswith("{") and seg.endswith("}"):
            continue  # placeholder — value already encoded/validated
        if i >= len(resolved_segs) or resolved_segs[i] != seg:
            raise MLSpaceError(
                f"Resolved path {op.path.format(**encoded)!r} diverges from the declared "
                f"route {op.path!r} at a static segment."
            )


def resolve_request(
    op: Op, values: dict[str, Any]
) -> tuple[str, str, dict[str, str], list[tuple[str, Any]], Any]:
    """Pure: (method, path_template, path_params, query_pairs, json_body).

    Raises MLSpaceError for missing required params / unconfirmed destructive ops.
    ``values`` must contain only non-None provided inputs (plus optional confirm).
    """
    missing = [f for f in op.required if values.get(f) in (None, "")]
    if missing:
        raise MLSpaceError(
            f"This action requires: {', '.join(missing)}.",
            hint="Provide the listed parameter(s) and retry.",
        )
    if op.confirm and not values.get("confirm"):
        raise MLSpaceError(
            "This action is destructive and irreversible.",
            hint="Re-run with confirm=true to proceed.",
        )

    # Per-endpoint preflight: a sync validator that runs BEFORE the query/body are
    # assembled. It may raise a deliberate MLSpaceError refusal (an honest "no",
    # e.g. jobs `list` without a region) OR normalise `values` in place (e.g. lift a
    # status filter to the exact enum casing the API accepts) — mirroring the silent
    # limit-clamp the handler already does. No entry for (op.method, op.path) is a
    # no-op, so every other op is byte-for-byte unaffected.
    check = PREFLIGHT.get((op.method, op.path))
    if check is not None:
        check(values)

    path_params: dict[str, str] = {}
    for placeholder, fname in op.path_params.items():
        value = values.get(fname)
        if value in (None, ""):
            raise MLSpaceError(f"This action requires path parameter '{fname}'.")
        path_params[placeholder] = _encode_path_param(
            fname, str(value), is_tail=placeholder in op.path_tail
        )
    if path_params:
        _assert_route_shape(op, path_params)

    query: list[tuple[str, Any]] = []
    for qkey, fname in op.query_params.items():
        value = values.get(fname)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                query.append((qkey, item))
        else:
            query.append((qkey, value))

    body = values.get(op.body_field) if op.body_field else None
    return op.method, op.path, path_params, query, body


# ---- request hooks: preflight (sync) / guard (async) / postprocess (pure) --
#
# Three per-endpoint extension points keyed by (METHOD, path). They exist because
# a handful of live MLSpace endpoints return a *false success* or a misleading
# empty result that a blind caller reads as truth (audited in docs/); the wrapper
# translates those into an honest signal WITHOUT hiding or inventing any field.
#
#   PREFLIGHT   sync, runs inside resolve_request before the request is built.
#               May raise a deliberate refusal or mutate `values` in place.
#   GUARDS      async, runs in the handler just before a (usually destructive)
#               request. May issue its own precheck call and raise to abort.
#   POSTPROCESS pure, runs in the handler on the decoded response before
#               formatting. Returns a (possibly re-shaped) result.
#
# Preflight and guards may refuse requests (fail-closed). Postprocessing is
# fail-open: unexpected response shapes or transform errors preserve the result.

PREFLIGHT: dict[tuple[str, str], Callable[[dict[str, Any]], None]] = {}
GUARDS: dict[tuple[str, str], Callable[[Any, dict[str, Any]], Awaitable[None]]] = {}
POSTPROCESS: dict[tuple[str, str], Callable[[Any, dict[str, Any]], Any]] = {}
# Async read-back checks fail closed after an accepted write, with an explicit
# warning that the mutation has already happened. They must never retry it.
VERIFY_WRITE: dict[tuple[str, str], Callable[[Any, Any, dict[str, Any]], Awaitable[None]]] = {
    ('POST', '/public/v2/data_transfer/v5/transfer'): verify_transfer_schedule,
    ('POST', '/public/v2/data_transfer/v5/transfer/{id_}'): partial(verify_transfer_schedule, update=True),
    ('PUT', '/public/v2/docker_registry/v2/repositories/{repository_id}'): verify_repository_labels,
}
PREFLIGHT[('POST', '/public/v2/data_transfer/v5/transfer')] = preflight_transfer_write
PREFLIGHT[('POST', '/public/v2/data_transfer/v5/transfer/{id_}')] = preflight_transfer_write


# Explicit registrations keep domain compatibility rules visible at dispatch.
PREFLIGHT[("POST", "/public/v2/jobs")] = preflight_job_run
PREFLIGHT[("GET", "/public/v2/jobs")] = preflight_jobs_list
GUARDS[("DELETE", "/public/v2/jobs/{job_name}")] = guard_job_delete
POSTPROCESS[("DELETE", "/public/v2/jobs/{job_name}")] = postprocess_job_delete
POSTPROCESS[("GET", "/public/v2/data_transfer/v3/history")] = history_evidence
POSTPROCESS[("GET", "/public/v2/data_transfer/v3/history/status/stream")] = history_evidence
POSTPROCESS[("GET", "/public/v2/data_transfer/v2/events/list")] = log_evidence
POSTPROCESS[("GET", "/public/v2/data_transfer/v3/connectors/{connector_type}/{id_}/try/logs")] = log_evidence


def _postprocess_delete_connectors(data: Any, values: dict[str, Any]) -> Any:
    """Re-shape delete_connectors' bare 200 list into an explicit {deleted, not_found}.

    delete_connectors replies 200 with ONLY the ids it actually deleted; ids that
    did not exist are silently absent (its siblings delete_transfers/delete_history
    already surface them under ``not_found_ids``). We compute the wrapper's OWN diff
    of requested-minus-returned and, only when that diff is non-empty (an
    already-broken case), re-pack into the honest sibling shape. A full success
    passes through as the untouched list.

    NOTE: the success shape (the response echoes the deleted ids verbatim) is
    inferred from the schema and the sibling endpoints, NOT verified against a live
    delete (no-write red line). A future verifier should confirm it on a
    self-created connector once that is permitted.
    """
    ids = values.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    if isinstance(data, list) and ids:
        not_found = [i for i in ids if i not in data]
        if not_found:
            return {
                "deleted_ids": data,
                "not_found_ids": not_found,
                "_wrapper_note": (
                    "delete_connectors replies 200 with only the ids it actually deleted. "
                    "These not_found_ids were NOT among the ids the API returned in its 200 "
                    "response — this is the wrapper's own diff of requested-minus-returned, "
                    "not a platform claim; the sibling endpoints delete_transfers/"
                    "delete_history return such ids under not_found_ids. Empty deleted_ids "
                    "means nothing was deleted; check for a wrong id/connector_type."
                ),
            }
    return data


POSTPROCESS[("DELETE", "/public/v2/data_transfer/v2/connectors")] = _postprocess_delete_connectors


def _is_empty_result(data: Any) -> bool:
    """True for a bare-empty result ([]/None/{}) or a dict whose every list value is
    empty (and which has at least one list value). Fail-open: anything else is not
    treated as empty."""
    if data == [] or data is None or data == {}:
        return True
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        return bool(lists) and all(len(v) == 0 for v in lists)
    return False


def _annotate_empty(reason: str) -> Callable[[Any, dict[str, Any]], Any]:
    """Build a postprocessor that annotates a *documented-ambiguous* empty read.

    Several allocation-scoped reads answer 200 ``[]`` for multiple distinct causes
    (bad id / feature disabled / genuinely none) with no way to tell them apart. We
    re-pack an empty result as ``{result, _wrapper_note}`` with a strictly
    conditional caveat that never asserts a specific cause; a non-empty result is
    returned untouched.
    """
    def fn(data: Any, values: dict[str, Any]) -> Any:
        if _is_empty_result(data):
            return {"result": data, "_wrapper_note": reason}
        return data

    return fn


POSTPROCESS[("GET", "/public/v2/allocations/")] = _annotate_empty(
    "Empty allocations list. If this server runs under a SERVICE account, GET "
    "/allocations/ is DOCUMENTED not to work for it and returns [] (not an error) — "
    "the real allocations are at mlspace_workspaces action=allocations. Under a "
    "personal account the workspace genuinely has none."
)
POSTPROCESS[
    ("GET", "/public/v2/workspaces/v3/{workspace_id}/allocations/{allocation_id}/queues")
] = _annotate_empty(
    "Empty queue list is AMBIGUOUS: the allocation_id may not exist, custom queues "
    "may be disabled for it, or there are genuinely none — the API returns 200 [] for "
    "all three. Verify the allocation_id via mlspace_workspaces action=allocations."
)
POSTPROCESS[("GET", "/public/v2/queues/defaults")] = _annotate_empty(
    "Empty defaults are AMBIGUOUS (bad allocation_id / feature off / none). The "
    "sibling mlspace_queues action=list returns 404 'allocation not found' for a "
    "bogus allocation_id — cross-check there."
)


# ---- FastMCP registration --------------------------------------------------


def _optional(tp: Any) -> Any:
    """``Optional[tp]`` built from a runtime value (tp is a value, not a static type)."""
    return Optional[tp]  # noqa: UP045


def _build_handler(dt: DomainTool, actions: list[str], settings: Settings):
    action_type = Literal[tuple(actions)]  # type: ignore[valid-type]
    params = list(dt.params) + [
        Param("target", str, "Connected workspace ID or unambiguous name from mlspace_contexts. "
              "Required when several workspaces are connected; separate from API workspace_id."),
        Param("resource_ref", ResourceRef, "Typed address of an existing job/notebook. "
              "Must agree with target and explicit address arguments; does not replace confirm."),
        CONFIRM_PARAM, RESPONSE_FORMAT_PARAM,
    ]
    if any(dt.actions[a].reveal_result_keys for a in actions):
        params.append(Param(
            "reveal_secret", bool,
            "Return the credential in plaintext only for an authorized credential task. "
            "Requires confirm=true; the value enters the agent client/tool transcript. "
            "Supported actions: " + ", ".join(
                a for a in actions if dt.actions[a].reveal_result_keys
            ) + ". Default false keeps credentials masked.",
            default=False,
        ))

    sig_params = [
        inspect.Parameter("action", inspect.Parameter.KEYWORD_ONLY, annotation=action_type)
    ]
    annotations: dict[str, Any] = {"action": action_type}
    for p in params:
        anno = _optional(p.type)
        field_info = Field(default=p.default, description=p.description)
        sig_params.append(
            inspect.Parameter(
                p.name, inspect.Parameter.KEYWORD_ONLY, default=field_info, annotation=anno
            )
        )
        annotations[p.name] = anno
    sig_params.append(inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context))
    annotations["ctx"] = Context
    annotations["return"] = str

    actions_set = set(actions)

    async def handler(**kwargs: Any) -> str:
        ctx: Context = kwargs.pop("ctx")
        action: str = kwargs.pop("action")
        response_format = kwargs.pop("response_format", None) or "json"
        reveal_secret = kwargs.pop("reveal_secret", False)
        values = {k: v for k, v in kwargs.items() if v is not None}
        # Never log tool arguments, free-form action names, or returned content.
        log_meta = {'domain': dt.domain, 'action': action if action in dt.actions else '<unknown>'}
        diagnostics.event('tool.start', **log_meta)

        try:
            if action not in actions_set:
                # write action hidden by readonly, or simply unknown
                raise MLSpaceError(
                    f"Action '{action}' is not available"
                    + (" (server is in read-only mode; set MLSPACE_READONLY=false)."
                       if action in dt.actions else ".")
                )
            op = dt.actions[action]
            if settings.readonly and op.write:
                raise MLSpaceError(
                    f"Action '{action}' is disabled in read-only mode.",
                    hint="Set MLSPACE_READONLY=false to enable write actions.",
                )
            if reveal_secret:
                if not op.reveal_result_keys:
                    raise MLSpaceError("Credential disclosure is not supported for this action.")
                if not values.get("confirm"):
                    raise MLSpaceError(
                        "Credential disclosure requires confirm=true.",
                        hint="The secret will be visible to the agent client/tool transcript.",
                    )
            redact_keys = tuple(
                key for key in dt.redact_result_keys
                if not (reveal_secret and key in op.reveal_result_keys)
            )

            # pagination: only touch "limit" when it is a real query param of THIS op
            # (avoids corrupting an unrelated field that happens to be named "limit").
            if "limit" in op.query_params:
                if values.get("limit") is not None:
                    values["limit"] = max(1, min(int(values["limit"]), settings.list_max_limit))
                elif op.kind == "list":
                    values["limit"] = settings.list_default_limit

            clients = ctx.request_context.lifespan_context.clients
            selected = clients.selected
            environment = clients.environment
            ref = parse_reference(values.pop("resource_ref", None), environment)
            target = values.pop("target", None)
            if ref is not None:
                ref_selection = select_target(selected, ref.target)
                if ref_selection.id != ref.target:
                    raise MLSpaceError("resource_ref target must be a canonical configured ID.")
                if target is not None and select_target(selected, target).id != ref.target:
                    raise MLSpaceError("resource_ref conflicts with explicit target.")
                target = ref.target
            selection = select_target(selected, target)
            apply_reference(ref, dt.domain, action, values)
            # Check confirmation before even lazy key/namespace lookups.
            if op.confirm and not values.get("confirm"):
                raise MLSpaceError("This action is destructive and irreversible.",
                                   hint="Re-run with confirm=true to proceed.")
            if (dt.domain == "docker_registry" and action == "generate_password"
                    and not settings.allow_registry_password_rotation and not settings.dry_run):
                raise MLSpaceError(
                    "Registry password rotation is disabled in the server configuration.",
                    hint="Reuse an existing Docker login or ask the user to authorize rotation. "
                    "Only after approval may the operator enable "
                    "MLSPACE_ALLOW_REGISTRY_PASSWORD_ROTATION=true and restart the MCP server. "
                    "Do not change this setting yourself or bypass it with direct API calls.",
                )
            client = None

            async def selected_client():
                return await clients.resolve(selection.id)

            # auto-fill server-known ids the model omitted, so workspace-scoped
            # actions work without the model discovering them: workspace_id comes
            # straight from config; namespace is resolved (and cached) from it.
            # Falls back to the required-check below if namespace can't be resolved.
            needed = set(op.path_params.values()) | set(op.required)
            if "workspace_id" in needed and not values.get("workspace_id") and selection.id:
                values["workspace_id"] = selection.id
            if "namespace" in needed and not values.get("namespace"):
                client = await selected_client()
                ns = await client.workspace_namespace()
                if ns:
                    values["namespace"] = ns

            method, path, path_params, query, body = resolve_request(op, values)

            # lenient, spec-driven body check: catch obvious structural mistakes
            # (missing required / wrong scalar type / bad enum) before a round-trip.
            # Stays silent on anything ambiguous; the API remains the real validator.
            if body is not None and op.body_field:
                body_schema = bodyspec.schema_for(op.method, op.path)
                if body_schema:
                    issues = bodyspec.lint(body_schema, body)
                    if issues:
                        raise MLSpaceError(
                            "Request body is invalid: " + "; ".join(issues[:8]),
                            hint="Fix the listed field(s) and retry. See the action's "
                            "description for the required body shape.",
                        )

            # dry-run: validated but not sent — return a preview of the request that
            # WOULD be issued (lets an LLM assemble & check a write before applying it).
            if settings.dry_run and (op.write or op.side_effect):
                resolved = path.format(**path_params) if path_params else path
                # keep repeated query keys as ordered pairs (dict(query) would collapse a
                # multi-delete's ids to the last one) and redact any secret-looking body
                # field so the preview never duplicates a credential into output/traces.
                preview = {
                    "dry_run": True,
                    "would_send": {
                        "method": method,
                        "path": resolved,
                        "query": [[k, v] for k, v in query] if query else None,
                        "body": formatting.redact_secrets(body) if body is not None else None,
                    },
                    "note": "Not executed (server in dry-run). Body passed validation; "
                    "re-run without dry-run to apply.",
                }
                formatted = formatting.format_response(
                    preview, response_format, kind="item",
                    redact_keys=dt.redact_result_keys, list_cap=settings.list_max_limit,
                    log_tail=settings.log_tail_lines,
                )
                if len(selected) == 1:
                    diagnostics.event('tool.end', **log_meta, mode='dry-run')
                    return formatted
                diagnostics.event('tool.end', **log_meta, mode='dry-run')
                return with_context(formatted, environment=environment, target=selection.id,
                                    workspace_name=selection.name, refs=[],
                                    response_format=response_format, kind="item")

            if client is None:
                client = await selected_client()

            # Per-endpoint async guard: a precondition/existence check issued just
            # before a (usually destructive) request so the wrapper never reports a
            # false success. Existence guards fail closed; a deliberate
            # MLSpaceError refusal propagates to `except MLSpaceError` below and the
            # real request is never sent.
            guard = GUARDS.get((op.method, op.path))
            if guard is not None:
                await guard(client, values)

            data = await client.request(
                method, path, path_params=path_params, params=query or None, json_body=body
            )
            verify_write = VERIFY_WRITE.get((op.method, op.path))
            if verify_write is not None:
                await verify_write(client, data, values)

            # Per-endpoint postprocess: a pure re-shaping that turns a platform false
            # success into an honest signal (adds `_wrapper_note`, never hides fields).
            # Fail-open: on any error `data` is left exactly as returned.
            post = POSTPROCESS.get((op.method, op.path))
            if post is not None:
                try:
                    data = post(data, values)
                except Exception as exc:
                    diagnostics.failure('postprocess', exc, **log_meta)
                    logger.error("postprocess %s failed (%s)", op.path, type(exc).__name__)

            formatted = formatting.format_response(
                data,
                response_format,
                kind=op.kind,
                redact_keys=redact_keys,
                list_cap=settings.list_max_limit,
                log_tail=settings.log_tail_lines,
                # configs (catalog) returns every region; the /configs endpoint has no
                # region filter, so 'region' is applied here client-side (not as a query
                # param) to keep one region only. None => all regions.
                catalog_region=values.get("region") if op.kind == "catalog" else None,
            )
            if len(selected) == 1:
                diagnostics.event('tool.end', **log_meta)
                return formatted
            refs = response_references(dt.domain, action, data, environment=environment,
                                       target=selection.id, values=values,
                                       list_cap=settings.list_max_limit)
            note = "New resource address is unknown from the response." if (
                (dt.domain, action) in {("jobs", "restart"), ("jobs", "run"),
                                      ("notebooks", "create")} and not refs
            ) else None
            diagnostics.event('tool.end', **log_meta)
            return with_context(formatted, environment=environment, target=selection.id,
                                workspace_name=selection.name, refs=refs,
                                response_format=response_format, kind=op.kind,
                                address_note=note)
        except MLSpaceError as exc:
            diagnostics.failure('tool', exc, **log_meta, status=exc.status)
            # Protocol-correct failure: raising out of the tool makes FastMCP set
            # CallToolResult.isError=true, so a refusal (preflight, 4xx/5xx, missing
            # confirm, route-confusion) is no longer indistinguishable from success.
            message = exc.to_text()
            if (dt.domain, action) in {
                ("resources", "instance_types"),
                ("allocations", "list_instance_types"),
                ("queues", "instance_types"),
            }:
                # Keep the original API error and isError=true. A failed scoped
                # read must not invite the model to use the broad catalog instead.
                message += (
                    "\nLaunch availability is unverified. This failed call supplies no "
                    "verified launch candidates. Stop launch preparation and report "
                    "the unresolved access/scope to the user. Do not substitute "
                    "regional configs, broaden allocation/queue filters, propose an "
                    "instance/image combination, or request launch confirmation "
                    "until availability for the intended placement is verified."
                )
            raise ToolError(message) from exc
        except Exception as exc:  # unexpected: still surface as isError=true, not a crash
            diagnostics.failure('tool', exc, **log_meta)
            logger.error("tool %s action=%s failed unexpectedly (%s)",
                         dt.name, log_meta['action'], type(exc).__name__)
            raise ToolError(f"Error: unexpected failure ({type(exc).__name__}).") from None

    handler.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    handler.__annotations__ = annotations
    handler.__name__ = dt.name
    handler.__doc__ = dt.summary
    return handler


def register_domain(mcp: FastMCP, settings: Settings, dt: DomainTool) -> bool:
    """Register one umbrella tool. Returns False if it has no available actions
    (e.g. an all-write domain while readonly)."""
    actions = available_actions(dt, settings.readonly)
    if not actions:
        return False
    handler = _build_handler(dt, actions, settings)
    mcp.add_tool(
        handler,
        name=dt.name,
        description=build_description(dt, actions),
        annotations=build_annotations(dt, actions),
    )
    return True
