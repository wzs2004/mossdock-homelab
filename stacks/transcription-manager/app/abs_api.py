"""Audiobookshelf API 与音频路径的通用辅助。

由原分章模块中抽出、剥离分章逻辑后保留的通用能力，
供元数据匹配（library_automation）、文稿工具（book_tools）、
媒体同步（media_sync）共用。
"""

import functools
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ABS_URL = os.environ.get("ABS_BASE_URL", "http://audiobookshelf:80").rstrip("/")
TOKEN = os.environ.get("ABS_API_TOKEN", "").strip()
DATA = Path(os.environ.get("DATA_DIR", "/data"))
ROOTS = (Path("/audiobooks"), Path("/audiobooks115"))
ROOT = ROOTS[0]


def request(path, method="GET", body=None):
    if not TOKEN:
        raise RuntimeError("ABS_API_TOKEN is missing; set it in .env and restart the container")
    address = ABS_URL + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": "Bearer " + TOKEN}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(address, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"ABS API {method} {path}: HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"ABS API connection error: {error.reason}") from error


def audio_path(value):
    if not isinstance(value, str) or not any(value.startswith(str(root) + "/") for root in ROOTS):
        raise ValueError("Audio path is outside configured audiobook roots")
    result = Path(value).resolve(strict=True)
    if not any(result.is_relative_to(root.resolve()) for root in ROOTS) or not result.is_file():
        raise ValueError("Audio path is not a regular file inside audiobook roots")
    return result


def display_path(path):
    for root in ROOTS:
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return str(path)


def book_tracks(book):
    media = book.get("media") or {}
    tracks = []
    for file in media.get("audioFiles") or []:
        if file.get("index", 0) < 0:
            continue
        metadata = file.get("metadata") or {}
        path = metadata.get("path")
        if not path:
            folder = book.get("path", "")
            path = folder + "/" + metadata.get("filename", "")
        tracks.append((file.get("index", 0), audio_path(path)))
    if not tracks and book.get("isFile"):
        tracks = [(0, audio_path(book["path"]))]
    tracks.sort(key=lambda pair: (pair[0], str(pair[1])))
    return [path for _, path in tracks]


def probe_duration(path):
    stat = path.stat()
    return cached_duration(str(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=10000)
def cached_duration(filename, size, mtime):
    path = Path(filename)
    output = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True
    ).strip()
    return float(output)


def item_endpoint(item_id):
    return "/api/items/" + urllib.parse.quote(item_id, safe="")
