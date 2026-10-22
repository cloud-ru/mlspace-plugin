"""The distributed plugin must survive installation outside the source checkout."""

import importlib.util
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from mlspace_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def builder():
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "scripts/build_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_contains_same_plugin_for_both_clients(tmp_path):
    root, archive = builder().build_release(tmp_path / "with spaces")
    plugin = root
    portable = json.loads((plugin / "plugin.json").read_text())
    assert portable["version"] == __version__
    assert len(list((plugin / "skills").glob("*/SKILL.md"))) == 18
    for client in (".agents/plugins", ".claude-plugin"):
        marketplace = json.loads((root / client / "marketplace.json").read_text())
        entry = marketplace["plugins"][0]
        source = entry["source"]
        path = source["path"] if isinstance(source, dict) else source
        assert (root / path).resolve() == plugin
    for config in ("mcp.json", ".mcp.json"):
        server = json.loads((plugin / config).read_text())["mcpServers"]["mlspace"]
        assert server["args"][3] == ("${PLUGIN_ROOT}" if config == "mcp.json" else "${CLAUDE_PLUGIN_ROOT}")
    assert not list(root.rglob('*.whl'))
    with ZipFile(archive) as zipped:
        names = zipped.namelist()
        assert f"{root.name}/.claude-plugin/marketplace.json" in names
        assert not any(Path(name).name == ".env" or ".venv" in Path(name).parts for name in names)
        assert len(names) == len([p for p in root.rglob("*") if p.is_file()])
    assert (root / "SHA256SUMS").is_file()


def test_does_not_overwrite_existing_release(tmp_path):
    module = builder()
    root, _ = module.build_release(tmp_path / "out")
    marker = root / "keep.txt"
    marker.write_text("existing user data")
    with pytest.raises(FileExistsError):
        module.build_release(tmp_path / "out")
    assert marker.read_text() == "existing user data"
