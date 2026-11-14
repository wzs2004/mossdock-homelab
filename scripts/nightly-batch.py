#!/usr/bin/env python3
"""夜间批处理：把"来一张算一张"改成"攒一批、夜里清一轮"。

流程（顺序执行，避免 whisper 与 Immich 机器学习同时抢 CPU/磁盘）：
  1) 转写阶段：查一次书籍目录，把所有文稿不完整的书提交到转写队列，
     然后轮询容器内 tasks.sqlite3 等队列跑完（默认上限 6 小时）
  2) Immich 阶段：唤醒 Immich，等后台任务清空（默认上限 2 小时），再让它休眠

设计要点
  - 只在夜里跑，白天不产生额外负载
  - 两个阶段都是"跑完即停"，不是常驻
  - 队列没有进展就提前结束，不会空转一整晚
  - 目录接口很慢（实测约 3 分钟），因此只调用一次；等待阶段改查本地 SQLite
  - 带锁，避免重复执行

用法：
  python3 nightly-batch.py                # 正常执行
  python3 nightly-batch.py --dry-run      # 只打印计划，不实际排队/休眠
  python3 nightly-batch.py --skip-immich  # 只跑转写
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MANAGER = os.environ.get("MANAGER_URL", "http://127.0.0.1:13384")
GATE = os.environ.get("GATE_URL", "http://127.0.0.1:2283/api/server/ping")
CONTAINER = os.environ.get("MANAGER_CONTAINER", "transcription-manager")
PROJECT = Path(os.environ.get("ABS_PROJECT", Path(__file__).resolve().parent.parent))
MANAGER_ENV = PROJECT / "stacks/transcription-manager/.env"
LOG = Path(os.environ.get("BATCH_LOG", Path.home() / "Library/Logs/nightly-batch.log"))
LOCK = Path("/tmp/nightly-batch.lock")

TRANSCRIBE_HOURS = float(os.environ.get("TRANSCRIBE_HOURS", "6"))
IMMICH_HOURS = float(os.environ.get("IMMICH_HOURS", "2"))
# 攒够多少张才值得唤醒 Immich 跑一轮；低于阈值交给日常使用时顺带处理
IMMICH_MIN_PENDING = int(os.environ.get("IMMICH_MIN_PENDING", "20"))
STALL_MINUTES = 20

PSQL = ["docker", "exec", "immich_postgres", "psql", "-U", "postgres", "-d", "immich", "-tAc"]
TASK_SQL = ("import sqlite3;"
            "c=sqlite3.connect('/data/tasks.sqlite3');"
            "print(c.execute(\"select count(*), coalesce(sum(progress),0) from tasks "
            "where status in ('queued','running')\").fetchone())")


def log(message):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def read_token():
    if os.environ.get("EXTENSION_TOKEN"):
        return os.environ["EXTENSION_TOKEN"]
    env_file = MANAGER_ENV
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("EXTENSION_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("找不到 EXTENSION_TOKEN（.env 或环境变量）")


def api(path, body=None, token=None, timeout=300):
    url = MANAGER + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {}
    if token:
        headers["X-ASR-Extension-Token"] = token
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.load(reply)


def pending_books(token):
    """文稿不完整的书：目录接口较慢，整轮只调用一次。"""
    data = api("/api/catalog", token=token, timeout=600)
    books = data.get("books", [])
    todo = [b for b in books if int(b.get("done") or 0) < int(b.get("total") or 0)]
    return books, todo


def queue_transcripts(token, dry_run):
    books, todo = pending_books(token)
    log(f"书库共 {len(books)} 本，其中文稿不完整 {len(todo)} 本")
    if dry_run:
        for book in todo[:10]:
            log(f"  [dry-run] 将排队: {book['name']} ({book.get('done')}/{book.get('total')})")
        return 0
    queued = 0
    for book in todo:
        try:
            result = api("/api/queue-book", {"book": book["name"]}, token=token, timeout=120)
            queued += int(result.get("queued", 0))
        except Exception as exc:
            log(f"  排队失败 {book['name']}: {exc}")
    log(f"本轮新增转写任务 {queued} 个")
    return queued


def task_queue_state():
    """返回 (活跃任务数, 累计进度)。查本地 SQLite，秒回。"""
    try:
        out = subprocess.run(["docker", "exec", CONTAINER, "python3", "-c", TASK_SQL],
                             capture_output=True, text=True, timeout=30)
        text = out.stdout.strip().strip("()")
        count, progress = (int(x) for x in text.split(","))
        return count, progress
    except Exception:
        return None, None


def wait_transcripts(limit_hours):
    deadline = time.monotonic() + limit_hours * 3600
    last_progress = None
    stalled = 0
    while time.monotonic() < deadline:
        time.sleep(60)
        count, progress = task_queue_state()
        if count is None:
            continue
        if count == 0:
            log("转写队列已清空")
            return True
        if progress == last_progress:
            stalled += 1
            if stalled >= STALL_MINUTES:
                log(f"转写 {STALL_MINUTES} 分钟无进展，提前结束")
                return False
        else:
            stalled = 0
        last_progress = progress
        log(f"转写进行中：活跃任务 {count} 个")
    log("转写达到时间上限，结束本阶段")
    return False


def immich_pending():
    sql = ('select count(*) from asset_job_status where "metadataExtractedAt" is null '
           'or "facesRecognizedAt" is null')
    try:
        out = subprocess.run(PSQL + [sql], capture_output=True, text=True, timeout=30)
        return int(out.stdout.strip() or 0)
    except Exception:
        return None


def immich_batch(dry_run, limit_hours):
    pending = immich_pending()
    if pending is not None and pending < IMMICH_MIN_PENDING:
        log(f"Immich 待办 {pending} 个，低于阈值 {IMMICH_MIN_PENDING}，本轮不唤醒")
        return
    if dry_run:
        log(f"  [dry-run] 将唤醒 Immich，处理 {pending} 个待办资产后休眠")
        return
    log("唤醒 Immich")
    try:
        urllib.request.urlopen(GATE, timeout=180).read()
    except Exception as exc:
        log(f"唤醒失败: {exc}")
        return
    deadline = time.monotonic() + limit_hours * 3600
    last, stalled = None, 0
    while time.monotonic() < deadline:
        time.sleep(60)
        count = immich_pending()
        if count is None:
            continue
        if count == 0:
            log("Immich 后台任务已清空")
            break
        if count == last:
            stalled += 1
            if stalled >= STALL_MINUTES:
                log(f"Immich {STALL_MINUTES} 分钟无进展，提前结束")
                break
        else:
            stalled = 0
        last = count
        log(f"Immich 待办 {count} 个")
    log(f"休眠 Immich（剩余待办 {immich_pending()}）")
    subprocess.run(["docker", "stop", "immich_server", "immich_machine_learning",
                    "immich_redis", "immich_postgres"], capture_output=True, timeout=180)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-immich", action="store_true")
    parser.add_argument("--skip-transcribe", action="store_true")
    args = parser.parse_args()

    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 12 * 3600:
        log("已有实例在运行，退出")
        return 1
    LOCK.write_text(str(os.getpid()))
    try:
        log("=== 夜间批处理开始 ===")
        if not args.skip_transcribe:
            try:
                token = read_token()
                queued = queue_transcripts(token, args.dry_run)
                if queued or args.dry_run:
                    wait_transcripts(TRANSCRIBE_HOURS)
            except Exception as exc:
                log(f"转写阶段跳过: {exc}")
        if not args.skip_immich:
            try:
                immich_batch(args.dry_run, IMMICH_HOURS)
            except Exception as exc:
                log(f"Immich 阶段跳过: {exc}")
        log("=== 夜间批处理结束 ===")
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
