"""Install the user-scoped MLSpace plugin through each client's own CLI."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import diagnostics

MARKETPLACE_SOURCE = 'https://github.com/cloud-ru/mlspace-plugin.git'
PLUGIN_ID = 'mlspace@mlspace'


@dataclass(frozen=True)
class NativePluginStatus:
    client: str
    installed: bool = False
    enabled: bool = False
    version: str | None = None
    marketplace_source: str | None = None
    plugin_root: Path | None = None


def _run(client: str, executable: str, args: list[str], *, json_output: bool = False) -> Any:
    if client == 'codex' and (home := os.environ.get('CODEX_HOME')):
        try:
            Path(home).expanduser().mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise ValueError(f'codex: не удалось создать каталог CODEX_HOME: {home}. '
                             'Проверьте, что путь указывает на каталог с правом записи, затем повторите setup.') from exc
    command = shlex.join([executable, *args])
    # CLI arguments here are constructed by us and contain no credentials.
    log_command = shlex.join([Path(executable).name, *args])
    diagnostics.event('client.start', client=client, command=log_command)
    started = time.monotonic()
    timeout = 10 if args == ['--version'] else 180
    try:
        result = subprocess.run(
            [executable, *args], check=True, capture_output=True, text=True, timeout=timeout,
            cwd=Path.home(), stdin=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if not k.startswith('MLSPACE_')},
        )
        parsed = json.loads(result.stdout) if json_output else None
        if args == ['--version'] and (version := re.fullmatch(
                r'(?:codex-cli )?(\d{1,3}\.\d{1,3}\.\d{1,5})(?: \(Claude Code\))?',
                result.stdout.strip())):
            diagnostics.event('client.version', client=client, version=version[1])
        diagnostics.event('client.end', client=client, command=log_command, returncode=0,
                          duration_ms=round((time.monotonic() - started) * 1000))
        return parsed
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        category = 'unknown'
        if isinstance(exc, subprocess.CalledProcessError):
            reason = f'код возврата {exc.returncode}.'
            output = f'{exc.stderr or ""}\n{exc.stdout or ""}'.lower()
            # Classify known failures without printing any untrusted CLI output:
            # even a URL or a wrapper's startup message can contain a credential.
            if any(word in output for word in ('blocked by', 'organization policy',
                                               "organization's policy", 'strictknownmarketplaces',
                                               'blockedmarketplaces')):
                category = 'policy'
                reason += ' Клиент сообщил об ограничении политикой организации. Уточните у администратора разрешение marketplace MLSpace; setup не изменяет корпоративные политики.'
            elif 'unknown option' in output or 'unknown command' in output:
                category = 'unsupported_command'
                reason += ' Клиент не поддерживает команду или её параметры; проверьте --version и --help указанного исполняемого файла.'
            elif any(word in output for word in ('certificate', 'self signed', 'self-signed')):
                category = 'tls'
                reason += ' Не удалось проверить TLS-сертификат. Проверьте доверенные CA для клиента и Git; не отключайте проверку TLS.'
            elif any(word in output for word in ('could not resolve', 'unable to access', 'connection refused', 'proxy')):
                category = 'network'
                reason += ' Не удалось обратиться к marketplace. Проверьте сеть, DNS и настройки прокси клиента/Git.'
            else:
                reason += ' Выполните указанную команду в своём терминале для диагностики; не публикуйте сырой вывод с ключами.'
        elif isinstance(exc, subprocess.TimeoutExpired):
            category = 'timeout'
            reason = f'превышено время ожидания {timeout} секунд. Проверьте доступ к marketplace и работу обёртки клиента без интерактивного ввода.'
        elif isinstance(exc, FileNotFoundError):
            category = 'missing_executable'
            reason = 'исполняемый файл не найден. Проверьте путь, интерпретатор скрипта и --client-path.'
        elif isinstance(exc, OSError):
            category = 'process_start'
            reason = 'процесс не удалось запустить. Проверьте права на исполнение и --client-path.'
        else:
            category = 'invalid_json'
            reason = 'команда завершилась, но ожидаемый JSON не получен. Проверьте версию клиента и не добавляет ли обёртка текст в stdout.'
        diagnostics.failure('client', exc, client=client, command=log_command, reason=category,
                            returncode=getattr(exc, 'returncode', None),
                            duration_ms=round((time.monotonic() - started) * 1000))
        raise ValueError(f'{client}: {command}\n{reason} '
                         'Вывод клиента скрыт, поскольку может содержать секреты.') from exc


def inspect_native(client: str, executable: str) -> NativePluginStatus:
    """Read state before changing anything; never adopt another marketplace."""
    if client not in ('claude-code', 'codex'):
        raise ValueError('Клиент не поддерживает этот способ установки нативного плагина: ' + client)
    markets = _run(client, executable, ['plugin', 'marketplace', 'list', '--json'], json_output=True)
    plugins = _run(client, executable, ['plugin', 'list', '--json'], json_output=True)
    try:
        if client == 'codex':
            markets, plugins = markets['marketplaces'], plugins['installed']
        if not isinstance(markets, list) or not isinstance(plugins, list):
            raise TypeError
        matches = [row for row in markets if row['name'] == 'mlspace']
        if len(matches) > 1:
            raise TypeError
        source = None
        if matches:
            market = matches[0]
            if client == 'claude-code':
                kind, source = market['source'], market.get('url')
            else:
                kind = market['marketplaceSource']['sourceType']
                source = market['marketplaceSource']['source']
            if kind != 'git' or source != MARKETPLACE_SOURCE:
                raise ValueError(f'{client}: у marketplace mlspace другой источник; '
                                 'перед setup удалите или переименуйте этот marketplace в клиенте.')
        id_key = 'id' if client == 'claude-code' else 'pluginId'
        selected = [row for row in plugins if row[id_key] == PLUGIN_ID]
        if any('mlspace' in row[id_key].lower() and row[id_key] != PLUGIN_ID for row in plugins):
            raise ValueError(f'{client}: уже установлен другой плагин MLSpace. Разрешите конфликт в клиенте перед setup.')
        if not selected:
            return NativePluginStatus(client, marketplace_source=source)
        if len(selected) != 1 or (client == 'claude-code' and selected[0]['scope'] != 'user'):
            raise ValueError(f'{client}: MLSpace установлен вне области user. Разрешите конфликт областей установки в клиенте перед setup.')
        row = selected[0]
        if source is None:
            raise ValueError(f'{client}: не удалось подтвердить источник marketplace установленного плагина MLSpace. Проверьте установку плагина в клиенте.')
        version, enabled = row['version'], row['enabled']
        if not isinstance(version, str) or not isinstance(enabled, bool):
            raise TypeError
        root = None
        if client == 'claude-code':
            root = Path(row['installPath'])
        elif enabled:
            server = _run(client, executable, ['mcp', 'get', 'mlspace', '--json'], json_output=True)
            root = Path(server['transport']['env']['PLUGIN_ROOT'])
        if root is not None and (not root.is_absolute() or not root.is_dir()):
            raise TypeError
        return NativePluginStatus(client, True, enabled, version, source, root)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f'{client}: состояние плагина не поддерживается. Обновите клиент и повторите setup.') from exc


def install_native(client: str, executable: str, expected_version: str) -> NativePluginStatus:
    if diagnostics.current_path():
        try:
            _run(client, executable, ['--version'])
        except ValueError:
            pass  # The diagnostic version probe cannot prevent installation.
    status = inspect_native(client, executable)
    if status.installed and not status.enabled:
        raise ValueError(f'{client}: плагин MLSpace выключен. Включите {PLUGIN_ID} в клиенте и повторите setup.')
    if status.installed and status.version == expected_version:
        return status
    claude = client == 'claude-code'
    if status.marketplace_source is None:
        _run(client, executable, ['plugin', 'marketplace', 'add', MARKETPLACE_SOURCE,
                                 *(['--scope', 'user'] if claude else ['--json'])])
    else:
        _run(client, executable, ['plugin', 'marketplace', 'update' if claude else 'upgrade',
                                 'mlspace', *([] if claude else ['--json'])])
    action = ('update' if status.installed else 'install') if claude else 'add'
    _run(client, executable, ['plugin', action, PLUGIN_ID,
                             *(['--scope', 'user'] if claude else ['--json'])])
    result = inspect_native(client, executable)
    if not result.installed or not result.enabled or result.version != expected_version:
        raise ValueError(f'{client}: не удалось подтвердить ожидаемую версию MLSpace {expected_version}. '
                         'Обновите установщик из того же Git-релиза и повторите setup --force.')
    return result
