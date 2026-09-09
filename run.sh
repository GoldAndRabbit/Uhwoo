#!/usr/bin/env bash
# 启动 Multi-agent-werewolf：http://127.0.0.1:8130
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port "${PORT:-8130}" "$@"
