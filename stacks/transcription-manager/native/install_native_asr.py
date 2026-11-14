#!/usr/bin/env python3
"""把本机 Metal 版 whisper.cpp 装成常驻用户服务，供转写容器加速使用。

为什么要单独起服务：
    Docker Desktop 的容器拿不到 Apple GPU，容器内 whisper.cpp 只能跑 CPU
    （实测 60 秒音频要 ~170 秒）。宿主上同样是 whisper.cpp，但走 Metal，
    同一段音频 ~4 秒。这个服务把宿主引擎以常驻进程形式暴露给容器，
    容器通过 NATIVE_ASR_URL 调用。

依赖：一个已编译好的 whisper.cpp（带 Metal），目录下需要
    build/bin/whisper-server、build/bin/whisper-vad-speech-segments，以及 *.dylib。

用法：
    WHISPER_BUILD_DIR=~/path/to/whisper.cpp/build \
        python3 stacks/transcription-manager/native/install_native_asr.py
"""
import json
import os
import plistlib
import secrets
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent.parent.parent
SITE = json.loads((ROOT / 'site.json').read_text())
MODELS = Path(SITE['site'].get('models_dir', Path.home() / 'Library/Application Support/mossdock/models'))
MODEL = MODELS / os.environ.get('WHISPER_MODEL_FILE', 'ggml-large-v3-turbo-q5_0.bin')
VAD = MODELS / os.environ.get('WHISPER_VAD_FILE', 'ggml-silero-v6.2.0.bin')
BUILD = Path(os.environ.get(
    'WHISPER_BUILD_DIR',
    Path.home() / 'Documents/Codex/2026-11-06-mac-docker-audiobookshelf-tailscale/whisper.cpp/build'))
RUNTIME = Path.home() / 'Library/Application Support/audiobookshelf-native-asr'
LABEL = 'local.audiobookshelf.native-asr'

for path, label in ((MODEL, 'Whisper 模型'), (VAD, 'VAD 模型'), (BUILD / 'bin/whisper-server', 'whisper-server')):
    if not path.exists():
        raise SystemExit(f'缺少{label}：{path}')

RUNTIME.mkdir(parents=True, exist_ok=True)
(RUNTIME / 'bin').mkdir(exist_ok=True)
(RUNTIME / 'lib').mkdir(exist_ok=True)

shutil.copy2(BASE / 'native_asr.py', RUNTIME / 'native_asr.py')
shutil.copy2(ROOT / 'stacks/transcription-manager/app/asr_quality.py', RUNTIME / 'asr_quality.py')
subprocess.run(['/usr/bin/python3', '-m', 'pip', 'install', '--upgrade', '--quiet',
                '--target', str(RUNTIME / 'deps'), '-r', str(BASE / 'requirements-quality.txt')], check=True)

for name in ('whisper-server', 'whisper-vad-speech-segments', 'whisper-cli'):
    source = BUILD / 'bin' / name
    if source.exists():
        shutil.copy2(source, RUNTIME / 'bin' / name)
for file in BUILD.rglob('*.dylib'):
    target = RUNTIME / 'lib' / file.name
    if file.is_symlink():
        if not target.exists():
            target.symlink_to(file.resolve().name)
    else:
        shutil.copy2(file, target)

config = RUNTIME / 'config.json'
key = json.loads(config.read_text())['key'] if config.exists() else secrets.token_urlsafe(36)
config.write_text(json.dumps({'key': key, 'model_path': str(MODEL),
                              'model_name': MODEL.name, 'vad_model': str(VAD)}))
config.chmod(0o600)

# 容器侧的接入参数写进 stack 的 .env（该文件不提交）
env = ROOT / 'stacks/transcription-manager/.env'
lines = [line for line in env.read_text().splitlines()
         if not line.startswith(('NATIVE_ASR_URL=', 'NATIVE_ASR_KEY='))]
env.write_text('\n'.join(lines) + '\nNATIVE_ASR_URL=http://host.docker.internal:13385\n'
               f'NATIVE_ASR_KEY={key}\n')

plist = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
plist.write_bytes(plistlib.dumps({
    'Label': LABEL,
    'ProgramArguments': ['/usr/bin/python3', '-u', str(RUNTIME / 'native_asr.py')],
    'WorkingDirectory': str(RUNTIME),
    'EnvironmentVariables': {'DYLD_LIBRARY_PATH': str(RUNTIME / 'lib')},
    'RunAtLoad': True, 'KeepAlive': True,
    'StandardOutPath': str(RUNTIME / 'service.log'),
    'StandardErrorPath': str(RUNTIME / 'service.log'),
}))
domain = f'gui/{os.getuid()}'
subprocess.run(['launchctl', 'bootout', domain, str(plist)], capture_output=True)
subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)

print(f'Metal 常驻服务已安装：127.0.0.1:13385')
print(f'模型：{MODEL.name}')
print('容器侧已写入 NATIVE_ASR_URL / NATIVE_ASR_KEY（stacks/transcription-manager/.env）')
