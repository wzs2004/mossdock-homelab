# transcription-manager

把有声书转写成 SRT 文稿，供 Language Reactor 等工具读取；同时负责元数据自动匹配。
对外通过 edge 的 `/__ext/asr` 反代访问，端口 13384 只绑定回环。

## 首次构建顺序

`app/Dockerfile.manager` 基于 `local/audiobookshelf-base:1`（含 whisper-cli 与 Python 依赖），
底座需要用 `app/Dockerfile.base` 先构建一次（需要能访问 GitHub 拉 whisper.cpp 源码）：

```bash
docker build -f app/Dockerfile.base -t local/audiobookshelf-base:1 app/
docker compose -f stacks/transcription-manager/compose.yaml up -d --build
```

底座已经存在时，只需第二条命令。

## 数据放在哪（对机械盘很重要）

运行时数据全部在 Docker 命名卷 `transcription-data`（内置 SSD）：

| 内容 | 位置 |
|---|---|
| `tasks.sqlite3` 任务库（高频小写入） | `/data` 卷 |
| `asr-*/segment.wav` 临时分段（每段 3.9 MB，用完即删） | `/data` 卷 |
| `book-srt-backups/` 发布前备份 | `/data` 卷 |
| `legacy-transcripts/` 整期字幕拆分缓存 | `/data` 卷 |
| **最终发布的 `.srt`** | 音轨旁（机械盘，ABS/LR 要读） |
| 媒体音频 | 机械盘（只读） |

把 `/data` 放在机械盘时，转写期间实测写入 **1.8 GiB/h**；迁到 SSD 后降到
**125 MiB/h**（约 15 倍），机械盘上只剩最终字幕这类少量顺序写。

备份保留策略（`prune_data`，启动时和每 6 小时执行）：

- `BACKUP_TTL_DAYS`（默认 30）：只保留最近 N 天的 SRT 备份与拆分缓存
- `BACKUP_MAX_GB`（默认 2）：备份总量超过上限时从最旧的删起
- 崩溃残留的 `asr-*` 临时目录超过 1 天自动清除

## Metal 加速（强烈建议开启）

Docker 容器拿不到 Apple GPU，容器内的 whisper.cpp 只能跑 CPU。实测同一段
60 秒音频：

| 路径 | 耗时 | 相对实时 |
|---|---|---|
| 容器内 CPU（4 线程 / 8 线程） | 178 s / 129 s | 0.3–0.5x |
| 宿主 Metal 常驻引擎 | **10.8 s** | **5.6x** |

差 12–16 倍。做法是把宿主上已编译的 Metal 版 whisper.cpp 跑成常驻用户服务，
容器通过 `NATIVE_ASR_URL` 调用（Docker Desktop 的 `host.docker.internal`
能访问宿主 127.0.0.1 端口）。

```bash
# 需要一份带 Metal 的 whisper.cpp 编译产物（build/bin/whisper-server 等）
python3 stacks/transcription-manager/native/install_native_asr.py
docker compose -f stacks/transcription-manager/compose.yaml up -d --force-recreate
```

脚本会：复制引擎到 `~/Library/Application Support/audiobookshelf-native-asr/`、
装两个纯 Python 依赖、生成密钥、写 `NATIVE_ASR_URL` / `NATIVE_ASR_KEY` 到本 stack 的
`.env`、注册 LaunchAgent `local.audiobookshelf.native-asr`（登录自启、挂了自动重启）。

验证：

```bash
KEY=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/Library/Application Support/audiobookshelf-native-asr/config.json')))['key'])")
curl -s -H "X-ASR-Key: $KEY" http://127.0.0.1:13385/health
# {"engine": "ggml-large-v3-turbo-q5_0.bin · resident Metal", ..., "ready": true}
```

**回退**：把 `.env` 里的 `NATIVE_ASR_URL` 置空并重建容器，即回到 Docker CPU 模式
（会明显变慢，不建议）。

## Whisper 模型

模型不随镜像分发，放在 `site.json` 的 `site.models_dir`（默认
`/Users/a997/Library/Application Support/mossdock/models`，在内置 SSD 上，
避免每次转写都从机械盘读 500 MB），容器内挂载为 `/models`。

缺失时容器日志与面板会报「找不到 Whisper 模型」。恢复：

```bash
mkdir -p "$HOME/Library/Application Support/mossdock/models"
curl -L -C - -o "$HOME/Library/Application Support/mossdock/models/ggml-large-v3-turbo-q5_0.bin" \
  https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
ls -lh "$HOME/Library/Application Support/mossdock/models/"   # 应为 547 MB
```

换模型时改 `.env` 的 `WHISPER_MODEL_FILE` 并保持同名文件在 `models_dir` 下。

## 必需的密钥（写进 `.env`，不提交）

```dotenv
ABS_API_TOKEN=...
EXTENSION_TOKEN=...     # 扩展用它调用 /__ext/asr
```

## 字幕规则

- 断句按整句：句末标点处才断，逗号尽量不断。上限由 `REGROUP_MAX_CHARS`（默认 220）
  和 `REGROUP_MAX_SECONDS`（默认 18 秒）控制。
- 数字统一转阿拉伯数字（`Chapter Two → Chapter 2`），中文数字同样转换。

## 注意

本 stack 的容器名是 `transcription-manager`。如果机器上还跑着旧项目
（`2026-11-07-audiobookshelf-docker`）的转写容器，两者都占用 13384，先停掉旧的再
`docker compose up -d`，否则端口冲突。
