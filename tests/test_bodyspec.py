from __future__ import annotations

import importlib

import pytest
from specutil import load_spec

from mlspace_mcp import bodyspec, registry
from mlspace_mcp.server import DOMAIN_MODULES

NB_CREATE = ("POST", "/public/v2/notebooks/v2/{namespace}/notebook")


def _nb_schema():
    s = bodyspec.schema_for(*NB_CREATE)
    assert s is not None, "notebook create body schema must be bundled"
    return s


# ---- summarize --------------------------------------------------------------


def test_summarize_lists_required_and_nested_enum():
    out = bodyspec.summarize(_nb_schema())
    assert "name" in out and "instance_type" in out
    # nested required of `image`, including the enum on `type`
    assert "image{" in out
    assert "type:datahub|custom" in out


# ---- lint: high-confidence catches ------------------------------------------


def test_lint_passes_valid_body():
    body = {"name": "x", "image": {"name": "i", "tag": "0.1", "type": "datahub"}, "instance_type": "free.0gpu"}
    assert bodyspec.lint(_nb_schema(), body) == []


def test_lint_catches_missing_nested_required():
    body = {"name": "x", "image": {"name": "i", "tag": "0.1"}, "instance_type": "free.0gpu"}
    issues = bodyspec.lint(_nb_schema(), body)
    assert any("image.type" in i and "missing" in i for i in issues)


def test_lint_catches_missing_top_required():
    issues = bodyspec.lint(_nb_schema(), {"name": "x"})
    joined = " ".join(issues)
    assert "image" in joined and "instance_type" in joined


def test_lint_does_not_block_unknown_enum_value():
    # forward-compat: a value not in the baked enum is NOT rejected locally
    # (the live API validates enums; a hard check would fail-closed on new values)
    body = {"name": "x", "image": {"name": "i", "tag": "0.1", "type": "WRONG"}, "instance_type": "free.0gpu"}
    assert bodyspec.lint(_nb_schema(), body) == []


def test_lint_catches_scalar_type_mismatch():
    body = {"name": 123, "image": {"name": "i", "tag": "0.1", "type": "datahub"}, "instance_type": "free.0gpu"}
    assert any("name" in i and "string" in i for i in bodyspec.lint(_nb_schema(), body))


# ---- lint: must NOT over-reject (fail-open) ---------------------------------


def test_lint_allows_extra_fields():
    body = {"name": "x", "image": {"name": "i", "tag": "0.1", "type": "datahub"},
            "instance_type": "free.0gpu", "totally_unknown_field": 42}
    assert bodyspec.lint(_nb_schema(), body) == []


def test_lint_does_not_enforce_union_variants():
    # data_transfer connector create has an anyOf `parameters` — providing the
    # required top-level fields with an arbitrary parameters dict must NOT be rejected.
    s = bodyspec.schema_for("POST", "/public/v2/data_transfer/v3/connectors")
    if s is None:
        pytest.skip("connector create schema not present")
    body = {"name": "c", "source_type": "s3custom", "parameters": {"anything": "goes"}}
    assert bodyspec.lint(s, body) == []


def test_lint_fails_open_on_garbage_schema():
    assert bodyspec.lint({"$ref": "#/$defs/Missing"}, {"a": 1}) == []


def test_ref_sibling_union_keeps_validation_lenient():
    schema = {
        'type': 'object', 'properties': {
            'value': {'$ref': '#/$defs/Value', 'anyOf': [{'type': 'string'}, {'type': 'object'}]},
        }, '$defs': {'Value': {'type': 'string'}},
    }
    assert bodyspec.lint(schema, {'value': {'future': 'shape'}}) == []


def test_ref_sibling_required_fields_are_included_in_help():
    schema = {'$ref': '#/$defs/Value', 'required': ['name'],
              '$defs': {'Value': {'type': 'object', 'properties': {'name': {'type': 'string'}}}}}
    assert bodyspec.summarize(schema) == 'required: name'


