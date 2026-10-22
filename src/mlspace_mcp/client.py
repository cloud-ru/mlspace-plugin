"""The single HTTP entry point to the MLSpace API.

The client owns ALL header injection (Authorization + x-api-key + x-workspace-id),
path templating, query encoding (repeated keys for list values), and JSON bodies
(attached for any method, including DELETE). Tools never touch headers.

Frozen contract (do not change without updating every tools/*.py):

    async def request(
        self,
        method: str,
        path_template: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict | list[tuple] | None = None,
        json_body: Any | None = None,
    ) -> Any
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from . import diagnostics
from .auth import TokenManager
from .errors import MLSpaceError, error_from_exception, error_from_response

# Defence-in-depth: even values that reach the client WITHOUT going through
# resolve_request's per-segment encoding (the guard/namespace prechecks call
# ``request`` directly with a raw id) must never carry a traversal or control char
# into the final path. resolve_request is the primary route-confusion guard; this
# is the last line before the wire.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _assert_safe_path(path: str) -> None:
    if _CTRL_RE.search(path):
        raise MLSpaceError("Refusing to send a request whose path contains control chars.")
    for seg in path.split("/"):
        if seg in ("..", "."):
            raise MLSpaceError(
                f"Refusing a request whose path contains a '{seg}' traversal segment "
                f"({path!r}) — a path parameter must be a single opaque id/name."
            )


class MLSpaceClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: TokenManager,
        *,
        base_url: str,
        api_key: str,
        workspace_id: str,
        namespace: str = "",
    ):
        self._http = http
        self._tokens = tokens
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._workspace_id = workspace_id
        # k8s namespace of the configured workspace; resolved lazily from
        # workspace_id when not pinned via config.
        self._namespace: str | None = namespace or None
        self._ns_lock = asyncio.Lock()

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    async def workspace_namespace(self) -> str | None:
        """The configured workspace's k8s namespace, resolved once and cached.

        Returns None if it can't be determined (caller then falls back to asking
        the model for it). Single-flight: concurrent first-callers collapse into
        one ``workspaces/v3/{id}`` lookup.
        """
        if self._namespace is not None:
            return self._namespace
        async with self._ns_lock:
            if self._namespace is not None:
                return self._namespace
            try:
                data = await self.request(
                    "GET",
                    "/public/v2/workspaces/v3/{workspace_id}",
                    path_params={"workspace_id": self._workspace_id},
                )
            except Exception:
                return None  # leave uncached: retry on a later call
            ns = data.get("namespace") if isinstance(data, dict) else None
            if ns:
                self._namespace = ns
            return ns

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "x-api-key": self._api_key,
            "x-workspace-id": self._workspace_id,
        }

    async def request(
        self,
        method: str,
        path_template: str,
        *,
        path_params: dict[str, str] | None = None,
        params: Any = None,
        json_body: Any = None,
    ) -> Any:
        if path_params:
            try:
                path = path_template.format(**path_params)
            except KeyError as exc:
                raise MLSpaceError(
                    f"Missing path parameter {exc} for {path_template}."
                ) from exc
        else:
            path = path_template
        _assert_safe_path(path)
        url = self._base + path

        token, version = await self._tokens.get_token()
        resp = await self._send(method, url, token, params, json_body)

        if resp.status_code == 401:
            # token may be revoked/expired-early — single-flight force refresh, retry once
            token, _ = await self._tokens.refresh_after_401(version)
            resp = await self._send(method, url, token, params, json_body)

        if resp.status_code >= 400:
            raise error_from_response(resp)
        return self._parse(resp)

    async def _send(
        self, method: str, url: str, token: str, params: Any, json_body: Any
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {"headers": self._headers(token)}
        if params is not None:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body  # attached for any method, incl. DELETE
        try:
            return await diagnostics.request(self._http, method, url, **kwargs)
        except MLSpaceError:
            raise
        except Exception as exc:
            raise error_from_exception(exc) from exc

    @staticmethod
    def _parse(resp: httpx.Response) -> Any:
        if resp.status_code == 204 or not resp.content:
            return None
        ctype = resp.headers.get("content-type", "").lower()
        # explicit binary (e.g. dalle image) — don't dump raw bytes into context
        if ctype.startswith(("image/", "audio/", "video/", "application/octet-stream")):
            return f"<binary response, {len(resp.content)} bytes, content-type={ctype}>"
        # json / text / unknown-or-missing content-type — try json, then text
        try:
            return resp.json()
        except Exception:
            try:
                return resp.text
            except Exception:
                return f"<{len(resp.content)} bytes, content-type={ctype or 'unknown'}>"
