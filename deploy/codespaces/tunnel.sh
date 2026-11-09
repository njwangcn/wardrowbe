#!/usr/bin/env bash
# 给 Codespace 起一条 Cloudflare 快速隧道（免费、免注册），用于 github.dev 域名在你
# 所在网络打不开时换一个入口访问。
#
#   bash deploy/codespaces/tunnel.sh           # 启动并打印公网地址
#   bash deploy/codespaces/tunnel.sh stop      # 关掉隧道
#
# 隧道地址形如 https://xxx-yyy-zzz.trycloudflare.com，重启后地址会变。
set -euo pipefail
cd "$(dirname "$0")/../.."

BIN=/tmp/cloudflared
LOG=/tmp/cf.log
PORT="${PORT:-3000}"

if [ "${1:-}" = "stop" ]; then
  pkill -x cloudflared 2>/dev/null || true
  echo "隧道已关闭"
  exit 0
fi

if [ ! -x "$BIN" ]; then
  echo "==> 下载 cloudflared"
  curl -sL -o "$BIN" \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$BIN"
fi

pkill -x cloudflared 2>/dev/null || true
: > "$LOG"
setsid "$BIN" tunnel --url "http://localhost:${PORT}" --no-autoupdate > "$LOG" 2>&1 < /dev/null &

echo -n "==> 等待隧道就绪"
for _ in $(seq 1 40); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)
  if [ -n "${URL:-}" ]; then break; fi
  echo -n "."
  sleep 2
done
echo

if [ -z "${URL:-}" ]; then
  echo "没拿到地址，看日志：$LOG" >&2
  tail -20 "$LOG" >&2 || true
  exit 1
fi

echo "公网地址：$URL"

# NextAuth v4 必须知道自己的外部域名，否则登录回调会跳回 localhost/github.dev。
# 这里把 .env 里的 NEXTAUTH_URL 换成隧道地址，并重启前端让配置生效。
if [ -f .env ] && command -v docker >/dev/null 2>&1; then
  sed -i "/^NEXTAUTH_URL=/d" .env
  printf 'NEXTAUTH_URL=%s\n' "$URL" >> .env
  echo "==> 同步 NEXTAUTH_URL 并重启前端"
  docker compose -f docker-compose.preview.yml up -d frontend >/dev/null 2>&1 || true
  echo "完成，几十秒后即可用上面地址登录。"
fi

echo "（这个进程只在本容器里活着，Codespace 重启后需重新执行本脚本）"
