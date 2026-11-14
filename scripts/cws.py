#!/usr/bin/env python3
"""Chrome Web Store 发布工具：OAuth 授权 + 上传 zip + 提交审核。

只用标准库（urllib），CI 和本机都能跑。

常用命令
    python3 scripts/cws.py login              # 一次性授权（会开浏览器），凭证存本机
    python3 scripts/cws.py secrets            # 打印要填进 GitHub Secrets 的 4 个值
    python3 scripts/cws.py probe              # 校验 publisherId / itemId 能不能用
    python3 scripts/cws.py status all         # 看商店里三个扩展现在的状态
    python3 scripts/cws.py upload book-matching --publish
    python3 scripts/cws.py upload all --publish --dry-run

凭证查找顺序：环境变量 > ~/.config/mossdock/cws-creds.json
    CWS_CLIENT_ID / CWS_CLIENT_SECRET / CWS_REFRESH_TOKEN / CWS_PUBLISHER_ID
"""
from __future__ import annotations

import argparse
import glob
import http.server
import json
import os
import re
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "scripts" / "cws-items.json"
CREDS_PATH = Path.home() / ".config" / "mossdock" / "cws-creds.json"
PACKAGE_SCRIPT = ROOT / "scripts" / "package_extensions.py"

SCOPE = "https://www.googleapis.com/auth/chromewebstore"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API = "https://chromewebstore.googleapis.com"
UPLOAD_API = "https://chromewebstore.googleapis.com/upload"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/oauth2callback"


# ---------------------------------------------------------------- 基础工具

def die(msg: str, code: int = 1):
    print(f"✗ {msg}", file=sys.stderr)
    raise SystemExit(code)


def ok(msg: str):
    print(f"✓ {msg}")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_creds(data: dict):
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    CREDS_PATH.chmod(0o600)


def creds() -> dict:
    saved = load_json(CREDS_PATH, {}) or {}
    return {
        "client_id": os.environ.get("CWS_CLIENT_ID") or saved.get("client_id", ""),
        "client_secret": os.environ.get("CWS_CLIENT_SECRET") or saved.get("client_secret", ""),
        "refresh_token": os.environ.get("CWS_REFRESH_TOKEN") or saved.get("refresh_token", ""),
        "publisher_id": os.environ.get("CWS_PUBLISHER_ID") or saved.get("publisher_id", ""),
    }


def config() -> dict:
    cfg = load_json(CONFIG_PATH, {})
    if not cfg:
        die(f"找不到配置 {CONFIG_PATH}")
    return cfg


def http_request(url: str, method: str = "GET", body: bytes | None = None,
                 headers: dict | None = None, timeout: int = 120,
                 retries: int = 3) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    last = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                last = f"HTTP {e.code}"
                time.sleep(2 * attempt)
                continue
            return e.code, e.read()
        except (urllib.error.URLError, OSError, ssl.SSLError) as e:
            # 直连 Google 偶尔会被掐断（EOF / reset），重试几次通常就好了
            last = getattr(e, "reason", e)
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            die(f"网络错误（{url}）：{last}\n  ← 需要能直连 Google；本机有代理时设 HTTPS_PROXY")


def parse_json(raw: bytes, what: str) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        die(f"{what} 返回的不是 JSON：{raw[:400]!r}")
    return {}


