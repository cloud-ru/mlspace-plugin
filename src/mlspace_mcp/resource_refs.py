"""Validated resource addresses and small jobs/notebooks operation adapters.

A reference is an address, not permission to perform the operation. Output
references are best-effort and only read recognized resource response shapes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from .errors import MLSpaceError


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*", check_fields=False)
    @classmethod
    def safe_segment(cls, value: Any) -> Any:
        if isinstance(value, str) and (
            not value.strip() or value in {".", ".."}
            or re.search(r"[/\\?#\x00-\x1f\x7f]", value)
        ):
            raise ValueError("address fields must be non-empty opaque names or IDs")
        return value


class JobAddress(Address):
    job_name: str
    region: str | None = None


class NotebookAddress(Address):
    notebook_uuid: str | None = None
    notebook_name: str | None = None
    namespace: str | None = None
    region: str | None = None

    @model_validator(mode="after")
    def has_identity(self) -> NotebookAddress:
        if not self.notebook_uuid and not (self.notebook_name and self.namespace):
            raise ValueError("notebook address requires UUID or name and namespace")
        return self


class ResourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    environment: str
    target: str
    kind: Literal["job", "notebook"]
    address: JobAddress | NotebookAddress

    @model_validator(mode="after")
    def matching_kind(self) -> ResourceRef:
        if (self.kind == "job") != isinstance(self.address, JobAddress):
            raise ValueError("resource kind does not match address")
        return self


def parse_reference(value: Any, environment: str) -> ResourceRef | None:
    if value is None:
        return None
    try:
        ref = ResourceRef.model_validate(value)
    except (ValidationError, TypeError, ValueError) as exc:
        # Do not echo arbitrary input (it may contain credentials or huge payloads).
        raise MLSpaceError("Invalid resource_ref: use the typed resource address schema.") from exc
    if ref.environment != environment:
        raise MLSpaceError("resource_ref belongs to a different configured environment.")
    return ref


def _supply(values: dict[str, Any], name: str, value: Any) -> None:
    if value is None:
        return
    if name in values and values[name] != value:
        raise MLSpaceError(f"resource_ref conflicts with explicit {name}.")
    values[name] = value


def apply_reference(ref: ResourceRef | None, domain: str, action: str,
                    values: dict[str, Any]) -> None:
    if ref is None:
        return
    if domain == "jobs" and ref.kind == "job" and isinstance(ref.address, JobAddress):
        if action not in {"get", "logs", "list_nodes", "list_pods", "get_params",
                          "get_preemptors", "delete", "restart"}:
            raise MLSpaceError("resource_ref is not supported for this jobs action.")
        _supply(values, "job_name", ref.address.job_name)
        _supply(values, "region", ref.address.region)
        if action == "restart":
            body = values.get("body", {})
            if not isinstance(body, dict):
                raise MLSpaceError("resource_ref restart requires an object body.")
            body = dict(body)
            _supply(body, "job_name", ref.address.job_name)
            values["body"] = body
        return
    if domain == "notebooks" and ref.kind == "notebook" and isinstance(ref.address, NotebookAddress):
        address = ref.address
        if action in {"users_list", "users_set", "users_revoke"}:
            if not (address.notebook_name and address.namespace):
                raise MLSpaceError("resource_ref for users actions requires notebook name and namespace.")
        elif action in {"get", "delete", "pause", "resume", "modify"}:
            if not address.notebook_uuid:
                raise MLSpaceError("resource_ref for this notebook action requires notebook UUID.")
        else:
            raise MLSpaceError("resource_ref is not supported for this notebooks action.")
        # Body region may be a legitimate resume/modify override, not an address.
        for name, value in address.model_dump(exclude_none=True).items():
            _supply(values, name, value)
        return
    raise MLSpaceError("resource_ref kind is not supported for this domain/action.")


def response_references(domain: str, action: str, data: Any, *, environment: str,
                        target: str, values: dict[str, Any], list_cap: int) -> list[dict[str, Any]]:
    """No recursive id/name scan: only known jobs/notebooks resource operations."""
    allowed = {"jobs": {"list", "get", "run", "restart"},
               "notebooks": {"list", "get", "create", "resume", "modify"}}
    if action not in allowed.get(domain, set()):
        return []
    if action == "list":
        rows = data if isinstance(data, list) else None
        if isinstance(data, dict):
            for key in ("items", "jobs", "notebooks", "results"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        rows = rows[:list_cap] if isinstance(rows, list) else []
    else:
        rows = [data]
    refs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # These domain endpoints are workspace-scoped. An explicit owner wins;
        # a foreign owner is not inferred from the context that observed it.
        owner = row.get("workspace_id", target)
        if not isinstance(owner, str) or not owner:
            continue
        body = values.get("body")
        region = row.get("region") or values.get("region")
        if region is None and isinstance(body, dict):
            region = body.get("region")
        if domain == "jobs":
            identity = row.get("job_name")
            # A restart must confirm a NEW identity, not repeat the old address.
            original_name = body.get("job_name") if isinstance(body, dict) else None
            if action == "restart" and identity == original_name:
                continue
            address = {"job_name": identity, "region": region}
            kind = "job"
        else:
            address = {"notebook_uuid": row.get("uid") or row.get("uuid"),
                       "notebook_name": row.get("name"),
                       "namespace": row.get("namespace") or values.get("namespace"),
                       "region": region}
            kind = "notebook"
        try:
            reference = ResourceRef.model_validate({"version": 1, "environment": environment,
                "target": owner, "kind": kind, "address": address})
        except (ValidationError, TypeError, ValueError):
            continue  # an applied write must remain success even if its new address is unknown
        refs.append(reference.model_dump(exclude_none=True))
    return refs


def with_context(formatted: str, *, environment: str, target: str, workspace_name: str,
                 refs: list[dict[str, Any]], response_format: str,
                 kind: str, address_note: str | None = None) -> str:
    data: Any = formatted
    if response_format == "json" and kind != "log":
        try:
            data = json.loads(formatted)
        except (ValueError, TypeError):
            pass
    result = {"request_context": {"environment": environment, "target": target,
                                  "workspace_name": workspace_name},
              "data": data, "resource_refs": refs}
    if address_note:
        result["address_note"] = address_note
    text = json.dumps(result, ensure_ascii=False)
    return f"```json\n{text}\n```" if response_format == "markdown" else text
