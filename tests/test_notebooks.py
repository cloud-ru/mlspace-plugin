from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.notebooks import DOMAIN


def test_list_resolves():
    op = DOMAIN.actions["list"]
    method, path, path_params, _query, _body = resolve_request(op, {})
    assert method == "GET"
    assert path == "/public/v2/notebooks/v2/notebooks"
    assert path_params == {}


def test_get_resolves():
    op = DOMAIN.actions["get"]
    method, path, path_params, _query, _body = resolve_request(
        op, {"notebook_uuid": "nb-1"}
    )
    assert method == "GET"
    assert path == "/public/v2/notebooks/v1/notebook/{notebook_uuid}"
    assert path_params == {"notebook_uuid": "nb-1"}
