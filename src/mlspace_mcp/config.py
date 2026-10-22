"""Runtime settings, loaded from the ``MLSPACE_`` environment / ``.env``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
DEFAULT_BASE_URL = "https://api.ai.cloud.ru"
LITERAL_DOTENV_MARKER = "# mlspace-plugin: literal-dotenv-v1"

#: Explicit credentials path; native plugins use the shared user configuration.
ENV_FILE_VAR = "MLSPACE_ENV_FILE"

#: What a hand-written credentials file needs. The workspace x-api-key is optional:
#: when absent the server fetches it with the service-account token.
CREDENTIALS = ("CLIENT_ID", "CLIENT_SECRET", "WORKSPACE_ID")

APP_DIR = "mlspace-plugin"
INIT_HINT = "run setup from your MLSpace plugin installation to fill it in (see INSTALL.md)"


def user_config_file() -> Path:
    """``~/.config/mlspace-plugin/.env`` — the one place a person configures once.

    Honours ``XDG_CONFIG_HOME``. This is where ``mlspace-plugin setup`` writes, so the
    same credentials serve every way the server gets launched: through the plugin
    bundle, from a hand-written client config, or straight from a shell.
    """
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_DIR / ".env"


def credential_pointer_file() -> Path:
    """Stable per-user locator, independent of a client's filtered environment."""
    return Path.home() / '.config' / APP_DIR / 'credentials-path.json'


def credential_files() -> list[Path]:
    """The files consulted, in ASCENDING priority — the more specific, the later.

    ``pydantic-settings`` lets the last file win and skips ones that do not exist,
    so this is the whole precedence rule:

    1. the user config — written by ``setup``
    2. ``MLSPACE_ENV_FILE`` — an explicitly trusted file

    Never read a checkout dotenv automatically: it could redirect saved credentials.

    Real environment variables still beat every file; that is pydantic's own order.
    """
    primary = user_config_file()
    pointer = credential_pointer_file()
    if pointer.exists():
        try:
            data = json.loads(pointer.read_text())
            value = data.get('env_file') if isinstance(data, dict) else None
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError
            primary = Path(value)
        except (ValueError, OSError) as exc:
            raise ValueError(f'Invalid credentials-path file: {pointer}. '
                             'Run setup --path /absolute/path/to/credentials.env to repair it.') from exc
        if not primary.is_file():
            raise ValueError(f'Selected credentials file is missing: {primary}. '
                             'Run setup --path /absolute/path/to/credentials.env to repair it.')
    files = [primary]
    named = os.environ.get(ENV_FILE_VAR, "").strip()
    if named:
        files.append(Path(named))
    return files


def read_dotenv_file(path: Path) -> dict[str, str | None]:
    """Parse a dotenv file, preserving literals in files written by current init."""
    literal = False
    if path.is_file():
        literal = LITERAL_DOTENV_MARKER in path.read_text(encoding="utf-8").splitlines()
    return dict(dotenv_values(path, interpolate=not literal))


class ConnectedWorkspace(BaseModel):
    """One workspace explicitly selected during ``mlspace-plugin setup``."""

    model_config = ConfigDict(hide_input_in_errors=True)

    id: str
    name: str
    project_name: str
    api_key: SecretStr | None = None

    @field_validator("id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Workspace id must not be blank")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MLSPACE_", env_file=None, env_file_encoding="utf-8", extra="ignore",
        hide_input_in_errors=True,
    )

    # credentials
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    api_key: SecretStr = SecretStr("")
    workspace_id: str = ""
    workspaces: list[ConnectedWorkspace] = Field(default_factory=list)
    # optional: pin the workspace's k8s namespace; empty => resolved from workspace_id
    namespace: str = ""
    # where the credentials file was read from; set via MLSPACE_ENV_FILE. Kept on the
    # instance so a missing-credential error can name the exact file to create.
    env_file: str = ""

    # endpoint / behaviour
    ca_file: str = ""
    base_url: str = DEFAULT_BASE_URL
    # Write actions are available by default; the server is a working tool, not a
    # viewer. What still stands between an agent and an irreversible change is the
    # `confirm=true` gate on the 16 destructive actions, the jobs-delete existence
    # precheck, the rotation opt-in below, and preflight refusals — see registry.py.
    readonly: bool = False
    # Account-wide credential rotation needs a server-side opt-in, not just an
    # agent-supplied confirm flag. Never enable this automatically during setup.
    allow_registry_password_rotation: bool = False
    # dry-run: write/side-effecting actions are fully validated (body lint, path
    # resolve, confirm gate) but NOT sent — the tool returns a preview of the request
    # that WOULD be issued. Lets an LLM safely assemble & check a write before it runs.
    dry_run: bool = False
    transport: str = "streamable-http"  # streamable-http | stdio
    host: str = "127.0.0.1"
    port: int = 8000
    timeout: float = 60.0
    enabled_domains: str = ""  # comma-separated; empty = all

    # response shaping
    list_default_limit: int = Field(default=50, ge=1)
    list_max_limit: int = Field(default=200, ge=1)
    log_tail_lines: int = Field(default=200, ge=1)

    @property
    def enabled_domain_set(self) -> set[str] | None:
        names = {d.strip() for d in self.enabled_domains.split(",") if d.strip()}
        return names or None

    def require_credentials(self) -> None:
        """Raise if any required credential is unset (call before serving requests).

        When a credentials file was named (the plugin bundle always names one), the
        error quotes its absolute path and a ready-to-paste body: the caller running
        us inside a plugin cannot otherwise know where that directory lives, and
        "set these four variables" is useless advice when the client sanitizes the
        environment it hands us.
        """
        required = [
            ("CLIENT_ID", self.client_id),
            ("CLIENT_SECRET", self.client_secret.get_secret_value()),
        ]
        if not self.workspaces:
            required.append(("WORKSPACE_ID", self.workspace_id))
        missing = ["MLSPACE_" + name for name, value in required if not value]
        if not missing:
            return

        target = Path(self.env_file) if self.env_file else user_config_file()
        state = "is empty or lacks them" if target.is_file() else "does not exist yet"
        looked_in = ", ".join(str(p) for p in credential_files())
        raise RuntimeError(
            "\n".join(
                [
                    "Missing required credentials: " + ", ".join(missing) + ".",
                    f"Looked in: {looked_in}, and the environment.",
                    "",
                    f"Fix it: `mlspace-plugin setup` writes {target} for you.",
                    f"Or create it by hand ({target} {state}):",
                    *(f"  MLSPACE_{name}=..." for name in CREDENTIALS),
                ]
            )
        )

    def validate_bind(self) -> None:
        """Fail closed: v1 has no server-side auth, so refuse a non-loopback HTTP bind."""
        host = self.host.strip("[]").lower()  # accept bracketed/again-cased IPv6 loopback
        if self.transport != "stdio" and host not in LOOPBACK_HOSTS:
            raise RuntimeError(
                f"Refusing to bind streamable-HTTP to non-loopback host {self.host!r}: "
                "this server has no built-in authentication and would expose shared "
                "write credentials to the network. Bind 127.0.0.1 (default) and put it "
                "behind an authenticated proxy for remote access."
            )


