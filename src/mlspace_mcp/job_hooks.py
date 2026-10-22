"""Job-specific preflight, deletion guard and response compatibility hooks.

Leaf module: imports no dispatcher or domain declarations.
"""

from __future__ import annotations

from typing import Any

from ._status import JOB_STATUS_CANON
from .errors import MLSpaceError

# Earlier API observations motivated these compatibility guards. They are not
# current-tenant evidence. Binary commands are documented; non-binary entrypoints
# retain regional NFS path checks. The API cannot stat a file or verify an image command.
NFS_SCRIPT_PREFIX = "/home/jovyan"


def _preflight_job_region(values: dict[str, Any]) -> None:
    """Refuse a job `run` that leaves `region` to the API default.

    Runs UNCONDITIONALLY — the region trap does not depend on how `script` looks,
    so this must not sit inside the path branch (a missing or empty script would
    otherwise skip it and let the default region through).

    A top-level ``region=`` is accepted as well and copied into the body: `run`
    carries no top-level region parameter, so it would otherwise be dropped
    silently and the caller refused for something they did supply.
    """
    body = values.get("body")
    in_body = body.get("region") if isinstance(body, dict) else None
    if isinstance(in_body, str) and in_body.strip():
        return
    top = values.get("region")
    if isinstance(top, str) and top.strip():
        if isinstance(body, dict):
            body["region"] = top
        return
    raise MLSpaceError(
        "`region` must be explicit for a job submission. The API default (DGX2-MT) "
        "can select a different /home/jovyan volume and fail only after allocating "
        "a node.",
        hint=(
            "Set body.region. For an NFS entrypoint, verify it from a notebook or "
            "transfer attached to that same region because the Public API cannot stat "
            "the target region's NFS; for type=binary, verify that the command or "
            "executable is available in the selected image."
        ),
    )


def _preflight_job_script(values: dict[str, Any]) -> None:
    """Apply only the script checks supported for the selected job type.

    Empty strings are unsafe for every type. ``binary`` accepts a command or executable
    path, so the wrapper must preserve a non-empty value without pretending it can
    resolve the command in the selected image. Other types retain the established NFS
    path-shape checks. Existence is never claimed: the Public API has no remote stat.
    """
    body = values.get("body")
    script = body.get("script") if isinstance(body, dict) else values.get("script")
    job_type = body.get("type") if isinstance(body, dict) else values.get("type")
    if script is None:
        return  # absent/None: the API's own 422 is exemplary, don't duplicate it
    if not isinstance(script, str):
        return  # the spec-driven body lint reports the type error in the real handler
    if not script.strip():
        raise MLSpaceError(
            f"`script` is empty ({script!r}). The API would accept this and return a "
            "job_name, then allocate a node and fail within seconds with an empty "
            "error_message.",
            hint=(
                "Provide a non-empty entrypoint. For type=binary this may be a command "
                "or executable path; for other job types use the documented NFS script "
                f"path under {NFS_SCRIPT_PREFIX}/…."
            ),
        )
    if job_type == "binary":
        return
    if any(seg == ".." for seg in script.split("/")):
        # a '..' segment escapes the NFS home the prefix check exists to enforce:
        # "/home/jovyan/../tmp/x.sh" resolves to /tmp/x.sh
        problem = f"`script` contains a '..' path segment ({script!r})"
    elif script == NFS_SCRIPT_PREFIX or script.endswith("/"):
        problem = f"`script` points at a directory, not a file ({script!r})"
    elif not script.startswith("/"):
        problem = f"`script` is a relative path ({script!r})"
    elif not script.startswith(NFS_SCRIPT_PREFIX + "/"):
        problem = f"`script` points outside the region's NFS ({script!r})"
    else:
        return
    raise MLSpaceError(
        f"{problem}. The API would accept this and return a job_name, then the job "
        f"would allocate a node and fail within seconds with an empty error_message.",
        hint=(
            f"The script must already exist on the chosen region's NFS home, under "
            f"{NFS_SCRIPT_PREFIX}/… — upload it there first, then pass the absolute "
            f"path to the file (e.g. {NFS_SCRIPT_PREFIX}/myproject/train.sh). The same "
            "path in a different notebook region is a different volume."
        ),
    )


def preflight_job_run(values: dict[str, Any]) -> None:
    """Composed preflight for ``POST /jobs`` (one dict key, two independent concerns).

    Region first — it applies to every submission; then the script shape.
    """
    _preflight_job_region(values)
    _preflight_job_script(values)


def _preflight_jobs_list_region(values: dict[str, Any]) -> None:
    """Refuse a jobs `list` with no region and explain the silent count=0.

    Live finding: ``GET /jobs`` without ``region`` returns 200 ``{"count":0,
    "jobs":[]}`` for EVERY region, while a scoped ``GET /jobs?region=<r>`` returns the
    real, non-zero count — so an empty result here proves the scope is missing, not
    that the workspace has no jobs. (The exact per-region count is a fast-moving live
    datum, so it is deliberately NOT quoted here.) A deliberate refusal (like the
    /home/jovyan precondition) beats leaking a false emptiness to the model.
    """
    if not values.get("region"):
        raise MLSpaceError(
            'GET /jobs without `region` returns 200 {"count":0,"jobs":[]} for every '
            "region; with a region it returns the real (non-zero) count — an empty "
            "result here does NOT prove there are no jobs, only that the region scope "
            "is missing.",
            hint="Pass region=<e.g. DGX2-MT|A100-MT|SR003|SR005|SR006|SR008> (examples, "
            "not a closed set). For existing jobs, use mlspace_jobs_overview, which "
            "combines regions from configs and workspace allocations; configs alone "
            "can omit allocated regions. Jobs live per region.",
        )