def google_error(status: int, data: dict, what: str) -> str:
    err = data.get("error", data)
    if isinstance(err, dict):
        msg = err.get("message") or json.dumps(err, ensure_ascii=False)
        details = err.get("details") or []
    else:
        msg, details = str(err), []
    lines = [f"{what} 失败：HTTP {status} — {msg}"]
    for d in details[:3]:
        if isinstance(d, dict) and d.get("@type", "").endswith("LocalizedMessage"):
            lines.append(f"  详情：{d.get('message')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- OAuth

def build_auth_url(client_id: str, state: str = "") -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    status, raw = http_request(TOKEN_ENDPOINT, "POST", body,
                               {"Content-Type": "application/x-www-form-urlencoded"})
    data = parse_json(raw or b"{}", "换取 token")
    if status != 200 or "refresh_token" not in data:
        die(f"换取 refresh_token 失败：HTTP {status} {json.dumps(data, ensure_ascii=False)[:400]}\n"
            f"  ← 常见原因：OAuth 客户端不是「桌面应用」类型，或 redirect_uri 不匹配 {REDIRECT_URI}")
    return data


def access_token(force: bool = False) -> str:
    c = creds()
    if not c["refresh_token"] or not c["client_id"]:
        die("还没授权过，先跑：python3 scripts/cws.py login")
    body = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    status, raw = http_request(TOKEN_ENDPOINT, "POST", body,
                               {"Content-Type": "application/x-www-form-urlencoded"})
    data = parse_json(raw, "刷新 access_token")
    if status != 200 or "access_token" not in data:
        die(f"刷新 access_token 失败：HTTP {status} {json.dumps(data, ensure_ascii=False)[:300]}\n"
            "  ← refresh_token 失效（改过密码 / 授权超过 6 个月没用 / 客户端被删），重新 login")
    return data["access_token"]


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth2callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        cls = type(self)
        cls.code = (qs.get("code") or [None])[0]
        cls.error = (qs.get("error") or [None])[0]
        msg = "授权成功，可以关闭这个页面回到终端。" if cls.code else f"授权失败：{cls.error}"
        html = f"<html><meta charset='utf-8'><body style='font:16px -apple-system;padding:40px'>{msg}</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, *a):
        pass


def cmd_login(args):
    saved = load_json(CREDS_PATH, {}) or {}
    client_id = args.client_id or saved.get("client_id") or os.environ.get("CWS_CLIENT_ID", "")
    client_secret = args.client_secret or saved.get("client_secret") or os.environ.get("CWS_CLIENT_SECRET", "")

    if not client_id:
        print("在 Google Cloud 建一个 OAuth 客户端（类型：桌面应用），把 client_id 粘进来。")
        print("步骤见 docs/CWS_PUBLISH.md；下面只填一次，之后存在本机。")
        client_id = input("client_id: ").strip()
    if not client_secret:
        client_secret = input("client_secret: ").strip()
    if not client_id or not client_secret:
        die("client_id / client_secret 不能为空")

    url = build_auth_url(client_id)
    print("\n浏览器会打开授权页；没打开就手动访问：\n  " + url + "\n")
    print("登录要发插件的那个 Google 账号，点「继续/允许」。")

    http.server.HTTPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler) as srv:
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        if not args.no_browser:
            webbrowser.open(url)
        print(f"等待回调（127.0.0.1:{REDIRECT_PORT}，最多 180 秒）…")
        t.join(timeout=180)

    if not _CallbackHandler.code:
        die("没收到授权码（超时或被拒绝）。重跑 login，或换 --no-browser 手动粘贴。")

    data = exchange_code(client_id, client_secret, _CallbackHandler.code)
    publisher_id = args.publisher_id or saved.get("publisher_id") or ""
    save_creds({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": data["refresh_token"],
        "publisher_id": publisher_id,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    ok(f"授权完成，凭证已存 {CREDS_PATH}")
    print("下一步：python3 scripts/cws.py probe     # 用真实接口验证 ID 对不对")


# ---------------------------------------------------------------- 商店操作

def item_ref(slug: str, cfg: dict, publisher_id: str) -> tuple[str, str]:
    items = cfg.get("items", {})
    if slug not in items:
        die(f"未知扩展 {slug}；可用：{', '.join(items)}")
    item_id = (items[slug].get("itemId") or "").strip()
    if not item_id:
        die(f"{slug} 的 itemId 还没填。打开开发者后台该条目的页面，"
            f"把地址栏 /devconsole/ 后面那串填进 {CONFIG_PATH.name} 的 \"{slug}\".itemId")
    pid = (publisher_id or cfg.get("publisherId") or "").strip()
    if not pid:
        die("缺 publisherId：在 scripts/cws-items.json 或 CWS_PUBLISHER_ID 里设置")
    return pid, item_id


def api_call(path: str, token: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None, what: str = "请求") -> dict:
    h = {"Authorization": f"Bearer {token}"}
    h.update(headers or {})
    status, raw = http_request(f"{API}{path}", method, body, h)
    data = parse_json(raw, what)
    if status >= 400:
        die(google_error(status, data, what))
    return data


def cmd_probe(args):
    cfg = config()
    token = access_token()
    pid = args.publisher or creds()["publisher_id"] or cfg.get("publisherId", "")
    if not pid:
        die("缺 publisherId")
    print(f"publisherId = {pid}\n")
    bad = 0
    for slug, meta in cfg.get("items", {}).items():
        iid = (meta.get("itemId") or "").strip()
        if not iid:
            print(f"· {slug:22s} 未配置 itemId")
            bad += 1
            continue
        status, raw = http_request(
            f"{API}/v2/publishers/{pid}/items/{iid}:fetchStatus", "GET", None,
            {"Authorization": f"Bearer {token}"})
        data = parse_json(raw, "fetchStatus")
        if status == 200:
            print(f"✓ {slug:22s} {data.get('itemId', iid)}  "
                  f"taken_down={data.get('takenDown')} warned={data.get('warned')}")
        else:
            bad += 1
            print(f"✗ {slug:22s} {iid}  HTTP {status} — "
                  f"{(data.get('error') or {}).get('message', '')[:160]}")
    if bad:
        print("\n有条目对不上。注意：itemId 是「扩展 ID」，publisherId 是另一串 UUID，别弄反。")
        raise SystemExit(1)


def cmd_status(args):
    cfg = config()
    token = access_token()
    slugs = list(cfg.get("items", {})) if args.slug in (None, "all") else [args.slug]
    for slug in slugs:
        pid, iid = item_ref(slug, cfg, args.publisher)
        data = api_call(f"/v2/publishers/{pid}/items/{iid}:fetchStatus", token, what=f"{slug} 状态")
        print(f"[{slug}]")
        for k in ("itemId", "publicKey", "takenDown", "warned", "lastAsyncUploadState"):
            if k in data:
                print(f"   {k:22s} {data[k]}")
        print()


def build_zip(slug: str, no_build: bool) -> Path:
    pattern = str(ROOT / "dist" / f"abs-{slug}-*.zip")
    if not no_build:
        r = subprocess.run([sys.executable, str(PACKAGE_SCRIPT)], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode != 0:
            die(f"打包失败：{r.stderr.strip()[:500]}")
    zips = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not zips:
        die(f"没有 {slug} 的 zip（跑一次 python3 scripts/package_extensions.py）")
    return Path(zips[-1])


def cmd_upload(args):
    cfg = config()
    slugs = list(cfg.get("items", {})) if args.slug == "all" else [args.slug]
    token = "" if args.dry_run else access_token()  # dry-run 不碰网络
    failures = []
    for slug in slugs:
        pid, iid = item_ref(slug, cfg, args.publisher)
        zpath = build_zip(slug, args.no_build)
        size_kb = zpath.stat().st_size / 1024
        print(f"[{slug}] {zpath.name} ({size_kb:.0f} KB) → publishers/{pid}/items/{iid}")

        if args.dry_run:
            print("   （dry-run，不发送）")
            continue

        status, raw = http_request(
            f"{UPLOAD_API}/v2/publishers/{pid}/items/{iid}:upload?uploadType=media",
            "POST", zpath.read_bytes(),
            {"Authorization": f"Bearer {token}",
             "Content-Type": "application/zip",
             "X-Goog-Upload-Protocol": "raw"})
        data = parse_json(raw, "上传")
        if status >= 400:
            print("   " + google_error(status, data, "上传").replace("\n", "\n   "))
            failures.append(slug)
            continue
        state = data.get("uploadState")
        print(f"   上传：{state} 版本 {data.get('crxVersion')}")
        if state == "FAILED":
            failures.append(slug)
            continue

        if args.publish:
            # 还是草稿（从没发布过）就别提交审核了：商店会因为「商品详情不完整」直接拒，
            # 但上传本身已经成功。等后台补完商品详情、首次提交过之后，这里就会自动提交。
            status_body = api_call(
                f"/v2/publishers/{pid}/items/{iid}:fetchStatus", token, what="查状态")
            if not status_body.get("publishedItemRevisionStatus") and not args.force_publish:
                print("   ⚠ 该条目还是草稿（未发布过），跳过提交审核；"
                      "补齐商品详情后手动「提请审核」一次即可，之后就会自动走。")
                continue
            body = json.dumps({
                "publishType": "STAGED_PUBLISH" if args.staged else "DEFAULT_PUBLISH",
                "blockOnWarnings": bool(args.block_on_warnings),
            }).encode()
            pstatus, praw = http_request(
                f"{API}/v2/publishers/{pid}/items/{iid}:publish", "POST", body,
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            pdata = parse_json(praw, "提交审核")
            if pstatus >= 400:
                print("   " + google_error(pstatus, pdata, "提交审核").replace("\n", "\n   "))
                failures.append(slug)
                continue
            print(f"   提交：{pdata.get('state')}")
            warn = (pdata.get("warningInfo") or {}).get("warnings") or []
            for w in warn[:5]:
                print(f"   ⚠ {w}")

    if failures:
        die(f"有 {len(failures)} 个扩展没成功：{', '.join(failures)}")
    ok("全部完成")


def cmd_secrets(args):
    c = creds()
    cfg = load_json(CONFIG_PATH, {}) or {}
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not c[k]]
    if missing:
        die("本机还没完整凭证，先 login；缺：" + ", ".join(missing))
    print("把这 4 个值加到 GitHub 仓库 Settings → Secrets and variables → Actions：\n")
    print(f"CWS_CLIENT_ID      = {c['client_id']}")
    print(f"CWS_CLIENT_SECRET  = {c['client_secret']}")
    print(f"CWS_REFRESH_TOKEN  = {c['refresh_token']}")
    print(f"CWS_PUBLISHER_ID   = {c['publisher_id'] or cfg.get('publisherId', '')}")
    print("\n（这些等于你账号的上架权限，别贴到公开的地方，别提交进仓库。）")


def main():
    ap = argparse.ArgumentParser(description="Chrome Web Store 发布工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="一次性 OAuth 授权")
    p.add_argument("--client-id")
    p.add_argument("--client-secret")
    p.add_argument("--publisher-id")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("secrets", help="打印 GitHub Secrets 需要的值")
    p.set_defaults(func=cmd_secrets)

    p = sub.add_parser("probe", help="校验 publisherId / itemId")
    p.add_argument("--publisher")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("status", help="查看商店状态")
    p.add_argument("slug", nargs="?", default="all")
    p.add_argument("--publisher")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("upload", help="上传（可选提交审核）")
    p.add_argument("slug", help="扩展名或 all")
    p.add_argument("--publish", action="store_true", help="上传后提交审核")
    p.add_argument("--staged", action="store_true", help="只提交到预发布（不公开）")
    p.add_argument("--block-on-warnings", action="store_true")
    p.add_argument("--force-publish", action="store_true", help="草稿也强行提交审核")
    p.add_argument("--no-build", action="store_true", help="不重新打包，直接用 dist 里现成的 zip")
    p.add_argument("--publisher")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_upload)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
