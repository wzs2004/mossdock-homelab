#!/bin/bash
# 检查容器与 HTTP 端点
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
python3 - <<'PY'
import json, subprocess, urllib.request, urllib.error
site = json.load(open('site.json')); p = site['ports']
checks = [
    ('audiobookshelf', f"http://127.0.0.1:{p['abs']}/"),
    ('meta-bridge', f"http://127.0.0.1:{p.get('meta',13382)}/api/health"),
    ('lr-bridge', f"http://127.0.0.1:{p['lr_bridge']}/"),
    ('immich-gate', f"http://127.0.0.1:{p['immich_public']}/"),
]
for name, url in checks:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            print(f'OK   {name:24} {r.status} {url}')
    except urllib.error.HTTPError as e:
        print(f'OK   {name:24} {e.code} {url}')
    except Exception as e:
        print(f'FAIL {name:24} {e} {url}')
PY
echo '--- containers ---'
docker ps --format '{{.Names}}\t{{.Status}}' | sort | grep -E 'audiobookshelf|immich|mossdock|abs-lr' || true
