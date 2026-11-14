# immich-gate: 极简按需启停网关
# 只用 Python 标准库，不装任何第三方依赖。
#
# 注意：刻意不加 HEALTHCHECK。
# 若健康检查去连接 2283，就等于每 30 秒"访问"一次自己，
# 会让网关把 Immich 反复唤醒，空闲休眠永远无法生效。
FROM alpine:3.20

RUN apk add --no-cache python3 tzdata

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app
COPY gate.py /app/gate.py

EXPOSE 2283

CMD ["python3", "-u", "/app/gate.py"]
