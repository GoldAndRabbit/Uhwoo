#!/usr/bin/env zsh
# 后台起 uvicorn（生产口径：只监听 127.0.0.1，外网走 cloudflared 隧道进来）
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
set +u; [[ -f "$HOME/.zshrc" ]] && source "$HOME/.zshrc"; set -u   # 取 ALIYUN_BAILIAN_API_KEY 等

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
