#!/usr/bin/env python3
"""Generate src/mlspace_mcp/spec/body_schemas.json from the bundled OpenAPI spec.

For every operation that declares a JSON request body, emit a self-contained
JSON Schema (referenced component schemas inlined under "$defs", refs rewritten
to local "#/$defs/..."). Keyed by "METHOD /path". This is a build artifact,
loaded once at server startup — never on the per-request hot path.

Re-run after updating the bundled spec:  python scripts/gen_body_schemas.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "src" / "mlspace_mcp" / "spec" / "openapi.json"
OUT = ROOT / "src" / "mlspace_mcp" / "spec" / "body_schemas.json"

spec = json.loads(SPEC.read_text())
components = spec.get("components", {}).get("schemas", {})
_METHODS = ("get", "post", "put", "patch", "delete")


def rewrite_refs(node):
    """Rewrite schema refs and omit examples unused by runtime help/validation.

    Visit schema positions only: a property named ``examples`` and literal values
    in default/enum/const are data, not annotations or component references.
    """
    used: set[str] = set()
    maps = {'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'}
    children = {'items', 'additionalItems', 'additionalProperties', 'contains',
                'propertyNames', 'not', 'if', 'then', 'else', 'unevaluatedItems',
                'unevaluatedProperties', 'contentSchema'}
    arrays = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}

    def walk(n):
        if not isinstance(n, dict):
            return copy.deepcopy(n)
        result = {}
        for key, value in n.items():
            if key in {'example', 'examples'}:
                continue
            if (key == '$ref' and isinstance(value, str)
                    and value.startswith('#/components/schemas/')):
                name = value.split('/')[-1]
                used.add(name)
                result[key] = f'#/$defs/{name}'
            elif key in maps and isinstance(value, dict):
                result[key] = {name: walk(schema) for name, schema in value.items()}
            elif key == 'dependencies' and isinstance(value, dict):
                result[key] = {name: walk(schema) for name, schema in value.items()}
            elif (key in arrays or key == 'items') and isinstance(value, list):
                result[key] = [walk(schema) for schema in value]
            elif key in children:
                result[key] = walk(value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    return walk(node), used


def collect_defs(seed_names: set[str]) -> dict:
    """Transitively rewrite + gather all referenced component schemas into $defs."""
    defs: dict[str, dict] = {}
    queue = list(seed_names)
    while queue:
        name = queue.pop()
        if name in defs or name not in components:
            continue
        rewritten, more = rewrite_refs(components[name])
        defs[name] = rewritten
        queue.extend(more - defs.keys())
    return defs


def build() -> dict[str, dict]:
    """Return the {"METHOD /path": schema} mapping, deterministically ordered.

    Ordering is fully normalized so regeneration is byte-identical: the queue in
    collect_defs() drains a set (non-deterministic), so we sort each schema's
    "$defs" keys here, and sort the top-level mapping keys. json.dumps(...,
    sort_keys=True) at write time then normalizes ordering everywhere else.
    """
    out: dict[str, dict] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in _METHODS or not isinstance(op, dict):
                continue
            rb = op.get("requestBody") or {}
            schema = (((rb.get("content") or {}).get("application/json")) or {}).get("schema")
            if not schema:
                continue
            body_schema, used = rewrite_refs(schema)
            defs = collect_defs(used)
            if defs:
                body_schema["$defs"] = {name: defs[name] for name in sorted(defs)}
            out[f"{method.upper()} {path}"] = body_schema

    return {key: out[key] for key in sorted(out)}


def render(out: dict[str, dict]) -> str:
    """Serialize the mapping to deterministic (byte-stable) JSON text."""
    return json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    out = build()
    OUT.write_text(render(out))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(out)} request-body schemas, "
          f"{OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
