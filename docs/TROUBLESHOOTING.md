# 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| compose 报 `create_host_path: false` | NAS 没挂载 | 挂载 `/Volumes/NAS` 再启动，别改成 true |
| 扩展面板不出现 | 站点权限没给 | 扩展选项保存一次，刷新页面 |
| `/__ext/asr` 403 | 缺 `X-ASR-Extension-Token` | 扩展里填 `EXTENSION_TOKEN` |
| `/__ext/asr` 502 | 转写容器没起 | `docker logs transcription-manager` |
| 公网 443 不通 | relay 没跑/没 root | `sudo launchctl list | grep mossdock` |
| 证书报错 | 证书域名不匹配 | 重签并放到 `certificate.dir`，重启 ipv6-https |
| Cloudflare 530 | 隧道掉线/回源不对 | 看 `docker logs mossdock-tunnel`，核对 ingress |
| Immich 502 | 网关正在唤醒 | 等 10–30 秒重试 |
| 扫描后元数据被改回 | ABS 按标签重算 | 保留 `metadata/items/<id>/metadata.json`；必要时 `render` 后重写 |

## 常用命令

```bash
docker compose -f stacks/edge/compose.yaml ps
docker logs -f mossdock-accelerator
docker exec -it audiobookshelf sh
bash scripts/healthcheck.sh
python3 scripts/render.py --check
```
