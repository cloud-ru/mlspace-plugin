"""Tests for the kind='job' response typing (_normalise_job): the created→
'job not found' race note, the failed+empty-error deprecated-constants note, and
the additive canonical status. All fail-open and non-destructive to originals.
"""

from __future__ import annotations

import json

from mlspace_mcp import formatting


def _job(data):
    return json.loads(formatting.format_response(data, kind="job"))


def test_job_not_found_note_names_both_causes():
    # verbatim live #11/#1
    out = _job(
        {
            "job_name": "g",
            "status": "job not found",
            "error_code": 0,
            "error_message": "job not found",
        }
    )
    assert "_wrapper_note" in out
    note = out["_wrapper_note"].lower()
    # created/retry race AND does-not-exist/pruned, picking neither
    assert "created" in note and "retry" in note
    assert "not exist" in note and "prun" in note
    assert out["status"] == "job not found"  # original untouched
    assert "status_canonical" not in out  # not a canonical enum value


def test_failed_empty_error_points_to_logs():
    # verbatim live #6
    out = _job(
        {
            "job_name": "j",
            "status": "failed",
            "duration": "20s",
            "error_code": 0,
            "error_message": "",
        }
    )
    note = out["_wrapper_note"].lower()
    assert "logs" in note and "deprecated" in note
    assert out["status"] == "failed"  # original untouched


def test_running_job_gets_no_note():
    out = _job({"status": "running", "error_code": 0, "error_message": ""})
    assert "_wrapper_note" not in out


def test_normalise_job_fail_open_on_non_dict_and_missing_status():
    assert _job([1, 2]) == [1, 2]
    out = _job({"foo": 1})
    assert out == {"foo": 1}  # no status → no note, no canonical


def test_status_canonical_added_preserving_original_case():
    out = _job({"status": "failed"})
    assert out["status_canonical"] == "Failed"
    assert out["status"] == "failed"

    assert _job({"status": "running"})["status_canonical"] == "Running"


def test_status_canonical_absent_for_unknown_and_not_found():
    assert "status_canonical" not in _job({"status": "weird"})
    assert "status_canonical" not in _job({"status": "job not found"})
