# 换域名

## 一条命令

```bash
python3 scripts/change_domain.py example.com
python3 scripts/package_extensions.py
```

它会把 `site.json` 的域名改成：

| 键 | 值 |
|---|---|
| books | `books6.example.com` |
| books_tunnel | `books.example.com` |
| photos / photos_tunnel | `photos6.` / `photos.` |
| dav / dav_tunnel | `dav6.` / `dav.` |

不想带 `6` 后缀：`--plain`。只想改某一个：直接编辑 `site.json` 后 `python3 scripts/render.py`。

## 还要做的四件事

### 1. DNS

- IPv6 直连的三个域名：AAAA → 本机公网 IPv6。
  如果 Cloudflare 托管，`stacks/edge/ipv6/ddns.py` 会自动更新（改 `dns-auth.json` 的 zone/token）。
- Tunnel 域名：CNAME → `<tunnel-id>.cfargotunnel.com`（在 Cloudflare 面板加）。

### 2. 证书

证书目录在 `site.json.certificate.dir`，文件名 `fullchain.cer` + `<dav 域名>.key`。
用 acme.sh 重新签发后替换，再：

```bash
docker compose -f stacks/edge/compose.yaml restart ipv6-https
```

### 3. Cloudflare Tunnel

编辑 `stacks/edge/cloudflared/config.yml`（由模板生成）里的 hostname，然后：

```bash
docker compose -f stacks/edge/compose.yaml up -d --force-recreate tunnel
```

隧道本身在 Cloudflare 面板的 Public Hostnames 里要同步改，否则不回源。

### 4. 扩展

`render.py` 已把 `extensions/*/config.js` 改好；`package_extensions.py` 重新出包。
用户端在 `chrome://extensions` 点刷新；或重新拖入 `extensions/*`。
临时也可在扩展「选项」里手填新地址。

## 验证

```bash
curl -I https://books6.example.com/
curl -H "X-ASR-Extension-Token: $EXTENSION_TOKEN" https://books6.example.com/__ext/asr/api/health
bash scripts/healthcheck.sh
```
