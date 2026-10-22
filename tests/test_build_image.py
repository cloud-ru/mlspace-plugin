from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.build_image import DOMAIN


def test_list_resolves():
    method, path, path_params, query, body = resolve_request(
        DOMAIN.actions["list"], {}
    )
    assert method == "GET"
    assert path == "/public/v2/build-image/jobs"
    assert body is None


def test_get_logs_resolves():
    op = DOMAIN.actions["get_logs"]
    assert op.kind == "log"
    method, path, path_params, _, _ = resolve_request(op, {"job_name": "j1"})
    assert method == "GET"
    assert path_params == {"job_name": "j1"}
