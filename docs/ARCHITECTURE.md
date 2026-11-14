# 架构与端口

```
公网
 ├─ IPv6 直连 ──► 宿主 relay.py :443 ──► 127.0.0.1:18443 (mossdock-ipv6-https, Caddy)
 │                                        ├─ books6.*   → accelerator:8081
 │                                        ├─ photos6.*  → accelerator:8082
 │                                        └─ dav6.*     → webdav:8080
 └─ Cloudflare Tunnel ──► mossdock-accelerator:8081/8082 / webdav:8080
                            ├─ /                → audiobookshelf:80
                            ├─ /__ext/asr/*     → transcription-manager:13384
                            └─ /__ext/lr/*      → abs-lr-bridge:13381
```

| 组件 | 容器名 | 宿主端口 | 内部端口 | 网络 |
|---|---|---|---|---|
| Audiobookshelf | audiobookshelf | 127.0.0.1:13378 | 80 | mossdock-abs |
| 转写管理 | transcription-manager | 127.0.0.1:13384 | 13384 | mossdock-abs |
| LR 桥接 | abs-lr-bridge | 127.0.0.1:13381 | 13381 | abs + lr |
| Immich server | immich_server | 127.0.0.1:12283 | 2283 | mossdock-immich |
| Immich 网关 | immich_gate | 0.0.0.0:2283 | 2283 | immich |
| Immich ML / Redis / PG | immich_* | — | — | immich |
| 边缘 Caddy | mossdock-accelerator | — | 8081/8082 | edge + abs + lr + immich |
| Cloudflare Tunnel | mossdock-tunnel | — | — | edge + abs + lr + immich |
| IPv6 Caddy | mossdock-ipv6-https | 127.0.0.1:18443 | 443 | edge + abs + lr |
| WebDAV | mossdock-webdav | — | 8080 | edge |

## 数据位置（NAS）

```
/Volumes/NAS/Apps/Audiobookshelf/
  config/        absdatabase.sqlite
  metadata/      封面、items/<id>/metadata.json
  audiobooks/    第一个书库
  audiobooks115/ 第二个书库
  transcription-manager/data/   转写任务、字幕、备份
/Volumes/NAS/Apps/Immich/       Immich 上传目录
/Volumes/NAS/WebDAV/            WebDAV 根
/Volumes/NAS/Apps/whisper-models/  whisper ggml 模型
```

## 域名

| site.json 键 | 用途 |
|---|---|
| `books` / `photos` / `dav` | IPv6 直连（带自签/ACME 证书） |
| `books_tunnel` / `photos_tunnel` / `dav_tunnel` | Cloudflare Tunnel 入口 |
| `files_tunnel` | 预留（Copyparty，当前未部署） |
