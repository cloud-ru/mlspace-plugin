"""Response shaping for tool output.

Response policy:
- default ``json``: compact structured passthrough with an explicit, testable
  noise-key drop list (k8s manifest cruft) — NOT a universal recursive markdown
  renderer, which mangles the manifests many GET endpoints return.
- long strings (base64 images, embedded blobs) are elided with a size note.
- arrays are capped to ``list_cap`` rendered rows with a "… N more" footer.
- caller-supplied ``redact_keys`` are masked (credential-returning reads).
- ``kind="log"`` returns the last ``log_tail`` lines with a truncation header.
- ``kind="catalog"`` losslessly de-duplicates the configs cross-product: the
  same heavy image objects are re-listed under every instance_type, so we hoist
  them into a per-region ``image_catalog`` and leave light ``name:tag`` refs in
  place (the exact per-instance image mapping is preserved). Fail-open on shape.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ._status import JOB_STATUS_CANON

# Keys dropped from API JSON to cut Kubernetes/manifest noise.
# Deliberately NOT including the generic name "annotations": dropping it globally
# at every depth would silently delete legitimate user/model metadata fields. The
# bulky k8s annotation blob it carries is the last-applied-config, dropped by name.
NOISE_KEYS = frozenset(
    {
        "managedFields",
        "resourceVersion",
        "ownerReferences",
        "last-applied-configuration",
        "kubectl.kubernetes.io/last-applied-configuration",
    }
)

_STR_ELIDE_LEN = 4096
_REDACTED = "<redacted>"

# Advisory attached to each catalog region: the listed instance_types are the
# region's full catalog, NOT what the caller's allocation actually grants. Worded
# without any ": " so it can't trip the compact-catalog separator assertions.
_ALLOCATION_NOTE = (
    "This is the region's full catalog, not the selected workspace/allocation/queue's "
    "available launch options. For MT job launch preparation use only keys from a successful "
    "scoped mlspace_resources instance_types response and look up their specs/images "
    "here. If that availability check failed (including 403/405), eligibility remains "
    "unverified: stop preparation and resolve access or placement. Do not offer "
    "catalog-only candidates or try a paid job to test access. A successful read "
    "is still a point-in-time observation, not a scheduling guarantee."
)

# Marker that replaces a repo list element's heavy recent_image.extra_attrs (~39%
# of the response). Elision, not a silent drop: it names what was omitted and where
# to get the full value (get_image).
_EXTRA_ATTRS_MARKER = "<omitted in list — call get_image for the full docker config>"


def _elide_str(value: str) -> str:
    if len(value) > _STR_ELIDE_LEN:
        return f"<{len(value)} chars elided>"
    return value


def _mask_secret(value: Any) -> Any:
    """Uniform masking for a redacted value: keep type-shape signal, drop content."""
    if isinstance(value, str):
        return f"<redacted, {len(value)} chars>"
    if value is None:
        return None
    return _REDACTED


# Heuristic secret-key detection for the dry-run preview body, where a write domain
# may not declare exact `redact_result_keys` (e.g. a data-transfer connector body can
# carry a password / security_key). Matched by NAME substring — over-redacting a
# preview is safe; leaking a credential into tool output/traces is not.
_SECRET_NEEDLES = (
    "password", "passwd", "secret", "token", "credential",
    "private_key", "security_key", "access_key", "api_key", "apikey",
)


def _looks_secret(key: Any) -> bool:
    return isinstance(key, str) and any(n in key.lower() for n in _SECRET_NEEDLES)


def redact_secrets(obj: Any) -> Any:
    """Recursively mask values under secret-looking keys (by key NAME). Pure and
    fail-safe: non-dict/list inputs pass through. Used for the dry-run preview body."""
    if isinstance(obj, dict):
        return {
            k: _mask_secret(v) if _looks_secret(k) else redact_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_secrets(v) for v in obj]
    return obj


def _clean(obj: Any, redact_keys: frozenset[str], list_cap: int) -> Any:
    """Recursively drop noise keys, redact secrets, elide blobs, cap arrays."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key in NOISE_KEYS:
                continue
            if key in redact_keys:
                out[key] = _mask_secret(value)
                continue
            out[key] = _clean(value, redact_keys, list_cap)
        return out
    if isinstance(obj, list):
        cleaned = [_clean(v, redact_keys, list_cap) for v in obj[:list_cap]]
        if len(obj) > list_cap:
            cleaned.append(f"… {len(obj) - list_cap} more item(s) omitted (partial output)")
        return cleaned
    if isinstance(obj, str):
        return _elide_str(obj)
    return obj


