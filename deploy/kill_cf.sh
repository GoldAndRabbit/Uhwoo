#!/usr/bin/env zsh
# 只停 uhwoo 自己那条隧道：先按 pid 文件，再兜底按 config 路径匹配。
# 不要按 'cloudflared tunnel' 全匹配去杀 —— 本机还跑着别的项目的隧道。
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$REPO/logs/cf/.pid"

pids=()
if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  # 确认这个 pid 真的是 cloudflared，避免 pid 复用误杀
  if [[ -n "$pid" ]] && ps -p "$pid" -o comm= 2>/dev/null | grep -q cloudflared; then
    pids+=("$pid")
  fi
fi
pids+=( $(pgrep -f 'cloudflared.*deploy/cloudflared.yml' 2>/dev/null || true) )

if (( ${#pids} == 0 )); then echo "未运行"; rm -f "$PID_FILE"; exit 0; fi
echo "SIGTERM: ${pids[*]}"        # 不打完整 argv：token 模式下 argv 里有密钥
for pid in $pids; do kill "$pid" 2>/dev/null || true; done
sleep 1
for pid in $pids; do kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true; done
rm -f "$PID_FILE"
echo "已停止"
