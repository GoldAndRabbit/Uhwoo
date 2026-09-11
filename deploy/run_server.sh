#!/usr/bin/env zsh
# 后台起 uvicorn（生产口径：只监听 127.0.0.1，外网走 cloudflared 隧道进来）
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
set +u; [[ -f "$HOME/.zshrc" ]] && source "$HOME/.zshrc"; set -u   # 取 ALIYUN_BAILIAN_API_KEY 等

# 依赖有变动时补一下（uv 全命中缓存时不到 1 秒）
if command -v uv >/dev/null 2>&1; then
  [ -d .venv ] || uv venv --python 3.12 .venv
  VIRTUAL_ENV="$PWD/.venv" uv pip install -q -r requirements.txt
fi

PORT="${PORT:-8130}"
LOG_DIR="$REPO/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/server_$(date +%Y%m%d_%H%M%S).log"

./deploy/kill_server.sh >/dev/null 2>&1 || true
nohup "$REPO/.venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port "$PORT" \
  >"$LOG" 2>&1 &
pid=$!; disown $pid 2>/dev/null || true

for i in {1..20}; do
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/config"; then
    echo "✓ uvicorn 已就绪  pid=$pid  port=$PORT  log=$LOG"
    exit 0
  fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.5
done
echo "✗ uvicorn 启动失败，看日志：$LOG"; tail -20 "$LOG"; exit 1
