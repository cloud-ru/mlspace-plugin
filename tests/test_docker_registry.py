import pytest

from mlspace_mcp.errors import MLSpaceError
from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.docker_registry import DOMAIN


def test_list_repos_resolves():
    op = DOMAIN.actions["list_repos"]
    method, path, path_params, query, body = resolve_request(op, {})
    assert method == "GET"
    assert path == "/public/v2/docker_registry/v2/repositories/"
    assert body is None


def test_get_image_resolves_path_param():
    op = DOMAIN.actions["get_image"]
    method, path, path_params, query, body = resolve_request(op, {"image_id": "abc"})
    assert method == "GET"
    assert path_params == {"image_id": "abc"}


def test_generate_password_is_write_and_redacted():
    assert DOMAIN.actions["generate_password"].write is True
    assert "password" in DOMAIN.redact_result_keys


def test_generate_password_requires_confirm():
    # FIX-5: rotating the registry password is irreversible (old password stops
    # working) → must be confirm-gated like the other destructive ops.
    op = DOMAIN.actions["generate_password"]
    assert op.confirm is True
    # without confirm it is refused before any request
    with pytest.raises(MLSpaceError) as exc:
        resolve_request(op, {})
    assert "confirm=true" in exc.value.to_text()
    # with confirm it resolves to the rotate endpoint
    method, path, _pp, _q, _b = resolve_request(op, {"confirm": True})
    assert method == "GET"
    assert path.endswith("/users/generate_password")


def test_generate_password_hidden_in_readonly():
    from mlspace_mcp.registry import available_actions

    assert "generate_password" not in available_actions(DOMAIN, readonly=True)
    assert "generate_password" in available_actions(DOMAIN, readonly=False)


def test_list_repos_paginates():
    op = DOMAIN.actions["list_repos"]
    _m, _p, _pp, query, _b = resolve_request(op, {"page": 1, "page_size": 2})
    assert ("page", 1) in query
    assert ("page_size", 2) in query


def test_images_tags_not_paginated():
    # deliberate: pagination splits one image entity across pages (08#4), so
    # list_images/list_tags must NOT expose page/page_size.
    for action in ("list_images", "list_tags"):
        op = DOMAIN.actions[action]
        assert "page" not in op.query_params
        assert "page_size" not in op.query_params
