"""Check installed release contents and a real MCP process without API writes."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import __version__
from .client_setup import _config, _read_config, _skill_sources, install_state_file
from .config import Settings, read_dotenv_file
from .native_plugins import inspect_native
from .server import build_server
from .setup_ui import display
from .workspace_context import environment_id, normalized_endpoint, selected_workspaces


def verify_runtime(command: list[str], credentials: Path, *, direct: bool = False,
                   cwd: Path | None = None) -> None:
    """Native startup must find the saved pointer with no setup-only environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('MLSPACE_')
           and k not in ('XDG_CONFIG_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    if direct:
        env['MLSPACE_ENV_FILE'] = str(credentials)
    values = read_dotenv_file(credentials)
    fields: dict[str, Any] = {key.removeprefix('MLSPACE_').lower(): value for key, value in values.items()
              if key.startswith('MLSPACE_') and key.removeprefix('MLSPACE_').lower() in Settings.model_fields}
    if 'workspaces' in fields:
        fields['workspaces'] = json.loads(fields['workspaces'])
    # Explicit defaults prevent ambient setup flags from changing the expected inventory.
    fields.setdefault('readonly', False)
    fields.setdefault('enabled_domains', '')
    settings = Settings(_env_file=None, **fields)  # type: ignore[call-arg]

    async def probe() -> None:
        expected = await build_server(settings).list_tools()
        parameters = StdioServerParameters(command=command[0], args=command[1:], env=env,
                                           cwd=str(cwd or Path.home()))
        with anyio.fail_after(90):
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    if {t.name: t.inputSchema for t in tools} != {t.name: t.inputSchema for t in expected}:
                        raise ValueError('Набор инструментов MCP отличается от этого релиза')
                    result = await session.call_tool('mlspace_contexts', {})
                    catalogue = json.loads(result.content[0].text)  # type: ignore[union-attr]
                    expected_catalogue = {
                        'environment': environment_id(settings.base_url),
                        'endpoint': normalized_endpoint(settings.base_url),
                        'workspaces': [{'id': w.id, 'name': w.name, 'project_name': w.project_name}
                                       for w in selected_workspaces(settings)],
                    }
                    if result.isError or catalogue != expected_catalogue:
                        raise ValueError('Каталог воркспейсов MCP отличается от сохранённых настроек')
    logger = logging.getLogger('mcp.client.stdio')
    was_disabled = logger.disabled
    logger.disabled = True  # SDK parse errors include the raw child stdout.
    try:
        with open(os.devnull, 'w') as errors:
            asyncio.run(probe())
    except Exception as exc:
        raise ValueError('Не удалось проверить запуск MCP. Проверьте сохранённые ключи и CA-сертификат, наличие uvx, '
                         'локальной поставки и доступ к её зависимостям, затем повторите setup --force. '
                         'Вывод процесса скрыт, чтобы не раскрыть ключи.') from exc
    finally:
        logger.disabled = was_disabled


def verify_client(client: str, executable: str, credentials: Path, command: list[str]) -> None:
    sources = _skill_sources()
    overrides: set[str] = set()
    if client == 'opencode':
        config = _read_config(_config(client)).get('mcp', {}).get('mlspace', {})
        if config.get('command') != command or config.get('enabled') is not True:
            raise ValueError('opencode: установленная конфигурация MCP отличается от ожидаемой; повторите setup --force.')
        root = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'opencode/skills'
        state_path = install_state_file()
        if state_path.exists():
            overrides = set(json.loads(state_path.read_text()).get('skill_overrides', []))
        cwd = None
    else:
        status = inspect_native(client, executable)
        if not status.enabled or status.version != __version__ or status.plugin_root is None:
            raise ValueError(f'{client}: версия установленного плагина или состояние включения отличается от ожидаемого; повторите setup --force.')
        cwd = status.plugin_root
        root = cwd / 'skills'
        portable = client == 'codex'
        config = json.loads((cwd / ('mcp.json' if portable else '.mcp.json')).read_text())['mcpServers']['mlspace']
        root_variable = '${PLUGIN_ROOT}' if portable else '${CLAUDE_PLUGIN_ROOT}'
        args = ['--python', '>=3.10', '--from', root_variable, 'mlspace-plugin', '--transport', 'stdio']
        if config != {'type': 'stdio', 'command': 'uvx', 'args': args}:
            raise ValueError(f'{client}: команда запуска MCP отличается от этого релиза; повторите setup --force.')
        if not (cwd / 'pyproject.toml').is_file() or not (cwd / 'src/mlspace_mcp/__main__.py').is_file():
            raise ValueError(f'{client}: в плагине отсутствует код MCP; переустановите поставку целиком.')
        command = ['uvx', '--python', '>=3.10', '--from', str(cwd), 'mlspace-plugin', '--transport', 'stdio']
    for name, content in sources.items():
        path = root / name / 'SKILL.md'
        if not path.is_file():
            raise ValueError(f'{client}: отсутствует установленный skill: {path}; повторите setup --force.')
        if client == 'opencode' and str(path) in overrides:
            print(f'  Пользовательский skill сохранён (соответствие релизу не проверяется): {display(str(path))}', file=sys.stderr)
        elif path.read_text() != content:
            raise ValueError(f'{client}: установленный skill отличается от этого релиза: {path}; повторите setup --force.')
    verify_runtime(command, credentials, direct=client == 'opencode', cwd=cwd)
