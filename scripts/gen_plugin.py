"""Generate plugin manifests alongside the bundled Python source.

Skills in ``skills/*/SKILL.md`` are hand-written sources. Manifests are
derived: their version tracks ``mlspace_mcp.__version__`` and every client config
launches the source bundled in the installed plugin. ``tests/test_plugin_bundle.py``
runs this module with ``--check`` and fails when a manifest on disk differs.

Spec: https://github.com/agentplugins/agent-plugins-spec (v1.0.0)
      https://agentskills.io/specification (SKILL.md)

Usage:
    python scripts/gen_plugin.py            # write the manifests
    python scripts/gen_plugin.py --check    # exit 1 if a manifest is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mlspace_mcp import __version__  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT

SPEC = "1.0.0"
PLUGIN_SCHEMA = f"https://agent-plugins.org/schemas/{SPEC}/plugin.schema.json"
MCP_SCHEMA = f"https://agent-plugins.org/schemas/{SPEC}/mcp.schema.json"

def build() -> dict[str, str]:
    """Return the generated manifests as {relative path: content}."""
    identity = {
        "name": "mlspace",
        "version": __version__,
        "description": (
            "Cloud.ru MLSpace: training jobs, inference, notebooks, data transfer, "
            "registry and compute catalog — an MCP server plus skills."
        ),
    }
    metadata = {
        **identity,
        "author": {"name": "Cloud.ru"},
        "homepage": "https://cloud.ru/docs/aicloud/mlspace",
        "license": "MIT",
        "keywords": ["mlspace", "cloud.ru", "mlops", "training", "inference", "gpu"],
    }
    manifests = {
        "plugin.json": {"$schema": PLUGIN_SCHEMA, **metadata},
        ".codex-plugin/plugin.json": {
            **identity,
            "author": {"name": "Cloud.ru"},
            "skills": "./skills/",
            "mcpServers": "./mcp.json",
            "interface": {
                "displayName": "MLSpace",
                "shortDescription": "Manage Cloud.ru MLSpace with MCP and skills",
                "longDescription": identity["description"],
                "developerName": "Cloud.ru",
                "category": "Developer tools",
                "capabilities": ["Read", "Write"],
                "defaultPrompt": ["Show jobs across my selected MLSpace workspaces."],
            },
        },
        ".claude-plugin/plugin.json": metadata,
    }
    for filename in ("mcp.json", ".mcp.json"):
        manifests[filename] = {
            **({"$schema": MCP_SCHEMA} if filename == "mcp.json" else {}),
            "mcpServers": {
                "mlspace": {
                    "type": "stdio",
                    "command": "uvx",
                    # Resolve locally: no separately published MLSpace package.
                    "args": [
                        "--python", ">=3.10",
                        "--from",
                        "${PLUGIN_ROOT}" if filename == "mcp.json" else "${CLAUDE_PLUGIN_ROOT}",
                        "mlspace-plugin", "--transport", "stdio",
                    ],
                }
            },
        }
    return {
        name: json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        for name, manifest in manifests.items()
    }


def build_marketplaces() -> dict[str, str]:
    marketplaces = {
        ".agents/plugins/marketplace.json": {
            "name": "mlspace", "interface": {"displayName": "MLSpace"},
            "plugins": [{"name": "mlspace", "source": {"source": "local", "path": "./"},
                         "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                         "category": "Productivity"}],
        },
        ".claude-plugin/marketplace.json": {
            "name": "mlspace", "owner": {"name": "Cloud.ru"},
            "plugins": [{"name": "mlspace", "source": "./"}],
        },
    }
    return {name: json.dumps(data, indent=2) + "\n" for name, data in marketplaces.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify manifests match; do not write")
    args = ap.parse_args(argv)

    plugin_files = build()
    files = plugin_files | build_marketplaces()

    if args.check:
        stale = [
            rel
            for rel, want in files.items()
            if not (ROOT / rel).is_file() or (ROOT / rel).read_text() != want
        ]
        if stale:
            for rel in stale:
                print(f"stale: {rel}", file=sys.stderr)
            print("run: python scripts/gen_plugin.py", file=sys.stderr)
            return 1
        print(f"plugin manifests are up to date ({len(files)} files)")
        return 0

    for rel, content in files.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    print(f"wrote {len(files)} manifests to {PLUGIN.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
