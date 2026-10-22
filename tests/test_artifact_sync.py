"""Guard against openapi -> body_schemas.json drift.

Regenerate the artifact in-memory via the generator's own logic and assert it is
byte-identical to the committed src/mlspace_mcp/spec/body_schemas.json. If this
fails, someone changed the bundled OpenAPI spec (or the generator) without
re-running `python scripts/gen_body_schemas.py`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_GEN = _ROOT / "scripts" / "gen_body_schemas.py"
_ARTIFACT = _ROOT / "src" / "mlspace_mcp" / "spec" / "body_schemas.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_body_schemas", _GEN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_artifact_in_sync_with_openapi():
    gen = _load_generator()
    regenerated = gen.render(gen.build())
    committed = _ARTIFACT.read_text()
    assert regenerated == committed, (
        "body_schemas.json is stale: re-run "
        "`python scripts/gen_body_schemas.py`"
    )


def test_ref_rewrite_preserves_nullable_and_description():
    gen = _load_generator()
    rewritten, used = gen.rewrite_refs({'$ref': '#/components/schemas/CronViewModel',
                                      'nullable': True, 'description': 'Optional schedule'})
    assert rewritten == {'$ref': '#/$defs/CronViewModel', 'nullable': True,
                         'description': 'Optional schedule'}
    assert used == {'CronViewModel'}


def test_runtime_schema_omits_examples_but_preserves_fields_and_literal_values():
    gen = _load_generator()
    literal = {'examples': ['literal'], '$ref': '#/components/schemas/NotASchema'}
    schema = {
        'type': 'object', 'required': ['examples'], 'example': {'old': 'endpoint'},
        'properties': {
            'examples': {'type': 'string', 'examples': ['stale']},
            'nested': {'$ref': '#/components/schemas/Real', 'nullable': True},
            'items': {'type': 'array', 'items': {'type': 'string', 'example': 'stale'}},
        },
        'default': literal, 'const': literal, 'enum': [literal],
        'allOf': [{'example': 'stale', 'type': 'object'}],
        '$defs': {'examples': {'type': 'string', 'example': 'stale'}},
    }
    rewritten, used = gen.rewrite_refs(schema)
    assert 'example' not in rewritten
    assert rewritten['properties']['examples'] == {'type': 'string'}
    assert rewritten['required'] == ['examples']
    assert rewritten['properties']['nested'] == {'$ref': '#/$defs/Real', 'nullable': True}
    assert rewritten['properties']['items']['items'] == {'type': 'string'}
    assert rewritten['allOf'] == [{'type': 'object'}]
    assert rewritten['$defs']['examples'] == {'type': 'string'}
    assert rewritten['default'] == rewritten['const'] == literal
    assert rewritten['enum'] == [literal]
    assert used == {'Real'}
    assert 'example' in schema  # caller's source snapshot is not mutated


def test_sr009_routes_are_available_to_the_model():
    gen = _load_generator()
    schemas = gen.spec['components']['schemas']
    assert 'sr009' in schemas['TransferClusterEnum']['enum']
    neighbours = ['pd30', 'pd30.nfs2', 'pd42', 'pd42.nfs2', 'pd42.nfs3',
                  'sr005', 'sr006', 'sr006.nfs2', 'sr006.nfs3', 'sr006.nfs4']
    routes = schemas['TransferMultiClusterEnum']['enum']
    for neighbour in neighbours:
        assert f'{neighbour}->sr009' in routes
        assert f'sr009->{neighbour}' in routes
