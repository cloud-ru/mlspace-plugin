"""Pure (no-network) tests for the request-hook mechanisms in registry.py:
PREFLIGHT / POSTPROCESS validators + the formatting-layer log cause note and the
two intentional twin copies of the JobStatus enum. Style follows the other pure
tests: import the function/dict and call it directly.
"""

from __future__ import annotations

import pytest

from mlspace_mcp import formatting
from mlspace_mcp.errors import MLSpaceError
from mlspace_mcp.registry import (
    POSTPROCESS,
    resolve_request,
)
from mlspace_mcp.tools.jobs import DOMAIN as JOBS

# NOTE: jobs `get` has NO POSTPROCESS entry — the in-band 'job not found' note is
# produced solely by formatting._normalise_job (kind="job"), covered in
# test_job_status.py. The former duplicate postprocessor (whose note _normalise_job
# overwrote) was removed in FIX-6.

# --- POSTPROCESS: delete_connectors bare 200 [] ------------------------------

_DEL_CONN = POSTPROCESS[("DELETE", "/public/v2/data_transfer/v2/connectors")]


def test_delete_connectors_none_deleted_is_repacked():
    # verbatim live 07#7: requested one id, API returns []
    missing = "00000000-0000-4000-8000-000000000000"
    out = _DEL_CONN([], {"ids": [missing]})
    assert out["deleted_ids"] == []
    assert out["not_found_ids"] == [missing]
    assert "_wrapper_note" in out


def test_delete_connectors_full_success_passes_through_as_list():
    assert _DEL_CONN(["id-a"], {"ids": ["id-a"]}) == ["id-a"]


def test_delete_connectors_partial_reports_only_missing():
    out = _DEL_CONN(["id-a"], {"ids": ["id-a", "id-b"]})
    assert out["not_found_ids"] == ["id-b"]
    assert out["deleted_ids"] == ["id-a"]


# --- POSTPROCESS: documented-ambiguous empty allocation reads ----------------

_EMPTY_PATHS = [
    ("GET", "/public/v2/allocations/"),
    ("GET", "/public/v2/workspaces/v3/{workspace_id}/allocations/{allocation_id}/queues"),
    ("GET", "/public/v2/queues/defaults"),
]


@pytest.mark.parametrize("key", _EMPTY_PATHS)
def test_empty_allocation_read_is_annotated(key):
    fn = POSTPROCESS[key]
    out = fn([], {})
    assert isinstance(out, dict)
    assert out["result"] == []
    assert "_wrapper_note" in out and out["_wrapper_note"]
    if key == ("GET", "/public/v2/allocations/"):
        assert "mlspace_workspaces" in out["_wrapper_note"]  # points at the real source


@pytest.mark.parametrize("key", _EMPTY_PATHS)
def test_nonempty_allocation_read_passes_through(key):
    fn = POSTPROCESS[key]
    assert fn([{"id": "q1"}], {}) == [{"id": "q1"}]


# --- PREFLIGHT: jobs list region enforcement + status normalisation ----------


def test_jobs_list_without_region_is_refused():
    with pytest.raises(MLSpaceError) as exc:
        resolve_request(JOBS.actions["list"], {})
    msg = exc.value.message.lower()
    assert "region" in msg and "count" in msg


def test_jobs_list_with_region_and_status_resolves():
    _m, _p, _pp, query, _b = resolve_request(
        JOBS.actions["list"], {"region": "SR006", "status": ["Running"]}
    )
    assert ("region", "SR006") in query
    assert ("status", "Running") in query


def test_jobs_list_status_casing_normalised_scalar():
    _m, _p, _pp, query, _b = resolve_request(
        JOBS.actions["list"], {"region": "SR006", "status": "failed"}
    )
    assert ("status", "Failed") in query


def test_jobs_list_status_casing_normalised_list():
    _m, _p, _pp, query, _b = resolve_request(
        JOBS.actions["list"], {"region": "SR006", "status": ["failed", "pending"]}
    )
    statuses = [v for k, v in query if k == "status"]
    assert statuses == ["Failed", "Pending"]
    assert ("region", "SR006") in query


def test_jobs_list_status_already_canonical_and_unknown_preserved():
    _m, _p, _pp, query, _b = resolve_request(
        JOBS.actions["list"], {"region": "SR006", "status": ["Failed", "frobnicate"]}
    )
    statuses = [v for k, v in query if k == "status"]
    assert statuses == ["Failed", "frobnicate"]  # canonical kept, unknown untouched


# --- formatting: mpirun/ORTE likely_cause note on logs -----------------------

_MPIRUN_LOG = (
    "Starting job\n"
    "--------------------------------------------------------------------------\n"
    "mpirun was unable to launch the specified application as it could not access\n"
    "or execute an executable:\n"
    "Executable: /home/jovyan/train.py\n"
    "--------------------------------------------------------------------------\n"
)

_TRACEBACK_LOG = (
    "Traceback (most recent call last):\n"
    '  File "train.py", line 10, in <module>\n'
    "    raise ValueError('boom')\n"
    "ValueError: boom\n"
)


def test_log_mpirun_failure_surfaces_likely_cause():
    out = formatting.format_response(_MPIRUN_LOG, kind="log")
    assert "[likely_cause" in out
    assert "mpirun was unable to launch" in out
    # FIX-7: the note now names the concrete missing binary, not just the generic banner
    note = out.splitlines()[0]
    assert "Executable: /home/jovyan/train.py" in note


def test_log_plain_traceback_has_no_likely_cause():
    out = formatting.format_response(_TRACEBACK_LOG, kind="log")
    assert "[likely_cause" not in out
    assert "ValueError" in out  # full log still shown


def test_log_likely_cause_survives_tail_truncation():
    # banner at the very top, then many lines → it is swept out of the tail, but the
    # note (scanned from the full body) still leads the output.
    body = _MPIRUN_LOG + "\n".join(f"line{i}" for i in range(500))
    out = formatting.format_response(body, kind="log", log_tail=50)
    assert "[likely_cause" in out
    assert "mpirun was unable to launch" not in out.split("]", 1)[1]  # not in the tail body
