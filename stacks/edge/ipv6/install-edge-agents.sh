#!/bin/bash
# 安装 IPv6 直连所需的后台任务（443 relay + DDNS/证书维护）
# relay 需要绑定 443，必须用 sudo 安装为 LaunchDaemon（或由 root 运行）。
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"; DAEMONS="/Library/LaunchDaemons"
mkdir -p "$AGENTS"

sed "s|__EDGE_DIR__|$DIR|g" "$DIR/launchd/com.mossdock.ipv6maintenance.plist.tmpl" > "$AGENTS/com.mossdock.ipv6maintenance.plist"
launchctl unload "$AGENTS/com.mossdock.ipv6maintenance.plist" 2>/dev/null || true
launchctl load "$AGENTS/com.mossdock.ipv6maintenance.plist"

sed "s|__EDGE_DIR__|$DIR|g" "$DIR/launchd/com.mossdock.ipv6relay.plist.tmpl" > /tmp/com.mossdock.ipv6relay.plist
echo "relay 需要 root 绑定 443："
sudo cp /tmp/com.mossdock.ipv6relay.plist "$DAEMONS/com.mossdock.ipv6relay.plist"
sudo launchctl unload "$DAEMONS/com.mossdock.ipv6relay.plist" 2>/dev/null || true
sudo launchctl load "$DAEMONS/com.mossdock.ipv6relay.plist"
echo "已安装。日志: $DIR/relay.log / $DIR/maintenance.log"
