"""Type-aware job entrypoint preflight and real resolver/handler boundaries.

The explicit-region and unsafe NFS findings come from live experiments. Binary
command semantics come from the official CLI reference and bundled OpenAPI; these
tests prove local request fidelity and do not claim a successful live submit.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from conftest import make_settings, runtime_ctx

from mlspace_mcp.errors import MLSpaceError
from mlspace_mcp.registry import _build_handler, available_actions, resolve_request
from mlspace_mcp.tools.jobs import ACTIONS, DOMAIN

RUN = ACTIONS["run"]

GOOD_BODY = {
    "script": "/home/jovyan/user/train.sh",
    "base_image": "cr.ai.cloud.ru/ws/img:latest",
    "instance_type": "cpu.2C.8G",
    "region": "SR006",
    "type": "binary",
}


def _run(script, *, job_type="pytorch2"):
    return resolve_request(
        RUN,
        {"body": {**GOOD_BODY, "script": script, "type": job_type}},
    )


def _ctx() -> SimpleNamespace:
    """Production-shaped runtime with no mocked routes: any HTTP fails the test."""
    return runtime_ctx(httpx.AsyncClient(transport=_NO_NETWORK))


def _refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"dry-run must not issue an HTTP request: {request.method} {request.url}")


_NO_NETWORK = httpx.MockTransport(_refuse)


def test_non_binary_nfs_path_is_accepted():
    method, path, _, _, body = _run("/home/jovyan/user/train.sh")
    assert (method, path) == ("POST", "/public/v2/jobs")
    assert body["script"] == "/home/jovyan/user/train.sh"


@pytest.mark.parametrize(
    "script",
    [
        "ls -lah",
        "/usr/local/bin/trainer --epochs 10",
        "/home/jovyan/NSC/mpi_omp_test",
        "python /home/jovyan/launch.py -f script.py",
    ],
)
def test_binary_command_is_forwarded_to_the_api_unchanged(script):
    _, _, _, _, body = _run(script, job_type="binary")
    assert body["script"] == script


async def test_binary_command_reaches_the_validated_dry_run_body_unchanged():
    settings = make_settings(readonly=False, dry_run=True)
    handler = _build_handler(DOMAIN, available_actions(DOMAIN, settings.readonly), settings)

    out = await handler(
        action="run",
        body={
            "type": "binary",
            "script": "ls -lah",
            "region": "SR008",
            "base_image": "example/image:tag",
            "instance_type": "cpu.2C.8G",
        },
        ctx=_ctx(),
    )

    preview = json.loads(out)
    assert preview["would_send"]["body"]["script"] == "ls -lah"


def test_nfs_path_requires_explicit_region():
    body = {k: v for k, v in GOOD_BODY.items() if k != "region"}
    with pytest.raises(MLSpaceError) as exc:
        resolve_request(RUN, {"body": body})
    text = exc.value.to_text()
    assert "region" in text
    assert "DGX2-MT" in text
    assert "cannot stat" in text


def test_region_is_required_even_without_a_script():
    """The region trap is independent of the script: a body whose script is absent or
    empty must not slip past the region gate (it used to, nested in the path branch)."""
    for body in ({"base_image": "i", "instance_type": "t"}, {"script": "", "instance_type": "t"}):
        with pytest.raises(MLSpaceError) as exc:
            resolve_request(RUN, {"body": body})
        assert "region" in exc.value.to_text()


def test_top_level_region_is_accepted_and_copied_into_the_body():
    """`run` has no top-level region parameter; a caller who passes one anyway meant
    it, so honour it instead of refusing them for something they did supply."""
    body = {k: v for k, v in GOOD_BODY.items() if k != "region"}
    _, _, _, _, sent = resolve_request(RUN, {"body": body, "region": "SR006"})
    assert sent["region"] == "SR006"


@pytest.mark.parametrize(
    "script",
    [
        "/tmp/not_on_nfs.sh",             # observed live: accepted, burned 19s
        "train.sh",                        # observed live: accepted, burned 18s
        "./train.sh",
        "/opt/scripts/train.sh",
        "/home/jovyanX/train.sh",          # prefix must be a real path segment
        "/home/jovyan/../tmp/train.sh",    # '..' escapes the home the prefix enforces
        "/home/jovyan/a/../../../tmp/x.sh",
        "/home/jovyan/",                   # a directory is not an executable script
        "/home/jovyan",                    # the NFS root itself
        "",                                # observed live: accepted, burned a node
        "   ",
    ],
)
def test_non_binary_off_nfs_paths_are_refused_before_submission(script):
    with pytest.raises(MLSpaceError) as exc:
        _run(script)
    assert "/home/jovyan" in exc.value.hint


def test_refusal_explains_the_real_consequence_not_just_a_rule():
    with pytest.raises(MLSpaceError) as exc:
        _run("/tmp/x.sh")
    text = exc.value.to_text()
    assert "job_name" in text          # the API would look like it succeeded
    assert "empty error_message" in text


@pytest.mark.parametrize("script", ["", "   "])
def test_empty_binary_script_is_refused_before_submission(script):
    with pytest.raises(MLSpaceError) as exc:
        _run(script, job_type="binary")
    assert "empty" in exc.value.message


def test_non_string_script_is_left_to_the_api():
    method, _, _, _, _ = resolve_request(RUN, {"body": {**GOOD_BODY, "script": None}})
    assert method == "POST"
