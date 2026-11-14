FROM python:3.12-alpine

WORKDIR /app
RUN apk add --no-cache ffmpeg
RUN pip install --no-cache-dir mutagen==1.47.0
COPY bridge.py srt_order.py /app/
CMD ["python", "bridge.py"]
