"""Terminal onboarding shared by humans and explicitly configured automation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import USER_AGENT, diagnostics
from .client_setup import (
    CLIENTS,
    atomic_write,
    detect_clients,
    install_clients,
    install_state_file,
    managed_clients,
    prepare_clients,
    server_command,
)
from .config import (
    DEFAULT_BASE_URL,
    LITERAL_DOTENV_MARKER,
    credential_files,
    credential_pointer_file,
    read_dotenv_file,
    user_config_file,
)
from .setup_conflicts import Resolutions, choose_resolutions
from .setup_ui import choose_workspaces, display, hidden_input, masked_prompt
from .setup_verification import verify_client
from .tls import safe_error as _safe_error
from .tls import tls_context

HEADER = f'# MLSpace credentials, written by mlspace-plugin setup.\n{LITERAL_DOTENV_MARKER}\n'


def _ask(label: str, secret: bool = True, hidden: bool = True) -> str:
    with hidden_input() if hidden else nullcontext():
        while True:
            value = masked_prompt(f'  {label}: ') if hidden else input(f'  {label}: ')
            if re.match(r'^\s*(?:export\s+)?MLSPACE_\w+\s*=', value):
                print('Вставьте только значение ключа: без MLSPACE_...=, export и кавычек вокруг значения.', file=sys.stderr)
            elif value.strip():
                return value if secret else value.strip()
            else:
                print('Поле обязательно. Вставьте значение ключа; звёздочки подтверждают ввод и скрывают его содержимое.', file=sys.stderr)


def _resolve_base_url(target: Path, override: str | None) -> str:
    saved = read_dotenv_file(target) if target.is_file() else {}
    return ((override or '').strip() or os.environ.get('MLSPACE_BASE_URL', '').strip()
            or str(saved.get('MLSPACE_BASE_URL') or DEFAULT_BASE_URL)).rstrip('/')


def _fetch_workspaces(key_id: str, key_secret: str, *, base_url: str,
                      ca_file: str = '') -> list[dict[str, str]]:
    from .auth import TokenManager
    from .client import MLSpaceClient

    async def fetch() -> Any:
        async with httpx.AsyncClient(
            timeout=30, verify=tls_context(ca_file), headers={"User-Agent": USER_AGENT},
        ) as http:
            tokens = TokenManager(http, base_url=base_url, client_id=key_id, client_secret=key_secret)
            client = MLSpaceClient(http, tokens, base_url=base_url, api_key='', workspace_id='')
            return await client.request('GET', '/public/v2/workspaces/v3/')

    response = asyncio.run(fetch())
    spaces = response.get('workspaces') if isinstance(response, dict) else None
    if not isinstance(spaces, list) or not spaces:
        raise ValueError('Для этих ключей не найдено доступных воркспейсов. Проверьте права ключей и наличие воркспейсов в MLSpace.')
    catalogue = []
    seen = set()
    for row in spaces:
        if not isinstance(row, dict) or not (key := row.get('id') or row.get('workspace_id')):
            raise ValueError('API MLSpace вернул воркспейс без ID. Повторите setup; если ошибка повторится, обратитесь в поддержку.')
        if str(key) in seen:
            raise ValueError('API MLSpace вернул повторяющиеся ID воркспейсов. Повторите setup; если ошибка повторится, обратитесь в поддержку.')
        seen.add(str(key))
        catalogue.append({'id': str(key), 'name': str(row.get('name') or key),
                          'project_name': str(row.get('project_name') or '')})
    return sorted(catalogue, key=lambda r: (r['name'].casefold(), r['project_name'], r['id']))
def _write(path: Path, values: dict[str, str]) -> None:
    """Atomically replace ``path`` with a 0600 dotenv file."""
    if path.is_symlink():
        raise ValueError("Файл ключей не может быть символической ссылкой. Укажите обычный файл через --path.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent == user_config_file().parent:
        path.parent.chmod(0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            body = "".join(
                f"MLSPACE_{name}={json.dumps(value, ensure_ascii=False)}\n"
                for name, value in values.items()
            )
            fh.write(HEADER + body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def save_credential_pointer(env_file: Path) -> None:
    pointer = credential_pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pointer.parent.chmod(0o700)
    atomic_write(pointer, json.dumps({'env_file': str(env_file.absolute())}) + '\n')


@dataclass
class WorkspaceVerification:
    verified_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _verify(values: dict[str, str], *, base_url: str) -> WorkspaceVerification:
    """Check workspace details and IDs, retaining successful selections for retry.

    The caller preserves the previous file if any selected workspace fails.
    """
    import asyncio

    import httpx
    from pydantic import SecretStr

    from .auth import TokenManager
    from .config import Settings
    from .workspace_context import WorkspaceClients

    async def run() -> WorkspaceVerification:
        result = WorkspaceVerification()
        try:
            # Explicit values keep old environment selectors out of this check.
            settings = Settings(  # type: ignore[call-arg]
                _env_file=None,
                base_url=base_url,
                client_id=values["CLIENT_ID"],
                client_secret=SecretStr(values["CLIENT_SECRET"]),
                api_key=SecretStr(values.get("API_KEY", "")),
                workspace_id=values.get("WORKSPACE_ID", ""),
                workspaces=json.loads(values.get("WORKSPACES", "[]")),
                namespace="",
            )
            async with httpx.AsyncClient(
                timeout=30, verify=tls_context(values.get("CA_FILE", "")),
                headers={"User-Agent": USER_AGENT},
            ) as http:
                tokens = TokenManager(
                    http, base_url=base_url, client_id=values["CLIENT_ID"],
                    client_secret=values["CLIENT_SECRET"],
                )
                clients = WorkspaceClients(settings, http, tokens)
                for selection in clients.selected:
                    try:
                        client = await clients.resolve(selection.id)
                        data = await client.request(
                            "GET", "/public/v2/workspaces/v3/{workspace_id}",
                            path_params={"workspace_id": selection.id},
                        )
                        if not isinstance(data, dict) or data.get("id") != selection.id:
                            result.errors.append(f"{selection.id}: API не подтвердил ID воркспейса; проверьте доступ и повторите выбор")
                            continue
                        result.verified_ids.append(selection.id)
                    except Exception as exc:  # noqa: BLE001 — verify every selected workspace
                        result.errors.append(f"{selection.id}: {_safe_error(exc)}")
                print(
                    f"  Проверено выбранных воркспейсов: {len(result.verified_ids)}/{len(clients.selected)} "
                    "(доступ к сведениям и совпадение ID)",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001 — report failures without exposing transport details
            result.errors.append(_safe_error(exc))
        return result

    return asyncio.run(run())



def _clients(requested: list[str], paths: list[str], non_interactive: bool) -> dict[str, str]:
    detected = detect_clients()
    explicit = {}
    for item in paths:
        name, separator, path = item.partition('=')
        executable = Path(path).expanduser().absolute()
        if not separator or name not in CLIENTS or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError('Укажите исполняемый файл: --client-path CLIENT=/абсолютный/путь. CLIENT: claude-code, codex или opencode; файл должен иметь право запуска.')
        detected[name] = str(executable)
        explicit[name] = str(executable)
    managed = managed_clients(explicit)
    detected = managed | detected
    if requested:
        if any(name not in detected for name in requested):
            raise ValueError('Указанный клиент не найден. Установите его или укажите --client-path CLIENT=/абсолютный/путь.')
        return {name: detected[name] for name in requested}
    if explicit:
        return managed | explicit
    if managed:
        print('Будут обновлены все ранее подключённые к MLSpace клиенты: ' + ', '.join(managed), file=sys.stderr)
        return managed | explicit
    if not explicit and install_state_file().exists():
        raise ValueError('Ранее подключённые клиенты MLSpace больше не найдены. Явно выберите новый клиент '
                         'через --client или --client-path.')
    if len(detected) == 1:
        print('Найден клиент: ' + next(iter(detected)), file=sys.stderr)
        return detected
    if not detected:
        if non_interactive:
            raise ValueError('Клиент не найден. Укажите --client-path CLIENT=/абсолютный/путь или сохраните только настройки через --config-only.')
        print('Не найдены Claude Code, Codex и OpenCode. Введите CLIENT=/абсолютный/путь, '
              'например codex=/usr/local/bin/codex. Enter отменяет настройку; после установки клиента запустите setup снова.', file=sys.stderr)
        custom = input('Клиент и исполняемый файл (CLIENT=/путь): ').strip()
        if custom:
            return _clients([], [custom], True)
        raise ValueError('Установите Claude Code, Codex или OpenCode, затем запустите setup снова. '
                         'Для других MCP-клиентов используйте --config-only и подключите MCP вручную.')
    if non_interactive:
        raise ValueError('Найдено несколько клиентов. Выберите через --client; повторите флаг для нескольких клиентов.')
    print('Выберите клиенты для подключения MCP и skills. Печатайте для поиска, ↑/↓ перемещают курсор, Space отмечает клиент, Enter подтверждает выбор (нужен хотя бы один), Ctrl-C отменяет настройку.', file=sys.stderr)
    selected = choose_workspaces([{'id': name, 'name': name, 'project_name': path}
                                  for name, path in detected.items()])
    return {row['id']: detected[row['id']] for row in selected}


def run_init(path: Path | None = None, *, force: bool = False, verify: bool = True,
             base_url: str | None = None, config_only: bool = False,
             non_interactive: bool = False, from_env: bool = False,
             credentials_file: Path | None = None, workspace_ids: list[str] | None = None,
             clients: list[str] | None = None, client_paths: list[str] | None = None,
             ca_file: Path | None = None, replace_credentials: bool = False,
             list_workspaces: bool = False, search: str = '', offset: int = 0, limit: int = 20) -> int:
    try:
        return _run_init(path, force=force, verify=verify, base_url=base_url,
                         config_only=config_only, non_interactive=non_interactive,
                         from_env=from_env, credentials_file=credentials_file,
                         workspace_ids=workspace_ids or [], clients=clients or [],
                         client_paths=client_paths or [], ca_file=ca_file,
                         replace_credentials=replace_credentials, list_workspaces=list_workspaces,
                         search=search, offset=offset, limit=limit)
    except KeyboardInterrupt:
        diagnostics.event('setup.cancelled')
        print('\nНастройка отменена. Если сохранение ещё не завершилось, прежний файл ключей сохранён; после завершённого сохранения новые настройки остаются на диске.', file=sys.stderr)
        return 130
    except EOFError as exc:
        diagnostics.failure('setup.input', exc)
        print('\nДля ввода ключей нужен интерактивный терминал. Запустите setup из вашей поставки MLSpace самостоятельно в терминале (команда в INSTALL.md; SSH тоже подходит). '
              'Для настройки агентом следуйте INSTALL_AGENT.md; ключи вводите только в терминале, не в чате.', file=sys.stderr)
        return 1
    except (ValueError, OSError, httpx.HTTPError) as exc:
        diagnostics.failure('setup', exc)
        # Only our own ValueErrors carry displayable text. Third-party errors may echo keys.
        message = str(exc) if type(exc) is ValueError else _safe_error(exc)
        print('Ошибка настройки: ' + '\n'.join(display(line) for line in message.splitlines()), file=sys.stderr)
        return 1


def _run_init(path: Path | None, **options: Any) -> int:
    target = (path or credential_files()[0]).expanduser().absolute()
    listing = options['list_workspaces']
    non_interactive = options['non_interactive']
    if not non_interactive and not sys.stdin.isatty():
        raise EOFError
    if options['from_env'] and options['credentials_file']:
        raise ValueError('Выберите один источник ключей: --from-env или --credentials-file.')
    if target.is_symlink():
        raise ValueError('Файл ключей не может быть символической ссылкой. Укажите обычный файл через --path.')
    if target.exists() and not target.is_file():
        raise ValueError('Путь для ключей должен указывать на обычный файл. Выберите другой путь через --path.')
    if target.exists() and not options['force'] and not listing:
        raise ValueError('Настройки уже существуют. Добавьте --force для повторной настройки с сохранёнными ключами. Для ввода новых ключей добавьте --replace-credentials.')
    if not options['verify'] and (not options['config_only'] or not options['workspace_ids']):
        raise ValueError('Для --no-verify нужны --config-only и явные ID через --workspace. Настройки будут сохранены без проверки API и подключения клиентов.')
    if options['offset'] < 0 or not 1 <= options['limit'] <= 100:
        raise ValueError('Укажите --offset не меньше 0 и --limit от 1 до 100.')
    saved = read_dotenv_file(target) if target.is_file() else {}
    endpoint = _resolve_base_url(target, options['base_url'])
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Адрес API (--base-url) должен быть HTTPS URL без логина, пароля, параметров запроса и фрагмента.')
    if os.environ.get('SSL_CERT_DIR'):
        raise ValueError('SSL_CERT_DIR не переносится в запуск MCP из клиента. Уберите эту переменную перед setup и укажите '
                         '--ca-file /путь/к/доверенному-ca.pem либо установите CA в системное хранилище доверия.')
    selected_clients = {} if options['config_only'] or listing else _clients(
        options['clients'], options['client_paths'], non_interactive)
    diagnostics.event('setup.step', phase='clients', count=len(selected_clients))
    command = server_command() if selected_clients and 'opencode' in selected_clients | managed_clients(selected_clients) else []
    resolutions: Resolutions = {}
    if selected_clients:
        plan = prepare_clients(selected_clients, target, command, collect_conflicts=True)
        resolutions = choose_resolutions(plan.get('conflicts', []), non_interactive=non_interactive)
        print('MCP и skills будут установлены вместе для клиентов: ' +
              ', '.join(row[0] for row in plan['registrations']), file=sys.stderr)
    if not listing:
        print('[1/4] Ключи доступа Cloud.ru. Подготовьте Cloud.ru Key ID и Cloud.ru Key Secret; вставляйте только значения. Оба поля скрыты звёздочками. '
              'Ключи будут сохранены в локальном файле настроек с доступом только для вашего пользователя. Не отправляйте их в чат.', file=sys.stderr)
    if options['credentials_file']:
        source = read_dotenv_file(options['credentials_file'].expanduser())
        if not listing:
            print('Ключи читаются из указанного --credentials-file; их значения не выводятся.', file=sys.stderr)
    elif options['from_env']:
        source = dict(os.environ)
        if not listing:
            print('Ключи читаются из MLSPACE_CLIENT_ID и MLSPACE_CLIENT_SECRET; их значения не выводятся.', file=sys.stderr)
    elif not options['replace_credentials'] and str(saved.get('MLSPACE_BASE_URL') or DEFAULT_BASE_URL).rstrip('/') == endpoint:
        source = saved
        if source and not listing:
            print('Используем ранее сохранённые ключи. Чтобы заменить их, повторите setup --force --replace-credentials.', file=sys.stderr)
    else:
        source = {}
    keys = {}
    with hidden_input():
        for name, label in [('CLIENT_ID', 'Cloud.ru Key ID'), ('CLIENT_SECRET', 'Cloud.ru Key Secret')]:
            value = source.get('MLSPACE_' + name)
            if not value or not value.strip():
                if non_interactive or options['credentials_file'] or options['from_env']:
                    raise ValueError('Для этого адреса API отсутствуют ключи. Введите их через setup в терминале '
                                     'либо явно укажите --from-env или --credentials-file.')
                value = _ask(label, secret=name == 'CLIENT_SECRET')
            keys[name] = value
    ca = str(options['ca_file'] or saved.get('MLSPACE_CA_FILE') or os.environ.get('SSL_CERT_FILE') or '')
    ca = str(Path(ca).expanduser().absolute()) if ca else ''
    if not listing:
        print('[2/4] Проверяем доверие TLS-сертификату и загружаем доступные воркспейсы из API MLSpace…', file=sys.stderr)
    if options['verify'] or listing:
        while True:
            try:
                rows = _fetch_workspaces(keys['CLIENT_ID'], keys['CLIENT_SECRET'], base_url=endpoint, ca_file=ca)
                break
            except ValueError:
                raise
            except Exception as exc:
                error = _safe_error(exc)
                if not non_interactive and 'Не удалось проверить TLS-сертификат' in error:
                    print(error, file=sys.stderr)
                    ca = input('Получите у администратора доверенный CA-сертификат в формате PEM. Введите путь к этому файлу (Enter отменяет настройку): ').strip()
                    if not ca:
                        raise ValueError('Настройка TLS отменена; новые ключи не сохранены. Получите доверенный CA у администратора и повторите setup --ca-file /путь/к/ca.pem.') from None
                    ca = str(Path(ca).expanduser().absolute())
                    continue
                raise ValueError(error) from None
    else:
        rows = [{'id': key, 'name': key, 'project_name': ''} for key in dict.fromkeys(options['workspace_ids'])]
    if listing:
        terms = options['search'].casefold().split()
        matches = [row for row in rows if all(term in ' '.join(row.values()).casefold() for term in terms)]
        offset, limit = options['offset'], options['limit']
        print(json.dumps({'total': len(matches), 'offset': offset, 'limit': limit,
                          'has_more': offset + limit < len(matches), 'workspaces': matches[offset:offset+limit]}, ensure_ascii=False))
        return 0
    print(f'Доступных воркспейсов: {len(rows)}.', file=sys.stderr)
    diagnostics.event('setup.step', phase='workspaces.loaded', count=len(rows))
    requested = options['workspace_ids']
    if requested:
        if set(requested) - {row['id'] for row in rows}:
            raise ValueError('Среди указанных --workspace есть недоступные ID. Найдите доступные через --list-workspaces --search и повторите выбор.')
        selected = [row for row in rows if row['id'] in requested]
    elif non_interactive:
        raise ValueError('Выберите ID через --workspace (повторите флаг для нескольких). Для поиска используйте --list-workspaces --search.')
    elif len(rows) == 1:
        selected = rows
        print('Единственный доступный воркспейс выбран автоматически: ' +
              display(f"{rows[0]['name']} | {rows[0]['project_name']} | {rows[0]['id']}"), file=sys.stderr)
    else:
        print('Выберите воркспейсы для MCP. Печатайте название, проект или ID для поиска; ↑/↓ перемещают курсор, Space отмечает строку. Можно выбрать несколько: отметки сохраняются при смене поиска. Ctrl-U очищает поиск, Enter подтверждает выбор (нужен хотя бы один), Ctrl-C отменяет настройку.', file=sys.stderr)
        old = json.loads(str(saved.get('MLSPACE_WORKSPACES') or '[]'))
        selected = choose_workspaces(rows, [row['id'] for row in old if isinstance(row, dict) and 'id' in row])
    values = {key.removeprefix('MLSPACE_'): value for key, value in saved.items()
              if key.startswith('MLSPACE_') and value is not None
              and key not in {'MLSPACE_API_KEY', 'MLSPACE_WORKSPACE_ID', 'MLSPACE_WORKSPACES', 'MLSPACE_NAMESPACE'}}
    values.update(keys)
    values.update(BASE_URL=endpoint, CA_FILE=ca)
    while True:
        values['WORKSPACES'] = json.dumps(selected, ensure_ascii=False, separators=(',', ':'))
        values.pop('WORKSPACE_ID', None)
        if len(selected) == 1:
            values['WORKSPACE_ID'] = selected[0]['id']
        print(f'[3/4] Проверяем доступ и ID выбранных воркспейсов: {len(selected)}…', file=sys.stderr)
        diagnostics.event('setup.step', phase='workspaces.verify', count=len(selected))
        if not options['verify']:
            break
        result = _verify(values, base_url=endpoint)
        if not result.errors:
            break
        if non_interactive:
            raise ValueError('; '.join(result.errors))
        print('Не удалось проверить часть выбранных воркспейсов:', file=sys.stderr)
        for error in result.errors:
            print('  ' + display(error), file=sys.stderr)
        print('Выберите воркспейсы повторно. Проверенные остались отмеченными; с непроверенных '
              'отметки сняты. Можно выбрать другие или снова отметить нужные для повторной проверки. '
              'Ключи повторно вводить не нужно. Изменения ещё не сохранены.\n'
              'Печатайте название, проект или ID для поиска; Space отмечает строку, Enter подтверждает '
              'выбор (нужен хотя бы один), Ctrl-C отменяет настройку.', file=sys.stderr)
        selected = choose_workspaces(rows, result.verified_ids)
    if ca:
        # Persist the validated public CA independently of /tmp and shell environment.
        content = Path(ca).read_text(encoding='ascii')
        destination = target.parent / ('ca-' + hashlib.sha256(content.encode()).hexdigest()[:16] + '.pem')
        tls_context(ca)
        atomic_write(destination, content)
        values['CA_FILE'] = str(destination)
    try:
        _write(target, values)
    except (OSError, ValueError) as exc:
        raise ValueError(f'Не удалось сохранить ключи в {target}. Клиенты ещё не подключены. '
                         'Проверьте путь и права на запись, затем повторите setup.') from exc
    print(f'Ключи сохранены: {target}. Права 0600: читать и менять файл может только ваш пользователь.', file=sys.stderr)
    try:
        save_credential_pointer(target)
    except (OSError, ValueError) as exc:
        raise ValueError(f'Ключи сохранены, но не удалось записать указатель на их файл в {credential_pointer_file()}. '
                         'Клиенты ещё не подключены. Проверьте права на запись и повторите setup --force с тем же --path.') from exc
    if selected_clients:
        print('[4/4] Подключаем MCP, устанавливаем skills и проверяем запуск сервера для каждого клиента…', file=sys.stderr)
        diagnostics.event('setup.step', phase='clients.install')
        try:
            install_clients(selected_clients, target, command, resolutions=resolutions)
            for client, executable, *_ in plan['registrations']:
                print(f'  {client}: установлен; проверяем содержимое skills и запуск процесса MCP…', file=sys.stderr)
                verify_client(client, executable, target, command)
                print(f'  {client}: skills из релиза проверены (сохранённые пользовательские версии исключены); запуск процесса MCP проверен.', file=sys.stderr)
        except (OSError, ValueError) as exc:
            reason = str(exc) if type(exc) is ValueError else _safe_error(exc)
            raise ValueError('Ключи сохранены; настройка клиентов не завершена. ' + reason) from exc
    if not options['verify']:
        print('По явному запросу сохранены НЕПРОВЕРЕННЫЕ настройки. Клиенты не подключены. Для проверки и подключения клиентов повторите setup --force без --no-verify.', file=sys.stderr)
        return 0
    if selected_clients:
        print('Готово. Откройте новую сессию выбранного клиента; если MCP не появился, перезапустите клиент. '
              'Клиент запускает MCP-сервер автоматически.\n'
              'Появление MCP и skills в новой сессии нужно проверить в самом клиенте.\n'
              'Отправьте клиенту: «Покажи выбранные воркспейсы и запущенные задачи, ничего не меняй».', file=sys.stderr)
    else:
        print('API и TLS проверены, ключи и выбранные воркспейсы сохранены. Выполнена настройка --config-only. Для подключения клиента запустите setup --force --client CLIENT (claude-code, codex или opencode).', file=sys.stderr)
    return 0