def load_settings() -> Settings:
    """Build ``Settings`` from the credential search order in ``credential_files``.

    ``env_file`` is fixed on the model at class-definition time, so the chain has to
    be supplied per-instantiation. Real environment variables still win over every
    file — pydantic's own order — so an ambient ``MLSPACE_*`` setup is untouched.
    """
    named = os.environ.get(ENV_FILE_VAR, "").strip()
    if named and os.path.isdir(named):
        raise RuntimeError(
            f"{ENV_FILE_VAR}={named!r} is a directory; it must name the credentials "
            "FILE itself, e.g. .../.env"
        )
    files = credential_files()
    sources: list[dict[str, str | None]] = []
    for path in files:
        values = read_dotenv_file(path) if path.is_file() else {}
        sources.append({key.upper(): value for key, value in values.items()})
    sources.append({key.upper(): value for key, value in os.environ.items()})

    selector_source = -1
    selector_kind = ""
    selected_catalogue: list[ConnectedWorkspace] = []
    for index in range(len(sources) - 1, -1, -1):
        source = sources[index]
        if "MLSPACE_WORKSPACES" in source:
            parsed = json.loads(str(source.get("MLSPACE_WORKSPACES") or ""))
            selected_catalogue = [ConnectedWorkspace.model_validate(item) for item in parsed]
            selector_source = index
            selector_kind = "catalogue" if selected_catalogue else "legacy"
            break
        if str(source.get("MLSPACE_WORKSPACE_ID") or "").strip():
            selector_source = index
            selector_kind = "legacy"
            break

    merged: dict[str, Any] = {}
    for source in sources:
        for key, value in source.items():
            if not key.startswith("MLSPACE_") or value is None:
                continue
            field_name = key.removeprefix("MLSPACE_").lower()
            if field_name in Settings.model_fields and field_name != "workspaces":
                merged[field_name] = value
    merged["workspaces"] = selected_catalogue if selector_kind == "catalogue" else []
    settings = Settings(_env_file=None, **merged)  # type: ignore[call-arg]

    # Workspace selection is one context rather than independent fields. Track the
    # provenance of selectors and workspace-bound scalar values before normalizing.
    api_key_source = -1
    api_key_workspace_id = ""
    api_key_value = ""
    namespace_source = -1
    namespace_workspace_id = ""
    namespace_value = ""
    legacy_workspace_id = ""
    for index, source in enumerate(sources):
        source_workspace_id = str(source.get("MLSPACE_WORKSPACE_ID") or "").strip()
        if source_workspace_id:
            legacy_workspace_id = source_workspace_id
        if "MLSPACE_API_KEY" in source:
            api_key_source = index
            api_key_workspace_id = legacy_workspace_id
            api_key_value = str(source.get("MLSPACE_API_KEY") or "")
        if "MLSPACE_NAMESPACE" in source:
            namespace_source = index
            namespace_workspace_id = legacy_workspace_id
            namespace_value = str(source.get("MLSPACE_NAMESPACE") or "")

    if selector_kind == "legacy":
        settings.workspaces = []
        selected_id = settings.workspace_id

        effective_key_source = api_key_source if api_key_workspace_id == selected_id else -1
        effective_key = api_key_value if effective_key_source >= 0 else ""
        for index in range(selector_source - 1, -1, -1):
            raw_catalogue = sources[index].get("MLSPACE_WORKSPACES")
            if raw_catalogue is None:
                continue
            try:
                parsed = json.loads(str(raw_catalogue))
                catalogue = [ConnectedWorkspace.model_validate(item) for item in parsed]
            except (TypeError, ValueError):
                continue
            match = next((item for item in catalogue if item.id == selected_id), None)
            if index > effective_key_source:
                effective_key_source = index
                effective_key = (
                    match.api_key.get_secret_value() if match is not None and match.api_key else ""
                )
            break
        settings.api_key = SecretStr(effective_key)

        if namespace_workspace_id != selected_id:
            settings.namespace = ""
        elif namespace_source >= 0:
            settings.namespace = namespace_value
    elif selector_kind == "catalogue" and len(settings.workspaces) == 1:
        workspace = settings.workspaces[0]
        if api_key_source > selector_source and api_key_workspace_id == workspace.id:
            workspace.api_key = SecretStr(api_key_value) if api_key_value else None
    return settings
