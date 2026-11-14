# 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| compose 报 `create_host_path: false` | NAS 没挂载 | 挂载 `/Volumes/NAS` 再启动，别改成 true |
| 扩展面板不出现 | 站点权限没给 | 扩展选项保存一次，刷新页面 |
| LR 面板一直显示“正在读取…”/“正在接收音频…” | 桥接请求被隧道或 NAS 卡住 | 1.5.0 起 30–60 秒会超时并自动重试，失败会给出原因和「重试」按钮；若反复失败，`docker logs abs-lr-bridge` 看服务端，并在扩展选项里重新「保存并授权连接」 |
| `/__ext/meta` 403 | 缺 `X-Meta-Token` | 扩展里填 `META_TOKEN` |
| `/__ext/meta` 502 | 元数据容器没起 | `docker logs meta-bridge` |
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
