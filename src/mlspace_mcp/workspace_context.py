"""A fixed selected catalogue and independent, lazily initialized HTTP clients."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from .auth import TokenManager
from .client import MLSpaceClient
from .config import Settings
from .errors import MLSpaceError


def normalized_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and (parsed.scheme.lower(), port) not in {("https", 443), ("http", 80)}:
        host += f":{port}"
    return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))


def environment_id(endpoint: str) -> str:
    return "mlspace:" + hashlib.sha256(normalized_endpoint(endpoint).encode()).hexdigest()


@dataclass(frozen=True)
class WorkspaceSelection:
    id: str
    name: str
    project_name: str
    api_key: str = field(default="", repr=False)
    namespace: str = ""


def selected_workspaces(settings: Settings) -> tuple[WorkspaceSelection, ...]:
    if settings.workspaces:
        return tuple(WorkspaceSelection(
            item.id, item.name, item.project_name,
            item.api_key.get_secret_value() if item.api_key else "",
            settings.namespace if len(settings.workspaces) == 1
            and item.id == settings.workspace_id else "",
        ) for item in settings.workspaces)
    return (WorkspaceSelection(settings.workspace_id, settings.workspace_id, "",
                               settings.api_key.get_secret_value(), settings.namespace),)


def select_target(selected: tuple[WorkspaceSelection, ...], target: str | None) -> WorkspaceSelection:
    if target is None:
        if len(selected) == 1:
            return selected[0]
        raise MLSpaceError("Choose target from mlspace_contexts; several workspaces are configured.")
    by_id = [item for item in selected if item.id == target]
    matches = by_id or [item for item in selected if item.name == target]
    if len(matches) > 1:
        raise MLSpaceError("Workspace target is ambiguous; use a configured workspace ID.")
    if not matches:
        raise MLSpaceError("Workspace target is not configured; see mlspace_contexts.")
    return matches[0]


class WorkspaceClients:
    """Never discover membership or switch an existing client's credentials."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient, tokens: TokenManager):
        self.selected = selected_workspaces(settings)
        if len({item.id for item in self.selected}) != len(self.selected):
            raise MLSpaceError("Duplicate configured workspace IDs.")
        self.endpoint = normalized_endpoint(settings.base_url)
        self.environment = environment_id(self.endpoint)
        self._http = http
        self._tokens = tokens
        self._clients: dict[str, MLSpaceClient] = {}
        self._locks = {item.id: asyncio.Lock() for item in self.selected}

    def select(self, target: str | None) -> WorkspaceSelection:
        """Resolve membership without HTTP; use before validating operation addresses."""
        return select_target(self.selected, target)

    def catalogue(self) -> dict:
        return {"environment": self.environment, "endpoint": self.endpoint,
                "workspaces": [{"id": item.id, "name": item.name,
                                "project_name": item.project_name} for item in self.selected]}

    async def resolve(self, target: str | None) -> MLSpaceClient:
        selection = self.select(target)
        async with self._locks[selection.id]:
            if selection.id not in self._clients:
                key = selection.api_key
                if not key:
                    bootstrap = MLSpaceClient(self._http, self._tokens, base_url=self.endpoint,
                                              api_key="", workspace_id=selection.id)
                    data = await bootstrap.request("GET", "/public/v2/workspaces/v1/x_api_key")
                    resolved_key = data.get("x-api-key") if isinstance(data, dict) else None
                    if not isinstance(resolved_key, str) or not resolved_key.strip():
                        raise MLSpaceError("API key unavailable for the selected workspace.")
                    key = resolved_key
                self._clients[selection.id] = MLSpaceClient(
                    self._http, self._tokens, base_url=self.endpoint, api_key=key,
                    workspace_id=selection.id, namespace=selection.namespace,
                )
            return self._clients[selection.id]
