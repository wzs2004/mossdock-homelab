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
