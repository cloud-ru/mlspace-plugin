"""Conformance of the shipped Agent Plugins 1.0.0 bundle in the repository root.

The bundle is what other agents (Codex, Cursor, Copilot, Gemini CLI, …) actually
install, so it must stay VALID against the two specs, and its generated manifests
must stay IN SYNC with the package version. Rules below are transcribed from the specification
text, which both specs declare authoritative over their JSON schemas:

  Agent Plugins 1.0.0  https://github.com/agentplugins/agent-plugins-spec
  Agent Skills         https://agentskills.io/specification
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gen_plugin  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT
SPEC = "1.0.0"

# §5.5 plugin name / Agent Skills `name`: lowercase alnum, no leading/trailing or
# doubled separator. Plugins additionally allow '.', skills do not.
_PLUGIN_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_SKILL_NAME = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _frontmatter(path: Path) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter as a skill loader would; skills are edited by hand."""
    import yaml

    text = path.read_text()
    assert text.startswith("---\n"), f"{path} must open with YAML frontmatter"
    _, fm, body = text.split("---\n", 2)
    return yaml.safe_load(fm), body


# --- manifests are generated, never hand-edited --------------------------------


def test_manifests_are_in_sync_with_the_package():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_plugin.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_manifest_uses_only_the_closed_field_set():
    manifest = json.loads((PLUGIN / "plugin.json").read_text())
    permitted = {
        "$schema", "name", "version", "description", "author",
        "homepage", "repository", "license", "keywords", "extensions",
    }
    assert set(manifest) <= permitted, f"unknown top-level: {set(manifest) - permitted}"
    assert manifest["$schema"] == f"https://agent-plugins.org/schemas/{SPEC}/plugin.schema.json"
    assert _PLUGIN_NAME.match(manifest["name"]) and len(manifest["name"]) <= 64


def test_manifest_version_tracks_the_package():
    from mlspace_mcp import __version__

    assert json.loads((PLUGIN / "plugin.json").read_text())["version"] == __version__


@pytest.mark.parametrize("path", [".codex-plugin/plugin.json", ".claude-plugin/plugin.json"])
def test_client_manifests_share_identity_and_one_skill_tree(path):
    files = gen_plugin.build()
    assert path in files, f"missing client manifest: {path}"
    manifest = json.loads(files[path])
    portable = json.loads(files["plugin.json"])
    for key in ("name", "version", "description"):
        assert manifest[key] == portable[key]
    assert "$schema" not in manifest
    if path.startswith(".codex"):
        assert manifest["skills"] == "./skills/"
        assert manifest["mcpServers"] == "./mcp.json"
        assert manifest["author"]["name"] == "Cloud.ru"
        assert manifest["interface"]["displayName"] == "MLSpace"
    else:
        assert manifest["author"] == {"name": "Cloud.ru"}
        assert manifest["license"] == "MIT"
        assert manifest["keywords"] == portable["keywords"]


@pytest.mark.parametrize("config", ["mcp.json", ".mcp.json"])
def test_mcp_launches_bundled_plugin_source(config):
    server = json.loads(gen_plugin.build()[config])["mcpServers"]["mlspace"]
    assert server["command"] == "uvx"
    assert server["args"] == [
        "--python", ">=3.10", "--from", "${PLUGIN_ROOT}" if config == "mcp.json" else "${CLAUDE_PLUGIN_ROOT}",
        "mlspace-plugin", "--transport", "stdio",
    ]
    assert "env" not in server


@pytest.mark.parametrize("manifest", [".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"])
def test_repository_marketplace_points_to_existing_plugin(manifest):
    data = json.loads((ROOT / manifest).read_text())
    assert data['name'] == 'mlspace'
    source = data['plugins'][0]['source']
    relative = source['path'] if isinstance(source, dict) else source
    assert (ROOT / relative / 'plugin.json').is_file()
    assert (ROOT / relative / 'skills/mlspace-list-running-jobs/SKILL.md').is_file()


# --- Agent Plugins §7.2: MCP configuration ------------------------------------


def test_mcp_config_declares_one_stdio_server_with_the_matching_schema_version():
    mcp = json.loads((PLUGIN / "mcp.json").read_text())
    assert set(mcp) == {"$schema", "mcpServers"}  # §7.2.1: no other top-level fields
    # §10.1: an mcp.json targeting a different version than plugin.json is invalid.
    assert mcp["$schema"] == f"https://agent-plugins.org/schemas/{SPEC}/mcp.schema.json"

    server = mcp["mcpServers"]["mlspace"]
    assert set(server) <= {"type", "command", "args", "env", "cwd"}  # stdio variant
    assert server["type"] == "stdio"
    # §7.2.1: a single executable token — a bare name or './'-relative, never a shell line.
    assert server["command"] == "uvx"
    assert " " not in server["command"]


def test_bundle_carries_no_credentials():
    """§7.2.1/§9.2: `env` and `headers` are visible package data, not a secret channel."""
    server = json.loads((PLUGIN / "mcp.json").read_text())["mcpServers"]["mlspace"]
    env = server.get("env", {})
    # every secret the server can read, independent of which ones are required
    forbidden = {"MLSPACE_CLIENT_ID", "MLSPACE_CLIENT_SECRET", "MLSPACE_API_KEY",
                 "MLSPACE_WORKSPACE_ID", "MLSPACE_WORKSPACES"}
    assert not forbidden & set(env)
    # §9.2: the client supplies these itself; declaring them invalidates the entry.
    assert not {"PLUGIN_ROOT", "PLUGIN_DATA"} & set(env)

    # Assignment-shaped only: the playbooks legitimately NAME these things in prose
    # ("workspaces get_api_key"), so the words alone would be noise. What must never
    # appear is a word bound to a value.
    secret = re.compile(
        r"(client[_-]?secret|api[_-]?key|password|access[_-]?token)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_.-]{12,}",
        re.I,
    )
    for path in [*(PLUGIN / "skills").glob("*/SKILL.md"), *(PLUGIN / name for name in gen_plugin.build())]:
        if path.is_file():
            found = secret.search(path.read_text())
            assert not found, f"possible secret in {path}: {found.group(0)[:40]}"


# --- Agent Skills: SKILL.md ---------------------------------------------------


@pytest.mark.parametrize("skill_dir", sorted((PLUGIN / "skills").iterdir()), ids=lambda p: p.name)
def test_skill_frontmatter_conforms(skill_dir: Path):
    fields, body = _frontmatter(skill_dir / "SKILL.md")

    name = fields["name"]
    assert name == skill_dir.name, "the spec requires name to match the parent directory"
    assert _SKILL_NAME.match(name) and 1 <= len(name) <= 64

    description = fields["description"]
    assert 1 <= len(description) <= 1024
    # Startup loads ONLY name+description, so activation hinges on this line naming
    # both the product and the tools the procedure drives.
    assert "MLSpace" in description
    assert body.strip()


async def test_skill_descriptions_name_only_tools_the_skill_actually_drives(settings_rw):
    """A description claiming an unused tool dilutes matching and misleads the model."""
    from mlspace_mcp.server import build_server

    registered = {tool.name for tool in await build_server(settings_rw).list_tools()}
    for skill in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        fields, body = _frontmatter(skill)
        claimed = set(re.findall(r"mlspace_[a-z_]+", fields["description"]))
        assert claimed <= registered
        for tool in claimed:
            word = tool.removeprefix("mlspace_").replace("_", "[_ ]")
            assert re.search(rf"\b({tool}|{word}[ _][a-z_]+)\b", body), (
                f"{skill.parent.name} claims {tool} but never calls it"
            )
