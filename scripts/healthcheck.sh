#!/bin/bash
# 检查容器与 HTTP 端点
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
python3 - <<'PY'
import json, subprocess, urllib.request, urllib.error
site = json.load(open('site.json')); p = site['ports']
# immich-gate 是按需唤醒的：冷启动要 10-15 秒，超时给足，否则会误报 FAIL
checks = [
    ('audiobookshelf', f"http://127.0.0.1:{p['abs']}/", 5),
    ('meta-bridge', f"http://127.0.0.1:{p.get('meta',13382)}/api/health", 5),
    ('transcription', f"http://127.0.0.1:{p['manager']}/api/health", 15),
    ('lr-bridge', f"http://127.0.0.1:{p['lr_bridge']}/", 5),
    ('econ-audio', f"http://127.0.0.1:{p.get('econ',13387)}/api/health", 5),
    ('immich-gate', f"http://127.0.0.1:{p['immich_public']}/", 40),
]
for name, url, timeout in checks:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            print(f'OK   {name:24} {r.status} {url}')
    except urllib.error.HTTPError as e:
        print(f'OK   {name:24} {e.code} {url}')
    except Exception as e:
        print(f'FAIL {name:24} {e} {url}')
PY
echo '--- containers ---'
docker ps --format '{{.Names}}\t{{.Status}}' | sort | grep -E 'audiobookshelf|immich|mossdock|abs-lr|econaudio' || true
