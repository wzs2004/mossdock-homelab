#!/usr/bin/env python3
"""按需把 The Economist 某一周的音频下载进 Audiobookshelf 书库。

只用标准库；由 Chrome 扩展经 Caddy（/__ext/econ）调用。
目录结构、文件命名、ID3v2.3 标签、metadata.json、sync-manifest.json、封面
都对齐历史上跑过的 weekly_sync.py，只是把 docker cp / sqlite3 换成直接写
挂载目录 + ABS HTTP API。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SITE = "https://monkfishare.gitlab.io/econaudio/assets/audio/economist_{}.json"
COVER = "https://www.economist.com/content-assets/images/{}_DE_US.jpg"
UA = "Mozilla/5.0 (compatible; personal-weekly-audio-sync/1.0)"

MIN_BYTES = 40_000
MAX_AUDIO = 200 * 1024 * 1024
MAX_COVER = 10 * 1024 * 1024
DOWNLOAD_WORKERS = 5
PROBE_WORKERS = 8
PROBE_TTL = 600
PROBE_ERROR_TTL = 60
PROBE_TIMEOUT = 30
MAX_WEEKS = 52
JOB_HISTORY = 20


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


LISTEN = (os.environ.get("ECON_LISTEN") or "0.0.0.0").strip() or "0.0.0.0"
PORT = _int_env("ECON_PORT", 13387)
TOKEN = (os.environ.get("ECON_TOKEN") or "").strip()
BOOKS = Path(os.environ.get("ECON_BOOKS") or "/audiobooks/The Economist - Weekly Editions")
STAGING = Path(os.environ.get("ECON_STAGING") or "/staging")
ABS_BASE_URL = (os.environ.get("ABS_BASE_URL") or "http://audiobookshelf:80").rstrip("/")
ABS_API_TOKEN = (os.environ.get("ABS_API_TOKEN") or "").strip()
ABS_LIBRARY_PATH = (os.environ.get("ABS_LIBRARY_PATH") or "/audiobooks").rstrip("/") or "/"

JOB_FIELDS = ("id", "edition", "state", "total", "completed", "failed",
              "message", "startedAt", "finishedAt")


def log(message: str) -> None:
    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 源站数据

def safe_name(s, limit=140):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s or "Untitled")[:limit].rstrip(" .")


def edition_candidates(today: dt.date, weeks: int):
    """最近 N 个周六；沿用历史脚本「周五就能看到周六刊」的提前量。"""
    upcoming = today + dt.timedelta(days=(5 - today.weekday()) % 7)
    if upcoming > today + dt.timedelta(days=1):
        upcoming -= dt.timedelta(days=7)
    return [upcoming - dt.timedelta(days=7 * n) for n in range(weeks)]


def request_json(url: str, timeout: int = 35):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def load_edition(day: str):
    """下载并校验一期节目单；校验规则与历史脚本一致。"""
    data = request_json(SITE.format(day))
    tracks = data.get("archive")
    if data.get("edition") != day or not isinstance(tracks, list) or not tracks:
        raise ValueError("Source edition JSON is missing or malformed: " + day)
    for track in tracks:
        if not all(track.get(k) for k in ("headline", "articleSection", "audio_url")):
            raise ValueError("Source track is incomplete")
        if not track["audio_url"].startswith("https://www.economist.com/content-assets/audio/"):
            raise ValueError("Unexpected audio host: " + str(track["audio_url"])[:120])
    return tracks


# ---------------------------------------------------------------- 探测缓存

_PROBE_LOCK = threading.Lock()
_PROBE_CACHE: dict[str, dict] = {}


def _cache_put(day: str, tracks, error, ttl: int) -> None:
    with _PROBE_LOCK:
        _PROBE_CACHE[day] = {"tracks": tracks, "error": error, "until": time.monotonic() + ttl}


def probe_edition(day: str):
    """返回 (tracks|None, error|None)；404 视为不存在，其他错误只记录。"""
    with _PROBE_LOCK:
        entry = _PROBE_CACHE.get(day)
        if entry is not None and entry["until"] > time.monotonic():
            return entry["tracks"], entry["error"]
        _PROBE_CACHE.pop(day, None)
    try:
        tracks = load_edition(day)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            _cache_put(day, None, None, PROBE_TTL)
            return None, None
        error = "HTTP %s" % exc.code
    except Exception as exc:  # 网络抖动等：记录但不拖垮整个接口
        error = ("%s: %s" % (type(exc).__name__, exc))[:200]
    else:
        _cache_put(day, len(tracks), None, PROBE_TTL)
        return len(tracks), None
    _cache_put(day, None, error, PROBE_ERROR_TTL)
    return None, error


def library_status(day: str) -> dict:
    """书库里的落地情况：是否已有目录、完成条数、是否已扫描。"""
    folder = BOOKS / day[:4] / day
    mp3s = []
    manifest = None
    try:
        if folder.is_dir():
            mp3s = [p for p in folder.glob("*.mp3") if p.is_file() and p.stat().st_size > MIN_BYTES]
            state_file = folder / "sync-manifest.json"
            if state_file.is_file():
                try:
                    manifest = json.loads(state_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = None
    except OSError as exc:
        log("读取书库目录失败：%s" % exc)
    completed = len(mp3s)
    scanned = False
    if isinstance(manifest, dict):
        if isinstance(manifest.get("completed"), int):
            completed = manifest["completed"]
        scanned = bool(manifest.get("scanned"))
    return {"inLibrary": bool(mp3s) or (folder.is_dir() and manifest is not None),
            "completed": completed, "scanned": scanned}


def list_editions(weeks: int) -> dict:
    days = [d.isoformat() for d in edition_candidates(dt.date.today(), weeks)]
    probed = {}
    with ThreadPoolExecutor(max_workers=max(1, min(PROBE_WORKERS, len(days)))) as pool:
        tasks = {pool.submit(probe_edition, day): day for day in days}
        for future in as_completed(tasks):
            day = tasks[future]
            try:
                probed[day] = future.result()
            except Exception as exc:
                probed[day] = (None, ("%s: %s" % (type(exc).__name__, exc))[:200])
    editions, errors = [], []
    for day in days:
        tracks, error = probed.get(day, (None, "probe skipped"))
        if error:
            errors.append({"edition": day, "error": error})
        if tracks is None:
            continue
        item = {"edition": day, "tracks": tracks}
        item.update(library_status(day))
        editions.append(item)
    payload = {"editions": editions}
    if errors:
        payload["errors"] = errors
    return payload


# ---------------------------------------------------------------- 下载 & 打标

def http_download(url: str, destination: Path, max_bytes: int, attempts: int = 3) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(request, timeout=120) as response, temp.open("wb") as out:
                written = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("文件超过上限 %d 字节" % max_bytes)
                    out.write(chunk)
            length = temp.stat().st_size
            if length < MIN_BYTES or length > max_bytes:
                raise ValueError("Invalid size %d for %s" % (length, url))
            return temp
        except urllib.error.HTTPError as exc:
            temp.unlink(missing_ok=True)
            last_error = "HTTP %s" % exc.code
            if exc.code in (400, 401, 403, 404, 410):
                break
        except Exception as exc:
            temp.unlink(missing_ok=True)
            last_error = str(exc) or type(exc).__name__
        if attempt < attempts:
            time.sleep(2)
    raise RuntimeError("下载失败：%s" % last_error)


def _synchsafe(size):
    return bytes(((size >> 21) & 127, (size >> 14) & 127, (size >> 7) & 127, size & 127))


def _text_frame(name, value):
    # ID3v2.3 UTF-16 frames are recognized by ABS and common MP3 players.
    value_bytes = b"\x01" + str(value).encode("utf-16")
    return name.encode("ascii") + len(value_bytes).to_bytes(4, "big") + b"\0\0" + value_bytes


def tag_mp3(source, destination, track, day, position, count):
    frames = b"".join([
        _text_frame("TIT2", track["headline"]),
        _text_frame("TPE1", "The Economist"),
        _text_frame("TALB", "The Economist Weekly Edition " + day),
        _text_frame("TCON", track["articleSection"]),
        _text_frame("TRCK", f"{position}/{count}"),
    ])
    with source.open("rb") as inp, destination.open("wb") as out:
        header = inp.read(10)
        if len(header) == 10 and header[:3] == b"ID3":
            old_size = sum((header[6 + i] & 127) << (21 - i * 7) for i in range(4))
            inp.seek(10 + old_size + (10 if header[5] & 16 else 0))
        else:
            inp.seek(0)
        # 标题帧在前，逐帧顺序与历史产物一致（TIT2/TPE1/TALB/TCON/TRCK）。
        out.write(b"ID3\x03\0\0" + _synchsafe(len(frames)) + frames)
        shutil.copyfileobj(inp, out, 1024 * 1024)


def import_track(folder: Path, day: str, position: int, count: int, track: dict):
    name = f"{position:03d} - {safe_name(track['headline'], 130)}.mp3"
    dest = folder / name
    if dest.is_file() and dest.stat().st_size > MIN_BYTES:
        return name, "already-downloaded"
    raw = folder / (name + ".source")
    temp_tagged = folder / (name + ".tagging")
    try:
        part = http_download(track["audio_url"], raw, MAX_AUDIO)
        with part.open("rb") as inp:
            magic = inp.read(3)
        if magic != b"ID3" and not (magic[:2] and magic[0] == 255 and magic[1] & 224 == 224):
            raise ValueError("Downloaded content is not MP3: " + name)
        tag_mp3(part, temp_tagged, track, day, position, count)
        os.replace(temp_tagged, dest)
        return name, "downloaded"
    finally:
        (folder / (name + ".source.part")).unlink(missing_ok=True)
        temp_tagged.unlink(missing_ok=True)


def cover_download(day: str, folder: Path) -> bool:
    dest = folder / "cover.jpg"
    if dest.exists() and dest.stat().st_size > 10_000:
        return True
    try:
        part = http_download(COVER.format(day.replace("-", "")), dest, MAX_COVER)
        with part.open("rb") as stream:
            if stream.read(3)[:2] != b"\xff\xd8":
                raise ValueError("Cover is not JPEG")
        os.replace(part, dest)
        return True
    except Exception as exc:
        log("封面获取失败，将下次重试：%s" % str(exc)[:160])
        return False


# ---------------------------------------------------------------- ABS 扫描

def abs_scan() -> dict:
    """通过 HTTP API 触发书库扫描；没有 token 时由调用方按“跳过”处理。"""
    if not ABS_API_TOKEN:
        raise RuntimeError("ABS_API_TOKEN 未配置，跳过扫描")
    headers = {"Authorization": "Bearer " + ABS_API_TOKEN, "Content-Type": "application/json"}
    request = urllib.request.Request(ABS_BASE_URL + "/api/libraries", headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        libraries = json.load(response).get("libraries", [])
    for library in libraries:
        if library.get("mediaType") != "book":
            continue
        if not any(str(f.get("fullPath", "")).rstrip("/") == ABS_LIBRARY_PATH
                   for f in library.get("folders", [])):
            continue
        url = ABS_BASE_URL + "/api/libraries/" + library["id"] + "/scan"
        request = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
        return {"libraryId": library["id"], "status": status}
    raise RuntimeError("没有找到挂载 %s 的 book 书库" % ABS_LIBRARY_PATH)


# ---------------------------------------------------------------- 任务状态

_JOBS_LOCK = threading.RLock()
_JOBS: dict[str, dict] = {}
_JOB_IDS: list[str] = []
_ACTIVE: dict[str, str] = {}


def _snapshot(job: dict) -> dict:
    return {key: job.get(key) for key in JOB_FIELDS}


def _prune_locked() -> None:
    overflow = len(_JOB_IDS) - JOB_HISTORY
    if overflow <= 0:
        return
    kept = []
    for job_id in _JOB_IDS:
        job = _JOBS.get(job_id)
        if overflow > 0 and job is not None and job.get("state") != "running":
            _JOBS.pop(job_id, None)
            overflow -= 1
        else:
            kept.append(job_id)
    _JOB_IDS[:] = kept


def _set_progress(job_id: str, completed: int, failed: int, total: int) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job["completed"] = int(completed)
        job["failed"] = int(failed)
        job["total"] = int(total)


def _set_message(job_id: str, message: str) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job.get("state") == "running":
            job["message"] = message[:500]


def _get_job(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return _snapshot(job) if job is not None else None


def _recent_jobs(limit: int = JOB_HISTORY) -> list[dict]:
    with _JOBS_LOCK:
        jobs = [_snapshot(_JOBS[job_id]) for job_id in reversed(_JOB_IDS) if job_id in _JOBS]
    return jobs[:limit]


def start_job(day: str, force: bool):
    """同一期已有任务在跑就复用；返回 (job_snapshot, reused)。"""
    with _JOBS_LOCK:
        existing = _ACTIVE.get(day)
        if existing and _JOBS.get(existing, {}).get("state") == "running":
            return _snapshot(_JOBS[existing]), True
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "edition": day, "state": "running", "total": 0,
               "completed": 0, "failed": 0, "message": "任务已创建，正在读取节目单",
               "startedAt": now_iso(), "finishedAt": None}
        _JOBS[job_id] = job
        _JOB_IDS.append(job_id)
        _ACTIVE[day] = job_id
        _prune_locked()
        snapshot = _snapshot(job)
    thread = threading.Thread(target=run_job, args=(job_id, day, force),
                              name="econ-job-" + job_id, daemon=True)
    thread.start()
    return snapshot, False


def run_job(job_id: str, day: str, force: bool) -> None:
    state, message = "error", "任务异常结束"
    try:
        state, message = sync_edition(
            day, force,
            lambda completed, failed, total: _set_progress(job_id, completed, failed, total),
            lambda note: _set_message(job_id, note))
    except Exception as exc:
        log("任务 %s 失败：%s: %s" % (job_id, type(exc).__name__, exc))
        state = "error"
        message = ("%s: %s" % (type(exc).__name__, exc))[:400]
    finally:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["state"] = state
                job["message"] = (message or "")[:500]
                job["finishedAt"] = now_iso()
            if _ACTIVE.get(day) == job_id:
                _ACTIVE.pop(day, None)
            _prune_locked()
        log("任务 %s（%s）%s：%s" % (job_id, day, state, (message or "")[:200]))


# ---------------------------------------------------------------- 同步逻辑

def sync_edition(day: str, force: bool, progress, note=lambda message: None):
    """照搬 weekly_sync.py 的 sync()：直接写挂载目录，不再 staging + docker cp。"""
    tracks = load_edition(day)
    count = len(tracks)
    progress(0, 0, count)
    album = "The Economist Weekly Edition " + day
    target = BOOKS / day[:4] / day
    state_file = target / "sync-manifest.json"
    old = None
    if state_file.is_file():
        try:
            old = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
    if not force and isinstance(old, dict) and old.get("expected") == count and old.get("completed") == count:
        notes = ["已同步 %s：%d 条；跳过重复下载" % (day, count)]
        if not old.get("scanned"):
            note("已同步 %d 条，正在补做扫描" % count)
            try:
                result = abs_scan()
                old["scanned"] = True
                state_file.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
                notes.append("已补做 Audiobookshelf 扫描（library=%s，HTTP %s）"
                             % (result["libraryId"], result["status"]))
            except Exception as exc:
                notes.append("扫描未完成：" + str(exc)[:160])
        progress(count, 0, count)
        return "done", "；".join(notes)
    if not BOOKS.is_dir():
        raise RuntimeError("书库目录不存在：%s" % BOOKS)
    log("期刊 %s，节目单 %d 条，目标：%s" % (day, count, target))
    target.mkdir(parents=True, exist_ok=True)
    note("正在下载封面和 %d 条音频" % count)
    ok_cover = cover_download(day, target)
    metadata = {
        "title": album,
        "subtitle": "The Economist, " + day,
        "author": "The Economist",
        "narrator": "The Economist",
        "publishedYear": day[:4],
        "description": f"{count} audio articles. Source: monkfishare.gitlab.io/econaudio",
        "genres": ["News", "Current Affairs", "Business"],
    }
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    failures: list[dict] = []
    finished = 0
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        tasks = {pool.submit(import_track, target, day, i, count, item): i
                 for i, item in enumerate(tracks, 1)}
        for job in as_completed(tasks):
            i = tasks[job]
            try:
                name, status = job.result()
                finished += 1
                progress(finished, len(failures), count)
                log("[%02d/%d] %s: %s" % (i, count, status, name))
            except Exception as exc:
                failures.append({"index": i, "headline": tracks[i - 1]["headline"], "error": str(exc)[:300]})
                progress(finished, len(failures), count)
                log("[%02d/%d] 下载失败: %s" % (i, count, str(exc)[:180]))
    present = sum(1 for p in target.glob("*.mp3") if p.stat().st_size > MIN_BYTES)
    note("正在写入 sync-manifest.json")
    manifest = {"edition": day, "expected": count, "completed": present,
                "cover": "original" if ok_cover else "missing", "errors": failures,
                "checkedAt": now_iso(), "source": SITE.format(day)}
    state_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(present, len(failures), count)
    if failures or present != count or not ok_cover:
        message = ("完成 %d/%d 条，封面：%s；失败 %d 条，可再次同步续传"
                   % (present, count, "已获取" if ok_cover else "缺失", len(failures)))
        return ("partial" if present else "error"), message
    note("文件已就位，正在触发 Audiobookshelf 扫描")
    try:
        result = abs_scan()
    except Exception as exc:
        log("扫描失败，文件已就位：%s" % str(exc)[:200])
        return "done", ("已入库 %d 条；扫描未完成：%s" % (present, str(exc)[:160]))
    manifest["scanned"] = True
    state_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log("已触发 Audiobookshelf 扫描：HTTP %s，library=%s" % (result["status"], result["libraryId"]))
    return "done", ("已入库 %d 条，封面已获取；已触发 Audiobookshelf 扫描（library=%s，HTTP %s）"
                    % (present, result["libraryId"], result["status"]))


# ---------------------------------------------------------------- HTTP

class EconHandler(BaseHTTPRequestHandler):
    server_version = "EconAudio/1.0"

    # -- 路由 ------------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            self._route(method)
        except ValueError as exc:
            self._fail(400, str(exc))
        except Exception as exc:  # 单请求出错不能影响服务
            log("请求异常 %s %s：%s: %s" % (method, self.path, type(exc).__name__, exc))
            self._fail(500, "服务内部错误")

    def _route(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/health":
            if method != "GET":
                return self._fail(405, "只支持 GET")
            return self._health()
        if not self._authorized():
            return self._fail(401, "缺少或错误的 X-Econ-Token")
        if path == "/api/editions":
            if method != "GET":
                return self._fail(405, "只支持 GET")
            return self._editions(parsed.query)
        if path == "/api/sync":
            if method != "POST":
                return self._fail(405, "只支持 POST")
            return self._sync()
        if path == "/api/jobs":
            if method != "GET":
                return self._fail(405, "只支持 GET")
            return self._reply(200, {"jobs": _recent_jobs()})
        match = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]{4,64})", path)
        if match:
            if method != "GET":
                return self._fail(405, "只支持 GET")
            job = _get_job(match.group(1))
            if job is None:
                return self._fail(404, "没有这个任务")
            return self._reply(200, job)
        return self._fail(404, "未知接口")

    # -- 具体接口 --------------------------------------------------------
    def _authorized(self) -> bool:
        if not TOKEN:
            return False
        supplied = self.headers.get("X-Econ-Token") or ""
        try:
            return secrets.compare_digest(TOKEN, supplied)
        except TypeError:
            return False

    def _health(self) -> None:
        books = False
        try:
            books = BOOKS.is_dir() and os.access(BOOKS, os.W_OK)
        except OSError:
            books = False
        self._reply(200, {"ok": True, "books": books, "abs": bool(ABS_API_TOKEN)})

    def _editions(self, query: str) -> None:
        raw = (parse_qs(query).get("weeks") or ["16"])[0]
        try:
            weeks = int(raw)
        except (TypeError, ValueError):
            raise ValueError("weeks 必须是整数")
        if weeks < 1:
            raise ValueError("weeks 必须大于 0")
        weeks = min(weeks, MAX_WEEKS)
        self._reply(200, list_editions(weeks))

    def _sync(self) -> None:
        body = self._json_body()
        day = str(body.get("edition") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("edition 必须是 YYYY-MM-DD")
        try:
            parsed = dt.date.fromisoformat(day)
        except ValueError as exc:
            raise ValueError("edition 不是合法日期：%s" % day) from exc
        if parsed.isoformat() != day:
            raise ValueError("edition 必须是 YYYY-MM-DD")
        job, reused = start_job(day, bool(body.get("force")))
        self._reply(200 if reused else 202, {"job": job["id"], "edition": day})

    # -- 工具 ------------------------------------------------------------
    def _json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Content-Length 无效")
        if length <= 0:
            raise ValueError("请求体为空")
        if length > 1_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _fail(self, status: int, message: str) -> None:
        self._reply(status, {"error": message})

    def log_message(self, fmt, *args):
        if os.environ.get("ECON_VERBOSE") == "1":
            super().log_message(fmt, *args)


def main() -> None:
    if not TOKEN:
        raise SystemExit("ECON_TOKEN 必须设置（扩展的 X-Econ-Token 就是它）")
    try:
        STAGING.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log("暂存目录不可用（忽略，同步直接写书库目录）：%s" % exc)
    if not BOOKS.is_dir():
        log("警告：书库目录还不存在：%s（/api/health 会报 books=false）" % BOOKS)
    try:
        server = ThreadingHTTPServer((LISTEN, PORT), EconHandler)
    except OSError as exc:
        raise SystemExit("无法监听 %s:%d：%s" % (LISTEN, PORT, exc))
    log("econaudio 监听 http://%s:%d；书库=%s；ABS 扫描=%s"
        % (LISTEN, PORT, BOOKS, "已配置" if ABS_API_TOKEN else "未配置（跳过）"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
