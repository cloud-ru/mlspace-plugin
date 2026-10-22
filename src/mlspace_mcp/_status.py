"""Single source of truth for the JobStatus canonical spelling map.

Kept in its own tiny leaf module (it imports NOTHING from the package) so both
``registry.py`` and ``formatting.py`` can share ONE copy without reintroducing a
tools -> registry -> formatting import cycle. Previously each held an intentional
twin copy guarded by a drift test; now there is exactly one.
"""

from __future__ import annotations

# Mirrors the spec ``JobStatus-Input`` enum: casefold -> canonical spelling.
JOB_STATUS_CANON: dict[str, str] = {
    s.casefold(): s
    for s in (
        "Completed", "Completing", "Deleted", "Failed", "Pending", "Running",
        "Stopped", "Succeeded", "Terminated", "Terminating", "Restarting",
    )
}
