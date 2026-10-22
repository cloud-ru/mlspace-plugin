"""FIX-1 regression: a path parameter can never re-route the request.

Offline (respx) proof of the A-CRIT-1 finding: a jobs `get` with
job_name='../docker_registry/v1/users/generate_password' must NOT reach the
mutating generate_password endpoint. Also proves the one deliberate multi-segment
path tail (async_inference predict_path) still works.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.auth import TokenManager
from mlspace_mcp.client import MLSpaceClient
from mlspace_mcp.errors import MLSpaceError
from mlspace_mcp.registry import _build_handler, available_actions, resolve_request
from mlspace_mcp.tools.async_inference import DOMAIN as ASYNC_INF
from mlspace_mcp.tools.jobs import ACTIONS as JOB_ACTIONS
from mlspace_mcp.tools.jobs import DOMAIN as JOBS

TOKEN_BODY = {"token": {"access_token": "AT-1", "expires_in": 3600}}

_ATTACK = "../docker_registry/v1/users/generate_password"


def _ctx(http: httpx.AsyncClient) -> SimpleNamespace:
    return runtime_ctx(http)

def _handler(domain, readonly=False):
    settings = make_settings(readonly=readonly)
    return _build_handler(domain, available_actions(domain, settings.readonly), settings)


# --- pure layer: resolve_request refuses the traversal, fail-closed ----------


def test_traversal_job_name_is_refused_fail_closed():
    with pytest.raises(MLSpaceError) as exc:
        resolve_request(JOB_ACTIONS["get"], {"job_name": _ATTACK})
    assert "job_name" in exc.value.message


def test_plain_slash_in_segment_is_refused():
    with pytest.raises(MLSpaceError):
        resolve_request(JOB_ACTIONS["get"], {"job_name": "a/b"})


# --- full handler: the malicious GET never touches generate_password ---------


@respx.mock
async def test_full_handler_traversal_never_hits_generate_password():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    # If routing were still broken this route would be hit and leak the password.
    pw_route = respx.get(
        f"{BASE_URL}/public/v2/docker_registry/v1/users/generate_password"
    ).mock(return_value=httpx.Response(200, json={"password": "SYNTHETIC_ONLY"}))
    # Any HTTP GET at all would prove the refusal did not fire before the wire.
    any_route = respx.route(method="GET", host="api.test.local").mock(
        return_value=httpx.Response(200, json={"status": "job not found"})
    )
    handler = _handler(JOBS)
    async with httpx.AsyncClient() as http:
        # fail-closed: the traversal is refused as a protocol error (isError=true)
        with pytest.raises(ToolError) as exc:
            await handler(action="get", job_name=_ATTACK, ctx=_ctx(http))
    assert pw_route.call_count == 0  # the mutating endpoint was never reached
    assert any_route.call_count == 0  # no job request went out either
    assert "SYNTHETIC_ONLY" not in str(exc.value)  # secret never surfaced


# --- the deliberate tail (predict_path) still works with multiple segments ---


def test_predict_path_tail_keeps_its_slashes():
    method, path, path_params, _q, _b = resolve_request(
        ASYNC_INF.actions["predict"],
        {
            "region": "DGX2-INF",
            "inference_name": "svc",
            "predict_path": "v1/models/svc:predict",
            "body": {"x": 1},
        },
    )
    assert method == "POST"
    # separators preserved; the colon is not mangled
    assert path_params["predict_path"] == "v1/models/svc:predict"


def test_predict_path_tail_still_refuses_traversal():
    with pytest.raises(MLSpaceError):
        resolve_request(
            ASYNC_INF.actions["predict"],
            {
                "region": "R",
                "inference_name": "svc",
                "predict_path": "v1/../../escape",
                "body": {},
            },
        )


@respx.mock
async def test_full_handler_predict_tail_reaches_the_right_path():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    route = respx.post(
        f"{BASE_URL}/public/v2/async_inferences/v1/DGX2-INF/svc/v1/models/svc:predict"
    ).mock(return_value=httpx.Response(200, json={"request_id": "r1"}))
    handler = _handler(ASYNC_INF)
    async with httpx.AsyncClient() as http:
        out = await handler(
            action="predict",
            region="DGX2-INF",
            inference_name="svc",
            predict_path="v1/models/svc:predict",
            body={"x": 1},
            ctx=_ctx(http),
        )
    assert route.call_count == 1
    assert "request_id" in out


# --- client defence-in-depth: a raw traversal id is refused at the wire ------


@pytest.mark.parametrize("job_name", ["../secrets", "a\r\nb"])
@respx.mock
async def test_client_refuses_unsafe_raw_path_before_any_http(job_name):
    """Defence in depth for prechecks that call the client with a raw id."""
    respx.post(AUTH_URL).respond(200, json=TOKEN_BODY)
    async with httpx.AsyncClient() as http:
        tokens = TokenManager(http, BASE_URL, "cid", "csecret")
        client = MLSpaceClient(http, tokens, base_url=BASE_URL, api_key="k", workspace_id="w")
        with pytest.raises(MLSpaceError, match="Refusing"):
            await client.request("GET", "/public/v2/jobs/{job_name}",
                                 path_params={"job_name": job_name})
    assert not respx.calls


# --- variants: every route-changing character is refused, ordinary names pass ---


@pytest.mark.parametrize("value", ["a?b", "a#b", "a\\b", "..", ".", "a\x00b", "a\nb", "a\x7fb"])
def test_route_changing_segment_values_are_refused(value):
    with pytest.raises(MLSpaceError):
        resolve_request(JOB_ACTIONS["get"], {"job_name": value})


@pytest.mark.parametrize(("value", "encoded"), [
    ("lm-mpi-job-b6e90146", "lm-mpi-job-b6e90146"),
    ("name with space", "name%20with%20space"),
    ("a%2Fb", "a%252Fb"),  # an encoded separator stays literal, inside one segment
])
def test_ordinary_and_encoded_names_stay_in_one_segment(value, encoded):
    _, path, path_params, _, _ = resolve_request(JOB_ACTIONS["get"], {"job_name": value})
    assert path == "/public/v2/jobs/{job_name}"
    assert path_params == {"job_name": encoded}


@respx.mock
async def test_encoded_traversal_never_leaves_the_jobs_route():
    respx.post(AUTH_URL).respond(200, json=TOKEN_BODY)
    seen = respx.route().respond(200, json={"job_name": "x", "status": "running"})
    async with httpx.AsyncClient() as http:
        await _handler(JOBS)(action="get", job_name="..%2Fdocker_registry%2Fv1", ctx=_ctx(http))
    paths = [c.request.url.raw_path.decode() for c in seen.calls if "service_auth" not in str(c.request.url)]
    assert len(paths) == 1
    assert paths[0].startswith("/public/v2/jobs/") and paths[0].count("/") == 4


@pytest.mark.parametrize("tail", ["v1/models/svc?x=1", "v1/models/svc#frag", "v1\\models"])
def test_predict_tail_refuses_query_fragment_and_backslash(tail):
    op = ASYNC_INF.actions["predict"]
    values = {name: "svc" for name in op.required}
    values.update({field: tail for placeholder, field in op.path_params.items()
                   if placeholder in op.path_tail})
    with pytest.raises(MLSpaceError):
        resolve_request(op, values)
