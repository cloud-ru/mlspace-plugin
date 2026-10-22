"""User-scoped client registration and packaged skill installation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import __version__
from .config import credential_pointer_file
from .jsonc import loads as load_jsonc
from .jsonc import set_member as set_jsonc_member
from .native_plugins import inspect_native, install_native
from .setup_conflicts import Conflict, Resolutions, backup_replacements, conflict_message

CLIENTS = {'claude-code': 'claude', 'codex': 'codex', 'opencode': 'opencode'}


def install_state_file() -> Path:
    return credential_pointer_file().parent / 'install-state.json'


def detect_clients() -> dict[str, str]:
    return {name: path for name, executable in CLIENTS.items()
            if (path := shutil.which(executable))}


def runtime_command() -> list[str]:
    """Recreate this installation, never substitute an indexed package for it."""
    source = Path(__file__).resolve()
    cached = any(re.fullmatch(r'archive-v\d+', part) for part in source.parts)
    if cached and 'site-packages' in source.parts:
        records = list(source.parent.parent.glob('mlspace_plugin-*.dist-info/direct_url.json'))
        uvx = shutil.which('uvx')
        if not uvx or len(records) != 1:
            raise ValueError('Не удалось определить источник установки. Установите MLSpace из Git или '
                             'локальной поставки через `uv tool install`, затем повторите setup.')
        try:
            info = json.loads(records[0].read_text())
            url = urlsplit(info['url'])
            vcs = info.get('vcs_info', {})
            if url.password or (url.username and url.scheme != 'ssh') or url.query or url.fragment or info.get('subdirectory'):
                raise ValueError
            if vcs.get('vcs') == 'git' and url.scheme in ('https', 'ssh') and url.hostname:
                commit = vcs['commit_id']
                if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40,64}', commit):
                    raise ValueError
                return [uvx, '--python', '>=3.10', '--from', f"git+{info['url']}@{commit}", 'mlspace-plugin']
            if url.scheme == 'file' and url.netloc in ('', 'localhost') and 'dir_info' in info:
                root = Path(unquote(url.path)).resolve()
                if (root / 'pyproject.toml').is_file() and not _temporary_source(root):
                    return [uvx, '--python', '>=3.10', '--from', str(root), 'mlspace-plugin']
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ValueError('Некорректный источник установки. Повторите установку из доверенного Git-репозитория '
                             'или локальной поставки; не включайте токены в URL.') from None
        raise ValueError('Для локального wheel или временных исходников сначала выполните '
                         '`uv tool install --python ">=3.10" /путь/к/поставке` перед setup либо используйте '
                         'setup --config-only для уже установленного нативного плагина.')
    executable = Path(sys.executable).parent / 'mlspace-plugin'
    if not executable.is_file():
        raise ValueError('Перед подключением клиентов установите пакет командой `uv tool install --python ">=3.10" .`.')
    # An editable installation under /tmp disappears on reboot; reject that source.
    if _temporary_source(source):
        raise ValueError('Пакет установлен во временном каталоге. Сначала установите wheel через `uv tool install`.')
    return [str(executable.absolute())]


def _temporary_source(path: Path) -> bool:
    return path.is_relative_to(Path(tempfile.gettempdir()).resolve()) or str(path).startswith('/tmp/')


def server_command() -> list[str]:
    return [*runtime_command(), '--transport', 'stdio']


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise ValueError(f'Символическая ссылка не может быть заменена: {path}. Укажите обычный файл.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _skill_sources() -> dict[str, str]:
    bundled = files('mlspace_mcp').joinpath('skills')
    # Editable installs use the very same versioned source that goes into the wheel.
    source = Path(__file__).resolve().parents[2] / 'skills'
    root = bundled if bundled.is_dir() else source
    result = {item.name: item.joinpath('SKILL.md').read_text(encoding='utf-8')
              for item in root.iterdir() if item.is_dir() and item.joinpath('SKILL.md').is_file()}
    if not result:
        raise ValueError('В пакете не найдены skills. Переустановите wheel MLSpace.')
    return result


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _config(client: str) -> Path:
    if client == 'claude-code':
        custom = os.environ.get('CLAUDE_CONFIG_DIR')
        return Path(custom) / '.claude.json' if custom else Path.home() / '.claude.json'
    if client == 'codex':
        return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    explicit = os.environ.get('OPENCODE_CONFIG')
    if explicit:
        return Path(explicit).expanduser().absolute()
    root = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'opencode'
    jsonc, plain = root / 'opencode.jsonc', root / 'opencode.json'
    if jsonc.exists() and plain.exists():
        raise ValueError('Найдены одновременно opencode.json и opencode.jsonc. Явно укажите нужный файл через OPENCODE_CONFIG.')
    return jsonc if jsonc.exists() else plain


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.suffix == '.toml':
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib
        return dict(tomllib.loads(path.read_text()))
    return load_jsonc(path.read_text())


def managed_clients(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Find surviving managed registrations so one product updates together."""
    state_path = install_state_file()
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    detected = detect_clients()
    result = {}
    for client in state.get('clients', {}):
        if client not in CLIENTS:
            continue
        config = _config(client)
        previous_path = Path(state.get('configs', {}).get(client, str(config)))
        if client == 'opencode' and 'mlspace' not in _read_config(previous_path).get('mcp', {}):
            continue  # The user removed this connection; never recreate it.
        if previous_path.absolute() != config.absolute():
            variable = {'claude-code': 'CLAUDE_CONFIG_DIR', 'codex': 'CODEX_HOME',
                        'opencode': 'OPENCODE_CONFIG'}[client]
            raise ValueError(f'Ранее подключённый {client} использует {previous_path}. '
                             f'Перед обновлением клиентов MLSpace восстановите прежнее значение {variable}.')
        candidates = [(overrides or {}).get(client), state.get('executables', {}).get(client),
                      detected.get(client)]
        executable = next((path for path in candidates if path and Path(path).is_file()
                           and os.access(path, os.X_OK)), None)
        if not executable and client != 'opencode':
            raise ValueError(f'Исполняемый файл ранее подключённого {client} недоступен. Восстановите его или укажите '
                             f'--client-path {client}=/абсолютный/путь перед обновлением MLSpace.')
        if client != 'opencode' and not inspect_native(client, executable or CLIENTS[client]).installed:
            continue
        result[client] = executable or CLIENTS[client]  # OpenCode config is written directly.
    return result


