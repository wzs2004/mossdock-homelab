#!/usr/bin/env python3
"""Local, opt-in transcription queue for every mounted Audiobookshelf library."""

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOTS = {"audiobooks": Path("/audiobooks").resolve(),
         "audiobooks115": Path("/audiobooks115").resolve()}
LIBRARY_NAMES = {"audiobooks": "The Economist · Audio Collections", "audiobooks115": "115 · 有声书"}
EXTENSION_TOKEN = os.environ.get("EXTENSION_TOKEN", "")
DATA = Path(os.environ.get("DATA_DIR", "/data"))
MODEL = Path(os.environ.get("WHISPER_MODEL", "/models/ggml-small.en.bin"))
LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "en").strip().lower()
THREADS = max(1, min(12, int(os.environ.get("WHISPER_THREADS", "4"))))
CHUNK_SECONDS = max(60, min(900, int(os.environ.get("CHUNK_SECONDS", "300"))))
PORT = int(os.environ.get("PORT", "13384"))
SUPPORTED = {".mp3", ".m4a", ".m4b", ".wma", ".aac", ".flac", ".wav", ".ogg", ".opus"}
DB = DATA / "tasks.sqlite3"
LOCK = threading.RLock()
WAKE = threading.Event()
CATALOG = {}
CATALOG_AT = 0.0
ACTIVE = None
ACTIVE_PATH = None
COMBINED_CACHE = {}
AUTO_TRANSCRIBE = os.environ.get("AUTO_TRANSCRIBE", "0") == "1"
AUTO_SETTLE = max(0, int(os.environ.get("AUTO_SETTLE_SECONDS", "300")))
AUTO_INTERVAL = max(5, int(os.environ.get("AUTO_SCAN_SECONDS", "60")))
AUTO_MAX_ATTEMPTS = 3
COMMAND_TIMEOUT = max(60, int(os.environ.get("ASR_COMMAND_TIMEOUT_SECONDS", "1800")))
NATIVE_ASR_URL = os.environ.get("NATIVE_ASR_URL", "").rstrip("/")
NATIVE_ASR_KEY = os.environ.get("NATIVE_ASR_KEY", "")
LIBRARY_AUTOMATION = {}
AUTO_STATE = {"last_scan": 0, "error": ""}


def database():
    connection = sqlite3.connect(DB, timeout=20)
    connection.row_factory = sqlite3.Row
    return connection


def execute(query, args=()):
    with LOCK:
        with database() as db:
            return db.execute(query, args).rowcount


