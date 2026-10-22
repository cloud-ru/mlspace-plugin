from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.async_inference import DOMAIN


def test_list_resolves():
    method, path, path_params, query, body = resolve_request(
        DOMAIN.actions["list"], {"inference_name": "svc"}
    )
    assert method == "GET"
    assert path == "/public/v2/async_inferences/v1/"
    assert ("inference_name", "svc") in query


def test_status_resolves():
    method, path, path_params, query, body = resolve_request(
        DOMAIN.actions["get_status"], {"request_id": "r1", "inference_name": "svc"}
    )
    assert method == "GET"
    assert path == "/public/v2/async_inferences/v1/{request_id}/status"
    assert path_params == {"request_id": "r1"}