def _preflight_jobs_list_status(values: dict[str, Any]) -> None:
    """Normalise a jobs `list` status filter to the enum casing the API accepts.

    The list endpoint filters on the Capitalized ``JobStatus-Input`` enum, but a
    model naturally passes get's lowercase status (e.g. 'failed'). This is a pure
    translation to the value the API itself requires (precedent: the silent limit
    clamp in the handler), NOT invention: unknown values are left untouched so the
    API returns its own exemplary 422.
    """
    raw = values.get("status")
    if raw is None:
        return
    if isinstance(raw, (list, tuple)):
        values["status"] = [JOB_STATUS_CANON.get(str(s).casefold(), s) for s in raw]
    else:
        values["status"] = JOB_STATUS_CANON.get(str(raw).casefold(), raw)


def preflight_jobs_list(values: dict[str, Any]) -> None:
    """Composed preflight for ``GET /jobs`` (one dict key, two concerns).

    Region enforcement first (may refuse), then status-casing normalisation — the
    two live as separate, independently-testable helpers but share a single
    PREFLIGHT registration to avoid a key collision.
    """
    _preflight_jobs_list_region(values)
    _preflight_jobs_list_status(values)


def _precheck_unconfirmed(job_name: Any, reason: str) -> MLSpaceError:
    """The honest fail-CLOSED refusal shared by every 'could not confirm existence'
    branch of the delete guard."""
    return MLSpaceError(
        f"Could not confirm that job {job_name} exists before deleting it ({reason}). "
        "The delete was NOT sent. MLSpace replies status=\"deleted\" with a fresh "
        "deleted_at even for a name that does not exist, so issuing the DELETE without a "
        "confirmed precheck could report a FALSE 'deleted'.",
        hint="Retry, or confirm the exact job_name via `list` (with region) first, then "
        "re-run the delete.",
    )


async def guard_job_delete(client: Any, values: dict[str, Any]) -> None:
    """Refuse a jobs delete unless a status precheck CONFIRMS the job exists.

    Live finding: the delete endpoint replies 200 ``status="deleted"`` with a fresh
    ``deleted_at`` even for a name that never existed — a FALSE confirmation. One
    cheap status GET before the destructive call is the gate. This guard is
    FAIL-CLOSED (a safety validator, not a cosmetic transform): the DELETE proceeds
    ONLY when the precheck returns a concrete live status. A positive
    'job not found', a precheck error (timeout/403/500), or an unexpected shape all
    refuse — because none of them proves the job is really there to delete.

    Edge case (05#11): a job created <~2s ago also answers 'job not found' and is
    blocked here — fail-safe (better to refuse than to falsely confirm), covered by
    the retry-after-propagation hint.
    """
    job_name = values.get("job_name")
    try:
        body = await client.request(
            "GET", "/public/v2/jobs/{job_name}", path_params={"job_name": str(job_name)}
        )
    except MLSpaceError as exc:
        # precheck could not reach a verdict (4xx/5xx/timeout/odd body already mapped
        # to MLSpaceError) → fail-closed rather than let a blind DELETE claim success.
        raise _precheck_unconfirmed(job_name, f"precheck failed: {exc.message}") from exc
    except Exception as exc:  # defensive: any non-mapped failure is still unconfirmed
        raise _precheck_unconfirmed(job_name, f"precheck error: {type(exc).__name__}") from exc

    raw_status = body.get("status") if isinstance(body, dict) else None
    if not isinstance(raw_status, str):
        # null / object / missing: str() would turn these into a truthy "none"/"{...}"
        raise _precheck_unconfirmed(job_name, "precheck returned an unexpected shape")
    status = raw_status.strip().casefold()
    if status == "job not found":
        raise MLSpaceError(
            f"Job {job_name} does not exist (its status endpoint returns 'job not found') "
            "— nothing to delete. The delete endpoint would still reply status=\"deleted\" "
            "with a fresh deleted_at for this non-existent job; that is a FALSE confirmation.",
            hint="Verify the exact job_name via `list` (with region); if you created it "
            "<~2s ago, wait for it to propagate and retry.",
        )
    if not status:
        raise _precheck_unconfirmed(job_name, "precheck returned no status")
    # else: a concrete live status (pending/running/failed/…) → existence confirmed.


def postprocess_job_delete(data: Any, values: dict[str, Any]) -> Any:
    """Annotate a jobs delete so the caller does not read 'deleted' as a purge.

    Live finding: after a 200 ``status="deleted"`` the job KEEPS appearing in
    `list`/`get` with a terminal status, and each repeated delete returns a fresh
    ``deleted_at``. MLSpace delete is therefore LOGICAL (mark-deleted/stopped), not a
    history purge. We add a leading-underscore note and leave the original fields
    untouched. Fail-open on any other shape."""
    if isinstance(data, dict) and str(data.get("status", "")).strip().casefold() == "deleted":
        return {
            **data,
            "_wrapper_note": (
                "MLSpace delete is LOGICAL: status='deleted' with a fresh deleted_at means "
                "the job was marked deleted/stopped, NOT purged. It can still appear in "
                "`list`/`get` with a terminal status (completed/failed/stopped) afterward, "
                "and a repeated delete returns a fresh deleted_at again — do NOT treat this "
                "as proof the job is gone from history."
            ),
        }
    return data