def _image_ref(img: Any) -> str | None:
    """Stable ``name:tag`` identity for a configs image object, or None if the
    object isn't shaped like an image (no ``name``) — caller then leaves it inline."""
    if not isinstance(img, dict) or "name" not in img:
        return None
    name = img.get("name")
    tags = img.get("tags")
    if isinstance(tags, list):
        tag_str = ",".join(str(t) for t in tags)
    elif tags is None:
        tag_str = ""
    else:
        tag_str = str(tags)
    return f"{name}:{tag_str}"


def _shrink_region(region: Any) -> Any:
    """Hoist a region's repeated image objects into a deduped ``image_catalog``,
    replacing each instance_type's ``images`` with light refs. Fail-open: any
    sub-structure that isn't the expected shape is passed through untouched."""
    if not isinstance(region, dict):
        return region
    instances = region.get("instances_types")
    if not isinstance(instances, list):
        return region

    catalog: dict[str, Any] = {}
    new_instances: list[Any] = []
    for inst in instances:
        if not isinstance(inst, dict) or not isinstance(inst.get("images"), list):
            new_instances.append(inst)
            continue
        refs: list[Any] = []
        for img in inst["images"]:
            ref = _image_ref(img)
            if ref is None:
                # not image-shaped — keep inline so nothing is lost
                refs.append(img)
                continue
            existing = catalog.get(ref)
            if existing is None:
                catalog[ref] = img
            elif existing != img:
                # same name:tag but different object — disambiguate to stay lossless
                n = 2
                ref = f"{ref}#{n}"
                while ref in catalog and catalog[ref] != img:
                    n += 1
                    ref = f"{ref.rsplit('#', 1)[0]}#{n}"
                catalog.setdefault(ref, img)
            refs.append(ref)
        new_inst = dict(inst)
        new_inst["images"] = refs
        new_instances.append(new_inst)

    new_region = dict(region)
    new_region["instances_types"] = new_instances
    # don't clobber a (hypothetical) pre-existing region key of the same name
    key = "image_catalog" if "image_catalog" not in new_region else "_image_catalog"
    new_region[key] = catalog
    # Only on the matched shape with a NON-EMPTY catalog: warn that this list is the
    # region's full catalog, not the caller's allocation grant (a region with no
    # instances_types returned early above and stays untouched).
    if instances:
        new_region.setdefault("_allocation_note", _ALLOCATION_NOTE)
    return new_region


def _region_matches(region: Any, wanted: str) -> bool:
    """True if a region's ``key`` or ``name`` equals ``wanted`` (case-insensitive)."""
    if not isinstance(region, dict):
        return False
    target = wanted.casefold()
    for field_name in ("key", "name"):
        value = region.get(field_name)
        if isinstance(value, str) and value.casefold() == target:
            return True
    return False


def _shrink_configs(data: Any, region: str | None = None) -> Any:
    """Losslessly shrink a configs-shaped dict (``{"regions": [...]}``). Returns
    the input untouched if it isn't the expected shape (fail-open).

    If ``region`` is given, keep only the region whose ``key``/``name`` matches it
    (case-insensitive). If nothing matches, keep all regions (fail-open). The
    image-dedup is still applied to whichever region(s) are kept."""
    if not isinstance(data, dict) or not isinstance(data.get("regions"), list):
        return data
    regions = data["regions"]
    if region:
        filtered = [r for r in regions if _region_matches(r, region)]
        if filtered:  # fail-open: empty match → keep all
            regions = filtered
    out = dict(data)
    out["regions"] = [_shrink_region(r) for r in regions]
    return out


