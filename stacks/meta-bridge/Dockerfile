# 离线构建：复用本机已有的 LR 桥接镜像（python:3.12-alpine + ffmpeg）
FROM 2026-11-07-tailscale-audiobookshelf-langiage-reactor-abs-lr-bridge:latest
WORKDIR /app
COPY app.py /app/app.py
ENV PYTHONUNBUFFERED=1 PORT=13382
CMD ["python3", "-u", "/app/app.py"]