def prepare_clients(clients: dict[str, str], env_file: Path, command: list[str], *,
                    collect_conflicts: bool = False, resolutions: Resolutions | None = None) -> dict[str, Any]:
    """Check client capabilities and conflicts before setup saves credentials."""
    state_path = install_state_file()
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    clients = managed_clients(clients) | clients
    sources = _skill_sources()
    writes: dict[Path, str] = {}
    conflicts: list[Conflict] = []
    replacements: list[Conflict] = []
    overrides = set(state.get('skill_overrides', []))
    decisions = resolutions or {}
    examined: set[str] = set()

    def resolve(path: Path, kind: str) -> str:
        item = Conflict.inspect(path, kind)
        decision = decisions.get(str(path))
        if decision is None:
            conflicts.append(item)
            return ''
        action, approved = decision
        examined.add(str(path))
        if item.fingerprint != approved:
            raise ValueError(f'Файл изменился после вашего выбора; запустите setup снова: {path}')
        if action == 'replace':
            replacements.append(item)
        elif action != 'keep' or kind != 'skill':
            raise ValueError(f'Недопустимый выбор для конфликтующего файла {path}. Повторите setup и выберите один из предложенных вариантов.')
        return action

    registrations: list[tuple[str, str, Path, dict[str, Any] | None, Any]] = []
    for client, executable in clients.items():
        if client not in CLIENTS:
            raise ValueError('Неизвестный клиент: ' + client)
        config = _config(client)
        if config.is_symlink():
            raise ValueError(f'Конфигурация клиента является символической ссылкой; настройте её вручную: {config}')
        data = _read_config(config)
        key = {'claude-code': 'mcpServers', 'codex': 'mcp_servers', 'opencode': 'mcp'}[client]
        entries = data.get(key, {})
        if not isinstance(entries, dict):
            raise ValueError(f'Неверный формат конфигурации MCP: {config}. Проверьте файл и повторите setup.')
        for name, entry in entries.items():
            if name != 'mlspace' and ('mlspace' in name.lower() or 'mlspace-plugin' in json.dumps(entry)):
                raise ValueError(f'В {config} уже есть подключение MLSpace {name}. Сначала выполните миграцию этого подключения.')
        if client != 'opencode':
            if 'mlspace' in entries:
                raise ValueError(f'В {config} уже есть прямое подключение MLSpace; '
                                 'перед установкой нативного плагина удалите это подключение.')
            inspect_native(client, executable)
            registrations.append((client, executable, config, None, None))
            continue
        entry = {'type': 'local', 'command': command,
                 'environment': {'MLSPACE_ENV_FILE': str(env_file.absolute())}, 'enabled': True}
        current = entries.get('mlspace')
        previous = state.get('clients', {}).get(client, {}).get('entry_hash')
        if current is not None:
            comparable = dict(current)
            comparable.pop('type', None) if client != 'opencode' else None
            if comparable != entry and _hash(json.dumps(current, sort_keys=True)) != previous:
                resolve(config, 'connection')
        registrations.append((client, executable, config, entry, current))
        skills_root = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'opencode/skills'
        for name, content in sources.items():
            target = skills_root / name / 'SKILL.md'
            # A kept override belongs to the user, including their subsequent edits.
            if str(target) in overrides and target.is_file():
                state.get('skills', {}).pop(str(target), None)
                continue
            overrides.discard(str(target))
            if target.is_symlink() or target.parent.is_symlink():
                if target.is_file() and target.read_text() == content:
                    continue
                raise ValueError(f'Существующий skill по символической ссылке отличается от версии плагина: {target}. Разрешите конфликт вручную и повторите setup.')
            if target.exists() and target.read_text() != content:
                if state.get('skills', {}).get(str(target)) != _hash(target.read_text()):
                    if resolve(target, 'skill') == 'keep':
                        overrides.add(str(target))
                        state.get('skills', {}).pop(str(target), None)
                        continue
            writes[target] = content
    if set(decisions) != examined:
        raise ValueError('Конфликтующие файлы изменились после вашего выбора; запустите setup снова.')
    if conflicts and not collect_conflicts:
        raise ValueError(conflict_message(conflicts))
    state['skill_overrides'] = sorted(overrides)
    return {'state_path': state_path, 'state': state, 'writes': writes,
            'registrations': registrations, 'conflicts': conflicts, 'replacements': replacements}