def _to_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _to_json_compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)


def _to_markdown(obj: Any) -> str:
    """Thin v1 markdown: a JSON code fence (no lossy recursive rendering)."""
    return "```json\n" + _to_json(obj) + "\n```"


def _normalise_job(data: Any) -> Any:
    """Type a jobs `get` response: flag the created→'job not found' race and the
    failed+empty-error trap, and add a canonical status. Strictly fail-open and
    additive — original ``status``/``error_code``/``error_message`` are untouched,
    only ``_wrapper_note``/``status_canonical`` (leading-underscore / unused keys)
    are added.

    ``_wrapper_note`` names BOTH indistinguishable causes and picks neither, so the
    wrapper translates the response shape without inventing a reason. Overlaps the
    mlspace-diagnose-failed-training-job skill (which also says error_code/error_message are deprecated —
    ignore) ON PURPOSE: this is the inline defense for a caller who went straight to
    get without loading the skill."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    raw = str(data.get("status", ""))
    status = raw.casefold()
    if status == "job not found":
        out["_wrapper_note"] = (
            "status='job not found' under HTTP 200 has two indistinguishable causes "
            "and this body picks NEITHER. (1) The job was created <~2-3s ago and has "
            "not propagated yet — retry the get in a few seconds BEFORE concluding "
            "anything. (2) The name is wrong, or the job does not exist / was pruned by "
            "retention. The API returns an identical body in both cases, so this 200 is "
            "NOT confirmation that creation failed — do not create a duplicate."
        )
    elif (
        status == "failed"
        and data.get("error_code") in (0, None)
        and data.get("error_message") in ("", None)
    ):
        out["_wrapper_note"] = (
            "error_code/error_message on GetJobStatusResponse are declared deprecated "
            "constants (const 0 and const empty-string) — they are empty BY CONTRACT on "
            "a failure, not by accident. The real reason is only in the logs. Call "
            "action=logs (job_name + region, tail~100); the wrapper returns them in "
            "chronological order with the cause at the end."
        )
    # Stable canonical spelling for get(lowercase) vs list(Capitalized) equality
    # checks. Absent for 'job not found' and any unknown value (not in the enum).
    canon = JOB_STATUS_CANON.get(status)
    if canon is not None:
        out["status_canonical"] = canon
    return out


def _shrink_repos(data: Any) -> Any:
    """Elide each repo list element's heavy ``recent_image.extra_attrs`` (~39% of the
    response) with a pointer marker. Fail-open: a non-list input, or any element not
    shaped like a repo, passes through untouched; an EMPTY ``extra_attrs`` ({}) is
    left as-is (nothing to omit). Does NOT mutate the input.

    Scope is strictly the repos LIST. list_images (also an array of Image-Output with
    extra_attrs) is deliberately OUT of scope — surfacing/rewriting it collides with
    the pagination entity-splitting bug (08#4) — and get_image (kind='item') returns
    extra_attrs untouched, which is exactly where the full config is wanted."""
    if not isinstance(data, list):
        return data
    out: list[Any] = []
    for repo in data:
        if not isinstance(repo, dict):
            out.append(repo)
            continue
        recent = repo.get("recent_image")
        if (
            isinstance(recent, dict)
            and isinstance(recent.get("extra_attrs"), dict)
            and recent["extra_attrs"]
        ):
            new_recent = dict(recent)
            new_recent["extra_attrs"] = _EXTRA_ATTRS_MARKER
            new_repo = dict(repo)
            new_repo["recent_image"] = new_recent
            out.append(new_repo)
        else:
            out.append(repo)
    return out


def _listish_is_empty(d: Any) -> bool:
    """True for a dict that has at least one list value and whose EVERY list value is
    empty. Fail-open: anything that isn't such a dict returns False."""
    if not isinstance(d, dict):
        return False
    lists = [v for v in d.values() if isinstance(v, list)]
    return bool(lists) and all(len(v) == 0 for v in lists)


def _hoist_manifest_name(item: Any) -> Any:
    """If ``item`` is a k8s-manifest-shaped dict (has ``kind`` and a dict
    ``metadata`` with a str ``name``) and lacks a top-level ``name``, add
    ``name`` = ``metadata.name``. Strictly additive and fail-open: nothing is
    removed or renamed; non-manifests are returned unchanged."""
    if isinstance(item, dict) and "kind" in item and "name" not in item:
        meta = item.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("name"), str):
            return {**item, "name": meta["name"]}
    return item


