# Audiobookshelf Plugins

三个配套 Audiobookshelf 使用的 Chrome 扩展，合并在一个仓库里，方便安装和统一换域名。

| 扩展 | 作用 |
|---|---|
| **book-matching** | 书籍页「元数据匹配」：选来源、看全部候选、一键应用（写入前备份） |
| **transcription-manager** | 转写任务、SRT 字幕、章节与文稿管理 |
| **language-reactor** | 把单条音轨连同 SRT 字幕送进 Language Reactor |

后端服务（Whisper 转写、LR 字幕桥接）仍在各自的服务仓库，本仓库只放浏览器扩展和安装脚本。

## 安装（macOS）

Chrome 从 137 版起**不再允许从商店外静默安装扩展**，所以「一键」只能做到“脚本准备好 + 打开页面”，最后需要你点两下：

1. 下载并双击 [`install/install-macos.command`](install/install-macos.command)（首次可能需右键 → 打开）
   或直接运行：
   ```bash
   curl -fsSL https://wzs2004.github.io/audiobookshelf-plugins/install-macos.command -o /tmp/install.command && bash /tmp/install.command
   ```
2. Chrome 会打开 `chrome://extensions`，右上角打开 **开发者模式**
3. 把 `~/Downloads/AudiobookshelfPlugins/` 里的三个文件夹拖进页面
4. 打开每个扩展的「选项」，填服务器地址和密钥

> 也可以手动下载 [Releases](https://github.com/wzs2004/audiobookshelf-plugins/releases/latest) 里的 zip，解压后拖入。

## 换域名

所有扩展的域名集中写在各自的 `config.js`，用脚本一次改完：

```bash
python3 scripts/apply_domain.py books7.example.com
python3 scripts/package_extensions.py      # 重新出包到 dist/
```

然后重新加载扩展（chrome://extensions 里点刷新）。

## 目录结构

```
extensions/
  book-matching/          # config.js 里改域名
  transcription-manager/
  language-reactor/
scripts/
  apply_domain.py         # 一键换域名
  package_extensions.py   # 打包成 dist/*.zip
  make_icons.py           # 生成图标
install/
  install-macos.command   # 下载 + 解压 + 打开安装页面
docs/                     # GitHub Pages 安装页
dist/                     # 当前版本 zip（Release 附件同源）
```

## 开发

```bash
python3 scripts/apply_domain.py      # 改域名
python3 scripts/package_extensions.py
```

版本号在各 `extensions/*/manifest.json`，发布新版本时递增并打 tag。