def install_clients(clients: dict[str, str], env_file: Path, command: list[str], *,
                    resolutions: Resolutions | None = None) -> list[str]:
    # Revalidate after human input: files may have changed since the preflight.
    plan = prepare_clients(clients, env_file, command, resolutions=resolutions)
    state, state_path = plan['state'], plan['state_path']
    try:
        backup_replacements(plan['replacements'], state_path.parent / 'backups')
    except OSError as exc:
        raise ValueError(f'Не удалось создать резервную копию конфликтующих файлов в {state_path.parent / "backups"}. '
                         'Оригиналы не заменены. Проверьте права на запись и свободное место, затем повторите setup.') from exc
    replacements = {item.path: item for item in plan['replacements']}
    retry = [*runtime_command(), 'setup', '--force']
    for client, executable, *_ in plan['registrations']:
        retry.extend(['--client-path', f'{client}={executable}'])
    retry_hint = ('Сначала устраните указанную причину; повтор без изменений может дать ту же ошибку.\n'
                  'Повторите команду: ' + shlex.join(retry))
    installed = []
    for client, executable, config, entry, _current in plan['registrations']:
        try:
            if client == 'opencode':
                text = config.read_text() if config.exists() else '{}\n'
                updated = set_jsonc_member(text, ['mcp', 'mlspace'], entry)
                if updated != text:
                    if config in replacements:
                        replacements[config].verify_unchanged()
                    atomic_write(config, updated)
                saved = _read_config(config).get('mcp', {}).get('mlspace')
                if saved != entry:
                    raise ValueError(f'Не удалось подтвердить подключение {client}; повторите setup --force.')
                record: dict[str, Any] = {'mode': 'direct', 'version': __version__,
                          'entry_hash': _hash(json.dumps(saved, sort_keys=True))}
            else:
                status = install_native(client, executable, __version__)
                record = {'mode': 'native', 'version': status.version,
                          'marketplace_source': status.marketplace_source}
            state.setdefault('clients', {})[client] = record
            state.setdefault('configs', {})[client] = str(config.absolute())
            state.setdefault('executables', {})[client] = executable
            atomic_write(state_path, json.dumps(state, indent=2))
            installed.append(client)
        except (ValueError, OSError) as exc:
            raise ValueError(f'Не удалось подключить {client}. Уже подключены: '
                             f'{", ".join(installed) or "нет"}. {exc}\n{retry_hint}') from exc
        except KeyboardInterrupt:
            print(f'Настройка клиентов прервана. Уже подключены: {", ".join(installed) or "нет"}.\n'
                  + retry_hint, file=sys.stderr)
            raise
    try:
        for target, content in plan['writes'].items():
            if not target.exists() or target.read_text() != content:
                if target in replacements:
                    replacements[target].verify_unchanged()
                atomic_write(target, content, 0o644)
            state.setdefault('skills', {})[str(target)] = _hash(content)
            atomic_write(state_path, json.dumps(state, indent=2))
    except (OSError, ValueError) as exc:
        reason = str(exc) if type(exc) is ValueError else 'Проверьте права на запись в каталоге skills.'
        raise ValueError(f'MCP подключён: {", ".join(installed)}. Установка skills не завершена; '
                         f'{reason} Повторите setup --force. '
                         'Проверенные ключи остаются сохранёнными.') from exc
    return installed
