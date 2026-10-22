"""Spec-driven request-body help + LENIENT validation.

Schemas come from the bundled build artifact ``spec/body_schemas.json`` (one
self-contained JSON Schema per "METHOD /path", refs under ``$defs``). Loaded once.

The live API is the authoritative validator.
``lint`` is **fail-open** — it reports only HIGH-CONFIDENCE structural problems
(missing required fields, clear scalar-type mismatches) and stays silent on
anyOf/oneOf variants, unknown constructs and extra properties. Enum membership
is shown as a hint but not enforced because the live API can add values.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_ARTIFACT = Path(__file__).parent / "spec" / "body_schemas.json"
_MAX_DEPTH = 8


@lru_cache(maxsize=1)
def _all() -> dict[str, dict]:
    if not _ARTIFACT.exists():
        return {}
    return json.loads(_ARTIFACT.read_text())


def schema_for(method: str, path: str) -> dict | None:
    return _all().get(f"{method.upper()} {path}")


def _resolve(node: Any, defs: dict) -> Any:
    """Resolve a $ref / single-ref allOf to its target schema (one hop)."""
    seen = 0
    while isinstance(node, dict) and seen < 16:
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            target = defs.get(name, {})
            node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        elif "allOf" in node and len(node["allOf"]) == 1 and "$ref" in node["allOf"][0]:
            name = node["allOf"][0]["$ref"].split("/")[-1]
            target = defs.get(name, {})
            # merge sibling keys (e.g. description) over the target
            node = {**target, **{k: v for k, v in node.items() if k != "allOf"}}
        else:
            break
        seen += 1
    return node


def _types(schema: dict) -> set[str]:
    t = schema.get("type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return set(t)
    return set()


def _enum_str(schema: dict) -> str:
    e = schema.get("enum")
    if not isinstance(e, list):
        return ""
    if len(e) <= 6:
        return ":" + "|".join(str(x) for x in e)
    return f":<{len(e)} options>"  # mark long enums instead of dropping silently


# ---- description hint -------------------------------------------------------


def summarize(schema: dict) -> str:
    """One-line shape hint for the tool description (required fields + nested)."""
    defs = schema.get("$defs", {})
    root = _resolve(schema, defs)
    if not isinstance(root, dict) or "properties" not in root:
        return ""
    required = root.get("required", []) or []
    props = root.get("properties", {})

    def field(name: str) -> str:
        sub = _resolve(props.get(name, {}), defs)
        if "object" in _types(sub) or "properties" in sub:
            sub_req = sub.get("required", []) or []
            inner = ", ".join(
                n + _enum_str(_resolve(sub.get("properties", {}).get(n, {}), defs))
                for n in sub_req
            )
            return f"{name}{{{inner}}}" if inner else f"{name}{{...}}"
        return name + _enum_str(sub)

    req = [field(n) for n in required]
    optional_all = [n for n in props if n not in required]
    optional = optional_all[:6]
    parts = []
    if req:
        parts.append("required: " + ", ".join(req))
    if optional:
        more = f" (+{len(optional_all) - len(optional)} more)" if len(optional_all) > len(optional) else ""
        parts.append("optional: " + ", ".join(optional) + more)
    return "; ".join(parts)


# ---- lenient validation -----------------------------------------------------

_JSON_TYPE: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _type_ok(value: Any, types: set[str]) -> bool:
    if not types:
        return True
    if value is None:
        return True  # nullability is often implicit; stay lenient
    for t in types:
        py = _JSON_TYPE.get(t)
        if py is None:
            return True  # unknown type token -> don't judge
        if t in ("integer", "number") and isinstance(value, bool):
            continue  # bool is not a number here
        if t == "integer" and isinstance(value, float) and value.is_integer():
            return True  # 5.0 for an integer field: backends coerce — don't reject
        if isinstance(value, py):
            return True
    return False


def lint(schema: dict, value: Any) -> list[str]:
    """Return high-confidence problems with ``value`` against ``schema`` (or [])."""
    defs = schema.get("$defs", {})
    errors: list[str] = []

    def check(node: Any, val: Any, path: str, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        node = _resolve(node, defs)
        if not isinstance(node, dict):
            return
        # unions / combinators: too ambiguous to enforce safely -> skip
        if any(k in node for k in ("anyOf", "oneOf")) or (
            "allOf" in node and len(node.get("allOf", [])) > 1
        ):
            return

        types = _types(node)
        if types and not _type_ok(val, types):
            errors.append(f"{path or 'body'}: expected {'/'.join(sorted(types))}, got {type(val).__name__}")
            return

        # NOTE: enum membership is deliberately NOT enforced here. Enums are exactly
        # what a cloud platform expands over time (new regions/connectors/...); a
        # hard local check would fail-closed on a value the live API now accepts,
        # violating this module's fail-open contract. The API (422) validates enums;
        # summarize() still surfaces the known values as a hint to the model.

        if ("object" in types or "properties" in node) and isinstance(val, dict):
            for req in node.get("required", []) or []:
                # flag only when the KEY is absent; explicit null is left to the API
                # (a required+nullable field sent as null is valid — never reject it)
                if req not in val:
                    errors.append(f"{path + '.' if path else ''}{req}: required field is missing")
            for key, sub in (node.get("properties") or {}).items():
                if key in val and val[key] is not None:
                    check(sub, val[key], f"{path + '.' if path else ''}{key}", depth + 1)
        elif "array" in types and isinstance(val, list):
            items = node.get("items")
            if isinstance(items, dict) and val:
                check(items, val[0], f"{path}[]", depth + 1)

    try:
        check(schema, value, "", 0)
    except Exception:
        return []  # fail-open: never block on an internal hiccup
    return errors
