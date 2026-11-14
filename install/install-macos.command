#!/bin/bash
# 双击运行：下载 Audiobookshelf 工具箱（三合一扩展）并准备安装
set -uo pipefail
REPO="wzs2004/mossdock-homelab"
DEST="$HOME/Downloads/AudiobookshelfPlugins"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$DEST"

echo "==> 下载插件包"
if ! curl -fL --retry 2 -o "$TMP/repo.zip" "https://codeload.github.com/$REPO/zip/refs/heads/main"; then
  echo "下载失败，请检查网络"; read -r -p "回车退出"; exit 1
fi
unzip -q "$TMP/repo.zip" -d "$TMP/src"

# 只装三合一的 toolkit，旧的三个独立扩展不再分发
ZIPS=$(find "$TMP/src" -path '*/dist/abs-toolkit-*.zip' -maxdepth 5 | sort | tail -1)
[ -z "$ZIPS" ] && { echo "包内未找到 dist/abs-toolkit-*.zip"; exit 1; }
while IFS= read -r z; do
  [ -z "$z" ] && continue
  name="$(basename "$z")"
  slug="toolkit"
  echo "==> 准备 $name"
  rm -rf "$DEST/$slug"; mkdir -p "$DEST/$slug"
  unzip -qo "$z" -d "$DEST/$slug"
  xattr -dr com.apple.quarantine "$DEST/$slug" 2>/dev/null || true
done <<< "$ZIPS"

open -a "Google Chrome" "chrome://extensions/" 2>/dev/null || true
open "$DEST" 2>/dev/null || true

osascript <<'APPLESCRIPT' 2>/dev/null || true
tell application "System Events"
  display dialog "最后两步（Chrome 137 起禁止商店外静默安装）：
1) 在 chrome://extensions 右上角打开「开发者模式」
2) 点「加载已解压的扩展程序」，选 Downloads/AudiobookshelfPlugins/toolkit

装好后点扩展图标，填服务器地址和四个密钥（转写 / 元数据 / 桥接 / 经济学人）。" buttons {"知道了"} default button 1 with title "Audiobookshelf 工具箱安装"
end tell
APPLESCRIPT
echo; echo "完成：$DEST"
