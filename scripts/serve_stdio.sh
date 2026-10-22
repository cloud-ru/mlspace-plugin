#!/usr/bin/env bash
# Launch the MLSpace MCP server over stdio for blind-model CLI tests.
# Use the configured user credentials or an explicit MLSPACE_ENV_FILE.
set -euo pipefail
cd "$(dirname "$0")/.."
export MLSPACE_TRANSPORT=stdio
export MLSPACE_READONLY=true
export MLSPACE_LOG_LEVEL="${MLSPACE_LOG_LEVEL:-ERROR}"
exec .venv/bin/python -m mlspace_mcp
