from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.tensorboards import DOMAIN


def test_list_resolves():
    method, path, path_params, query, body = resolve_request(
        DOMAIN.actions["list"], {"limit": 5, "search": "exp"}
    )
    assert method == "GET"
    assert path == "/public/v2/tensorboards/v2/tensorboards"
    assert ("limit", 5) in query
    assert ("search", "exp") in query


def test_get_resolves():
    method, path, path_params, query, body = resolve_request(
        DOMAIN.actions["get"], {"tensorboard_uuid": "abc"}
    )
    assert method == "GET"
    assert path_params == {"tensorboard_uuid": "abc"}
