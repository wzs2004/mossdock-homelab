#!/bin/bash
# 在新机器（或本机迁移）拉起全部服务。
# 用法: bash scripts/deploy.sh [--build] [--only abs|manager|lr|immich|edge|webdav]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 预检"
command -v docker >/dev/null || { echo "缺少 docker"; exit 1; }
docker compose version >/dev/null || { echo "缺少 docker compose"; exit 1; }
NAS_ROOT=$(python3 -c "import json;print(json.load(open('site.json'))['site']['nas_root'])")
[ -d "$NAS_ROOT" ] || { echo "NAS 未挂载: $NAS_ROOT（先挂载再部署）"; exit 1; }

echo "==> 渲染配置"
python3 scripts/render.py

echo "==> 创建网络"
for net in $(python3 -c "import json;n=json.load(open('site.json'))['networks'];print(' '.join(n.values()))"); do
  docker network inspect "$net" >/dev/null 2>&1 || docker network create "$net" >/dev/null
  echo "   $net"
done
docker network inspect mossdock-edge >/dev/null 2>&1 || docker network create mossdock-edge >/dev/null

ensure_env() { [ -f "$1/.env" ] || { cp "$1/.env.example" "$1/.env"; echo "   已生成 $1/.env（请填入密钥）"; }; }
for d in audiobookshelf transcription-manager lr-bridge immich edge webdav; do ensure_env "stacks/$d"; done

start() {
  local name=$1 extra=${2:-}
  echo "==> 启动 $name"
  # shellcheck disable=SC2086
  docker compose -f "stacks/$name/compose.yaml" $extra up -d
}
ONLY=${2:-all}
case "${ONLY}" in
  abs) start audiobookshelf ;;
  manager) start transcription-manager "--build" ;;
  lr) start lr-bridge "--build" ;;
  immich) start immich "--build" ;;
  edge) start edge ;;
  webdav) start webdav ;;
  all)
    start audiobookshelf
    start transcription-manager "--build"
    start lr-bridge "--build"
    start immich "--build"
    start webdav
    start edge
    ;;
  *) echo "未知目标 $ONLY"; exit 1 ;;
esac

echo "==> 巡检"
bash scripts/healthcheck.sh || true
