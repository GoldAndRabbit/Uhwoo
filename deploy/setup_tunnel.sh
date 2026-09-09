#!/usr/bin/env zsh
# 一次性：登录 Cloudflare → 建 uhwoo 隧道 → 写 config → 把 uhwoo.com / www 指到隧道。
# 幂等，重复跑不会重复建。
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
NAME="${TUNNEL_NAME:-uhwoo}"
DOMAIN="${DOMAIN:-uhwoo.com}"
PORT="${PORT:-8130}"
CONF="$REPO/deploy/cloudflared.yml"

if [[ ! -f "$HOME/.cloudflared/cert.pem" ]]; then
  echo "→ 需要先授权：浏览器会打开 Cloudflare，选中 $DOMAIN 这个域名点 Authorize"
  cloudflared tunnel login
fi

if ! cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$NAME"; then
  echo "→ 创建隧道 $NAME"
  cloudflared tunnel create "$NAME"
fi
UUID="$(cloudflared tunnel list 2>/dev/null | awk -v n="$NAME" '$2==n{print $1}')"
[[ -n "$UUID" ]] || { echo "✗ 拿不到隧道 UUID"; exit 1; }

cat > "$CONF" <<YML
# uhwoo 隧道：公网 https://$DOMAIN → 本机 uvicorn
tunnel: $UUID
credentials-file: $HOME/.cloudflared/$UUID.json
originRequest:
  connectTimeout: 30s
  noTLSVerify: true
  # SSE 事件流不能被缓冲，也不能被 100s 掐断
  disableChunkedEncoding: false
ingress:
  - hostname: $DOMAIN
    service: http://127.0.0.1:$PORT
  - hostname: www.$DOMAIN
    service: http://127.0.0.1:$PORT
  - service: http_status:404
YML
echo "✓ 写好 $CONF (tunnel=$UUID)"

for h in "$DOMAIN" "www.$DOMAIN"; do
  echo "→ 绑定 DNS $h"
  cloudflared tunnel route dns "$NAME" "$h" 2>&1 | tail -1
done
echo "✓ 完成。接着跑：deploy/run_server.sh && deploy/run_cf.sh"
