from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.queues import DOMAIN


def test_list_read_resolves():
    op = DOMAIN.actions["list"]
    method, path, path_params, query, body = resolve_request(
        op, {"allocation_id": "alloc-1"}
    )
    assert method == "GET"
    assert path == "/public/v2/queues/"
    assert ("allocation_id", "alloc-1") in query
    assert body is None


def test_unassign_is_delete_with_body():
    op = DOMAIN.actions["unassign"]
    assert op.method == "DELETE"
    assert op.body_field == "body"
    method, path, path_params, query, body = resolve_request(
        op, {"queue_id": "q1", "body": {"org_structure": {}}, "confirm": True}
    )
    assert path_params == {"queue_id": "q1"}
    assert body == {"org_structure": {}}
