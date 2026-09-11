#!/usr/bin/env bash
# 启动 Multi-agent-werewolf：http://127.0.0.1:8130
set -euo pipefail
cd "$(dirname "$0")"

# 有 uv 就用 uv：建环境和装依赖都走它的全局缓存（macOS 上是 APFS 写时复制），
# 全命中时不到 1 秒，而且和 days4fun 那边的做法一致。没有 uv 就退回 venv + pip。
if command -v uv >/dev/null 2>&1; then
  [ -d .venv ] || uv venv --python 3.12 .venv
  VIRTUAL_ENV="$PWD/.venv" uv pip install -q -r requirements.txt
else
  [ -d .venv ] || python3.12 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port "${PORT:-8130}" "$@"
