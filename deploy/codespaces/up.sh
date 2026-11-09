#!/usr/bin/env bash
# Codespaces 一键起预览栈。由 .devcontainer/devcontainer.json 的 postCreateCommand 调用。
# 中转站密钥从 Codespaces Secrets（环境变量）读取后写入本地 .env，.env 已被 gitignore。
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "==> 生成 .env（密钥来自 Codespaces Secrets，不入库）"
if [ ! -f .env ]; then
  cp .env.example .env
fi
# 追加/覆盖预览所需项（幂等）
apply() {  # apply KEY VALUE
  local k="$1" v="$2"
  if grep -q "^${k}=" .env; then
    sed -i "s|^${k}=.*|${k}=${v}|" .env
  else
    printf '%s=%s\n' "$k" "$v" >> .env
  fi
}

# Codespaces 端口转发域名：NextAuth 回调与 CORS 要用它，不能是 localhost
APP_URL_VAL="${APP_URL:-}"
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  APP_URL_VAL="https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
fi
[ -n "$APP_URL_VAL" ] && { apply APP_URL "$APP_URL_VAL"; apply NEXTAUTH_URL "$APP_URL_VAL"; }

apply DEBUG true
apply AI_INTERNAL_ENABLED false
# 把外部注入的中转站配置落进 .env（未设置则保持 false/空）
apply TRYON_ENABLED "${TRYON_ENABLED:-false}"
apply TRYON_BASE_URL "${TRYON_BASE_URL:-}"
apply TRYON_API_KEY "${TRYON_API_KEY:-}"
apply TRYON_MODEL "${TRYON_MODEL:-}"
apply TRYON_MODE "${TRYON_MODE:-chat}"

echo "==> 等待 Docker daemon 就绪"
for i in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then break; fi
  sleep 2
done
if ! docker info >/dev/null 2>&1; then
  echo "Docker 未就绪，请稍后在终端手动执行：bash deploy/codespaces/up.sh" >&2
  exit 1
fi

echo "==> 启动预览栈（首次构建约 3–8 分钟）"
docker compose -f docker-compose.preview.yml up -d --build

echo "==> 数据库迁移"
docker compose -f docker-compose.preview.yml exec -T backend alembic upgrade head || true

echo ""
echo "完成。前端 http://localhost:3000 （Codespaces 会自动弹出预览）"
echo "DEBUG=true，点 Login 直接免密进入。"
if [ "${TRYON_ENABLED:-false}" != "true" ]; then
  echo ""
  echo "虚拟试穿未启用：请在 GitHub → Settings → Secrets and variables → Codespaces"
  echo "添加 TRYON_BASE_URL / TRYON_API_KEY / TRYON_MODEL / TRYON_MODE，并把 TRYON_ENABLED=true，"
  echo "然后重建 Codespace（或在终端重跑本脚本）。"
fi
