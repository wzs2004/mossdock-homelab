# 备份与恢复

## 备份

```bash
bash scripts/backup.sh                 # 配置 + 数据库 + 元数据（推荐，快）
bash scripts/backup.sh ~/Backups/x --full   # 额外备份媒体文件（很慢，很大）
```

产物：

```
$HOME/Backups/mossdock-<时间>/
  site.json  secrets/  certs/
  absdatabase.sqlite  abs-metadata/
  immich.sql
  media/  immich-upload/   （仅 --full）
```

## 恢复

```bash
ABS=/Volumes/NAS/Apps/Audiobookshelf
rsync -a mossdock-*/abs-metadata/ "$ABS/metadata/"
cp mossdock-*/absdatabase.sqlite "$ABS/config/"
docker exec -i immich_postgres psql -U <DB_USERNAME> < mossdock-*/immich.sql
cp mossdock-*/site.json ./
bash scripts/render.py
bash scripts/deploy.sh
```

## 注意

- 恢复 `absdatabase.sqlite` 前先停 `audiobookshelf` 容器。
- Immich 恢复要求 `DB_PASSWORD` 与备份时一致。
- 媒体文件建议直接放在 NAS 上做快照，不必每次全量 rsync。