def _hoist_manifest_names(cleaned: Any) -> Any:
    """Apply :func:`_hoist_manifest_name` to a bare top-level list, or to each element
    inside every list value of a dict. Other shapes pass through untouched."""
    if isinstance(cleaned, list):
        return [_hoist_manifest_name(x) for x in cleaned]
    if isinstance(cleaned, dict):
        return {
            k: [_hoist_manifest_name(x) for x in v] if isinstance(v, list) else v
            for k, v in cleaned.items()
        }
    return cleaned


# --- log normalisation -------------------------------------------------------
# The jobs `logs` endpoint is not chronological when `tail` is passed: it returns
# the newest N lines in REVERSE order, which inverts Python tracebacks (the
# ValueError lands above its own frames) and makes a top-down reader name the
# wrong root cause. Verified live 2026-08-03 with a controlled marker script.
_TS_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")

# The same endpoint encodes state as English prose inside an HTTP 200 body.
# Matching these is unavoidable; doing it once here beats every caller guessing.
_LOG_SENTINELS: tuple[tuple[str, str], ...] = (
    (
        "No such job",
        "logs_expired: the platform no longer retains logs for this job (they age out "
        "roughly 3 months after completion). The job itself still exists — `get` and "
        "`get_params` work. This is NOT a missing job.",
    ),
    (
        "in queue. Try later",
        "job_pending: the job is still queued and has produced no output yet. "
        "Poll `get` until status leaves `pending`, then re-read the logs.",
    ),
)


def _log_lines_chronological(lines: list[str]) -> tuple[list[str], bool]:
    """Return ``lines`` oldest-first plus whether we had to reverse them.

    Only timestamped lines vote, and only a clear majority flips the order, so a
    log with no timestamps (or a mixed banner) is passed through untouched."""
    stamps = [(i, m.group(1)) for i, line in enumerate(lines) if (m := _TS_RE.match(line))]
    if len(stamps) < 3:
        return lines, False
    pairs = list(zip(stamps, stamps[1:], strict=False))
    ascending = sum(1 for (_, a), (_, b) in pairs if b >= a)
    descending = sum(1 for (_, a), (_, b) in pairs if b <= a)
    if descending > ascending:
        return list(reversed(lines)), True
    return lines, False


# A leading RFC3339/ISO-ish timestamp (verbose logs prepend one), stripped so the
# extracted cause line reads cleanly.
_TS_PREFIX = re.compile(
    r"^\s*\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?\]?\s*"
)


def _strip_leading_timestamp(line: str) -> str:
    return _TS_PREFIX.sub("", line, count=1)


