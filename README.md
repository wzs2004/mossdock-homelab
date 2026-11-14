# mossdock-homelab

一套可被 AI 直接接管的自托管环境仓库：**Audiobookshelf + 元数据匹配 + Language Reactor + Immich + 边缘代理/隧道**，
外加配套 Chrome 扩展。

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
[extensions/](extensions/) 目录（开发者模式加载已解压的扩展）。

三个扩展各自独立，可同时安装：

| 扩展 | 作用 | 安装包 |
|---|---|---|
| **转写管理** | 书库首页看转写队列，进书后开始转写并查看进度与报错 | `dist/abs-transcription-manager-1.0.0.zip` |
| **书籍匹配** | 在书籍页选择元数据来源，查看全部候选并一键应用 | `dist/abs-book-matching-1.6.0.zip` |
| **Language Reactor** | 把单条音轨连同字幕送进 Language Reactor | `dist/abs-language-reactor-1.5.0.zip` |

转写管理需要服务端 `stacks/transcription-manager`（对外经 `/__ext/asr`），
密钥用该 stack `.env` 里的 `EXTENSION_TOKEN`。

> Chrome 137 起禁止商店外静默安装，最后需要把文件夹拖进 `chrome://extensions`。

Language Reactor 扩展 1.5.0 起，读取书库和传输音频都有超时与自动重试：隧道卡住时会显示失败原因和「重试」按钮，不会再无限停在“正在读取…”。

## 目录

```
site.json                 唯一事实来源（域名/端口/路径/网络）
stacks/                   每个服务一个 compose
extensions/               Chrome 扩展
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
