"""Live read-only terminal demo through a separate MCP stdio process.

Run from the checkout with .venv/bin/python scripts/demo_multi_workspace.py.
--direct bypasses inherited proxies for this demo process only. Existing credential
files are read but never rewritten; the demo catalogue travels in the child's env.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mlspace_mcp.auth import TokenManager
from mlspace_mcp.client import MLSpaceClient
from mlspace_mcp.config import Settings, credential_files, load_settings

ROOT = Path(__file__).resolve().parents[1]


def fingerprint() -> dict[str, str | None]:
    return {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file()
            else None for p in credential_files()}


async def discover(settings: Settings) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=10) as http:
        tokens = TokenManager(http, settings.base_url, settings.client_id,
                              settings.client_secret.get_secret_value())
        client = MLSpaceClient(http, tokens, base_url=settings.base_url,
                               api_key="", workspace_id="")
        data = await client.request("GET", "/public/v2/workspaces/v3/")
    return sorted([
        {"id": row["id"], "name": row.get("name") or row["id"],
         "project_name": row.get("project_name") or ""}
        for row in data["workspaces"]
    ], key=lambda row: (row["project_name"], row["name"], row["id"]))


@asynccontextmanager
async def connect(settings: Settings, catalogue: list[dict[str, str]], quiet):
    env = {**os.environ, "MLSPACE_CLIENT_ID": settings.client_id,
           "MLSPACE_CLIENT_SECRET": settings.client_secret.get_secret_value(),
           "MLSPACE_BASE_URL": settings.base_url,
           "MLSPACE_WORKSPACES": json.dumps(catalogue),
           "MLSPACE_API_KEY": "", "MLSPACE_WORKSPACE_ID": "", "MLSPACE_NAMESPACE": "",
           "MLSPACE_READONLY": "true", "MLSPACE_DRY_RUN": "false",
           "MLSPACE_LOG_LEVEL": "CRITICAL", "MLSPACE_TIMEOUT": "10",
           "MLSPACE_ENABLED_DOMAINS": "workspaces,jobs,notebooks,resources"}
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "mlspace_mcp", "--transport", "stdio"], cwd=str(ROOT), env=env)
    async with stdio_client(params, errlog=quiet) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            yield session


async def call(session: ClientSession, tool: str, args: dict[str, Any] | None = None):
    result = await session.call_tool(tool, args or {})
    if result.isError:
        raise RuntimeError(f"MCP call failed: {tool}")
    return json.loads(result.content[0].text)


async def demo(emit, quiet) -> None:
    settings = load_settings()
    settings.require_credentials()
    before = fingerprint()
    emit("MLSpace: несколько равноправных воркспейсов")
    emit("Настоящий API + отдельный MCP-процесс через stdio; только чтение.")
    emit("Это демонстрация инструментов, без LLM-интерпретации разговора.")
    try:
        catalogue = await discover(settings)
        if len(catalogue) < 2:
            raise RuntimeError("Multi-workspace demo needs at least two visible workspaces")
        selected = catalogue[:10]
        emit(f"\n1. Доступно: {len(catalogue)}. Временно выбрано для демо: {len(selected)}.")
        for row in selected:
            emit(f"   {row['name']}  ({row['project_name']})")
        emit("   Выбор передаётся только временному процессу; файлы подключения не меняются.")
        primary = next((row for row in selected if row["id"] == settings.workspace_id),
                       selected[0])
        sibling = next(row for row in selected if row["id"] != primary["id"])
        names = {row["id"]: row["name"] for row in selected}

        async with connect(settings, selected, quiet) as session:
            contexts = await call(session, "mlspace_contexts")
            assert {row["id"] for row in contexts["workspaces"]} == set(names)
            emit("\n2. mlspace_contexts()")
            emit("   MCP видит: " + ", ".join(row["name"] for row in contexts["workspaces"]))

            emit('\n3. Общий обзор: mlspace_jobs_overview(status=["Running", "Pending"])')
            emit("   targets не передан. Лимиты демо: 2 страницы на регион, 30 запросов.")
            overview = await call(session, "mlspace_jobs_overview", {
                "status": ["Running", "Pending"], "page_size": 5,
                "max_pages": 2, "max_requests": 30, "max_items": 12})
            counts = Counter(row["provenance"]["request_workspace_id"]
                             for row in overview["items"])
            for row in selected:
                checked = [s for s in overview["checked_scope"]
                           if s.get("target") == row["id"] and s.get("source") == "jobs"]
                emit(f"   {row['name']}: регионов проверено {len(checked)}, "
                     f"задач в показанной части {counts[row['id']]}")
            emit(f"   complete={overview['complete']}; "
                 f"fetch_complete={overview['fetch_complete']}; "
                 f"output_truncated={overview['output_truncated']}")
            for failure in overview["failures"]:
                emit(f"   Неполная проверка: {names.get(failure.get('target'), '?')}: "
                     f"{failure.get('reason')} (HTTP {failure.get('http_status', '—')})")

            emit("\n4. Адресное чтение ноутбуков только в " + primary["name"])
            notebooks = await call(session, "mlspace_notebooks_overview", {
                "targets": [primary["id"]], "page_size": 2,
                "max_pages": 2, "max_requests": 3, "max_items": 4})
            for item in notebooks["items"]:
                row = item["data"]
                emit(f"   {row.get('name', '?')}: {row.get('status', '?')}")
            emit(f"   complete={notebooks['complete']}; показанная часть: "
                 f"{len(notebooks['items'])}. Paused остаются в списке.")

            emit("\n5. Параллельно: workspaces get в " + primary["name"] +
                 " и " + sibling["name"])
            async def read_workspace(row):
                result = await call(session, "mlspace_workspaces", {
                    "action": "get", "target": row["id"], "response_format": "json"})
                assert result["request_context"]["target"] == row["id"]
                assert result["data"]["id"] == row["id"]
                return row["name"]
            for name in await asyncio.gather(read_workspace(primary), read_workspace(sibling)):
                emit(f"   {name}: ID ответа совпал с адресом запроса.")

            reference = next((item["resource_ref"] for item in notebooks["items"]
                              if item.get("resource_ref")), None)
            if reference:
                emit("\n6. После чтения другого workspace возвращаемся к выбранному ноутбуку")
                result = await call(session, "mlspace_notebooks", {
                    "action": "get", "resource_ref": reference, "response_format": "json"})
                assert result["request_context"]["target"] == primary["id"]
                emit("   Адрес из resource_ref сохранил воркспейс: " + primary["name"])
                emit("   Сервер не переключал глобальный текущий workspace.")

            emit('\n7. Запрос без выбора: mlspace_workspaces(action="get")')
            refused = await session.call_tool("mlspace_workspaces", {"action": "get"})
            assert refused.isError
            emit("   isError=true. MCP требует target: несколько воркспейсов подключены.")
            assert await call(session, "mlspace_contexts") == contexts
            emit("   Подключённый набор после всех вызовов сохранился.")

        emit("\n8. Отдельный процесс с ОДНИМ workspace: " + primary["name"])
        async with connect(settings, [primary], quiet) as session:
            result = await call(session, "mlspace_workspaces", {
                "action": "get", "response_format": "json"})
            assert result["id"] == primary["id"]
            emit('   Тот же action="get" без target успешно выбрал единственный workspace.')
        emit("\nДЕМО ЗАВЕРШЕНО: проверки адресации пройдены; полнота обзоров указана выше.")
    finally:
        unchanged = before == fingerprint()
        emit("Файлы существующего подключения: " + ("БЕЗ ИЗМЕНЕНИЙ" if unchanged else "ИЗМЕНИЛИСЬ"))
        assert unchanged, "Existing credential file changed during demo"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", action="store_true", help="Bypass proxies for this process only")
    parser.add_argument("--transcript", type=Path, help="Save only the displayed, filtered summary")
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.direct:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(key, None)
    transcript = args.transcript.open("w", encoding="utf-8") if args.transcript else None

    def emit(text):
        print(text, flush=True)
        if transcript:
            print(text, file=transcript, flush=True)

    try:
        with open(os.devnull, "w") as quiet:
            asyncio.run(asyncio.wait_for(demo(emit, quiet), timeout=150))
        return 0
    except Exception as exc:
        # Never print raw API responses, environment, credential values or tracebacks.
        emit("ДЕМО НЕ ЗАВЕРШЕНО: " + type(exc).__name__)
        return 1
    finally:
        if transcript:
            transcript.close()


if __name__ == "__main__":
    raise SystemExit(main())
