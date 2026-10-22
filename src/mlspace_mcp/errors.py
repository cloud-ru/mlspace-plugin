"""Structured, user-safe errors and HTTP/exception → message mapping.

Our own secrets (Authorization / x-api-key / client_secret) are kept out of error
text by construction: we never put request headers or the request body into
messages. We DO surface the API's response body (truncated) on 4xx — under the
assumption the API does not echo submitted credentials back; if that ever proves
false for some endpoint, add a scrub pass here.
"""

from __future__ import annotations

import re

import httpx

_BODY_LIMIT = 600

# A generic error page detector. Matches only when the body BEGINS with an HTML
# document/root/title/heading tag — so it never fires on JSON or plain text that
# merely mentions "<html>" mid-string. Used solely on 4xx bodies (see below).
_HTML_START = re.compile(r"\s*<(?:!doctype\s+html|html[\s>]|title>|h1>)", re.IGNORECASE)


def _body_as_text(resp: httpx.Response) -> str:
    """Human-readable body text, UNWRAPPING a bare JSON string.

    Some upstreams return an HTML error page inside a JSON string with a
    ``content-type: application/json`` header; then ``resp.json()`` yields a ``str``
    (not a dict), and that str is the HTML. Fail-open: on any decode trouble fall
    back to ``resp.text`` (then to "")."""
    try:
        parsed = resp.json()
    except Exception:
        parsed = None
    if isinstance(parsed, str):
        return parsed
    try:
        return resp.text or ""
    except Exception:
        return ""


def _is_html_error_page(resp: httpx.Response) -> bool:
    """True if the response body is a generic HTML error page (by content-type or
    by a leading HTML tag). Fail-open: any unexpected shape returns False, which
    keeps the pre-existing (raw-body) behaviour."""
    try:
        ctype = resp.headers.get("content-type", "").lower()
        if "html" in ctype:
            return True
        return bool(_HTML_START.match(_body_as_text(resp)))
    except Exception:
        return False


def _html_note(status: int) -> str:
    """A concise, honest description of a generic HTML error page for ``status``.

    Names ONLY the response shape and the indistinguishable states it implies —
    it invents no reason the page did not carry. 400/422 wording is a preventive
    fail-safe (no HTML case has been observed there; every observed one is 404,
    plus text/plain 500 which this does not touch)."""
    note = (
        "the server returned a generic HTML error page instead of a structured JSON "
        "error. It carries no machine-readable reason"
    )
    if status == 404:
        note += (
            " and does not distinguish among the possible causes — wrong resource "
            "name, unsupported route, or a resource that is not ready yet. Treat as: "
            "not found by the upstream service."
        )
    elif 500 <= status < 600:
        note += " — the upstream handler failed before producing one."
    else:
        note += "."
    return note


class MLSpaceError(Exception):
    """An error safe to surface to the LLM/user. Tools render it via ``to_text``."""

    def __init__(self, message: str, *, status: int | None = None, hint: str | None = None):
        self.message = message
        self.status = status
        self.hint = hint
        super().__init__(message)

    def to_text(self) -> str:
        parts = [f"Error: {self.message}"]
        if self.hint:
            parts.append(self.hint)
        return " ".join(parts)


def _safe_body(resp: httpx.Response) -> str:
    try:
        text = resp.text or ""
    except Exception:
        return ""
    text = text.strip().replace("\n", " ")
    if len(text) > _BODY_LIMIT:
        text = text[:_BODY_LIMIT] + "…"
    return text


def _detail(resp: httpx.Response) -> str:
    """Render an API error body into a concise, actionable message.

    Handles FastAPI-style ``{"detail": [{"loc": [...], "msg": "..."}]}`` (the
    shape MLSpace returns for validation errors) so the model gets per-field
    reasons it can fix, and falls back to the raw (truncated) body otherwise.
    """
    try:
        data = resp.json()
    except Exception:
        return _safe_body(resp)
    if isinstance(data, dict):
        detail = data.get("detail", data)
        if isinstance(detail, list):
            parts = []
            for item in detail[:8]:
                if isinstance(item, dict):
                    loc = [str(p) for p in item.get("loc", []) if p != "body"]
                    msg = item.get("msg") or item.get("type") or ""
                    parts.append(f"{'.'.join(loc)}: {msg}".strip(": "))
                else:
                    parts.append(str(item))
            if parts:
                return "; ".join(parts)
        if isinstance(detail, str):
            return detail
        # other shapes (e.g. {"reason": ...}), possibly nested under "detail"
        scope = detail if isinstance(detail, dict) else data
        for key in ("reason", "message", "error_message"):
            if isinstance(scope.get(key), str) and scope[key]:
                return scope[key]
    return _safe_body(resp)


