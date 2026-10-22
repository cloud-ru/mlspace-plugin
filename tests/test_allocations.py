from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.allocations import DOMAIN


def test_get_resolves():
    op = DOMAIN.actions["get"]
    method, path, path_params, query, body = resolve_request(
        op, {"allocation_id": "abc"}
    )
    assert method == "GET"
    assert path == "/public/v2/allocations/{allocation_id}"
    assert path_params == {"allocation_id": "abc"}
    assert body is None


def test_set_default_is_write_with_body():
    op = DOMAIN.actions["set_default"]
    assert op.write is True
    method, path, path_params, query, body = resolve_request(
        op, {"allocation_id": "abc", "body": {"default_for": "job"}}
    )
    assert method == "POST"
    assert body == {"default_for": "job"}
