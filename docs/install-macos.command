#!/bin/bash
# 双击运行：下载 Audiobookshelf Chrome 扩展并准备安装
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

ZIPS=$(find "$TMP/src" -path '*/dist/*.zip' -maxdepth 5 | sort)
[ -z "$ZIPS" ] && { echo "包内未找到 dist/*.zip"; exit 1; }
while IFS= read -r z; do
  [ -z "$z" ] && continue
  name="$(basename "$z")"; slug="${name%.zip}"; slug="${slug%-*}"
  echo "==> 安装 $slug"
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
2) 把 Downloads/AudiobookshelfPlugins 里的扩展文件夹拖进页面

装好后打开每个扩展的「选项」，填服务器地址和密钥。" buttons {"知道了"} default button 1 with title "Audiobookshelf 插件安装"
end tell
APPLESCRIPT
echo; echo "完成：$DEST"
