# AGENTS.md — 给 AI 的操作手册

> 这个仓库是**整套自托管环境的唯一事实来源**。人类把仓库交给 AI 后，AI 只需要读这里，
> 就能在这台（或一台新）Mac 上完成部署、换域名、恢复、升级，不需要再问一遍背景。

## 0. 先读什么

1. `site.json` — 所有域名、端口、路径、网络名（**唯一事实来源**）
2. `scripts/render.py` — 由 site.json 生成 Caddy / cloudflared / .env / 扩展 config.js
3. `docs/ARCHITECTURE.md` — 每个服务是什么、跑在哪
4. `docs/SECRETS.md` — 密钥清单与获取方式（仓库里**没有**密钥）

## 1. 铁律

- **绝不提交密钥**：`stacks/*/.env`、`stacks/lr-bridge/config.json`、
  `stacks/edge/cloudflared/tunnel-credentials.json`、证书、`.env` 里的 token 都在 `.gitignore`。
- **改配置先改 `site.json`，再跑 `python3 scripts/render.py`**。不要手改生成物
  （`stacks/edge/Caddyfile`、`stacks/edge/ipv6/Caddyfile`、`stacks/edge/cloudflared/config.yml`、
  `extensions/*/config.js` 等都是生成物）。
- **动数据前先备份**：`bash scripts/backup.sh`。Audiobookshelf / Immich 的数据都在
  `/Volumes/NAS/Apps`，容器重建不会丢，但删目录会。
- **NAS 未挂载时不要启动任何 stack**：compose 里用了 `create_host_path: false`，会直接报错，
  这是故意的；不要改成 `true` 去“修复”。
- 改动后必须跑：`python3 scripts/render.py --check`、`bash scripts/healthcheck.sh`。

## 2. 当前架构

| 服务 | 容器 | 访问 | 数据 |
|---|---|---|---|
| Audiobookshelf | `audiobookshelf` | `127.0.0.1:13378`，公网 `{{books}}` | `/Volumes/NAS/Apps/Audiobookshelf` |
| 元数据匹配 | `meta-bridge` | 经 Caddy `/__ext/meta` | 无持久数据（备份在 `/Audiobookshelf/meta-backups`） |
| 转写管理 | `transcription-manager` | `127.0.0.1:13384`，经 Caddy `/__ext/asr` | `/Volumes/NAS/Apps/Audiobookshelf/transcription-manager` |
| LR 桥接 | `abs-lr-bridge` | 经 Caddy `/__ext/lr` | 只读挂载书库 |
| Immich | `immich_*` + `immich_gate` | `:2283`，公网 `{{photos}}` | `/Volumes/NAS/Apps/Immich` |
| 边缘 Caddy | `mossdock-accelerator` | 内部 8081/8082 | 无 |
| Cloudflare Tunnel | `mossdock-tunnel` | `books/photos/dav.mossdock.autos` | 凭证文件 |
| IPv6 直连 | `mossdock-ipv6-https` + 宿主 relay | `*6.mossdock.autos` | 证书目录 |
| WebDAV | `mossdock-webdav` | `dav6.mossdock.autos` | `/Volumes/NAS/WebDAV` |

详细端口/DNS 见 `docs/ARCHITECTURE.md`。

## 3. 任务：换域名（最常见）

```bash
python3 scripts/change_domain.py 新域名.com          # 例：example.com → books6.example.com / books.example.com
# 或只改某一个子域：直接编辑 site.json 的 domains，再 python3 scripts/render.py
python3 scripts/package_extensions.py                # 重新出扩展包
```

然后按 `docs/DOMAIN_CHANGE.md` 完成 4 件事：
1. **DNS**：AAAA 指向本机公网 IPv6（`stacks/edge/ipv6/ddns.py` 会自动改 Cloudflare 记录），
   Tunnel 域名用 CNAME 指向 tunnel。
2. **证书**：acme.sh 重新签发，放到 `site.json.certificate.dir`，重启 `ipv6-https`。
3. **Tunnel ingress**：更新 `stacks/edge/cloudflared/config.yml` 后
   `docker compose -f stacks/edge/compose.yaml up -d --force-recreate tunnel`。
4. **扩展**：`render.py` 已重写 `extensions/*/config.js`；用户在 `chrome://extensions` 点刷新，
   或重新导入（见 README）。

> 扩展同时支持在「选项」里手动改服务器地址，所以域名切换后即使没重新打包也能先用。

## 4. 任务：在新电脑部署

见 `docs/DEPLOY_NEW_MACHINE.md`。最短路径：

```bash
git clone <repo> ~/mossdock-homelab && cd ~/mossdock-homelab
python3 scripts/render.py
# 填 stacks/*/.env 里的密钥（见 docs/SECRETS.md）
bash scripts/deploy.sh
bash scripts/healthcheck.sh
```

前置：Docker Desktop、NAS 挂载到同一路径（或改 `site.json.site.nas_root`）、
把 `docs/BACKUP.md` 里的备份恢复到对应目录。

## 5. 任务：恢复 / 迁移数据

```bash
bash scripts/backup.sh                 # 现有机器
# 拷贝 $HOME/Backups/mossdock-* 到新机器
rsync -a mossdock-*/abs-metadata/ /Volumes/NAS/Apps/Audiobookshelf/metadata/
cp mossdock-*/absdatabase.sqlite /Volumes/NAS/Apps/Audiobookshelf/config/
docker exec -i immich_postgres psql -U postgres < immich.sql
```

## 6. 任务：改扩展 / 发版

```bash
python3 scripts/package_extensions.py     # dist/*.zip + 版本号
# 打 tag 发 Release（GitHub API 或网页）
```

扩展版本号在 `scripts/package_extensions.py` 的 `VERSIONS`，改完要递增
（`extensions/<x>/manifest.json` 里的 `version` 要同步改，否则会打警告）。

**发到 Chrome 应用商店是全自动的**：往 `main` 推改动过 `extensions/<x>/**` 的提交，
GitHub Actions 会打包 → 上传 → 提交审核（`cws-v*` 标签 = 三个全发）。
配置与排错见 [docs/CWS_PUBLISH.md](docs/CWS_PUBLISH.md)；手动发用
`python3 scripts/cws.py upload <slug> --publish`。

## 7. 验证清单

- [ ] `python3 scripts/render.py --check` 全部 OK
- [ ] `bash scripts/healthcheck.sh` 四项 OK
- [ ] 浏览器打开 `https://<books 域名>` 能登录、能播放
- [ ] 扩展面板能打开、匹配/转写接口返回 200
- [ ] `docker ps` 没有反复重启的容器

## 8. 已知坑

- **Chrome 137+ 不能静默装扩展**：只能开发者模式拖文件夹，或上架商店；脚本已自动化到剩两步。
- **macOS 公网 IPv6**：`relay.py` 要 root 绑定 443；`maintenance.sh` 负责 DDNS + 证书续期，
  用 `stacks/edge/ipv6/install-edge-agents.sh` 安装为 LaunchDaemon/Agent。
- **Cloudflare Tunnel** 凭证是 JSON 文件，不是 token；换隧道要同时换 `tunnel_id` 和凭证文件。
- **Immich 网关**会按需启停容器，所以 `docker ps` 里 Immich 服务消失是正常的（有访问会唤醒）。
- **ABS 扫描会重算元数据**：用户手改的元数据靠 `metadata/items/<id>/metadata.json` 兜底，
  不要删除这个目录。
