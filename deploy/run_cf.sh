#!/usr/bin/env zsh
# 后台启动 uhwoo 的 cloudflared 隧道，自动探测可用协议 (quic ⇄ http2)。
#
# 两种模式，token 优先（和 pitchasso 一致，不需要 cert.pem）：
#   1) .env / 环境变量里的 CLOUDFLARED_TUNNEL_TOKEN —— 公网主机名在 Zero Trust 面板里配
#   2) deploy/cloudflared.yml —— cloudflared tunnel login 之后走 setup_tunnel.sh 生成
#
# 本机可能同时跑着别的项目的隧道，所以只按自己的 pid 文件停自己那条，别误伤。
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
[[ -f "$REPO/.env" ]] && source "$REPO/.env"

CONF="$REPO/deploy/cloudflared.yml"
if [[ -n "${CLOUDFLARED_TUNNEL_TOKEN:-}" ]]; then
  MODE=token
elif [[ -f "$CONF" ]]; then
  MODE=config
else
  echo "✗ 既没有 CLOUDFLARED_TUNNEL_TOKEN，也没有 $CONF"
  echo "  token 拿法：Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel →"
  echo "  cloudflared → 名字 uhwoo → 复制 token → 写进 $REPO/.env："
  echo "  CLOUDFLARED_TUNNEL_TOKEN=eyJ..."
  exit 1
fi

LOG_DIR="$REPO/logs/cf"; mkdir -p "$LOG_DIR"
STATE_FILE="$LOG_DIR/.last_protocol"
PID_FILE="$LOG_DIR/.pid"

# 协议是会翻的：GFW 对 cloudflared 握手的封锁在 quic(UDP/7844) 和 http2(TCP/7844+TLS)
# 之间来回切，写死任一个都会在下次翻盘时失效。所以起一个 → 轮询日志 ~18s 看有没有
# "Registered tunnel connection"，连上就记到 .last_protocol（下次优先试），否则换另一个。
last="$(cat "$STATE_FILE" 2>/dev/null || true)"
case "$last" in
  http2) order=(http2 quic) ;;
  quic)  order=(quic http2) ;;
  *)     order=(quic http2) ;;
esac

try_protocol() {
  local proto="$1"
  local log="$LOG_DIR/$(date +%Y%m%d_%H%M%S)_${proto}.log"
  if [[ "$MODE" == token ]]; then
    nohup cloudflared tunnel --protocol "$proto" run --token "$CLOUDFLARED_TUNNEL_TOKEN" >"$log" 2>&1 &
  else
    nohup cloudflared --config "$CONF" tunnel --protocol "$proto" run >"$log" 2>&1 &
  fi
  local pid=$!
  disown $pid 2>/dev/null || true
  echo "$pid" > "$PID_FILE"
  local i
  for i in {1..18}; do
    if grep -q "Registered tunnel connection" "$log" 2>/dev/null; then
      echo "$proto" > "$STATE_FILE"
      echo "✓ 协议=$proto 已连  pid=$pid  mode=$MODE  log=$log  停止用 deploy/kill_cf.sh"
      return 0
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  echo "✗ 协议=$proto 18s 内未注册成功，换下一个 (log=$log)"
  kill "$pid" 2>/dev/null || true; sleep 1; kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  return 1
}

./deploy/kill_cf.sh >/dev/null 2>&1 || true
for proto in "${order[@]}"; do
  try_protocol "$proto" && exit 0
done
echo "✗ quic/http2 都连不上。排查：grep argotunnel /etc/hosts；用 DoH 重查真实 IP；或开代理。"
exit 1
