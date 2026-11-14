# 在新电脑上部署

## 0. 前置

- macOS + Docker Desktop（或 OrbStack），已登录
- NAS 挂载路径与旧机一致，或改 `site.json` 的 `site.nas_root` / `site.data_root`
- 一个域名（可继续用 `mossdock.autos`，也可以换）

## 1. 取仓库

```bash
git clone https://github.com/wzs2004/mossdock-homelab.git ~/mossdock-homelab
cd ~/mossdock-homelab
```

## 2. 恢复数据

把旧机 `bash scripts/backup.sh` 生成的目录拷过来，按 `docs/BACKUP.md` 还原：
数据库、`metadata/`、Immich 的 `pg_dumpall`、以及（可选）媒体文件。

## 3. 填密钥

```bash
cp stacks/audiobookshelf/.env.example stacks/audiobookshelf/.env
# 每个 stack 同理；密钥清单见 docs/SECRETS.md
```

至少需要：`ABS_API_TOKEN`、`META_TOKEN`、`LR_BRIDGE_KEY`、`DB_PASSWORD`、
Cloudflare tunnel 凭证 JSON。

## 4. 渲染 + 启动

```bash
python3 scripts/render.py
bash scripts/deploy.sh
bash scripts/healthcheck.sh
```

首次启动会构建两个本地镜像（元数据服务、LR 桥接、Immich 网关），需要几分钟。

## 5. 对外访问

- **有公网 IPv6**：把 `books6/photos6/dav6` 的 AAAA 指到本机，跑
  `stacks/edge/ipv6/install-edge-agents.sh` 安装 DDNS/证书/relay。
- **没有 IPv6**：只用 Cloudflare Tunnel，填 `stacks/edge/cloudflared/tunnel-credentials.json`，
  `docker compose -f stacks/edge/compose.yaml up -d tunnel`。

## 6. 扩展

```bash
python3 scripts/package_extensions.py
```

在 `chrome://extensions` 开发者模式加载 `extensions/*`，或跑 `install/install-macos.command`。
