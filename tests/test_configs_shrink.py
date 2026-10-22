from __future__ import annotations

import json

from mlspace_mcp import formatting

_BIG_DESC = "x" * 2000  # heavy field that makes re-listing the same image costly


def _img(name: str, tag: str) -> dict:
    return {
        "name": name,
        "tags": [tag],
        "description": f"{name} {_BIG_DESC}",
        "size_bytes": 123456789,
    }


def _configs() -> dict:
    """2 regions, 3 instance_types each, with overlapping-but-different image sets."""
    img1, img2, img3 = _img("img1", "v1"), _img("img2", "v2"), _img("img3", "v3")

    def region(key: str) -> dict:
        return {
            "key": key,
            "name": f"region-{key}",
            "instances_types": [
                {"key": "itA", "resource": {"gpu": 8}, "images": [img1, img2]},
                {"key": "itB", "resource": {"gpu": 4}, "images": [img1]},
                {"key": "itC", "resource": {"gpu": 2}, "images": [img1, img3]},
            ],
        }

    return {"regions": [region("R1"), region("R2")]}


def _refs_of(instance: dict) -> set[str]:
    return {r if isinstance(r, str) else json.dumps(r) for r in instance["images"]}


def test_configs_shrink_is_lossless_and_small():
    data = _configs()
    raw = formatting.format_response(data)  # no catalog kind → untouched baseline
    shrunk = formatting.format_response(data, kind="catalog")
    out = json.loads(shrunk)

    # (1) much smaller — and the heavy object is de-duplicated, not re-listed.
    # Per region the 3 instances reference 5 image slots (img1 x3, img2, img3) but
    # only 3 unique objects; across 2 regions that's 10 heavy copies collapsed to 6.
    assert len(shrunk) < len(raw)
    assert shrunk.count(_BIG_DESC) == 6
    assert raw.count(_BIG_DESC) == 10

    # (2) every region + every instance_type key still present
    assert [r["key"] for r in out["regions"]] == ["R1", "R2"]
    for region in out["regions"]:
        assert [it["key"] for it in region["instances_types"]] == ["itA", "itB", "itC"]

    expected = {
        "itA": {"img1:v1", "img2:v2"},
        "itB": {"img1:v1"},
        "itC": {"img1:v1", "img3:v3"},
    }
    for region in out["regions"]:
        by_key = {it["key"]: it for it in region["instances_types"]}
        # (3) per-instance image mapping unchanged
        for it_key, refs in expected.items():
            assert _refs_of(by_key[it_key]) == refs
        # (5) no union leakage: itB did not gain img2/img3
        assert _refs_of(by_key["itB"]) == {"img1:v1"}

        # (4) each unique heavy image object appears exactly once in the catalog
        catalog = region["image_catalog"]
        assert set(catalog) == {"img1:v1", "img2:v2", "img3:v3"}
        for ref, img in catalog.items():
            assert img["name"] + ":" + img["tags"][0] == ref
            assert img["description"].endswith(_BIG_DESC)  # full heavy object retained


def test_configs_shrink_fail_open_on_wrong_shape():
    # missing "regions" → pass through untouched
    weird = {"foo": "bar", "items": [1, 2, 3]}
    assert json.loads(formatting.format_response(weird, kind="catalog")) == weird

    # region without instances_types → left as-is, no image_catalog injected
    no_its = {"regions": [{"key": "R1", "name": "r1"}]}
    out = json.loads(formatting.format_response(no_its, kind="catalog"))
    assert out == no_its
    assert "image_catalog" not in out["regions"][0]


def test_catalog_region_filter_keeps_only_match_and_is_lossless():
    data = _configs()
    shrunk = formatting.format_response(data, kind="catalog", catalog_region="R1")
    out = json.loads(shrunk)

    # only R1 remains
    assert [r["key"] for r in out["regions"]] == ["R1"]
    region = out["regions"][0]

    # all instance_types intact within the kept region
    assert [it["key"] for it in region["instances_types"]] == ["itA", "itB", "itC"]
    expected = {
        "itA": {"img1:v1", "img2:v2"},
        "itB": {"img1:v1"},
        "itC": {"img1:v1", "img3:v3"},
    }
    by_key = {it["key"]: it for it in region["instances_types"]}
    for it_key, refs in expected.items():
        assert _refs_of(by_key[it_key]) == refs

    # all images intact (lossless within the region): every unique heavy object kept
    catalog = region["image_catalog"]
    assert set(catalog) == {"img1:v1", "img2:v2", "img3:v3"}
    for ref, img in catalog.items():
        assert img["name"] + ":" + img["tags"][0] == ref
        assert img["description"].endswith(_BIG_DESC)


def test_catalog_region_filter_is_case_insensitive_on_key_and_name():
    data = _configs()
    # match by key, different case
    out = json.loads(formatting.format_response(data, kind="catalog", catalog_region="r1"))
    assert [r["key"] for r in out["regions"]] == ["R1"]
    # match by name (region-R2), different case
    out = json.loads(formatting.format_response(data, kind="catalog", catalog_region="REGION-R2"))
    assert [r["key"] for r in out["regions"]] == ["R2"]


def test_catalog_nonmatching_region_keeps_all_fail_open():
    data = _configs()
    out = json.loads(
        formatting.format_response(data, kind="catalog", catalog_region="does-not-exist")
    )
    assert [r["key"] for r in out["regions"]] == ["R1", "R2"]


def test_catalog_output_is_compact_json():
    """The catalog is large even after dedup, so it is emitted without indentation."""
    catalog_out = formatting.format_response(_configs(), kind="catalog")
    parsed = json.loads(catalog_out)
    assert len(catalog_out) < len(json.dumps(parsed, indent=2))


def test_shrunk_configs_carry_allocation_note():
    # every matched region (non-empty instances_types) is annotated: the catalog is
    # NOT filtered by the caller's allocation.
    out = json.loads(formatting.format_response(_configs(), kind="catalog"))
    for region in out["regions"]:
        assert "_allocation_note" in region


def test_catalog_guidance_does_not_recommend_a_paid_probe_after_failed_availability():
    """Regression: this response note contradicted the launch skill in a live LLM run."""
    out = json.loads(formatting.format_response(_configs(), kind="catalog"))
    for region in out["regions"]:
        note = region["_allocation_note"]
        assert "only reliable check is to try a run" not in note
        assert "unverified" in note
