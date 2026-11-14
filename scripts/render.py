#!/usr/bin/env python3
"""根据 site.json 渲染全部配置：Caddyfile、cloudflared、各 stack 的 .env、扩展 config.js。

用法: python3 scripts/render.py [--check]
  --check  只检查生成结果与现有文件是否一致，不写入
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK = '--check' in sys.argv
site = json.loads((ROOT / 'site.json').read_text())
D, P, S, N = site['domains'], site['ports'], site['site'], site['networks']

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if CHECK:
        old = path.read_text() if path.exists() else None
        print(('OK   ' if old == text else 'DIFF ') + str(path.relative_to(ROOT)))
        return
    path.write_text(text, encoding='utf-8')

# ---------- Caddy / cloudflared ----------
vals = {
    'accel_books': P['accel_books'], 'accel_photos': P['accel_photos'],
    'meta_port': P.get('meta', 13382), 'lr_bridge_port': P['lr_bridge'],
    'manager_port': P.get('manager', 13384),
    'books_domain': D['books'], 'photos_domain': D['photos'], 'dav_domain': D['dav'],
    'cert_fullchain': 'fullchain.cer', 'cert_key': D['dav'].replace('.', '.') + '.key',
    'tunnel_id': site['cloudflare']['tunnel_id'],
    'books_tunnel': D['books_tunnel'], 'photos_tunnel': D['photos_tunnel'],
    'dav_tunnel': D['dav_tunnel'], 'files_tunnel': D['files_tunnel'],
}
def render_tpl(name):
    text = (ROOT / 'stacks/edge/templates' / name).read_text()
    for k, v in vals.items():
        text = text.replace('{{%s}}' % k, str(v))
    return text

write(ROOT / 'stacks/edge/Caddyfile', render_tpl('accelerator.Caddyfile.tmpl'))
write(ROOT / 'stacks/edge/ipv6/Caddyfile', render_tpl('ipv6.Caddyfile.tmpl'))
write(ROOT / 'stacks/edge/cloudflared/config.yml', render_tpl('cloudflared.yml.tmpl'))

# ---------- .env（保留已有密钥） ----------
def merge_env(path, values):
    current = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.split('=', 1); current[k.strip()] = v
    current.update({k: str(v) for k, v in values.items()})
    order = list(values) + [k for k in current if k not in values]
    text = '# 由 scripts/render.py 从 site.json 生成；密钥类请直接改本文件，勿提交\n'
    text += '\n'.join(f'{k}={current[k]}' for k in order if k in current and current[k] != '') + '\n'
    write(path, text)

ABS = f"{N['abs']}"
merge_env(ROOT/'stacks/audiobookshelf/.env', {
    'TZ': S['timezone'], 'ABS_PORT': P['abs'],
    'ABS_DATA': f"{S['data_root']}/Audiobookshelf", 'ABS_NETWORK': ABS})
merge_env(ROOT/'stacks/transcription-manager/.env', {
    'TZ': S['timezone'], 'MANAGER_PORT': P.get('manager', 13384),
    'ABS_DATA': f"{S['data_root']}/Audiobookshelf",
    'MANAGER_DATA': f"{S['data_root']}/Audiobookshelf/transcription-manager",
    'MODEL_DIR': S.get('models_dir', f"{S['data_root']}/whisper-models"),
    'ABS_NETWORK': ABS,
    'WHISPER_MODEL_FILE': 'ggml-large-v3-turbo-q5_0.bin',
    'WHISPER_LANGUAGE': 'en', 'WHISPER_THREADS': 4,
    'AUTO_SCAN_SECONDS': 60, 'AUTO_SETTLE_SECONDS': 300})
# ---------- 扩展 config.js ----------
origin = f"https://{D['books']}"
for slug, extra in [('book-matching', ('MANAGER_URL', '/__ext/meta')),
                    ('language-reactor', ('BRIDGE_URL', '/__ext/lr')),
                    ('transcription-manager', ('MANAGER_URL', '/__ext/asr'))]:
    write(ROOT/'extensions'/slug/'config.js',
          f"/* 由 scripts/render.py 生成：改 site.json 后重跑即可 */\n"
          f"var ABS_URL = '{origin}';\nvar {extra[0]} = ABS_URL + '{extra[1]}';\n")

# ---------- LR bridge config.json ----------
key = ''
cfg = ROOT/'stacks/lr-bridge/config.json'
if cfg.exists():
    try: key = json.loads(cfg.read_text()).get('key', '')
    except Exception: pass
if not key:
    env = ROOT/'stacks/lr-bridge/.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith('LR_BRIDGE_KEY='): key = line.split('=', 1)[1]
write(cfg, json.dumps({'key': key, 'library': '/audiobooks', 'port': P['lr_bridge']}, indent=2) + '\n')

# ---------- 安装脚本同步到 Pages ----------
src = ROOT/'install/install-macos.command'
if src.exists():
    write(ROOT/'docs/install-macos.command', src.read_text())

print('render done' + (' (check only)' if CHECK else ''))