def rows(query, args=()):
    with LOCK:
        with database() as db:
            return [dict(row) for row in db.execute(query, args).fetchall()]


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "transcripts").mkdir(exist_ok=True)
    (DATA / "logs").mkdir(exist_ok=True)
    with database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tasks (
            path TEXT PRIMARY KEY, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            updated REAL NOT NULL DEFAULT 0, started REAL NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS observations (
            path TEXT PRIMARY KEY, signature TEXT NOT NULL, folder_signature TEXT NOT NULL,
            stable_since REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS published_subtitles (
            path TEXT PRIMARY KEY, audio_signature TEXT NOT NULL, content_hash TEXT NOT NULL)""")
        db.execute("CREATE TABLE IF NOT EXISTS quality_requests (path TEXT PRIMARY KEY, requested REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS reset_requests (path TEXT PRIMARY KEY, requested REAL NOT NULL)")
        fields = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        if "started" not in fields:
            db.execute("ALTER TABLE tasks ADD COLUMN started REAL NOT NULL DEFAULT 0")
        # The original manager used relative paths from /audiobooks115.
        db.execute("UPDATE tasks SET path='audiobooks115/' || path WHERE path NOT LIKE 'audiobooks115/%' AND path NOT LIKE 'audiobooks/%'")
        db.execute("UPDATE tasks SET status='queued', progress=0, stage='等待恢复' WHERE status='running'")


def safe_audio(relative):
    if not isinstance(relative, str) or not relative or relative.startswith("/") or "\x00" in relative:
        raise ValueError("无效的音轨路径")
    library_id, sep, subpath = relative.partition("/")
    if not sep or not subpath or library_id not in ROOTS:
        raise ValueError("找不到音轨所属书库")
    root = ROOTS[library_id].resolve()
    path = (root / subpath).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in SUPPORTED:
        raise ValueError("音轨不存在或不在已挂载书库内")
    return path


def scan(force=False):
    global CATALOG, CATALOG_AT
    with LOCK:
        # Keep the live catalog reasonably fresh even between automatic scans.
        if not force and CATALOG_AT > 0 and time.monotonic() - CATALOG_AT < 10:
            return dict(CATALOG)
        found = {}
        for library_id, root in ROOTS.items():
            if not root.is_dir():
                continue
            for folder, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(folder) / d).is_symlink()]
                for name in files:
                    path = Path(folder) / name
                    if path.suffix.lower() not in SUPPORTED or path.is_symlink():
                        continue
                    subpath = path.relative_to(root)
                    relative = library_id + "/" + subpath.as_posix()
                    parts = subpath.parts
                    book_name = "/".join(parts[:2]) if len(parts) >= 3 else (parts[0] if len(parts) >= 2 else "根目录")
                    if library_id == "audiobooks" and len(parts) >= 2:
                        book_name = "/".join(parts[:-1])
                    book = library_id + "/" + book_name
                    found[relative] = {"path": relative, "name": name, "book": book,
                                       "library": library_id, "library_name": LIBRARY_NAMES[library_id]}
        CATALOG = found
        CATALOG_AT = time.monotonic()
        return dict(found)


def fingerprint(path):
    stat = path.stat()
    # Matches chapter_worker's cache key, including the in-container path.
    # Use the absolute path inside ABS, matching chapter_worker's existing cache.
    source = path.as_posix()
    payload = [[source, stat.st_size, stat.st_mtime_ns]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def subtitle_paths(path):
    digest = fingerprint(path)
    return [path.with_suffix(".srt"), DATA / "transcripts" / (digest + ".srt"),
            Path("/chapter-transcripts") / (digest + ".srt")]


def stale_generated_subtitle(path):
    existing = path.with_suffix(".srt")
    record = rows("SELECT * FROM published_subtitles WHERE path=?", (str(path),))
    return bool(record and existing.is_file() and record[0]["audio_signature"] != fingerprint(path)
                and record[0]["content_hash"] == hashlib.sha256(existing.read_bytes()).hexdigest())


def subtitle(path):
    # Explicit reset must never reuse old read-only/legacy subtitles while ASR runs.
    if rows("SELECT path FROM reset_requests WHERE path=?", (relative_audio(path),)):
        return None
    for candidate in subtitle_paths(path):
        try:
            if candidate == path.with_suffix(".srt") and stale_generated_subtitle(path):
                continue
            if candidate.is_file() and candidate.stat().st_size > 0 and cues(candidate.read_text(encoding="utf-8-sig", errors="replace"), 0):
                return candidate
        except OSError:
            continue
    try:
        return combined_subtitle(path)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Legacy subtitle unavailable for {path}: {exc}", flush=True)
        return None


def relative_audio(path):
    path = path.resolve()
    for library, root in ROOTS.items():
        if path.is_relative_to(root.resolve()):
            return library + '/' + path.relative_to(root.resolve()).as_posix()
    return ''


def combined_subtitle(path):
    """Reuse legacy issue transcripts only after validating the complete timeline."""
    source = path.parent / "transcript.srt"
    if not source.is_file() or source.is_symlink():
        return None
    tracks = sorted(p for p in path.parent.iterdir()
                    if p.is_file() and p.suffix.lower() in SUPPORTED and not p.is_symlink())
    if path not in tracks:
        return None
    try:
        from srt_order import read_order
        tracks, srt_status = read_order(source, tracks)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if srt_status == 'partial':
        return None
    signature = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in [source, *tracks]]
    key = hashlib.sha256(json.dumps([str(path.parent), signature], ensure_ascii=False).encode()).hexdigest()
    with LOCK:
        cached = COMBINED_CACHE.get(path.parent)
        if cached and cached[0] == key:
            return cached[1].get(path.name)
        results = {}
        folder = DATA / "legacy-transcripts" / key
        marker = folder / "index.json"
        if marker.is_file():
            names = json.loads(marker.read_text())
            results = {name: folder / filename for name, filename in names.items()
                       if (folder / filename).is_file()}
        else:
            entries = cues(source.read_text(encoding="utf-8-sig"), 0)
            durations = []
            for track in tracks:
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                        "-of", "default=nw=1:nk=1", str(track)],
                                       capture_output=True, text=True, check=True, timeout=30)
                duration = float(probe.stdout.strip())
                if duration <= 0 or not duration < float("inf"):
                    return None
                durations.append(duration)
            total = sum(durations)
            if entries and max(b for _, b, _ in entries) <= total + 2 and (srt_status == 'complete' or abs(total - max(b for _, b, _ in entries)) <= max(40, total * .02)):
                folder.mkdir(parents=True, exist_ok=True)
                offset = 0.0
                for index, (track, duration) in enumerate(zip(tracks, durations)):
                    selected = [(max(0, a-offset), min(duration, b-offset), text)
                                for a, b, text in entries if b > offset and a < offset+duration]
                    if selected:
                        target = folder / f"{index}.srt"
                        temp = target.with_suffix(".tmp")
                        temp.write_text(render_srt(selected), encoding="utf-8")
                        temp.replace(target)
                        results[track.name] = target
                    offset += duration
                temp = marker.with_suffix(".tmp")
                temp.write_text(json.dumps({name: file.name for name, file in results.items()}))
                temp.replace(marker)
        COMBINED_CACHE[path.parent] = (key, results)
        return results.get(path.name)


def model_error():
    if not MODEL.is_file():
        return "找不到 Whisper 模型：" + str(MODEL)
    if MODEL.name.endswith(".en.bin") and LANGUAGE != "en":
        return "当前模型仅支持英语。识别中文请挂载多语言模型，并修改 WHISPER_LANGUAGE。"
    return ""


def history_subtitle(relative):
    if not rows("SELECT path FROM tasks WHERE path=?",(relative,)):
        return None
    observations=rows("SELECT signature FROM observations WHERE path=?",(relative,))
    if not observations or not re.fullmatch(r"[a-f0-9]{64}",observations[0]['signature']):
        return None
    file=DATA/'transcripts'/(observations[0]['signature']+'.srt')
    return file if file.is_file() and cues(file.read_text(errors='replace'),0) else None


def library():
    items = scan()
    tasks = {entry["path"]: entry for entry in rows("SELECT * FROM tasks")}
    books = {}
    summary = {key: 0 for key in ("done", "missing", "queued", "running", "failed", "cancelled")}
    now = time.time()
    visible_paths = set()
    for rel in sorted(items, key=str.casefold):
        item = dict(items[rel])
        try:
            path = safe_audio(rel)
        except (OSError, ValueError):
            continue
        visible_paths.add(rel)
        task = tasks.get(rel) or {}
        file_subtitle = subtitle(path)
        status = task.get("status", "missing")
        if status != "running" and file_subtitle:
            status = "done"
        elif status == "done" and not file_subtitle:
            status = "missing"
        progress = task.get("progress", 0) if status in {"queued", "running", "failed"} else (100 if status == "done" else 0)
        elapsed = max(0, int(now - (task.get("started") or task.get("updated") or now))) if status == "running" else 0
        remaining = int(elapsed * (100-progress) / progress) if status == "running" and progress >= 3 else None
        observation = rows("SELECT * FROM observations WHERE path=?", (rel,))
        observation = observation[0] if observation else {}
        stage = task.get("stage", "")
        if status == "missing" and AUTO_TRANSCRIBE:
            stage = "等待文件稳定后自动识别"
        if status == "failed" and observation.get("attempts", 0) < AUTO_MAX_ATTEMPTS and AUTO_TRANSCRIBE:
            stage = "等待自动重试"
        item.update(status=status, progress=progress,
                    stage=stage, attempts=observation.get("attempts", 0),
                    retry_at=observation.get("retry_at", 0), elapsed_seconds=elapsed, eta_seconds=remaining,
                    error=task.get("error", "") if status == "failed" else "",
                    has_subtitle=bool(file_subtitle), published=path.with_suffix(".srt").is_file(),
                    subtitle_revision=(f"{file_subtitle.stat().st_mtime_ns}:{file_subtitle.stat().st_size}"
                                       if file_subtitle else None),
                    has_log=task_log_path(rel).is_file())
        summary[status] += 1
        book = books.setdefault(item["book"], {"name": item["book"].split("/", 1)[1],
                                                      "id": item["book"], "library": item["library"],
                                                      "library_name": item["library_name"],
                                                      "tracks": [], "done": 0, "total": 0})
        book["tracks"].append(item)
        book["total"] += 1
        book["done"] += int(status == "done")
    # Retain historical task rows/logs in SQLite, but never advertise deleted
    # sources as current books or count them in the live library.
    historical_count = sum(1 for rel in tasks if rel not in visible_paths)
    return {"summary": summary, "count": len(visible_paths), "source_count":len(visible_paths),
            "history_count":historical_count, "books": sorted(books.values(), key=lambda x: (x["library"], x["name"].casefold())),
            "libraries": [{"id": key, "name": name} for key, name in LIBRARY_NAMES.items() if ROOTS[key].is_dir()],
            "model": MODEL.name, "language": LANGUAGE, "model_error": model_error(),
            "engine": "Metal 常驻加速" if NATIVE_ASR_URL else "Docker CPU",
            "library_automation": __import__("library_automation").status(visible_paths),
            "media_sync": __import__("media_sync").status(),
            "automation": {"enabled": AUTO_TRANSCRIBE, "settle_seconds": AUTO_SETTLE,
                           "max_attempts": AUTO_MAX_ATTEMPTS, **AUTO_STATE}}


def set_task(relative, status, progress=0, stage="", error=""):
    execute("""INSERT INTO tasks(path,status,progress,stage,error,updated,started) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET status=excluded.status,progress=excluded.progress,
               stage=excluded.stage,error=excluded.error,updated=excluded.updated,
               started=CASE WHEN excluded.status='queued' THEN 0
                            WHEN tasks.started=0 THEN excluded.started ELSE tasks.started END""",
            (relative, status, progress, stage, error[:1000], time.time(), time.time() if status == "running" else 0))


def task_log_path(relative):
    return DATA / "logs" / (hashlib.sha256(relative.encode("utf-8")).hexdigest() + ".log")


def queue(relative, automatic=False, force=False):
    if relative not in scan():
        raise ValueError("书库中不存在该音轨")
    path = safe_audio(relative)
    if subtitle(path) and not force:
        raise ValueError("已有识别结果，可以直接查看；需要改善时请选择高精度重转写")
    if model_error():
        raise ValueError(model_error())
    with LOCK:
        current = rows("SELECT status FROM tasks WHERE path=?", (relative,))
        if current and current[0]["status"] in {"queued", "running"}:
            return False
        if force:
            execute("INSERT OR REPLACE INTO quality_requests(path,requested) VALUES(?,?)", (relative,time.time()))
        if not automatic:
            observe(relative, path, folder_signature(path.parent), time.time())
            execute("UPDATE observations SET attempts=0,retry_at=0,stable_since=? WHERE path=?", (time.time()-AUTO_SETTLE-1,relative))
        set_task(relative, "queued", stage="自动识别队列" if automatic else "已加入队列")
    WAKE.set()
    return True


def cancel(relative):
    global ACTIVE
    if relative not in scan():
        raise ValueError("书库中不存在该音轨")
    with LOCK:
        current = rows("SELECT status FROM tasks WHERE path=?", (relative,))
        if not current or current[0]["status"] not in {"queued", "running"}:
            raise ValueError("这条音轨没有待取消的任务")
        set_task(relative, "cancelled", stage="已取消")
        if ACTIVE_PATH == relative and ACTIVE is not None and ACTIVE.poll() is None:
            ACTIVE.terminate()
    WAKE.set()


class Cancelled(Exception):
    pass


def ensure_running(relative):
    current = rows("SELECT status FROM tasks WHERE path=?", (relative,))
    if not current or current[0]["status"] != "running":
        raise Cancelled()


PROGRESS_LINE = re.compile(r"progress\s*=\s*(\d{1,3})%", re.I)
FFMPEG_TIME = re.compile(r"^out_time=(\d\d:\d\d:\d\d\.\d+)")


def run_command(argv, relative, label, base_percent, step_percent, segment_seconds):
    global ACTIVE, ACTIVE_PATH
    ensure_running(relative)
    with LOCK:
        ensure_running(relative)
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        ACTIVE, ACTIVE_PATH = proc, relative
    recent = []

    def consume():
        last = -1
        with task_log_path(relative).open("ab") as log:
            for raw in iter(proc.stdout.readline, b""):
                log.write(raw)
                log.flush()
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    recent.append(line)
                    if len(recent) > 12:
                        recent.pop(0)
                match = PROGRESS_LINE.search(line)
                if match:
                    part = min(100, int(match.group(1)))
                else:
                    match = FFMPEG_TIME.search(line)
                    part = min(100, int(seconds(match.group(1).replace(".", ",")) / segment_seconds * 100)) if match else -1
                if part >= 0 and part != last:
                    last = part
                    pct = min(99, int(base_percent + step_percent * part / 100))
                    if rows("SELECT status FROM tasks WHERE path=?", (relative,))[0]["status"] == "running":
                        set_task(relative, "running", pct, f"{label} · {part}%")

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    command_started = time.monotonic()
    try:
        while proc.poll() is None:
            if time.monotonic() - command_started > COMMAND_TIMEOUT:
                raise RuntimeError(f"{label} 超过 {COMMAND_TIMEOUT} 秒，已停止并等待重试")
            try:
                ensure_running(relative)
            except Cancelled:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise
            time.sleep(0.5)
        reader.join(timeout=5)
        ensure_running(relative)
        if proc.returncode != 0:
            tail = " / ".join(recent[-3:])[:420]
            raise RuntimeError(f"音频处理进程退出码 {proc.returncode}：{tail}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        reader.join(timeout=5)
        if proc.stdout:
            proc.stdout.close()
        with LOCK:
            if ACTIVE is proc:
                ACTIVE, ACTIVE_PATH = None, None


TIMING = re.compile(r"(?m)^(\d{1,3}:\d\d:\d\d[,.]\d{3})\s+-->\s+(\d{1,3}:\d\d:\d\d[,.]\d{3})")


def seconds(value):
    h, m, s = value.replace(".", ",").split(":")
    ss, ms = s.split(",")
    return int(h) * 3600 + int(m) * 60 + int(ss) + float('0.' + ms)


def srt_time(s):
    ms = max(0, round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def cues(content, offset):
    output = []
    for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        match = TIMING.search(block)
        if not match:
            continue
        lines = [line.strip() for line in block[match.end():].splitlines() if line.strip()]
        if lines and seconds(match[2]) > seconds(match[1]):
            output.append((seconds(match[1]) + offset, seconds(match[2]) + offset, "\n".join(lines)))
    return output


def render_srt(items):
    return "\n\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{line}"
                         for i, (a, b, line) in enumerate(items, 1)) + "\n"


def publish(relative, source=None, replace=False):
    path = safe_audio(relative)
    existing = path.with_suffix(".srt")
    stale = stale_generated_subtitle(path)
    valid_existing = existing.is_file() and bool(cues(existing.read_text(encoding="utf-8-sig", errors="replace"), 0))
    if valid_existing and not stale and not replace:
        return "已有原字幕，未覆盖"
    source = source or subtitle(path)
    if not source:
        raise ValueError("没有可发布的字幕")
    if existing.exists() and replace:
        folder=DATA/'sidecar-backups';folder.mkdir(exist_ok=True)
        digest=hashlib.sha256(existing.read_bytes()).hexdigest()
        backup=folder/(hashlib.sha256(relative.encode()).hexdigest()+'-'+digest+'.srt')
        if not backup.exists():backup.write_bytes(existing.read_bytes())
        temp=existing.with_suffix('.srt.quality.tmp');temp.write_bytes(source.read_bytes());temp.replace(existing)
        execute("INSERT OR REPLACE INTO published_subtitles(path,audio_signature,content_hash) VALUES(?,?,?)",
                (str(path),fingerprint(path),hashlib.sha256(existing.read_bytes()).hexdigest()))
        __import__('media_sync').WAKE.set()
        return '高精度 SRT 已替换，旧字幕已备份'
    if existing.exists() and (stale or not valid_existing):
        # Preserve the previous generated result before publishing for a replacement audio.
        backup = existing.with_name(existing.name + ".asr-" + hashlib.sha256(existing.read_bytes()).hexdigest()[:16] + ".bak")
        existing.rename(backup)
    # Link an already-complete temporary file exclusively. Never overwrite user files.
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".asr-", suffix=".tmp", delete=False) as stream:
        temp = Path(stream.name)
        stream.write(source.read_bytes())
    try:
        os.link(temp, existing)
    except FileExistsError:
        return "已有原字幕，未覆盖"
    finally:
        temp.unlink(missing_ok=True)
    execute("INSERT OR REPLACE INTO published_subtitles(path,audio_signature,content_hash) VALUES(?,?,?)",
            (str(path), fingerprint(path), hashlib.sha256(existing.read_bytes()).hexdigest()))
    __import__("media_sync").WAKE.set()
    return "字幕已保存到书库"


def native_request(endpoint, data=None, method=None, prompt=""):
    request = urllib.request.Request(NATIVE_ASR_URL+endpoint, data=data, method=method,
                                    headers={"X-ASR-Key": NATIVE_ASR_KEY,
                                             "X-ASR-Language": LANGUAGE,
                                             "Content-Type": "audio/wav",
                                             "X-ASR-Prompt": urllib.parse.quote(prompt[:800])})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            # Retry reads and connection-before-send failures; never blindly replay a POST timeout.
            safe_retry = data is None or getattr(exc.reason, "errno", None) in {61, 100, 101, 111, 113}
            if attempt == 2 or not safe_retry:
                raise
            time.sleep(2 * (attempt+1))


def native_transcribe(wav, relative, base, step, label, prompt="", details=False):
    started = time.monotonic()
    payload = wav.read_bytes()
    while True:
        ensure_running(relative)
        if time.monotonic()-started > COMMAND_TIMEOUT:
            raise RuntimeError("等待 Metal 工作进程超时")
        if native_request("/health").get("active"):
            set_task(relative, "running", int(base), "等待 Metal 工作进程完成上一分段")
            time.sleep(2)
            continue
        try:
            job = (native_request("/jobs", payload, prompt=prompt) if prompt else native_request("/jobs", payload))["id"]
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise
            time.sleep(2)
    started = time.monotonic()
    try:
        while True:
            ensure_running(relative)
            if time.monotonic()-started > COMMAND_TIMEOUT:
                raise RuntimeError("Metal 识别超时，等待重试")
            result = native_request("/jobs/"+job)
            if result["status"] == "done":
                with task_log_path(relative).open("a") as log:
                    log.write(result.get("log", "")+"\n")
                return result if details else result["srt"]
            if result["status"] in {"failed", "cancelled"}:
                raise RuntimeError(result.get("error", "Metal 任务已取消"))
            set_task(relative, "running", min(99, int(base+step*result["progress"]/100)),
                     f"{result.get('stage', 'Metal 加速')} · {label} · {result['progress']}%")
            time.sleep(1)
    finally:
        try:
            native_request("/jobs/"+job, method="DELETE")
        except Exception:
            pass


def asr_prompt(relative):
    title=' / '.join(Path(relative).parts[-3:-1])
    if 'green mile' in title.lower():
        title += '. John Coffey. Paul Edgecombe. Delacroix. Percy Wetmore. Brutus Howell.'
    return title[:700]


def transcribe(relative):
    from asr_quality import (PROFILE, RECOGNITION_PROFILE, acoustic_silences, cli_to_verbose, extract_words,
                             regroup, render_srt as quality_srt, core_words, deduplicate_overlap, quality_report, suppress_silence, speech_spans)
    path=safe_audio(relative);initial_fingerprint=fingerprint(path)
    force=bool(rows('SELECT path FROM quality_requests WHERE path=?',(relative,)))
    if subtitle(path) and not force:return
    if model_error():raise RuntimeError(model_error())
    probe=subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],text=True,timeout=90).strip()
    try:duration=float(probe)
    except ValueError:raise ValueError('音轨没有有效时长，请检查损坏文件或重新扫描媒体库')
    if not 0<duration<120*3600:raise ValueError('音频时长不合理，无法识别')
    segments=max(1,int((duration+CHUNK_SECONDS-.01)//CHUNK_SECONDS));prompt=asr_prompt(relative)
    profile=hashlib.sha256(json.dumps([MODEL.name,LANGUAGE,bool(NATIVE_ASR_URL),RECOGNITION_PROFILE,prompt]).encode()).hexdigest()[:12]
    all_words=[];all_silences=[];all_speech=[];notes=[]
    with task_log_path(relative).open('a') as log:log.write(f'\n高精度转写 {MODEL.name}, {segments} 段，保留原始时间轴与重叠上下文\n')
    with tempfile.TemporaryDirectory(prefix='asr-',dir=DATA) as directory:
        work=Path(directory)
        for index in range(segments):
            ensure_running(relative)
            core=index*CHUNK_SECONDS;end=min(duration,core+CHUNK_SECONDS)
            offset=max(0,core-1.5);extract_end=min(duration,end+1.5)
            checkpoint=DATA/'segments'/initial_fingerprint/f'{profile}-{CHUNK_SECONDS}-{index}.json'
            base=index/segments*98
            if checkpoint.exists():
                result=json.loads(checkpoint.read_text())
                set_task(relative,'running',int((index+1)/segments*98),f'复用高精度分段 {index+1}/{segments}')
            else:
                wav=work/'segment.wav';output=work/'segment'
                run_command(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-ss',str(offset),'-i',str(path),'-t',str(extract_end-offset),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav),'-progress','pipe:1'],relative,f'提取含上下文的第 {index+1}/{segments} 段',base,.12*98/segments,extract_end-offset)
                if NATIVE_ASR_URL:
                    result=native_transcribe(wav,relative,base+.12*98/segments,.88*98/segments,f'第 {index+1}/{segments} 段',prompt=prompt,details=True)
                else:
                    gaps,peak=acoustic_silences(wav)
                    spans=speech_spans(wav,shutil.which('whisper-vad-speech-segments'),MODEL.parent/'ggml-silero-v6.2.0.bin')
                    if spans == [] and peak<60:
                        words=[];warnings=[]
                    else:
                        run_command(['whisper-cli','-m',str(MODEL),'-f',str(wav),'-l',LANGUAGE,'-t',str(THREADS),'-ml','100000','-sow','-bs','5','-bo','5','--prompt',prompt,'-ojf','-of',str(output),'-pp'],relative,f'高精度识别第 {index+1}/{segments} 段',base+.12*98/segments,.88*98/segments,extract_end-offset)
                        produced=output.with_suffix('.json')
                        if not produced.exists():raise RuntimeError('Whisper 未生成词级时间戳 JSON，请查看日志')
                        words,warnings=extract_words(cli_to_verbose(json.loads(produced.read_text())))
                        if spans and not words:raise RuntimeError('检测到语音但 CPU 识别为空，保留原字幕供重试')
                    if spans is None:warnings.append('Silero 模型或程序缺失，CPU 使用能量停顿分析')
                    result={'words':words,'silences':gaps,'speech_spans':spans or [],'quality':{'warnings':warnings}}

                if isinstance(result,str):
                    result={'words':[{'start':max(0,a),'end':z,'text':t,'probability':1} for a,z,t in cues(result,0)],'silences':[]}
                elif 'words' not in result:
                    result['words']=[{'start':a,'end':z,'text':t,'probability':1,'uncertain':True} for a,z,t in cues(result.get('srt',''),0)]
                checkpoint.parent.mkdir(parents=True,exist_ok=True)
                temp=checkpoint.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False));temp.replace(checkpoint)
                wav.unlink(missing_ok=True)
            words=core_words(result.get('words',[]),offset,core,end,index)
            all_words.extend(words)
            all_silences.extend((a+offset,z+offset) for a,z in result.get('silences',[]) if core<=(a+z)/2+offset<end)
            all_speech.extend((max(core,a+offset),min(end,z+offset)) for a,z in result.get('speech_spans',[]) if z+offset>core and a+offset<end)
            notes.extend(result.get('quality',{}).get('warnings',[]))
            set_task(relative,'running',min(98,int((index+1)/segments*98)),f'完成 {index+1}/{segments} 段')
    all_words=suppress_silence(deduplicate_overlap(all_words),all_silences,all_speech)
    if not all_words:raise RuntimeError('整条音轨未检测到可识别语音，没有生成虚构字幕')
    if fingerprint(path)!=initial_fingerprint:raise RuntimeError('识别过程中音频发生变化，未覆盖已有字幕')
    set_task(relative,'running',99,'合并词级时间轴、规范数字和复核断句')
    content=quality_srt(regroup(all_words,all_silences))
    target=DATA/'transcripts'/(initial_fingerprint+'.srt');temp=target.with_suffix('.srt.tmp');temp.write_text(content,encoding='utf-8')
    ensure_running(relative);temp.replace(target)
    report=quality_report(all_words,all_speech or None);report.update(model=MODEL.name,warnings=notes)
    folder=DATA/'quality';folder.mkdir(exist_ok=True)
    (folder/(initial_fingerprint+'.json')).write_text(json.dumps(report,ensure_ascii=False))
    (DATA/'transcripts'/(initial_fingerprint+'.words.json')).write_text(json.dumps({'profile':PROFILE,'words':all_words,'silences':all_silences,'speech_spans':all_speech},ensure_ascii=False))
    publish(relative,source=target,replace=force)
    execute('DELETE FROM quality_requests WHERE path=?',(relative,))
    execute('DELETE FROM reset_requests WHERE path=?',(relative,))


def folder_signature(folder):
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                   and (p.suffix.lower() in SUPPORTED or p.name in {"transcript.srt", "sync-manifest.json"}))
    return hashlib.sha256(json.dumps([(p.name, p.stat().st_size, p.stat().st_mtime_ns)
                                     for p in files], ensure_ascii=False).encode()).hexdigest()


def observe(relative, path, signature, now):
    """Persist settling and retry state across restarts and replace-at-same-path imports."""
    source = fingerprint(path)
    with LOCK:
        old = rows("SELECT * FROM observations WHERE path=?", (relative,))
        if not old:
            execute("INSERT INTO observations(path,signature,folder_signature,stable_since) VALUES(?,?,?,?)",
                    (relative, source, signature, now))
        elif old[0]["signature"] != source:
            execute("UPDATE observations SET signature=?,folder_signature=?,stable_since=?,attempts=0,retry_at=0 WHERE path=?",
                    (source, signature, now, relative))
            current = rows("SELECT status FROM tasks WHERE path=?", (relative,))
            if current and current[0]["status"] != "running":
                set_task(relative, "missing", stage="音频已更新，等待稳定")
        elif old[0]["folder_signature"] != signature:
            execute("UPDATE observations SET folder_signature=?,stable_since=? WHERE path=?", (signature, now, relative))
        return rows("SELECT * FROM observations WHERE path=?", (relative,))[0]


def automatic_scan(now=None):
    now = time.time() if now is None else now
    groups = {}
    for relative in scan(force=True):
        try:
            path = safe_audio(relative)
            groups.setdefault(path.parent, []).append((relative, path))
        except (OSError, ValueError):
            continue
    for folder, tracks in groups.items():
        try:
            signature = folder_signature(folder)
        except OSError:
            continue
        for relative, path in tracks:
            try:
                observation = observe(relative, path, signature, now)
                if now - observation["stable_since"] < AUTO_SETTLE:
                    continue
                if rows("SELECT path FROM quality_requests WHERE path=?",(relative,)):
                    continue
                existing = subtitle(path)
                current = rows("SELECT * FROM tasks WHERE path=?", (relative,))
                task = current[0] if current else {}
                if existing:
                    if existing != path.with_suffix(".srt") or stale_generated_subtitle(path):
                        publish(relative)
                    if task.get("status") != "running" and task.get("status") != "done":
                        set_task(relative, "done", 100, "已复用现有字幕")
                    continue
                if not AUTO_TRANSCRIBE or model_error() or task.get("status") in {"running", "queued", "cancelled"}:
                    continue
                if observation["attempts"] >= AUTO_MAX_ATTEMPTS or now < observation["retry_at"]:
                    continue
                queue(relative, automatic=True)
            except (OSError, ValueError) as exc:
                AUTO_STATE["error"] = f"{relative}: {exc}"
    __import__("book_tools").update_manuscripts()
    AUTO_STATE["last_scan"] = now


def automatic_worker():
    while True:
        AUTO_STATE["error"] = ""
        try:
            automatic_scan()
        except Exception as exc:
            AUTO_STATE["error"] = str(exc)
            print(f"Automatic scan failed: {exc}", flush=True)
        time.sleep(AUTO_INTERVAL)


def worker():
    while True:
        task = rows("SELECT t.path FROM tasks t LEFT JOIN observations o ON o.path=t.path "
                    "WHERE t.status='queued' AND (o.stable_since<=? OR ?=0) ORDER BY t.updated ASC LIMIT 1",
                    (time.time() - AUTO_SETTLE, int(AUTO_TRANSCRIBE)))
        if not task:
            WAKE.wait(3)
            WAKE.clear()
            continue
        relative = task[0]["path"]
        try:
            path = safe_audio(relative)
            observation = observe(relative, path, folder_signature(path.parent), time.time())
            if AUTO_TRANSCRIBE and time.time() - observation["stable_since"] < AUTO_SETTLE:
                continue
        except (OSError, ValueError) as exc:
            set_task(relative, "failed", stage="音轨不可用", error=str(exc))
            continue
        with LOCK:
            if not execute("UPDATE tasks SET status='running',progress=0,stage='准备识别',updated=?,started=? WHERE path=? AND status='queued'",
                           (time.time(), time.time(), relative)):
                continue
        execute("UPDATE observations SET attempts=attempts+1 WHERE path=?", (relative,))
        try:
            transcribe(relative)
            ensure_running(relative)
            set_task(relative, "done", 100, "识别完成")
        except Cancelled:
            pass
        except Exception as exc:
            if rows("SELECT status FROM tasks WHERE path=?", (relative,))[0]["status"] == "running":
                last = rows("SELECT progress FROM tasks WHERE path=?", (relative,))[0]
                attempt = rows("SELECT attempts FROM observations WHERE path=?", (relative,))[0]["attempts"]
                execute("UPDATE observations SET retry_at=? WHERE path=?",
                        (time.time() + min(3600, 300 * 2 ** min(attempt, 4)), relative))
                set_task(relative, "failed", last["progress"], stage="识别失败", error=str(exc))
                with task_log_path(relative).open("ab") as log:
                    log.write(f"\nFAILED: {exc}\n".encode("utf-8", errors="replace"))
            print(f"Transcription failed [{relative}]: {exc}", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "ASRManager/2.0"

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def headers_ok(self, mutate=False):
        host = self.headers.get("Host", "").split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost"}:
            return False
        token = self.headers.get("X-ASR-Extension-Token", "")
        if not EXTENSION_TOKEN or not secrets.compare_digest(token, EXTENSION_TOKEN):
            return False
        if mutate and self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return False
        return True

    def respond(self, status, content, kind="application/json; charset=utf-8", attachment=False):
        if not isinstance(content, bytes):
            content = (json.dumps(content, ensure_ascii=False) if "json" in kind else content).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        if attachment:
            self.send_header("Content-Disposition", 'attachment; filename="transcript.srt"')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if not self.headers_ok():
            return self.respond(403, {"error": "请求来源或密钥无效"})
        url = urllib.parse.urlsplit(self.path)
        try:
            if url.path.startswith("/api/books/"):
                return self.respond(200, __import__("book_tools").get(url))
            if url.path == "/api/health":
                return self.respond(200, {"status": "ok", "model": MODEL.name, "model_error": model_error()})
            if url.path == "/api/catalog":
                return self.respond(200, library())
            if url.path == "/api/task-log":
                relative = urllib.parse.parse_qs(url.query).get("path", [""])[0]
                if not rows("SELECT path FROM tasks WHERE path=?",(relative,)):
                    safe_audio(relative)
                logfile = task_log_path(relative)
                if not logfile.is_file():
                    return self.respond(200, {"log": "尚无日志"})
                with logfile.open("rb") as stream:
                    stream.seek(max(0, logfile.stat().st_size - 16000))
                    return self.respond(200, {"log": stream.read().decode("utf-8", errors="replace")[-12000:]})
            if url.path == "/api/book-transcript":
                return self.respond(410, {"error": "TXT 文稿已移除，请使用 /api/books/transcript 获取 SRT"})
            if url.path == "/api/transcript":
                relative = urllib.parse.parse_qs(url.query).get("path", [""])[0]
                try:
                    file = subtitle(safe_audio(relative))
                except (ValueError,OSError):
                    file = history_subtitle(relative)
                if not file:
                    return self.respond(404, {"error": "尚无字幕"})
                return self.respond(200, file.read_bytes(), "text/plain; charset=utf-8",
                                    attachment="download=1" in url.query)
            return self.respond(404, {"error": "页面不存在"})
        except (OSError, ValueError, RuntimeError) as exc:
            return self.respond(400, {"error": str(exc)})

    def do_POST(self):
        if not self.headers_ok(mutate=True):
            return self.respond(403, {"error": "请求来源无效"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("请求数据过大或为空")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("无效的请求")
            if self.path.startswith("/api/books/"):
                return self.respond(200, __import__("book_tools").post(self.path, payload))
            relative = payload.get("path")
            if self.path == "/api/media-sync":
                import media_sync
                return self.respond(200, media_sync.request_sync())
            if self.path.startswith("/api/automation/"):
                return self.respond(410, {"error": "自动匹配与自动目录已移除，请使用当前书籍按钮"})
            if self.path == "/api/queue":
                queued = queue(relative)
                return self.respond(200, {"ok": True, "queued": queued})
            if self.path == "/api/queue-book":
                book = payload.get("book")
                tracks = [item for item in scan().values() if item["book"] == book]
                if not tracks or len(tracks) > 2000:
                    raise ValueError("找不到这本书或音轨数量超过单次上限（2000）")
                count = 0
                for track in tracks:
                    if not subtitle(safe_audio(track["path"])):
                        count += int(queue(track["path"]))
                return self.respond(200, {"ok": True, "queued": count})
            if self.path == "/api/cancel":
                cancel(relative)
                return self.respond(200, {"ok": True})
            if self.path == "/api/publish":
                result = publish(relative)
                return self.respond(200, {"ok": True, "message": result})
            return self.respond(404, {"error": "接口不存在"})
        except (ValueError, TypeError, json.JSONDecodeError, OSError, RuntimeError) as exc:
            return self.respond(400, {"error": str(exc)})


if __name__ == "__main__":
    import sys
    sys.modules["transcription_manager"] = sys.modules[__name__]
    init()
    print(f"Transcription manager: libraries={list(ROOTS)}, model={MODEL.name}, language={LANGUAGE}", flush=True)
    import library_automation
    LIBRARY_AUTOMATION = library_automation.STATE
    import media_sync
    threading.Thread(target=media_sync.run, daemon=True).start()
    # Matching and chapter replacement run only on explicit book actions.
    threading.Thread(target=automatic_worker, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
