# mossdock-homelab

一套可被 AI 直接接管的自托管环境仓库：**Audiobookshelf + 元数据匹配 + Language Reactor + Immich + 边缘代理/隧道**，
外加两个配套 Chrome 扩展。

- 换域名：改一处 `site.json`，一条命令重新生成所有配置
- 换电脑：照 `docs/DEPLOY_NEW_MACHINE.md` 从零拉起
- 给 AI：先读 [AGENTS.md](AGENTS.md)

## 快速开始（现有机器）

```bash
python3 scripts/render.py            # 由 site.json 生成所有配置
bash scripts/healthcheck.sh          # 巡检
bash scripts/deploy.sh               # 拉起/更新全部服务
```

## 换域名

```bash
python3 scripts/change_domain.py example.com
python3 scripts/package_extensions.py
```

DNS、证书、隧道、扩展的后续步骤见 [docs/DOMAIN_CHANGE.md](docs/DOMAIN_CHANGE.md)。

## Chrome 扩展

访问 **https://wzs2004.github.io/mossdock-homelab/** 下载安装脚本，或直接看
[extensions/](extensions/) 三个目录（开发者模式加载已解压的扩展）。

> Chrome 137 起禁止商店外静默安装，最后需要把文件夹拖进 `chrome://extensions`。

## 目录

```
site.json                 唯一事实来源（域名/端口/路径/网络）
stacks/                   每个服务一个 compose
extensions/               三个 Chrome 扩展
scripts/                  render / deploy / backup / healthcheck / package
docs/                     GitHub Pages 安装页 + 运维文档
```

## 文档

- [架构与端口](docs/ARCHITECTURE.md)
- [新电脑部署](docs/DEPLOY_NEW_MACHINE.md)
- [换域名](docs/DOMAIN_CHANGE.md)
- [密钥清单](docs/SECRETS.md)
- [备份与恢复](docs/BACKUP.md)
- [排障](docs/TROUBLESHOOTING.md)
