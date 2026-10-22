from __future__ import annotations

import json

from mlspace_mcp import formatting


def test_drops_noise_keys():
    data = {"name": "svc", "managedFields": [1, 2], "metadata": {"resourceVersion": "9", "x": 1}}
    out = json.loads(formatting.format_response(data))
    assert "managedFields" not in out
    assert "resourceVersion" not in out["metadata"]
    assert out["metadata"]["x"] == 1


def test_caps_long_lists():
    data = {"items": list(range(500))}
    out = json.loads(formatting.format_response(data, list_cap=10))
    assert len(out["items"]) == 11  # 10 + footer
    assert "more item(s) omitted" in out["items"][-1]


def test_truncation_does_not_invent_pagination_for_nested_arrays():
    # A connector's nested array is not a paginated collection. The generic
    # formatter also serves actions using page/page_size or no pagination at all.
    data = {"parameters": {"tables": ["one", "two", "three"]}}
    out = json.loads(formatting.format_response(data, list_cap=2))
    tables = out["parameters"]["tables"]
    assert tables[:2] == ["one", "two"]
    assert "1 more item(s) omitted" in tables[2]
    assert not any(param in tables[2] for param in ("offset", "limit", "page_size"))


def test_redacts_secret_keys():
    data = {"x_api_key": "supersecretvalue"}
    out = json.loads(formatting.format_response(data, redact_keys=("x_api_key",)))
    assert out["x_api_key"] == "<redacted, 16 chars>"


def test_elides_huge_strings():
    data = {"image": "Q" * 5000}
    out = json.loads(formatting.format_response(data))
    assert out["image"] == "<5000 chars elided>"


def test_log_tail():
    text = "\n".join(f"line{i}" for i in range(1000))
    out = formatting.format_response(text, kind="log", log_tail=50)
    assert out.startswith("[showing last 50 of 1000 lines]")
    assert out.strip().endswith("line999")


def test_empty_and_scalar():
    assert "empty response" in formatting.format_response(None)
    assert formatting.format_response("hello") == "hello"
    assert formatting.format_response(42) == "42"


def test_markdown_is_fenced_json():
    out = formatting.format_response({"a": 1}, "markdown")
    assert out.startswith("```json")
    assert out.endswith("```")


def test_legit_annotations_field_is_preserved():
    # "annotations" is a generic field name — must NOT be silently dropped
    data = {"name": "m", "annotations": {"team": "ml", "purpose": "test"}}
    out = json.loads(formatting.format_response(data))
    assert out["annotations"] == {"team": "ml", "purpose": "test"}


def test_log_json_branch_redacts_and_caps():
    data = {"token": "supersecretvalue", "lines": list(range(500))}
    out = formatting.format_response(
        data, kind="log", redact_keys=("token",), list_cap=10
    )
    parsed = json.loads(out)
    assert parsed["token"] == "<redacted, 16 chars>"
    assert len(parsed["lines"]) == 11  # 10 + footer


# registry_repos: elide heavy recent_image.extra_attrs with a get_image pointer ---


def test_repos_list_omits_extra_attrs_with_marker():
    # verbatim-shaped live 08#6 element
    data = [
        {
            "name": "jupyter-cuda-devel",
            "images_count": 3,
            "recent_image": {
                "id": "img-1",
                "extra_attrs": {
                    "architecture": "amd64",
                    "config": {"Env": ["NVIDIA_VISIBLE_DEVICES=all", "PATH=/usr/bin"]},
                },
            },
        }
    ]
    repo = json.loads(formatting.format_response(data, kind="registry_repos"))[0]
    blob = json.dumps(repo)
    assert "architecture" not in blob
    assert "NVIDIA_VISIBLE_DEVICES" not in blob
    # elision, not a silent drop: the marker points at get_image
    assert "get_image" in repo["recent_image"]["extra_attrs"]
    # light fields kept
    assert repo["name"] == "jupyter-cuda-devel"
    assert repo["images_count"] == 3
    assert repo["recent_image"]["id"] == "img-1"


def test_repos_shrink_fail_open():
    # non-list input passes through untouched
    assert json.loads(formatting.format_response({"foo": 1}, kind="registry_repos")) == {"foo": 1}
    # empty extra_attrs ({}) left as-is — nothing to omit (list_images stays out of scope)
    data = [{"name": "r", "recent_image": {"id": "i", "extra_attrs": {}}}]
    out = json.loads(formatting.format_response(data, kind="registry_repos"))
    assert out[0]["recent_image"]["extra_attrs"] == {}


# empty-list scope/completeness note ------------------------------------------


def test_empty_list_gets_scope_note():
    out = json.loads(formatting.format_response({"inferences": []}, kind="list"))
    assert "_scope_note" in out


def test_empty_list_with_completeness_signal_has_no_note():
    # count/failed are honest totals → the "no signal" note would slightly lie
    assert "_scope_note" not in json.loads(
        formatting.format_response({"jobs": [], "count": 0}, kind="list")
    )
    assert "_scope_note" not in json.loads(
        formatting.format_response({"masked_nodes": [], "failed": 0}, kind="list")
    )


def test_scope_note_only_on_empty_list_kind():
    # non-empty list → no note
    assert "_scope_note" not in json.loads(
        formatting.format_response({"inferences": [{"name": "x"}]}, kind="list")
    )
    # default kind='item' is never annotated
    assert "_scope_note" not in json.loads(formatting.format_response({"inferences": []}))


def test_bare_empty_list_is_not_wrapped():
    # a bare [] keeps its type (not turned into a dict)
    assert json.loads(formatting.format_response([], kind="list")) == []


# hoist metadata.name onto manifest-shaped list items -------------------------


def test_hoist_manifest_name_from_metadata():
    data = {
        "inferences": [
            {
                "apiVersion": "x",
                "kind": "InferenceService",
                "metadata": {"name": "svc-1"},
                "spec": {},
                "status": {},
            }
        ]
    }
    out = json.loads(formatting.format_response(data, kind="list"))
    assert out["inferences"][0]["name"] == "svc-1"


def test_hoist_leaves_flat_and_nonmanifest_items():
    # flat item (integration-mock shape) already addressable → unchanged
    out = json.loads(formatting.format_response({"inferences": [{"name": "svc-a"}]}, kind="list"))
    assert out["inferences"][0] == {"name": "svc-a"}
    # non-manifest → unchanged
    out = json.loads(formatting.format_response({"items": [{"foo": 1}]}, kind="list"))
    assert out["items"][0] == {"foo": 1}
