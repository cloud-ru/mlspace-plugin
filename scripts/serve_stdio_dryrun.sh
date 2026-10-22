#!/usr/bin/env bash
# Same as serve_stdio.sh but with writes ENABLED + dry-run: an LLM sees the full
# write surface (descriptions, body hints) and can "call" writes — they are validated
# and previewed, never sent. Safe to point a blind model at against prod.
set -euo pipefail
cd "$(dirname "$0")/.."
export MLSPACE_TRANSPORT=stdio
export MLSPACE_READONLY=false
export MLSPACE_DRY_RUN=true
export MLSPACE_LOG_LEVEL="${MLSPACE_LOG_LEVEL:-ERROR}"
exec .venv/bin/python -m mlspace_mcp
