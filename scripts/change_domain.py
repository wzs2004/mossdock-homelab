#!/usr/bin/env python3
"""一键换域名：更新 site.json 并重新渲染所有配置与扩展。

用法:
  python3 scripts/change_domain.py example.com          # books6/books 方案（IPv6 直连 + Tunnel）
  python3 scripts/change_domain.py example.com --plain  # 不带 6 后缀：books/photos/dav
"""
import json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
args = [a for a in sys.argv[1:] if not a.startswith('--')]
if not args:
    print(__doc__); sys.exit(1)
base = args[0].replace('https://', '').replace('http://', '').strip('/')
plain = '--plain' in sys.argv
sfx = '' if plain else '6'
site = json.loads((ROOT/'site.json').read_text())
site['domains'].update({
    'base': base,
    'books': f'books{sfx}.{base}', 'photos': f'photos{sfx}.{base}', 'dav': f'dav{sfx}.{base}',
    'books_tunnel': f'books.{base}', 'photos_tunnel': f'photos.{base}',
    'dav_tunnel': f'dav.{base}', 'files_tunnel': f'files.{base}',
})
(ROOT/'site.json').write_text(json.dumps(site, ensure_ascii=False, indent=2) + '\n')
for script in ('render.py', 'package_extensions.py'):
    subprocess.run([sys.executable, str(ROOT/'scripts'/script)], check=True)
print('\n新域名:')
for k, v in site['domains'].items(): print(f'  {k:14} {v}')
print('\n下一步：')
print('  1. DNS：把上面几个域名指向这台机器（AAAA 用 ddns.py，或 Tunnel CNAME）')
print('  2. 证书：重新签发并放到', site['certificate']['dir'])
print('  3. docker compose -f stacks/edge/compose.yaml up -d --force-recreate')
print('  4. 在 chrome://extensions 里点三个扩展的刷新')
