# 发布扩展到 Chrome 应用商店（GitHub Actions 自动上传）

推代码到 GitHub → Actions 自动打包、上传、提交审核。整条链只用 `scripts/cws.py`（标准库，无依赖）。

```
改 extensions/<x>/**  +  加版本号  →  git push main
                                      ↓
                          .github/workflows/cws-publish.yml
                                      ↓
                    python3 scripts/package_extensions.py   (dist/abs-<x>-<ver>.zip)
                                      ↓
                    python3 scripts/cws.py upload <x> --publish
                                      ↓
                    Chrome Web Store API v2: :upload → :publish
```

## 触发方式

| 操作 | 结果 |
|---|---|
| 推 `main`，只改了 `extensions/book-matching/**` | 只发「书籍匹配」 |
| 推 `main`，同时改了两个扩展目录 | 发这两个 |
| 推标签 `cws-v*` | 把 `cws-items.json` 里登记的全部发一遍 |
| 仓库页 Actions →「发布到 Chrome 应用商店」→ Run workflow | 手动选一个 / all，可勾 dry-run |

> 商店不接受重复版本号：`manifest.json` 的 `version` 和 `scripts/package_extensions.py` 里
> `VERSIONS[slug]` 必须一起往上加，否则上传会被拒。

## 一次性配置（只做一次，约 10 分钟）

### 1. 开 API + 建 OAuth 客户端（在浏览器里）

1. 打开 <https://console.cloud.google.com/projectcreate>，新建项目（名字随便，如 `mossdock-cws`）。
2. 打开 <https://console.cloud.google.com/apis/library/chromewebstore.googleapis.com>，点 **启用**。
3. 打开 <https://console.cloud.google.com/auth/overview>（Google Auth Platform）→ **开始使用**：
   - User type 选 **外部 / External**
   - 填应用名（如 `mossdock-cws`）、支持邮箱
   - Audience：**测试用户（Test users）**里加上你自己的 Google 账号（就是发布扩展那个）
   - 完成后留在「测试」状态即可，不用发布应用
4. 打开 <https://console.cloud.google.com/auth/clients> → **创建客户端**：
   - 应用类型：**桌面应用 / Desktop app**
   - 名字随便 → 创建 → **下载 JSON** 或直接复制 `client_id` / `client_secret`

> 必须是「桌面应用」类型：脚本用 `http://127.0.0.1:8765/oauth2callback` 收回调。

### 2. 在本机授权一次

```bash
cd ~/Documents/Codex/mossdock-homelab
python3 scripts/cws.py login
# 粘 client_id → 粘 client_secret → 浏览器选账号 → 允许
```

凭证存 `~/.config/mossdock/cws-creds.json`（0600，不进仓库）。
`refresh_token` 长期有效；改密码、撤销授权、或 6 个月不用要重新 login。

### 3. 把 4 个 secret 填进 GitHub

```bash
python3 scripts/cws.py secrets     # 打印 4 行，复制粘贴
```

仓库 → **Settings → Secrets and variables → Actions → New repository secret**：

| Secret | 值 |
|---|---|
| `CWS_CLIENT_ID` | OAuth 客户端 ID |
| `CWS_CLIENT_SECRET` | OAuth 客户端密钥 |
| `CWS_REFRESH_TOKEN` | `secrets` 命令打印的 refresh token |
| `CWS_PUBLISHER_ID` | 发布者 ID（同 `scripts/cws-items.json` 里的 `publisherId`） |

### 4. 填条目的 itemId

> 现在商店上架的是合并版 `extensions/toolkit`（Audiobookshelf 工具箱）。原来三个独立扩展是拆分版，
> 保留在仓库里但不再发布；`scripts/cws-items.json` 的 `items` 里只保留 `toolkit` 一条。
> workflow 会自动跳过没登记的目录，所以改旧扩展不会触发发布、也不会报红。

`scripts/cws-items.json` 里 `items.<slug>.itemId`：

```
Chrome 应用商店开发者信息中心 → 点开某个扩展 → 看地址栏
https://chrome.google.com/webstore/devconsole/<itemId>
                                                 ↑ 这串
```

然后验证（这一步会真的调 Google 接口）：

```bash
python3 scripts/cws.py probe      # 三条都 ✓ 才算通
python3 scripts/cws.py status all # 看每个条目的当前状态
```

## 日常发版

```bash
# 1. 改代码
vim extensions/book-matching/content.js

# 2. 加版本号（两处一起改）
#    extensions/book-matching/manifest.json      "version": "1.6.0" → "1.6.1"
#    scripts/package_extensions.py               VERSIONS['book-matching'] = '1.6.1'

# 3. 本地先试跑（可选，不上传）
python3 scripts/cws.py upload book-matching --publish --dry-run

# 4. 推上去，Action 自动发
git add -A && git commit -m "book-matching: 修 xxx" && git push
```

提交后去 [Actions](../../actions/workflows/cws-publish.yml) 看日志；
上传成功会显示 `上传：SUCCEEDED 版本 1.6.1` + `提交：PENDING_REVIEW`。

## 常见失败

| 现象 | 原因 / 处理 |
|---|---|
| `HTTP 401` / `invalid_grant` | refresh_token 失效 → 重新 `login`，更新 secret |
| `HTTP 404` on `:upload` | itemId 或 publisherId 错；`probe` 先确认 |
| `HTTP 400 ... version` | 版本号没递增 |
| `HTTP 403` | OAuth 客户端那个 Google 账号不是这个 publisher 的所有者，或 API 没启用 |
| `提交审核` 有 warning | 用 `--block-on-warnings` 可以让它直接失败好排查 |
| 条目不存在 | API **不能创建**新条目；新扩展必须先在开发者后台人工上传一次，之后才归 CI 管 |
| 条目还在草稿态 | 只上传、不提交审核（避免商店以「商品详情不全」拒绝）；补齐商品详情、首次提交审核后就自动走 |

## 相关文件

- `scripts/cws.py` — 授权 / probe / status / upload
- `scripts/cws-items.json` — publisherId + 已登记条目（当前只有 `toolkit`）
- `scripts/package_extensions.py` — 打包（`VERSIONS` 是版本号来源）
- `.github/workflows/cws-publish.yml` — 流水线
