"""CLI entry point for mlspace-plugin."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__, diagnostics
from .config import load_settings, user_config_file
from .server import build_server


def _configure_logging() -> None:
    """Basic stderr logging so the server's logger.warning/exception emit in prod.
    Level via MLSPACE_LOG_LEVEL (default INFO). stderr keeps stdout clean for stdio MCP.
    """
    level = os.environ.get("MLSPACE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        result = _main(argv)
        diagnostics.event('session.end', returncode=result)
        if result and (path := diagnostics.current_path()):
            print(f'Для диагностики пришлите файл: {path}', file=sys.stderr)
        return result
    except BaseException as exc:
        diagnostics.failure('entrypoint', exc)
        raise
    finally:
        diagnostics.close()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mlspace-plugin",
        description="MCP-сервер для публичного API v2 Cloud.ru MLSpace.",
    )
    parser.add_argument("--version", action="version", version=f"mlspace-plugin {__version__}")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio"],
        help="Использовать этот транспорт вместо MLSPACE_TRANSPORT.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Показать доступные инструменты и завершить работу (ключи не нужны).",
    )
    # Optional subcommand: with none given the CLI serves, as it always has.
    sub = parser.add_subparsers(dest="command")
    setup = sub.add_parser(
        "setup",
        help="Настроить ключи доступа, воркспейсы, MCP-клиенты и skills.",
        description=(
            "Введите Cloud.ru Key ID и Cloud.ru Key Secret, выберите доступные воркспейсы MLSpace. Настройки сохраняются в "
            f"{user_config_file()} с правами 0600 (доступ только вашему пользователю). Ввод выполняется в вашем терминале: ключи "
            "не передаются модели. Оба поля скрыты звёздочками. Затем подключаются MCP и skills выбранных клиентов."
        ),
    )
    setup.add_argument("--path", type=Path, help="Сохранить ключи и воркспейсы в указанный файл вместо стандартного файла настроек.")
    setup.add_argument("--force", action="store_true", help="Повторить настройку существующего файла с сохранёнными ключами. Конфликты пользовательских skills требуют отдельного выбора.")
    setup.add_argument(
        "--base-url",
        help="Сохранить этот HTTPS-адрес API MLSpace; по умолчанию используется адрес из файла или production.",
    )
    setup.add_argument(
        "--no-verify", action="store_true", help="Сохранить настройки без проверки API; нужны --config-only и --workspace. Клиенты не подключаются."
    )
    setup.add_argument("--client", dest="clients", action="append", choices=["claude-code", "codex", "opencode"], help="Подключить этот клиент; повторите флаг для нескольких. По умолчанию клиенты определяются автоматически.")
    setup.add_argument("--client-path", dest="client_paths", action="append", metavar="CLIENT=PATH", help="Путь к исполняемому файлу клиента, если он отсутствует в PATH: например codex=/usr/local/bin/codex.")
    setup.add_argument("--config-only", action="store_true", help="Сохранить только ключи и воркспейсы. Для подключения клиентов позже повторите setup --force --client CLIENT.")
    setup.add_argument("--non-interactive", action="store_true", help="Без вопросов в терминале: укажите ID через --workspace и источник ключей либо используйте сохранённые ключи.")
    setup.add_argument("--from-env", action="store_true", help="Прочитать ключи из переменных MLSPACE_CLIENT_ID / MLSPACE_CLIENT_SECRET.")
    setup.add_argument("--credentials-file", type=Path, help="Прочитать ключи из существующего dotenv-файла без вывода их значений.")
    setup.add_argument("--replace-credentials", action="store_true", help="Запросить новые ключи вместо сохранённых; для существующего файла также нужен --force.")
    setup.add_argument("--workspace", dest="workspace_ids", action="append", help="Выбрать воркспейс по точному ID; повторите флаг для нескольких.")
    setup.add_argument("--ca-file", type=Path, help="Файл доверенного CA в формате PEM от администратора. Копия сохраняется для следующих запусков MCP; проверка TLS включена.")
    setup.add_argument("--list-workspaces", action="store_true", help="Вывести страницу доступных воркспейсов в JSON без сохранения и установки.")
    setup.add_argument("--search", default="", help="Фильтровать JSON воркспейсов по названию, проекту или ID (с --list-workspaces).")
    setup.add_argument("--offset", type=int, default=0, help="Пропустить это число результатов JSON; не меньше 0, по умолчанию 0.")
    setup.add_argument("--limit", type=int, default=20, help="Число воркспейсов на странице JSON: 1–100, по умолчанию 20.")
    args = parser.parse_args(argv)
    if not args.list_tools:
        diagnostics.start('setup' if args.command == 'setup' else 'server')

    if args.command == "setup":
        from .init_cli import run_init

        return run_init(
            args.path,
            force=args.force,
            verify=not args.no_verify,
            base_url=args.base_url,
            config_only=args.config_only, non_interactive=args.non_interactive,
            from_env=args.from_env, credentials_file=args.credentials_file,
            workspace_ids=args.workspace_ids, clients=args.clients, client_paths=args.client_paths,
            ca_file=args.ca_file, replace_credentials=args.replace_credentials,
            list_workspaces=args.list_workspaces, search=args.search, offset=args.offset, limit=args.limit,
        )

    _configure_logging()
    settings = load_settings()
    if args.transport:
        settings.transport = args.transport

    if args.list_tools:
        mcp = build_server(settings)
        tools = mcp._tool_manager.list_tools()
        mode = "read-only" if settings.readonly else "read-write"
        print(f"mlspace-plugin {__version__} — {len(tools)} инструментов ({mode}):")
        for tool in sorted(tools, key=lambda t: t.name):
            print(f"  {tool.name}")
        return 0

    settings.validate_bind()
    settings.require_credentials()
    mcp = build_server(settings)

    if settings.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        print(
            f"mlspace-plugin: сервер streamable-HTTP запущен на "
            f"http://{settings.host}:{settings.port}/mcp",
            file=sys.stderr,
        )
        mcp.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
