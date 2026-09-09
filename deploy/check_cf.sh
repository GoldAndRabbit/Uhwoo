#!/usr/bin/env zsh
setopt null_glob            # 没有日志文件时别报 no matches found
REPO="$(cd "$(dirname "$0")/.." && pwd)"
echo "— uvicorn —"
pgrep -f 'uvicorn backend.main:app' >/dev/null && echo "  运行中 pid=$(pgrep -f 'uvicorn backend.main:app' | tr '\n' ' ')" || echo "  未运行"
curl -sf -o /dev/null -w "  本地 http://127.0.0.1:${PORT:-8130} → %{http_code}\n" "http://127.0.0.1:${PORT:-8130}/api/config" || echo "  本地不通"
echo "— cloudflared (uhwoo) —"
pids=()
PID_FILE="$REPO/logs/cf/.pid"      # token 模式下 argv 里是密钥，只能按 pid 文件认自己那条
if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  [[ -n "$pid" ]] && ps -p "$pid" -o comm= 2>/dev/null | grep -q cloudflared && pids+=("$pid")
fi
pids+=( $(pgrep -f 'cloudflared.*deploy/cloudflared.yml' 2>/dev/null || true) )
if (( ${#pids} )); then echo "  运行中 pid=${pids[*]}"; else echo "  未运行"; fi
tail -3 "$(ls -t "$REPO"/logs/cf/*.log 2>/dev/null | head -1)" 2>/dev/null | sed 's/^/  /'
echo "— 线上 —"
curl -sf -o /dev/null -w "  https://uhwoo.com → %{http_code}\n" https://uhwoo.com || echo "  外网不通"