def _log_notes(lines: list[str]) -> list[str]:
    """Scan the FULL chronological log for an mpirun/ORTE launch-failure banner and,
    if found, return a single ``[likely_cause: ...]`` note. Scanning the full body
    BEFORE any tail truncation is the point: the note survives even when the banner
    is swept out of the tail. Strictly limited to the mpirun/ORTE family — ordinary
    Python tracebacks are left alone (the chronological order already puts their
    exception line at the end, and a generic parse risks mis-attribution)."""
    for i, line in enumerate(lines):
        low = line.lower()
        if "mpirun was unable to launch" in low or "orte was unable to" in low:
            cause = _strip_leading_timestamp(line).strip()
            parts = [cause]
            # the immediate continuation ("… or execute an executable:")
            if i + 1 < len(lines) and "executable:" in lines[i + 1].lower():
                parts.append(_strip_leading_timestamp(lines[i + 1]).strip())
            # and the concrete "Executable: <path>" line a couple of lines down — the
            # actual missing binary, which makes the cause complete instead of generic.
            for j in range(i + 1, min(i + 6, len(lines))):
                stripped = _strip_leading_timestamp(lines[j]).strip()
                if stripped.lower().startswith("executable:"):
                    if stripped not in parts:
                        parts.append(stripped)
                    break
            return ["[likely_cause: " + " | ".join(parts) + "]"]
    return []


def _format_log(data: Any, log_tail: int, redact_keys: frozenset[str], list_cap: int) -> str:
    if data is None:
        return "(no log output)"
    if isinstance(data, (dict, list)):
        # some log endpoints wrap lines in JSON; clean (redact/elide/cap) then emit
        return _to_json(_clean(data, redact_keys, list_cap))
    text = str(data)
    notes: list[str] = []
    for needle, explanation in _LOG_SENTINELS:
        if needle in text:
            notes.append(f"[{explanation}]")
    lines, reversed_ = _log_lines_chronological(text.splitlines())
    if reversed_:
        notes.append(
            "[note: the API returned these lines newest-first; reordered to "
            "chronological so causes precede effects]"
        )
    # Extract a typed cause note from the FULL chronological body BEFORE the tail
    # truncation below, so the note survives even if the banner is swept out of it.
    notes += _log_notes(lines)
    if len(lines) > log_tail:
        # keep the END of the chronological log — the failure lives there
        lines = [f"[showing last {log_tail} of {len(lines)} lines]"] + lines[-log_tail:]
    return "\n".join(notes + lines) if notes else "\n".join(lines)


def format_response(
    data: Any,
    response_format: str = "json",
    *,
    kind: str = "item",
    redact_keys: tuple[str, ...] = (),
    list_cap: int = 200,
    log_tail: int = 200,
    catalog_region: str | None = None,
) -> str:
    """Render an API result for a tool reply.

    ``catalog_region`` (kind=="catalog" only) optionally filters the configs
    cross-product to a single region client-side; ``None`` keeps all regions."""
    if kind == "log":
        return _format_log(data, log_tail, frozenset(redact_keys), list_cap)
    if kind == "catalog":
        data = _shrink_configs(data, catalog_region)
    elif kind == "job":
        data = _normalise_job(data)
    elif kind == "registry_repos":
        data = _shrink_repos(data)
    if data is None or data == "":
        return "(empty response — the operation succeeded with no body)"
    cleaned = _clean(data, frozenset(redact_keys), list_cap)
    if kind == "list" and isinstance(cleaned, (dict, list)):
        # surface an addressable name on manifest-shaped list items (metadata.name)
        cleaned = _hoist_manifest_names(cleaned)
        # flag an empty list that carries no completeness signal (real absence vs a
        # filter/region/permission narrowing). Skip lists that DO signal a total
        # (failed/total/count) so we don't wrongly claim "no signal".
        if (
            isinstance(cleaned, dict)
            and _listish_is_empty(cleaned)
            and not (set(cleaned) & {"failed", "total", "count"})
            and "_scope_note" not in cleaned
        ):
            cleaned["_scope_note"] = (
                "The source returned empty and gives no completeness signal — this may "
                "be a real absence OR a narrowing by filter/region/permissions, not "
                "proof that nothing exists. Check region/status/search."
            )
    if isinstance(cleaned, (dict, list)):
        if response_format == "markdown":
            return _to_markdown(cleaned)
        # the catalog is large even after dedup → emit compact JSON
        if kind == "catalog":
            return _to_json_compact(cleaned)
        return _to_json(cleaned)
    # scalar (str/int/bool/float)
    return str(cleaned)
