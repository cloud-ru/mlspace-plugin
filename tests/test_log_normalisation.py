"""Log-shape defences, grounded in payloads captured from the live API.

Every fixture below is a verbatim (trimmed) response observed against the
MLSpace prod API on 2026-08-03 while running a controlled marker script, so
these are regression tests against real platform behaviour, not invented shapes.
"""

from __future__ import annotations

from mlspace_mcp import formatting

# `GET /jobs/{name}/logs?tail=15` — the API returns the newest lines FIRST, which
# puts the ValueError above the frames that raised it and the post-failure line
# above the ValueError. A top-down reader gets the causality backwards.
REVERSED_TAIL = """\
2026-08-03T09:47:01Z [1,0]<stdout>:UNREACHABLE_LINE should never print
2026-08-03T09:47:01Z [1,0]<stderr>:ValueError: BOOM_ROOT_CAUSE this is the real reason
2026-08-03T09:47:01Z [1,0]<stderr>:  File "<stdin>", line 3, in level_three
2026-08-03T09:47:01Z [1,0]<stderr>:  File "<stdin>", line 5, in level_two
2026-08-03T09:47:01Z [1,0]<stderr>:Traceback (most recent call last):
2026-08-03T09:47:00Z [1,0]<stdout>:MARKER_10 sequential-line-10
2026-08-03T09:46:59Z [1,0]<stdout>:MARKER_09 sequential-line-09
"""

# The same job fetched WITHOUT `tail` — already chronological, must be left alone.
CHRONOLOGICAL = """\
2026-08-03T09:46:59Z [1,0]<stdout>:MARKER_09 sequential-line-09
2026-08-03T09:47:00Z [1,0]<stdout>:MARKER_10 sequential-line-10
2026-08-03T09:47:01Z [1,0]<stderr>:Traceback (most recent call last):
2026-08-03T09:47:01Z [1,0]<stderr>:ValueError: BOOM_ROOT_CAUSE this is the real reason
"""


def _log(text: str, **kw) -> str:
    return formatting.format_response(text, kind="log", **kw)


def test_reversed_tail_is_restored_to_chronological_order():
    out = _log(REVERSED_TAIL)
    body = [ln for ln in out.splitlines() if "MARKER_" in ln or "Traceback" in ln or "ValueError" in ln]
    assert "Traceback" in body[-2], out
    assert "ValueError" in body[-1], out
    # the cause must now precede the effect
    assert out.index("Traceback (most recent call last)") < out.index("ValueError: BOOM_ROOT_CAUSE")
    assert out.index("MARKER_09") < out.index("MARKER_10")
    assert "newest-first" in out  # the reorder is announced so the model can trust it


def test_chronological_log_is_left_untouched():
    out = _log(CHRONOLOGICAL)
    assert "newest-first" not in out
    assert out.index("MARKER_09") < out.index("MARKER_10")
    assert out.index("Traceback") < out.index("ValueError")


def test_untimestamped_log_is_not_reordered():
    text = "zebra\nyankee\nxray\nwhiskey"
    assert _log(text).splitlines() == ["zebra", "yankee", "xray", "whiskey"]


def test_too_few_timestamps_to_judge_leaves_order_alone():
    text = "2026-08-03T09:47:01Z later\n2026-08-03T09:46:00Z earlier"
    assert _log(text).splitlines()[0].endswith("later")


def test_expired_logs_sentinel_is_typed_not_mistaken_for_a_missing_job():
    # verbatim body of a job whose logs aged out — HTTP 200, not 404
    out = _log(' \nℹ️ NODES INFO\nNodes: bmsrv-083.sr006\n \n"No such job"\n')
    assert "logs_expired" in out
    assert "NOT a missing job" in out


def test_queued_job_sentinel_is_typed():
    out = _log("Job lm-mpi-job-70580faf in queue. Try later\n")
    assert "job_pending" in out
    assert "logs_expired" not in out


def test_tail_keeps_the_end_of_the_chronological_log():
    text = "\n".join(f"2026-08-03T09:{m:02d}:00Z line{m:02d}" for m in range(1, 40))
    out = _log(text, log_tail=5)
    assert "line39" in out
    assert "line01" not in out
    assert "showing last 5" in out


def test_reversed_log_then_tail_still_yields_the_newest_lines():
    """Regression: taking the last N of a REVERSED log would return the OLDEST N."""
    text = "\n".join(f"2026-08-03T09:{m:02d}:00Z line{m:02d}" for m in range(39, 0, -1))
    out = _log(text, log_tail=3)
    assert "line39" in out, out
    assert "line01" not in out, out
