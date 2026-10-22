"""Call one read-only MLSpace tool through a temporary stdio MCP process."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from demo_multi_workspace import connect, discover

from mlspace_mcp.config import load_settings
from mlspace_mcp.formatting import redact_secrets
from mlspace_mcp.workspace_context import selected_workspaces


async def run(args, quiet) -> int:
    settings = load_settings()
    settings.require_credentials()
    if args.all_workspaces:
        catalogue = await discover(settings)
    else:
        catalogue = [{"id": w.id, "name": w.name, "project_name": w.project_name}
                     for w in selected_workspaces(settings)]
    arguments = json.loads(args.arguments)
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    async with connect(settings, catalogue, quiet) as session:
        result = await session.call_tool(args.tool, arguments)
        for content in result.content:
            if content.type != "text":
                continue
            try:
                payload = json.loads(content.text)
            except ValueError:
                payload = content.text
            print(json.dumps({"isError": result.isError, "result": redact_secrets(payload)},
                             ensure_ascii=False, indent=2))
        return int(result.isError)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-workspaces", action="store_true",
                        help="Explicitly select all visible workspaces for this temporary process")
    parser.add_argument("--direct", action="store_true", help="Bypass proxies for this process only")
    parser.add_argument("tool")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON object of tool arguments")
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parents[1])
    if args.direct:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(key, None)
    try:
        with open(os.devnull, "w") as quiet:
            return asyncio.run(asyncio.wait_for(run(args, quiet), timeout=150))
    except Exception as exc:
        print(json.dumps({"isError": True, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
