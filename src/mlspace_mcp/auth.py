"""Service-account token management for the MLSpace API.

``POST /public/v2/service_auth`` exchanges ``{client_id, client_secret}`` for a
short-lived (≈1h) bearer token. The response nests the token:
``{"token": {"access_token": "...", "expires_in": 3600}}`` — the sibling
status/error_* fields are deprecated and must NOT be read.

Refresh is single-flight: concurrent callers (and concurrent 401 retries against
a revoked token) collapse into exactly one ``/service_auth`` call.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from . import diagnostics
from .errors import MLSpaceError, error_from_exception, error_from_response

_EXPIRY_SKEW = 60.0  # refresh this many seconds before the token actually expires


class TokenManager:
    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        client_id: str,
        client_secret: str,
    ):
        self._http = http
        self._url = base_url.rstrip("/") + "/public/v2/service_auth"
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expiry: float = 0.0
        self._version: int = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _valid(self) -> bool:
        return self._token is not None and self._now() < self._expiry

    async def get_token(self) -> tuple[str, int]:
        """Return ``(token, version)``, refreshing if missing/expired."""
        if self._valid():
            return self._token, self._version  # type: ignore[return-value]
        async with self._lock:
            if self._valid():  # someone refreshed while we waited
                return self._token, self._version  # type: ignore[return-value]
            await self._refresh()
            return self._token, self._version  # type: ignore[return-value]

    async def refresh_after_401(self, used_version: int) -> tuple[str, int]:
        """Force a refresh after a 401, but only if no one else already has.

        All concurrent 401s share the same ``used_version`` so they collapse into
        a single ``/service_auth`` call.
        """
        async with self._lock:
            if self._version != used_version and self._valid():
                return self._token, self._version  # type: ignore[return-value]
            await self._refresh()
            return self._token, self._version  # type: ignore[return-value]

    async def _refresh(self) -> None:
        # IMPORTANT: never send Authorization/x-api-key/x-workspace-id here.
        try:
            resp = await diagnostics.request(
                self._http, 'POST', self._url,
                json={"client_id": self._client_id, "client_secret": self._client_secret},
            )
        except Exception as exc:  # transport-level
            raise error_from_exception(exc) from exc

        if resp.status_code == 401:
            raise MLSpaceError(
                "Invalid service-account credentials (401 from /service_auth).",
                status=401,
                hint="Check MLSPACE_CLIENT_ID and MLSPACE_CLIENT_SECRET.",
            )
        if resp.status_code >= 400:
            raise error_from_response(resp)

        try:
            payload = resp.json()
        except Exception as exc:
            raise MLSpaceError("Auth response was not valid JSON.") from exc

        token_obj = (payload or {}).get("token") or {}
        access_token = token_obj.get("access_token")
        expires_in = token_obj.get("expires_in")
        if not access_token:
            raise MLSpaceError(
                "Auth response missing token.access_token.",
                hint="Unexpected /service_auth response shape.",
            )
        try:
            ttl = float(expires_in) if expires_in is not None else 3600.0
        except (TypeError, ValueError):
            ttl = 3600.0
        self._token = access_token
        # non-positive TTL (0 / negative) -> treat as already expired, refresh next call
        self._expiry = self._now() + (max(ttl - _EXPIRY_SKEW, 1.0) if ttl > 0 else 0.0)
        self._version += 1
