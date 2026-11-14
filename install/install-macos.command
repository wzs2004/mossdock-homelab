#!/bin/bash
# 双击运行：下载并准备好三个 Audiobookshelf Chrome 扩展
set -uo pipefail
REPO="wzs2004/audiobookshelf-plugins"
DEST="$HOME/Downloads/AudiobookshelfPlugins"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> 准备目录 $DEST"
mkdir -p "$DEST"

echo "==> 获取最新版本"
JSON="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null || true)"
URLS="$(printf '%s' "$JSON" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    for a in d.get('assets',[]):
        if a.get('browser_download_url','').endswith('.zip'): print(a['browser_download_url'])
except Exception: pass
" 2>/dev/null || true)"

if [ -z "$URLS" ]; then
  echo "   未找到 Release，改用仓库内 dist/ 的包"
  BASE="https://raw.githubusercontent.com/$REPO/main/dist"
  URLS="$BASE/abs-book-matching-1.3.0.zip
$BASE/abs-transcription-manager-2.4.0.zip
$BASE/abs-language-reactor-1.3.0.zip"
fi

while IFS= read -r url; do
  [ -z "$url" ] && continue
  name="$(basename "$url")"
  echo "==> 下载 $name"
  curl -fL --retry 2 -o "$TMP/$name" "$url"
  slug="${name%.zip}"; slug="${slug%-*}"
  rm -rf "$DEST/$slug"
  mkdir -p "$DEST/$slug"
  unzip -qo "$TMP/$name" -d "$DEST/$slug"
  xattr -dr com.apple.quarantine "$DEST/$slug" 2>/dev/null || true
done <<< "$URLS"

echo "==> 打开 Chrome 扩展页和安装目录"
open -a "Google Chrome" "chrome://extensions/" 2>/dev/null || true
open "$DEST" 2>/dev/null || true

osascript <<'APPLESCRIPT' 2>/dev/null || true
tell application "System Events"
  display dialog "最后两步（Chrome 157 起不允许脚本直接装扩展）：
1) 在已打开的 Chrome「扩展程序」页右上角，打开「开发者模式」
2) 把 Downloads/AudiobookshelfPlugins 里的三个文件夹拖进页面

装好后打开扩展的「选项」，填入服务器地址和密钥即可。" buttons {"知道了"} default button 1 with title "Audiobookshelf 插件安装"
end tell
APPLESCRIPT

echo
echo "完成。安装目录：$DEST"
echo "Chrome 扩展页：chrome://extensions/"