def error_from_response(resp: httpx.Response) -> MLSpaceError:
    """Map an HTTP error response to an actionable MLSpaceError."""
    status = resp.status_code
    body = _safe_body(resp)
    # Detect a generic HTML error page once (fail-open → False keeps raw-body behaviour).
    is_html = _is_html_error_page(resp)
    if status == 400:
        detail = _html_note(400) if is_html else _detail(resp)
        return MLSpaceError(
            f"Bad request (400). {detail}".strip(),
            status=400,
            hint="Check the action's parameters and body against the API schema.",
        )
    if status == 401:
        return MLSpaceError(
            "Authentication failed (401).",
            status=401,
            hint="Check MLSPACE_CLIENT_ID/MLSPACE_CLIENT_SECRET and MLSPACE_API_KEY.",
        )
    if status == 403:
        return MLSpaceError(
            "Permission denied (403).",
            status=403,
            hint="The service account may lack access to this workspace or resource.",
        )
    if status == 404:
        detail = _html_note(404) if is_html else body
        return MLSpaceError(
            f"Not found (404). {detail}".strip(),
            status=404,
            hint="Check the resource name/id and that it exists in this workspace.",
        )
    if status == 409:
        return MLSpaceError(f"Conflict (409). {body}".strip(), status=409)
    if status == 422:
        detail = _html_note(422) if is_html else _detail(resp)
        return MLSpaceError(
            f"Validation error (422). {detail}".strip(),
            status=422,
            hint="The API rejected the request body or parameters.",
        )
    if status == 429:
        retry_after = resp.headers.get("retry-after")
        return MLSpaceError(
            "Rate limited (429).",
            status=429,
            hint=f"Retry after {retry_after}s." if retry_after else "Slow down and retry shortly.",
        )
    if status == 445:
        # 445 is a non-standard code some WAF/security layers emit; the body is a
        # large HTML page we deliberately keep out of the message/hint entirely. We
        # cannot prove from the status alone that it is a WAF, so we phrase it as a
        # possibility and give the one durable, actionable fact: it is deterministic.
        return MLSpaceError(
            "Request blocked before reaching the platform (HTTP 445) — a non-standard "
            "code used by some WAF/security layers.",
            status=445,
            hint="The request tripped a security rule (e.g. shell metacharacters or a "
            "path-traversal-looking value in some field). This is deterministic — change "
            "the offending input; do not retry the request unchanged.",
        )
    if 500 <= status < 600:
        # Not all MLSpace 5xx are transient: some endpoints fail deterministically
        # for a whole class of input (e.g. allocation assignments on cluster-type
        # allocations 500s every single time, verified live). Advising a blind
        # "retry later" there sends an agent into an unbounded retry loop, so the
        # hint deliberately bounds the retry and names the alternative.
        return MLSpaceError(
            f"MLSpace server error ({status}).",
            status=status,
            hint=(
                "Retry ONCE. If it fails again with the same status, treat it as a "
                "deterministic failure of this endpoint for this input — do not keep "
                "retrying; try a different action or report the endpoint as broken."
            ),
        )
    detail = _html_note(status) if is_html else body
    return MLSpaceError(f"Unexpected HTTP {status}. {detail}".strip(), status=status)


def error_from_exception(exc: Exception) -> MLSpaceError:
    """Map an httpx transport exception to an actionable MLSpaceError."""
    # TimeoutException is the base of Connect/Read/Write/PoolTimeout.
    if isinstance(exc, httpx.TimeoutException):
        return MLSpaceError(
            "Request to the MLSpace API timed out.",
            hint="Retry, or raise MLSPACE_TIMEOUT for slow operations.",
        )
    if isinstance(exc, httpx.ConnectError):
        return MLSpaceError(
            "Could not connect to the MLSpace API.",
            hint="Check network access and MLSPACE_BASE_URL.",
        )
    if isinstance(exc, httpx.RemoteProtocolError):
        return MLSpaceError(
            "Protocol error talking to the MLSpace API.",
            hint="Retry; if it persists the endpoint may be misbehaving.",
        )
    if isinstance(exc, httpx.HTTPError):
        return MLSpaceError(f"HTTP client error ({type(exc).__name__}).")
    return MLSpaceError(f"Unexpected error ({type(exc).__name__}).")
