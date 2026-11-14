#!/bin/bash
# 备份配置 + 数据库；--full 时额外备份媒体库（很大）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
OUT="${1:-$HOME/Backups/mossdock-$(date +%Y%m%d-%H%M%S)}"
FULL="${2:-}"
mkdir -p "$OUT"
echo "==> 备份到 $OUT"

# 配置与密钥
mkdir -p "$OUT/secrets"
for f in stacks/*/.env stacks/lr-bridge/config.json stacks/edge/cloudflared/tunnel-credentials.json; do
  [ -f "$f" ] && rsync -a "$f" "$OUT/secrets/$(echo "$f" | tr '/' '_')"
done
cp site.json "$OUT/"
[ -d "$CERT_DIR" ] && rsync -a "$CERT_DIR/" "$OUT/certs/" || true

# Audiobookshelf
ABS=$(python3 -c "import json;print(json.load(open('site.json'))['site']['data_root'])")/Audiobookshelf
if [ -f "$ABS/config/absdatabase.sqlite" ]; then
  docker exec audiobookshelf sh -c 'command -v sqlite3 >/dev/null && sqlite3 /config/absdatabase.sqlite ".backup /config/abs-backup.sqlite"' 2>/dev/null || cp "$ABS/config/absdatabase.sqlite" "$OUT/absdatabase.sqlite"
  [ -f "$ABS/config/abs-backup.sqlite" ] && mv "$ABS/config/abs-backup.sqlite" "$OUT/absdatabase.sqlite"
fi
rsync -a --exclude 'cache' "$ABS/metadata/" "$OUT/abs-metadata/"

# Immich 数据库
docker exec immich_postgres pg_dumpall -U "$(grep DB_USERNAME stacks/immich/.env | cut -d= -f2)" > "$OUT/immich.sql" 2>/dev/null || echo "跳过 Immich 数据库（容器未运行）"

if [ "$FULL" = "--full" ]; then
  echo "==> 备份媒体（会很慢）"
  rsync -a "$ABS/audiobooks" "$ABS/audiobooks115" "$OUT/media/" 2>/dev/null || true
  IMMICH_UPLOAD=$(grep UPLOAD_LOCATION stacks/immich/.env | cut -d= -f2)
  [ -d "$IMMICH_UPLOAD" ] && rsync -a "$IMMICH_UPLOAD/" "$OUT/immich-upload/"
fi

echo "==> 完成: $OUT"
du -sh "$OUT"
