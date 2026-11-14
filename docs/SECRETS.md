# 密钥清单

仓库里**不包含**任何密钥。下面这些需要在新机器上重新填/重新生成：

| 变量 | 位置 | 获取方式 |
|---|---|---|
| `ABS_API_TOKEN` | `stacks/transcription-manager/.env`、`stacks/lr-bridge/.env` | Audiobookshelf → 设置 → 用户 → API Token |
| `EXTENSION_TOKEN` | `stacks/transcription-manager/.env` | 自己生成：`openssl rand -hex 32`，同时填到扩展选项 |
| `LR_BRIDGE_KEY` | `stacks/lr-bridge/.env` | `openssl rand -hex 24`，同时填到 LR 扩展选项 |
| `DB_PASSWORD` | `stacks/immich/.env` | `openssl rand -hex 16`（恢复旧库时必须与备份一致） |
| Cloudflare tunnel 凭证 | `stacks/edge/cloudflared/tunnel-credentials.json` | Cloudflare Zero Trust → Tunnels → 下载 JSON |
| Cloudflare DNS token | `stacks/edge/ipv6/dns-auth.json` | Cloudflare API Token（Zone.DNS Edit） |
| TLS 证书 | `site.json.certificate.dir` | acme.sh / 证书服务商 |
| WebDAV htpasswd | `stacks/webdav/.env` 的 `WEBDAV_HTPASSWD` | `rclone` 文档 / `htpasswd` |

## 泄漏处理

一旦发现 `.env` 或 token 进了 git：

1. 立刻轮换（ABS token、EXTENSION_TOKEN、LR key、DB 密码）
2. 从 git 历史清除：`git filter-repo --path stacks/... --invert-paths`
3. 重新生成 tunnel 凭证
