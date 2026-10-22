from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.jobs import DOMAIN


def test_get_action_resolves():
    method, path, path_params, query, body = resolve_request(
        DOMAIN.actions["get"], {"job_name": "lm-mpi-job-1"}
    )
    assert method == "GET"
    assert path == "/public/v2/jobs/{job_name}"
    assert path_params == {"job_name": "lm-mpi-job-1"}


def test_params_path_uses_name_placeholder():
    # /params and /preemptors use {name}, mapped from the job_name field.
    _, path, path_params, _, _ = resolve_request(
        DOMAIN.actions["get_params"], {"job_name": "j1"}
    )
    assert path == "/public/v2/jobs/{name}/params"
    assert path_params == {"name": "j1"}
