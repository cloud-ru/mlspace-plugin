"""The shipped skills must drive the tools the server actually registers.

Skills in skills/*/SKILL.md are hand-written recipes. These are source
checks against the registered tool surface, not an LLM dialogue evaluation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from mlspace_mcp import bodyspec
from mlspace_mcp.registry import resolve_request
from mlspace_mcp.server import CONTEXT_INSTRUCTIONS, DOMAIN_MODULES, INSTRUCTIONS, build_server
from mlspace_mcp.tools.queues import DOMAIN as QUEUES

SKILLS = {
    path.parent.name: path.read_text()
    for path in sorted((Path(__file__).resolve().parents[1] / "skills").glob("*/SKILL.md"))
}


def _procedure(name: str) -> str:
    return SKILLS[name].split("## Procedure", 1)[1]


def _domain_actions() -> dict[str, set[str]]:
    from importlib import import_module

    return {d: set(import_module(f"mlspace_mcp.tools.{d}").DOMAIN.actions) for d in DOMAIN_MODULES}


DOMAIN_ACTIONS = _domain_actions()
ALL_ACTIONS = set().union(*DOMAIN_ACTIONS.values())
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# snake_case words after a domain that are field names in prose, not actions
PROSE_FIELDS = {"instance_type"}


def _references(body: str) -> list[tuple[str, str]]:
    """(domain, word) pairs like "jobs list_pods" that must name a real action.

    A word counts when it is an action of some domain or looks like one (snake_case),
    so an invented "jobs purge_all" is caught too. Tool names and PROSE_FIELDS are
    skipped; plain single words ("jobs across") are prose.
    """
    pairs = []
    tokens = body.split()
    for first, second in zip(tokens, tokens[1:], strict=False):
        domain = first.strip(".,;:()[]{}!?\"'`").lower().removeprefix("mlspace_")
        if domain not in DOMAIN_ACTIONS:
            continue
        match = _IDENT.match(second.lstrip(".,;:()[]{}!?\"'`*-"))
        word = match.group(0).lower() if match else ""
        if word.startswith("mlspace_") or word in PROSE_FIELDS:
            continue
        if word in ALL_ACTIONS or "_" in word:
            pairs.append((domain, word))
    return pairs


def test_eighteen_skills_ship():
    assert len(SKILLS) == 18


def test_reference_extraction_is_not_vacuous():
    pairs = {pair for body in SKILLS.values() for pair in _references(body)}
    assert {("jobs", "list_pods"), ("notebooks", "autoshutdown_set"),
            ("inference", "predict")} <= pairs
    # an invented action is extracted, so the per-skill check below would flag it
    assert ("jobs", "invented_action") in _references("then call jobs invented_action")
    assert _references("read jobs across regions") == []


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_skill_action_verbs_are_real(name):
    bad = [(d, v) for d, v in _references(SKILLS[name]) if v not in DOMAIN_ACTIONS[d]]
    assert not bad, f"{name} references unknown actions: {bad}"


async def test_skill_tool_names_exist(settings_rw):
    tools = {tool.name for tool in await build_server(settings_rw).list_tools()}
    for name, text in SKILLS.items():
        named = set(re.findall(r"\bmlspace_[a-z_]+\b(?!/)", text))
        assert named <= tools, (name, named - tools)


async def test_skill_examples_match_registered_schemas_and_body_contract(settings_rw):
    """Execute schema validation and the real resolver on machine-readable examples."""
    tools = {tool.name: tool for tool in await build_server(settings_rw).list_tools()}
    examples = [json.loads(raw) for text in SKILLS.values()
                for raw in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)]
    assert {example["tool"] for example in examples} == {
        "mlspace_jobs_overview", "mlspace_queue_inspect", "mlspace_queues",
    }
    for example in examples:
        args = example["arguments"]
        jsonschema.validate(args, tools[example["tool"]].inputSchema)
        if example["tool"] == "mlspace_queues":
            op = QUEUES.actions[args["action"]]
            method, path, path_params, _, body = resolve_request(op, args)
            assert (method, path) == ("POST", "/public/v2/queues/{queue_id}/nodes")
            assert path_params == {"queue_id": "destination-queue"}
            assert body == {"nodes": ["node-a"], "force_withdrawal": False}
            schema = bodyspec.schema_for(method, path)
            assert schema is not None and not bodyspec.lint(schema, body)


async def test_rules_live_once_in_the_server_not_in_skills(settings_ro):
    """Skills are the only recipe surface; shared rules reach every client via instructions."""
    mcp = build_server(settings_ro)
    assert CONTEXT_INSTRUCTIONS in INSTRUCTIONS
    assert "mlspace_playbooks" not in INSTRUCTIONS
    assert not await mcp.list_prompts()
    assert "mlspace_playbooks" not in {tool.name for tool in await mcp.list_tools()}
    for name, text in SKILLS.items():
        assert "Context and evidence rules" not in text, name


def test_dst781_invariants_reach_every_client_via_instructions():
    """Without the plugin (or before a skill loads) these rules still arrive."""
    for rule in ("do not recommend pausing a paused notebook to free capacity",
                 "Compute nodes and Jupyter servers are different objects",
                 "Missing or inaccessible workload means unknown, not idle or safe to move"):
        assert rule in INSTRUCTIONS


def test_compute_node_skill_targets_nodes_not_notebooks():
    body = _procedure("mlspace-transfer-compute-node")
    assert "Jupyter" in body and "not the node to move" in body
    assert "paused notebook configuration alone is not capacity" in body


def test_launch_skill_picks_images_from_a_field_that_survives_shrinking():
    """format_response(kind="catalog") collapses instances_types[].images into strings,
    so base_image must come from datahub_images/custom_images, which keep objects."""
    body = _procedure("mlspace-launch-training-job")
    assert "base_image" in body
    assert "datahub_images" in body or "custom_images" in body


def test_resource_skill_keeps_cpu_only_jobs_visible():
    body = _procedure("mlspace-discover-launchable-resources")
    assert "zero-GPU" in body
    assert "empty" in body and "images" in body
