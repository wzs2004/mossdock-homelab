#!/usr/bin/env python3
"""把 extensions/ 下三个扩展（含转写）打包到 dist/（排除本地密钥），用于 Release 或商店。"""
import json, pathlib, shutil, zipfile, hashlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSIONS = {'book-matching': '1.6.0', 'language-reactor': '1.5.0',
            'transcription-manager': '1.1.0'}
EXCLUDE = {'auth.js', 'preset.js', '.DS_Store'}
OUT = ROOT / 'dist'
if OUT.exists(): shutil.rmtree(OUT)
OUT.mkdir()
for slug, version in VERSIONS.items():
    src = ROOT / 'extensions' / slug
    target = OUT / slug
    shutil.copytree(src, target, ignore=shutil.ignore_patterns(*EXCLUDE, '*.zip'))
    mf = target / 'manifest.json'
    data = json.loads(mf.read_text(encoding='utf-8'))
    src_version = data.get('version')
    if src_version != version:
        print(f'⚠ {slug}: manifest.json 是 {src_version}，VERSIONS 是 {version}；'
              f'以 VERSIONS 打包。发商店前请把两处对齐。')
    data['version'] = version
    mf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    zpath = OUT / f'abs-{slug}-{version}.zip'
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(target.rglob('*')):
            if f.is_file(): z.write(f, f.relative_to(target))
    shutil.rmtree(target)
    print(f'{zpath.name}: {zpath.stat().st_size/1024:.0f} KB  sha256={hashlib.sha256(zpath.read_bytes()).hexdigest()[:16]}')
