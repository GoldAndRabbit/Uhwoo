#!/usr/bin/env zsh
# 只杀本项目的 uvicorn，别误伤别的服务
PATTERN='uvicorn backend.main:app'
pids=( $(pgrep -f "$PATTERN" 2>/dev/null || true) )
if (( ${#pids} == 0 )); then echo "未运行"; exit 0; fi
echo "SIGTERM: ${pids[*]}"
for pid in $pids; do kill "$pid" 2>/dev/null || true; done
sleep 1
pids=( $(pgrep -f "$PATTERN" 2>/dev/null || true) )
if (( ${#pids} )); then
  echo "SIGKILL: ${pids[*]}"
  for pid in $pids; do kill -9 "$pid" 2>/dev/null || true; done
fi
echo "已停止"
