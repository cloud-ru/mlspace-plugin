from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.workspaces import DOMAIN


def test_list_resolves():
    method, path, path_params, query, body = resolve_request(
        DOMAIN.actions["list"], {}
    )
    assert method == "GET"
    assert path == "/public/v2/workspaces/v3/"


def test_get_resolves_path_param():
    op = DOMAIN.actions["get"]
    method, path, path_params, query, body = resolve_request(op, {"workspace_id": "w1"})
    assert method == "GET"
    assert path_params == {"workspace_id": "w1"}


def test_api_key_redacted():
    assert "x-api-key" in DOMAIN.redact_result_keys
