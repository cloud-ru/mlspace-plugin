"""Contract/drift test: every implemented domain tool maps bijectively onto the
live (non-deprecated) operations of its domain in the bundled spec.

This catches: orphaned ops (a live endpoint no action reaches), stale ops (an
action pointing at a deprecated/removed endpoint), duplicate mappings, and typos
in method/path. It auto-extends as domain modules are added.
"""

from __future__ import annotations

import importlib

import pytest
from specutil import live_ops_by_domain

from mlspace_mcp.server import DOMAIN_MODULES


def _load_implemented():
    loaded = {}
    for name in DOMAIN_MODULES:
        full = f"mlspace_mcp.tools.{name}"
        try:
            module = importlib.import_module(full)
        except ModuleNotFoundError as exc:
            if exc.name == full:
                continue
            raise
        loaded[name] = module.DOMAIN
    return loaded


IMPLEMENTED = _load_implemented()

# Live spec ops we DELIBERATELY do not expose as actions, with the reason. These
# are subtracted from the "orphaned" check so the bijection stays meaningful for
# everything else. Keep this list tiny and well-justified.
INTENTIONALLY_EXCLUDED: dict[str, set[tuple[str, str]]] = {
    # Creating/deleting an entire workspace is a catastrophic blast radius on a
    # shared company-wide server — workspace lifecycle stays in the console.
    "workspaces": {
        ("POST", "/public/v2/workspaces/v3/"),       # bootstrap
        ("DELETE", "/public/v2/workspaces/v3/"),      # debootstrap
    },
    # Spec 2.8.1 ships newer versions of two notebook reads that are byte-identical
    # to the versions already exposed (verified live 2026-08-03: same status, same
    # 20 keys, same 404 on portal_ref, same missing fields vs `list`). Neither is
    # marked deprecated, so the contract gives a caller no way to choose. Exposing
    # both would make the model pick between indistinguishable actions, which costs
    # tokens and invites arbitrary choices — so each concept keeps exactly one action.
    "notebooks": {
        ("GET", "/public/v2/notebooks/v3/notebooks"),        # ≡ v2 `list`
        ("GET", "/public/v2/notebooks/v2/notebook/{notebook_uuid}"),  # ≡ v1 `get`
    },
}


def test_at_least_inference_present():
    assert "inference" in IMPLEMENTED, "the inference exemplar must exist"


@pytest.mark.parametrize("domain", sorted(IMPLEMENTED))
def test_domain_is_bijection_onto_live_ops(domain):
    dt = IMPLEMENTED[domain]
    live = live_ops_by_domain().get(domain, set())
    assert live, f"no live ops found for domain {domain}"

    action_pairs = [(op.method.upper(), op.path) for op in dt.actions.values()]

    # no duplicate (method, path) across actions
    assert len(action_pairs) == len(set(action_pairs)), (
        f"{domain}: two actions map to the same (method, path)"
    )

    mapped = set(action_pairs)

    stale = mapped - live
    assert not stale, f"{domain}: actions point at non-live/unknown ops: {sorted(stale)}"

    excluded = INTENTIONALLY_EXCLUDED.get(domain, set())
    # a deliberate exclusion must still be a real live op (else the reason is stale)
    bogus_exclusions = excluded - live
    assert not bogus_exclusions, (
        f"{domain}: INTENTIONALLY_EXCLUDED lists non-live ops: {sorted(bogus_exclusions)}"
    )

    orphaned = live - mapped - excluded
    assert not orphaned, f"{domain}: live ops with no action (uncovered): {sorted(orphaned)}"


@pytest.mark.parametrize("domain", sorted(IMPLEMENTED))
def test_domain_tool_shape(domain):
    dt = IMPLEMENTED[domain]
    assert dt.name == f"mlspace_{domain}"
    assert dt.actions, f"{domain}: no actions"
    # every path placeholder must be filled by a path_param mapping
    import re

    for action, op in dt.actions.items():
        placeholders = set(re.findall(r"{([^}]+)}", op.path))
        mapped_placeholders = set(op.path_params.keys())
        assert placeholders == mapped_placeholders, (
            f"{domain}.{action}: path placeholders {placeholders} != "
            f"path_params keys {mapped_placeholders}"
        )
        # required path fields must exist as params
        param_names = {p.name for p in dt.params}
        for fname in op.path_params.values():
            assert fname in param_names, f"{domain}.{action}: path field '{fname}' not in PARAMS"
        if op.body_field:
            assert op.body_field in param_names, (
                f"{domain}.{action}: body_field '{op.body_field}' not in PARAMS"
            )