def test_lint_allows_explicit_null_for_required_nullable_field():
    # a required+nullable field sent as null is valid — must NOT be rejected
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": ["string", "null"]}}}
    assert bodyspec.lint(schema, {"name": None}) == []
    # but an absent required key IS flagged
    assert bodyspec.lint(schema, {}) == ["name: required field is missing"]


def test_lint_accepts_integral_float_for_integer_field():
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
    assert bodyspec.lint(schema, {"n": 5.0}) == []  # backends coerce — don't reject
    assert bodyspec.lint(schema, {"n": 5.5})  # genuinely non-integral -> flagged


# ---- coverage proof: no body action is silently unguided --------------------


def _implemented_domains():
    out = {}
    for name in DOMAIN_MODULES:
        full = f"mlspace_mcp.tools.{name}"
        try:
            out[name] = importlib.import_module(full).DOMAIN
        except ModuleNotFoundError as exc:
            if exc.name == full:
                continue
            raise
    return out


def _spec_op(method: str, path: str) -> dict:
    return load_spec()["paths"][path][method.lower()]


def test_every_json_body_action_has_a_bundled_schema():
    """For every write action that carries a JSON request body in the spec, a
    body schema must be bundled (so the model is guided + lint can run). Actions
    whose spec body is absent (free-form predict) or non-JSON (multipart) are
    allowed to have no schema."""
    missing = []
    free_form = []
    for domain, dt in _implemented_domains().items():
        for action, op in dt.actions.items():
            if not op.body_field:
                continue
            rb = _spec_op(op.method, op.path).get("requestBody") or {}
            has_json = "application/json" in (rb.get("content") or {})
            schema = bodyspec.schema_for(op.method, op.path)
            if has_json and schema is None:
                missing.append(f"{domain}.{action}")
            elif not has_json and schema is None:
                free_form.append(f"{domain}.{action}")
    assert not missing, f"JSON-body actions with no bundled schema: {missing}"
    # free_form is informational — these are intentionally opaque (predict / multipart)
    assert isinstance(free_form, list)


def test_description_includes_body_shape_for_create():
    from mlspace_mcp.tools.notebooks import DOMAIN as NB
    desc = registry.build_description(NB, registry.available_actions(NB, readonly=False))
    assert "image{" in desc  # the create action's body shape is surfaced to the model


# ---- systemic false-reject sweep across ALL bundled body schemas ------------


def _resolve(node, defs, seen=0):
    while isinstance(node, dict) and seen < 16:
        seen += 1
        if "$ref" in node:
            node = defs.get(node["$ref"].split("/")[-1], {})
        elif "allOf" in node and len(node["allOf"]) == 1 and "$ref" in node["allOf"][0]:
            node = defs.get(node["allOf"][0]["$ref"].split("/")[-1], {})
        else:
            break
    return node


def _minimal_valid(schema, defs, depth=0):
    """Build a minimal body satisfying required fields/types/enums from a schema."""
    if depth > 8:
        return {}
    node = _resolve(schema, defs)
    if not isinstance(node, dict):
        return {}
    if node.get("enum"):
        return node["enum"][0]
    if any(k in node for k in ("anyOf", "oneOf")):
        return {}  # lint intentionally skips union variants
    t = node.get("type")
    types = {t} if isinstance(t, str) else set(t or [])
    if "string" in types:
        return "x"
    if "integer" in types:
        return 1
    if "number" in types:
        return 1.0
    if "boolean" in types:
        return True
    if "array" in types:
        return []
    if "object" in types or "properties" in node:
        props = node.get("properties", {})
        return {r: _minimal_valid(props.get(r, {}), defs, depth + 1) for r in node.get("required", [])}
    return {}


@pytest.mark.parametrize("key", sorted(bodyspec._all().keys()))
def test_lint_never_false_rejects_a_minimal_valid_body(key):
    schema = bodyspec._all()[key]
    body = _minimal_valid(schema, schema.get("$defs", {}))
    issues = bodyspec.lint(schema, body)
    assert issues == [], f"{key}: lint falsely rejected a minimal-valid body: {issues}"
