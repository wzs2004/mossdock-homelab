#!/usr/bin/env python3
"""一键换域名：把三个扩展的域名统一改成 SITE_DOMAIN。

用法:
    python3 scripts/apply_domain.py                 # 默认 books6.mossdock.autos
    python3 scripts/apply_domain.py books7.example.com
改完记得重新打包: python3 scripts/package_extensions.py
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOMAIN = (sys.argv[1] if len(sys.argv) > 1 else 'books6.mossdock.autos')
DOMAIN = DOMAIN.replace('https://', '').replace('http://', '').strip('/')
ORIGIN = f'https://{DOMAIN}'

# 扩展目录 -> config.js 里除 ABS_URL 外的第二个常量
EXTRA = {
    'book-matching': ('MANAGER_URL', "/__ext/asr"),
    'transcription-manager': ('MANAGER_URL', "/__ext/asr"),
    'language-reactor': ('BRIDGE_URL', "/__ext/lr"),
}

for slug, (extra_name, extra_path) in EXTRA.items():
    d = ROOT / 'extensions' / slug
    if not d.is_dir():
        print('skip missing', d); continue
    (d / 'config.js').write_text(
        f"/* 一键换域名：改这一行后运行 scripts/apply_domain.py */\n"
        f"var ABS_URL = '{ORIGIN}';\nvar {extra_name} = ABS_URL + '{extra_path}';\n", encoding='utf-8')
    mf = d / 'manifest.json'
    data = json.loads(mf.read_text(encoding='utf-8'))
    data['host_permissions'] = [f'{ORIGIN}/*']
    data.setdefault('icons', {'16': 'icons/icon16.png', '32': 'icons/icon32.png',
                              '48': 'icons/icon48.png', '128': 'icons/icon128.png'})
    mf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    html = d / 'options.html'
    if html.exists():
        text = html.read_text(encoding='utf-8')
        text = re.sub(r'https://[a-z0-9.-]+\.(autos|com|dev|app|cn)', ORIGIN, text)
        html.write_text(text, encoding='utf-8')
    print('updated', d.relative_to(ROOT))
