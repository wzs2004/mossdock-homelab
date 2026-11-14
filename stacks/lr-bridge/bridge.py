#!/usr/bin/env python3
"""Read-only local bridge for Audiobookshelf tracks and SRT captions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import mimetypes
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".deps"))
from mutagen import File as AudioFile  # noqa: E402

SUPPORTED = {".mp3", ".m4a", ".m4b", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aac"}
SRT_LINE = re.compile(r"(?m)^\s*(?:\d+\s*\n)?(\d{2,}:\d\d:\d\d[,\.]\d{1,3})\s*-->\s*(\d{2,}:\d\d:\d\d[,\.]\d{1,3})[^\n]*\n")


def timestamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    h, remaining = divmod(milliseconds, 3600000)
    m, remaining = divmod(remaining, 60000)
    s, ms = divmod(remaining, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    matches = list(SRT_LINE.finditer(text.replace("\r", "").lstrip("\ufeff")))
    normalized = text.replace("\r", "").lstrip("\ufeff")
    entries = []
    for index, match in enumerate(matches):
        start, end = timestamp(match.group(1)), timestamp(match.group(2))
        content = normalized[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(normalized)].strip()
        # The preceding entry's numeric cue ID can fall inside the captured block.
        content = re.sub(r"\n+\d+\s*$", "", content).strip()
        if content and end > start:
            entries.append((start, end, content))
    return entries


def split_srt(entries: list[tuple[float, float, str]], offset: float, duration: float) -> str:
    result = []
    end = offset + duration
    for start, finish, content in entries:
        if finish <= offset or start >= end:
            continue
        a, b = max(0.0, start - offset), min(duration, finish - offset)
        if b - a < .015:
            continue
        result.append(f"{len(result) + 1}\n{srt_time(a)} --> {srt_time(b)}\n{content}\n")
    return "\n".join(result) + ("\n" if result else "")


@dataclass(frozen=True)
class Track:
    name: str
    path: Path
    duration: float
    offset: float


@dataclass(frozen=True)
class Issue:
    id: str
    name: str
    directory: Path
    tracks: tuple[Track, ...]
    subtitles: Path | None
    library: str = "audiobooks"
    item_ids: tuple[str, ...] = ()
    subtitle_status: str = "legacy"


class LibraryIndex:
    def __init__(self, root: Path, roots=None):
        self.root = root.resolve()
        self.roots = roots or {"audiobooks": self.root}
        self.durations = {}
        self._issues: dict[str, Issue] = {}
        self._updated = float("-inf")
        self._lock = threading.RLock()

    def issues(self) -> dict[str, Issue]:
        with self._lock:
            if time.monotonic() - self._updated < 60:
                return self._issues
            result = {}
            item_paths = {}
            api_url = os.environ.get("ABS_BASE_URL", "")
            token = os.environ.get("ABS_API_TOKEN", "")
            if api_url and token:
                def api(path):
                    request = urllib.request.Request(api_url+path, headers={"Authorization":"Bearer "+token})
                    with urllib.request.urlopen(request,timeout=20) as response: return json.load(response)
                try:
                    for library in api("/api/libraries").get("libraries",[]):
                        for item in api("/api/libraries/"+library['id']+"/items?limit=0&minified=0").get("results",[]):
                            item_paths.setdefault(item.get("path", ""), []).append(item["id"])
                except Exception as exc:
                    print(f"ABS item mapping unavailable: {exc}", file=sys.stderr)
            for library_id, root in self.roots.items():
                root = root.resolve()
                folders = set()
                for file in root.rglob("*"):
                    if file.is_file() and file.suffix.lower() in SUPPORTED and not file.is_symlink():
                        if file.resolve().is_relative_to(root): folders.add(file.parent)
                for folder in sorted(folders):
                    offset, tracks = 0.0, []
                    for file in sorted(folder.iterdir()):
                        if not file.is_file() or file.suffix.lower() not in SUPPORTED or file.is_symlink(): continue
                        try:
                            stat = file.stat()
                            signature = (str(file),stat.st_size,stat.st_mtime_ns)
                            duration = self.durations.get(signature)
                            if duration is None:
                                audio = AudioFile(file)
                                if audio is not None: duration = float(audio.info.length)
                                else:
                                    duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(file)],text=True,timeout=20))
                                self.durations[signature] = duration
                            if duration <= 0: continue
                        except Exception as exc:
                            print(f"Skipping unreadable audio {file}: {exc}",file=sys.stderr)
                            continue
                        tracks.append(Track(file.stem,file,duration,offset));offset+=duration
                    if not tracks: continue
                    rel = str(folder.relative_to(root))
                    # Retain original Economist IDs used by existing LR jobs.
                    identity = rel if library_id == 'audiobooks' else library_id+'/'+rel
                    issue_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
                    subs=folder/'transcript.srt'
                    subtitle_status = 'legacy'
                    if subs.is_file():
                        try:
                            from srt_order import read_order
                            ordered, subtitle_status = read_order(subs, [t.path for t in tracks])
                            by_path = {t.path.resolve(): t for t in tracks}
                            offset = 0.0; reordered = []
                            for path in ordered:
                                t = by_path[path]
                                reordered.append(Track(t.name, t.path, t.duration, offset)); offset += t.duration
                            tracks = reordered
                        except (OSError, ValueError, KeyError, TypeError) as exc:
                            print(f'Combined SRT unavailable for {folder}: {exc}', file=sys.stderr)
                            subs = None
                    ids=set(item_paths.get(str(folder),[]))
                    for track in tracks: ids.update(item_paths.get(str(track.path),[]))
                    result[issue_id] = Issue(issue_id,rel,folder,tuple(tracks),subs if subs and subs.is_file() else None,library_id,tuple(sorted(ids)),subtitle_status)
            self._issues = result
            self._updated = time.monotonic()
            return result

    def track(self, issue_id: str, index: int) -> tuple[Issue, Track]:
        issue = self.issues().get(issue_id)
        if issue is None or index < 0 or index >= len(issue.tracks):
            raise KeyError("No matching audiobook article")
        return issue, issue.tracks[index]

    def subtitle(self, issue: Issue, track: Track) -> str:
        individual = track.path.with_suffix(".srt")
        if individual.is_file():
            text = individual.read_text(encoding="utf-8-sig")
            if parse_srt(text): return text
        if issue.subtitle_status == 'partial':
            raise KeyError('整书转写尚未完成，当前音轨没有独立 SRT')
        if not issue.subtitles:
            raise KeyError("No existing SRT subtitles for this issue")
        entries = parse_srt(issue.subtitles.read_text(encoding="utf-8-sig"))
        if not entries:
            raise KeyError("Subtitle file contains no valid timestamps")
        # A combined SRT cannot reliably be split if it isn't aligned to the MP3s.
        total = issue.tracks[-1].offset + issue.tracks[-1].duration
        if entries[-1][1] > total + 2 or (issue.subtitle_status == 'legacy' and abs(total - entries[-1][1]) > max(40.0, total * .02)):
            raise KeyError("Combined subtitles do not match the article audio timeline")
        result = split_srt(entries, track.offset, track.duration)
        if not result:
            raise KeyError("No captions overlap this audio article")
        return result

    def catalog(self) -> dict:
        issues = sorted(self.issues().values(), key=lambda issue: issue.name, reverse=True)
        return {"issues": [
            {"id": issue.id, "name": issue.name, "library": issue.library, "itemIds": list(issue.item_ids),
             "hasSubtitles": bool(issue.subtitles or any(t.path.with_suffix('.srt').is_file() for t in issue.tracks)),
             "tracks": [{"index": i, "name": track.name, "duration": round(track.duration, 2),
                         "hasSubtitles": bool(issue.subtitles or track.path.with_suffix('.srt').is_file())}
                        for i, track in enumerate(issue.tracks)]}
            for issue in issues
        ]}


def playable_audio(track):
    # Chrome cannot decode WMA. Cache a local conversion, preserving source media.
    if track.path.suffix.lower() not in {'.wma', '.aac'}:
        return track.path, track.path.name, mimetypes.guess_type(track.path.name)[0] or 'audio/mpeg'
    stat=track.path.stat()
    key=hashlib.sha256(json.dumps([str(track.path),stat.st_size,stat.st_mtime_ns]).encode()).hexdigest()
    folder=Path(tempfile.gettempdir())/'lr-audio-cache';folder.mkdir(exist_ok=True)
    target=folder/(key+'.mp3')
    with CONVERSION_LOCK:
        if not target.is_file():
            temp=target.with_suffix('.tmp.mp3')
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(track.path),'-vn','-c:a','libmp3lame','-q:a','3',str(temp)],
                           capture_output=True,check=True,timeout=1800)
            temp.replace(target)
    return target, track.name+'.mp3', 'audio/mpeg'


CONVERSION_LOCK=threading.RLock()


class BridgeHandler(BaseHTTPRequestHandler):
    index: LibraryIndex
    key: str

    def do_GET(self):
        if self.headers.get("X-LR-Bridge-Key") != self.key:
            self.error(403, "Bridge key required")
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/api/catalog":
            self.data(json.dumps(self.index.catalog(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
            return
        match = re.fullmatch(r"/api/(audio|subtitle)/([a-f0-9]{20})/(\d{1,4})", path)
        if not match:
            self.error(404, "Unknown endpoint")
            return
        media_type, issue_id, index = match.groups()
        try:
            issue, track = self.index.track(issue_id, int(index))
            audio_path, audio_name, audio_mime = playable_audio(track)
            if media_type == "subtitle":
                body = self.index.subtitle(issue, track)
                self.data(json.dumps({"filename": track.name + ".srt", "srt": body,
                                     "audioFilename": audio_name,
                                     "mimeType": audio_mime}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            else:
                self.send_response(200)
                self.send_header("Content-Type", audio_mime)
                self.send_header("Content-Length", str(audio_path.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with audio_path.open("rb") as source:
                    while chunk := source.read(256 * 1024):
                        self.wfile.write(chunk)
        except (KeyError, OSError, subprocess.SubprocessError) as exc:
            self.error(404, str(exc))

    def data(self, body: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def error(self, status: int, message: str):
        self.data_error(status, message)

    def data_error(self, status: int, message: str):
        body = json.dumps({"error": message}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if os.environ.get("LR_BRIDGE_VERBOSE") == "1":
            super().log_message(fmt, *args)


def run():
    config = json.loads((ROOT / "config.json").read_text())
    roots = {key:Path(value) for key,value in json.loads(os.environ.get("LR_BRIDGE_LIBRARIES", "{}")).items()}
    BridgeHandler.index = LibraryIndex(Path(os.environ.get("LR_BRIDGE_LIBRARY", config["library"])), roots or None)
    BridgeHandler.key = config["key"]
    servers = []
    address_override = os.environ.get("LR_BRIDGE_LISTEN")
    for address in [address_override or "127.0.0.1"]:
        try:
            server = ThreadingHTTPServer((address, int(config.get("port", 13381))), BridgeHandler)
            servers.append(server)
            print(f"LR bridge listening on http://{address}:{server.server_port}", flush=True)
        except OSError as exc:
            print(f"Cannot bind {address}: {exc}", file=sys.stderr, flush=True)
    if not servers:
        raise SystemExit("No address available for the bridge")
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    run()
