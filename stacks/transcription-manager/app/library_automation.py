"""Audiobookshelf 元数据自动匹配。

原来的"自动分章"已按需求彻底移除，这里只保留：
  - 定时扫描书库
  - 对缺资料的书自动调用 ABS 的 match 接口
  - 手动触发 scan / retry_failed / match

控制项只保留 auto_match 一个开关。
"""

import hashlib
import json
import os
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import abs_api

DATA = Path(os.environ.get("DATA_DIR", "/data"))
AUTO_MATCH = os.environ.get("AUTO_MATCH", "1") == "1"
STATE = {"last_scan": 0, "current": "", "matched": 0, "error": "", "running": False}
CONTROL_LOCK = threading.RLock()
WAKE = threading.Event()

# 旧版本状态文件里遗留的分章字段，读取时直接丢弃
_LEGACY_FIELDS = ("chapter_status", "chapter_error", "chapter_message", "chapter_count",
                  "chapter_algorithm", "chapter_preview", "chapter_review", "chapter_legacy")


def atomic_json(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(file)


def load_state():
    file = DATA / "library-automation.json"
    state = json.loads(file.read_text()) if file.exists() else {}
    state.pop("chapters", None)
    for key in ("metadata", "records"):
        state.setdefault(key, {})
    for entry in state["records"].values():
        for field in _LEGACY_FIELDS:
            entry.pop(field, None)
    return state


def save_state(state):
    state.pop("chapters", None)
    atomic_json(DATA / "library-automation.json", state)


def controls():
    with CONTROL_LOCK:
        file = DATA / "library-automation-controls.json"
        value = json.loads(file.read_text()) if file.exists() else {}
        value.pop("auto_chapters", None)
        return {"auto_match": AUTO_MATCH, "commands": [], **value}


def update_settings(values):
    if not values or set(values) - {"auto_match"} or any(type(v) is not bool for v in values.values()):
        raise ValueError("设置仅接受自动匹配开关")
    with CONTROL_LOCK:
        value = controls()
        value.update(values)
        atomic_json(DATA / "library-automation-controls.json", value)
    WAKE.set()
    return {"ok": True, "message": "设置已保存，重启后仍然生效", "settings": values}


def note(message):
    with (DATA / "library-automation.log").open("a") as log:
        log.write(time.strftime("%F %T") + " " + message + "\n")


def action(payload):
    kind = payload.get("action")
    if kind == "log":
        file = DATA / "library-automation.log"
        if not file.exists():
            return {"log": "暂无自动整理日志"}
        with file.open("rb") as stream:
            stream.seek(max(0, file.stat().st_size - 16000))
            return {"log": stream.read().decode(errors="replace")[-12000:]}
    if kind not in {"scan", "retry_failed", "match"}:
        raise ValueError("无效的自动整理操作")
    item_id = payload.get("item_id")
    state = load_state()
    if kind == "match" and (not isinstance(item_id, str) or item_id not in state["records"]):
        raise ValueError("找不到这本书，请先检查书库")
    with CONTROL_LOCK:
        value = controls()
        command = {"id": uuid.uuid4().hex, "action": kind, "item_id": item_id, "created": time.time()}
        if any(c["action"] == kind and c.get("item_id") == item_id for c in value["commands"]):
            return {"ok": True, "message": "该操作已在等待执行"}
        if len(value["commands"]) >= 100:
            raise ValueError("待执行操作过多，请稍后再试")
        value["commands"].append(command)
        atomic_json(DATA / "library-automation-controls.json", value)
    WAKE.set()
    return {"ok": True, "message": "已加入自动整理队列，可在下方查看进度"}


def status(live_audio_paths=None):
    config = controls()
    state = load_state()
    records = []
    for item_id, entry in state["records"].items():
        if live_audio_paths is not None:
            book_path = str(entry.get("path") or "").lstrip("/").rstrip("/")
            if not book_path or not any(p == book_path or p.startswith(book_path + "/")
                                        for p in live_audio_paths):
                continue
        metadata = state["metadata"].get(item_id, {})
        record = dict(entry, item_id=item_id)
        record["match_status"] = ("failed" if metadata.get("error") else "done") if metadata else "pending"
        record["match_error"] = metadata.get("error", "")
        record["match_updated"] = metadata.get("updated", False)
        record["retry_at"] = metadata.get("retry_at", 0)
        record["pending_actions"] = [c["action"] for c in config["commands"] if c.get("item_id") == item_id]
        records.append(record)
    totals = ({"matched": sum(not bool(x.get("error")) for x in state["metadata"].values()),
               "updated": sum(bool(x.get("updated")) for x in state["metadata"].values()),
               "unmatched": sum(bool(x.get("error")) for x in state["metadata"].values())}
              if live_audio_paths is None else
              {"matched": sum(x["match_status"] == "done" for x in records),
               "updated": sum(bool(x["match_updated"]) for x in records),
               "unmatched": sum(x["match_status"] == "failed" for x in records)})
    return {**STATE, "metadata_errors": totals["unmatched"],
            "settings": {"auto_match": config["auto_match"]},
            "pending_commands": len(config["commands"]),
            "totals": totals,
            "records": sorted(records, key=lambda r: r.get("title", "").casefold())}


def record_book(state, book):
    entry = state["records"].setdefault(book["id"], {})
    entry.update(title=((book.get("media") or {}).get("metadata") or {}).get("title", book["id"]),
                 path=book.get("path", ""), library_id=book.get("libraryId", ""), checked_at=time.time())
    return entry


def match_book(book, provider, state, now, force=False):
    meta = (book.get("media") or {}).get("metadata") or {}
    signature = hashlib.sha256(
        json.dumps([meta.get("title"), meta.get("authors")], sort_keys=True).encode()).hexdigest()
    previous = state["metadata"].get(book["id"], {})
    if not force and previous.get("signature") == signature and now < previous.get("retry_at", 0):
        return
    folder = DATA / "metadata-backups"
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / (hashlib.sha256(book["id"].encode()).hexdigest() + ".json")
    if not backup.exists():
        backup.write_text(json.dumps(book, ensure_ascii=False))
    if not meta.get("title"):
        return
    record = {"signature": signature, "retry_at": now + 3600, "checked_at": now}
    try:
        result = abs_api.request(abs_api.item_endpoint(book["id"]) + "/match", "POST",
                                 {"provider": provider or "audible",
                                  "overrideDetails": False, "overrideCover": False})
        if result.get("warning") or result.get("error"):
            record.update(error=result.get("warning") or str(result.get("error")), retry_at=now + 86400)
        else:
            record.update(updated=bool(result.get("updated")), retry_at=now + 30 * 86400)
            STATE["matched"] += 1
    except Exception as exc:
        record["error"] = str(exc)
    state["metadata"][book["id"]] = record
    save_state(state)
    note("匹配 · " + meta["title"] + " · " + (record.get("error") or ("已补充资料" if record.get("updated") else "资料完整")))


def process_commands():
    did_scan = False
    # 命令留在磁盘上直到完成，因此重启后的恢复是幂等的
    for command in controls()["commands"]:
        STATE["current"] = "执行操作 · " + command["action"]
        state = load_state()
        try:
            kind = command["action"]
            if kind == "scan":
                run_once()
                did_scan = True
            elif kind == "retry_failed":
                failed = [item_id for item_id, value in state["metadata"].items() if value.get("error")]
                for item_id in dict.fromkeys(failed):
                    book = abs_api.request(abs_api.item_endpoint(item_id))
                    record_book(state, book)
                    match_book(book, "audible", state, time.time(), force=True)
                save_state(state)
            elif kind == "match":
                book = abs_api.request(abs_api.item_endpoint(command["item_id"]))
                record_book(state, book)
                match_book(book, "audible", state, time.time(), force=True)
                state["records"][book["id"]].pop("operation_error", None)
                save_state(state)
            note("操作完成 · " + kind)
        except Exception as exc:
            STATE["error"] = str(exc)
            if command.get("item_id") in state["records"]:
                entry = state["records"][command["item_id"]]
                entry["operation_error"] = str(exc)
                save_state(state)
            note("操作失败 · " + command["action"] + " · " + str(exc))
        finally:
            with CONTROL_LOCK:
                config = controls()
                config["commands"] = [c for c in config["commands"] if c["id"] != command["id"]]
                atomic_json(DATA / "library-automation-controls.json", config)

    return did_scan


def run_once():
    state = load_state()
    match_candidates = []
    libraries = abs_api.request("/api/libraries").get("libraries", [])
    for library in libraries:
        if library.get("mediaType") != "book":
            continue
        items = abs_api.request(
            "/api/libraries/" + urllib.parse.quote(library["id"], safe="") + "/items?limit=0&minified=0"
        ).get("results", [])
        for item in items:
            if item.get("isMissing") or item.get("isInvalid"):
                continue
            STATE["current"] = (item.get("media") or {}).get("metadata", {}).get("title", item["id"])
            try:
                book = abs_api.request(abs_api.item_endpoint(item["id"]))
                record_book(state, book)
                if controls()["auto_match"]:
                    provider = library.get("provider") or "audible"
                    match_candidates.append((book, "audible" if provider == "google" else provider))
            except Exception as exc:
                STATE["error"] = f"{item['id']}: {exc}"
                note(STATE["error"])
    started = time.monotonic()
    attempted = 0
    for book, provider in match_candidates:
        if not controls()["auto_match"] or attempted >= 8 or time.monotonic() - started > 120:
            break
        before = state["metadata"].get(book["id"])
        STATE["current"] = "匹配书籍资料 · " + (
            (book.get("media") or {}).get("metadata") or {}).get("title", book["id"])
        match_book(book, provider, state, time.time())
        if state["metadata"].get(book["id"]) is not before:
            attempted += 1
    STATE.update(last_scan=time.time(), current="",
                 metadata_errors=sum(bool(x.get("error")) for x in state["metadata"].values()))
    save_state(state)


def run():
    while True:
        WAKE.clear()
        STATE.update(error="", running=True)
        try:
            did_scan = process_commands()
            if not did_scan and controls()["auto_match"]:
                run_once()
        except Exception as exc:
            STATE["error"] = str(exc)
        finally:
            STATE.update(running=False, current="")
        WAKE.wait(120)
