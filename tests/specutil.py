"""Test-only helpers over the bundled OpenAPI spec.

The spec is build/test data: it is loaded here (and by the slice generator),
never on the server hot path.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import mlspace_mcp

SPEC_PATH = Path(mlspace_mcp.__file__).parent / "spec" / "openapi.json"

# Mirror of the slice generator's domain mapping (merges + renames).
DOMAIN_MAP = {
    "inference": "inference",
    "async_inferences": "async_inference",
    "dalle": "dalle",
    "jobs": "jobs",
    "build-image": "build_image",
    "notebooks": "notebooks",
    "tensorboards": "tensorboards",
    "workspaces": "workspaces",
    "allocations": "allocations",
    "queues": "queues",
    "shared-cluster-queues": "queues",
    "docker_registry": "docker_registry",
    "data_transfer": "data_transfer",
    "configs": "resources",
    "instance_types": "resources",
    "nodes": "resources",
    "limiter": "resources",
    "service_auth": "_auth",
}

_METHODS = ("get", "post", "put", "patch", "delete")


def domain_of(path: str) -> str:
    m = re.match(r"/public/v2/([a-z0-9_\-]+)", path)
    seg = m.group(1) if m else "?"
    return DOMAIN_MAP.get(seg, seg)


@lru_cache(maxsize=1)
def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


@lru_cache(maxsize=1)
def live_ops_by_domain() -> dict[str, set[tuple[str, str]]]:
    """Non-deprecated (METHOD, path) pairs, grouped by tool domain."""
    out: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for path, ops in load_spec()["paths"].items():
        for method, op in ops.items():
            if method not in _METHODS or not isinstance(op, dict):
                continue
            if op.get("deprecated"):
                continue
            out[domain_of(path)].add((method.upper(), path))
    return dict(out)
