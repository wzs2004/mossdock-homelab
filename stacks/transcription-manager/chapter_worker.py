#!/usr/bin/env python3
"""Automatically transcribe unchaptered Audiobookshelf books and add chapters.

Audio stays in its original format. New chapter metadata is written through
Audiobookshelf's supported /api/items/:id/chapters endpoint.
"""

import argparse
import spoken_chapters
import bisect
import functools
import hashlib
import json
import logging
import math
import os
import re
import signal
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

LOG = logging.getLogger("chapterizer")
ROOTS = (Path("/audiobooks"), Path("/audiobooks115"))
ROOT = ROOTS[0]
DATA = Path(os.environ.get("DATA_DIR", "/data"))
MODEL = Path(os.environ.get("WHISPER_MODEL", "/models/ggml-small.en.bin"))
LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "en")
REQUIRE_SUBTITLES = os.environ.get("CHAPTER_REQUIRE_SUBTITLES", "0") == "1"
ALLOW_SINGLE = os.environ.get("CHAPTER_SINGLE_FILE", "0") == "1"
THREADS = max(1, int(os.environ.get("WHISPER_THREADS", "4")))
MIN_CHAPTER = max(30, int(os.environ.get("MIN_CHAPTER_SECONDS", "180")))
TARGET_CHAPTER = max(MIN_CHAPTER * 2, int(os.environ.get("TARGET_CHAPTER_SECONDS", "720")))
MAX_CHAPTER = max(TARGET_CHAPTER + 60, int(os.environ.get("MAX_CHAPTER_SECONDS", "1500")))
SETTLE_SECONDS = max(0, int(os.environ.get("SETTLE_SECONDS", "240")))
ABS_URL = os.environ.get("ABS_BASE_URL", "http://audiobookshelf:80").rstrip("/")
TOKEN = os.environ.get("ABS_API_TOKEN", "").strip()
SKIP_KEYWORDS = tuple(
    word.strip().casefold() for word in
    os.environ.get("SKIP_KEYWORDS", "the economist,经济学人").split(",")
    if word.strip()
)

TIME_PATTERN = re.compile(
    r"(?m)^(\d{1,3}:\d\d:\d\d[,.]\d{3})\s+-->\s+"
    r"(\d{1,3}:\d\d:\d\d[,.]\d{3})"
)
SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|(?<=[。！？])")
CHAPTER_ANNOUNCEMENT = re.compile(
    r"^\s*(?:chapter|part|section|book)\s+(?:\d+|[ivxlcdm]+|"
    r"one|two|three|four|five|six|seven|eight|nine|ten)\b|"
    r"^\s*第[一二三四五六七八九十百千万零〇\d]+[章节卷部篇回]",
    re.IGNORECASE,
)
CHAPTER_FILENAME = re.compile(
    r"^(?:\d{1,4}[\s._-]+)?(?:chapter|chap\.?|ch\.?)\s*[\divxlcdm]+\b|"
    r"^第[一二三四五六七八九十百千万零〇\d]+[章节回篇]",
    re.IGNORECASE,
)
GENERIC_TRACK_TITLE = re.compile(
    r"^(?:(?:track|audio|file|segment|disc|disk|cd|part|volume|vol|"
    r"音轨|文件|片段|分段|第?\d+段|第?\d+部分)\s*[-_.#:]?\s*\d+|"
    r"\d+\s*[-_. ]*\s*(?:track|audio|file|segment|disc|disk|cd|part)|"
    r"\d+)$",
    re.IGNORECASE,
)
STOPWORDS = set(
    "the a an and or but in on of for by to is are was were be been being "
    "that this those these with from into over at as it its his her their "
    "he she they we you i me us do does did have has had so then than just "
    "not no yes if what when where who how will would could should can may "
    "there here about after before again very much more some all one two "
    "because which while through any your our up out down also".split()
)


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


def seconds(timestamp):
    hours, minutes, rest = timestamp.replace(".", ",").split(":")
    second, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(second) + int(millis) / 1000


def srt_time(value):
    millis = max(0, round(value * 1000))
    hours, rem = divmod(millis, 3600000)
    minutes, rem = divmod(rem, 60000)
    second, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{second:02d},{ms:03d}"


def parse_srt(value):
    cues = []
    for block in re.split(r"\n\s*\n", value.replace("\r\n", "\n").strip()):
        match = TIME_PATTERN.search(block)
        if not match:
            continue
        start, end = seconds(match[1]), seconds(match[2])
        line = block[match.end():].splitlines()
        content = " ".join(x.strip() for x in line if x.strip())
        content = re.sub(r"<[^>]*>", "", content).strip()
        if content and end > start:
            cues.append((start, end, content))
    return sorted(cues)


def read_srt(path):
    if not path.is_file():
        return []
    return parse_srt(path.read_text("utf-8-sig", errors="replace"))


def render_srt(cues):
    return "\n\n".join(
        f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{sentence}"
        for index, (start, end, sentence) in enumerate(cues, 1)
    ) + "\n"


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


def is_excluded(book, paths=()):
    """Skip known chaptered collections even when their track chapters are absent."""
    media = book.get("media") or {}
    metadata = media.get("metadata") or {}
    names = [book.get("path", ""), book.get("title", ""),
             metadata.get("title", ""), *(str(path) for path in paths)]
    content = " ".join(str(name or "") for name in names).casefold()
    return any(keyword in content for keyword in SKIP_KEYWORDS)


def is_explicit_chapter_files(paths):
    """Conservatively recognize files explicitly labeled as complete chapters."""
    return len(paths) > 1 and all(CHAPTER_FILENAME.match(path.stem) for path in paths)


def is_generic_track_title(title):
    title = re.sub(r"\.[a-z0-9]{2,5}$", "", str(title or "").strip(), flags=re.I)
    title = re.sub(r"^\d{1,4}\s*[-._]+\s*", "", title)
    title = re.sub(r"\s+", " ", title)
    return bool(GENERIC_TRACK_TITLE.fullmatch(title))


def is_file_split_chapters(chapters, paths, durations):
    """Detect ABS chapter placeholders that merely mirror MP3 track splits.

    Only replace them when both the timestamps AND generic labels suggest
    technical tracks. Named chapter files are treated as authored chapters.
    """
    if len(paths) < 2 or len(chapters) != len(paths) or len(durations) != len(paths):
        return False
    offsets = [0.0]
    for duration in durations:
        offsets.append(offsets[-1] + duration)
    for index, chapter in enumerate(chapters):
        try:
            start, end = float(chapter["start"]), float(chapter["end"])
        except (ValueError, TypeError, KeyError):
            return False
        if abs(start - offsets[index]) > 3 or abs(end - offsets[index + 1]) > 3:
            return False
    if is_explicit_chapter_files(paths):
        return False
    threshold = math.ceil(len(chapters) * 0.8)
    generic = sum(is_generic_track_title(chapter.get("title")) for chapter in chapters)
    # ABS also mirrors files named "01 Author - Book", "02 Author - Book".
    # Identical book labels at file boundaries are technical splits, not chapter names.
    labels = [re.sub(r"^\s*\d{1,4}\s*[-._ ]*", "", str(chapter.get("title", "")).casefold())
              for chapter in chapters]
    repeated = Counter(label for label in labels if label).most_common(1)
    return generic >= threshold or bool(repeated and repeated[0][1] >= threshold)


def fingerprint(paths):
    contents = [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths]
    return hashlib.sha256(json.dumps(contents, ensure_ascii=False).encode()).hexdigest()


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


def transcribe(path):
    """Recognize one audio file, caching SRT by its size and modification time."""
    state = DATA / "transcripts"
    state.mkdir(parents=True, exist_ok=True)
    key = fingerprint([path])
    cached = state / (key + ".srt")
    if read_srt(cached):
        return read_srt(cached)
    if not MODEL.is_file():
        raise RuntimeError(f"Whisper model not found: {MODEL}")
    LOG.info("Transcribing %s", display_path(path))
    with tempfile.TemporaryDirectory(prefix="chapter-asr-", dir=DATA) as folder:
        work = Path(folder)
        wav = work / "audio.wav"
        output_base = work / "audio"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-y", "-i", str(path), "-vn", "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", str(wav)],
            check=True, capture_output=True,
        )
        with (DATA / "whisper.log").open("ab") as log:
            subprocess.run(
                ["whisper-cli", "-m", str(MODEL), "-f", str(wav),
                 "-l", LANGUAGE, "-t", str(THREADS), "-ml", "80",
                 "-osrt", "-of", str(output_base), "-np"],
                check=True, stdout=log, stderr=subprocess.STDOUT,
            )
        produced = output_base.with_suffix(".srt")
        if not read_srt(produced):
            raise RuntimeError("Whisper returned an empty transcript; see /data/whisper.log")
        produced.replace(cached)
    return read_srt(cached)


def save_srt_if_missing(path, cues):
    if path.exists():
        return
    # Atomic rename keeps a partially written subtitle from the ABS file watcher.
    temporary = path.with_name("." + path.name + ".chapterizer.tmp")
    try:
        temporary.write_text(render_srt(cues), encoding="utf-8")
        if not path.exists():
            temporary.replace(path)
    except OSError as error:
        # Audiobook storage can be read-only to the chapterizer user.
        # Whisper's transcript cache in /data remains available for retries.
        LOG.warning("Cannot save optional subtitle %s: %s", path, error)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def transcript(paths, durations):
    """Reuse existing SRT whenever possible, preserving user-authored subtitles."""
    folder = paths[0].parent
    total = sum(durations)
    if all(path.parent == folder for path in paths):
        candidates = [folder / "transcript.srt"]
        if len(paths) == 1:
            candidates.insert(0, paths[0].with_suffix(".srt"))
        for srt in candidates:
            cues = read_srt(srt)
            if len(cues) >= 5 and cues[0][0] <= total * 0.1 and cues[-1][1] >= total * 0.9:
                LOG.info("Using existing subtitle %s", display_path(srt))
                return cues

    cues, offset = [], 0.0
    for path, length in zip(paths, durations):
        local = path.with_suffix(".srt")
        part = read_srt(local)
        if not part or (not REQUIRE_SUBTITLES and (part[0][0] > length * 0.1 or part[-1][1] < length * 0.9)):
            if REQUIRE_SUBTITLES:
                raise RuntimeError("Waiting for shared transcription queue; chapterizer will not transcribe twice")
            part = transcribe(path)
            if not local.exists():
                save_srt_if_missing(local, part)
        cues.extend((start + offset, end + offset, value) for start, end, value in part)
        offset += length
    if len(paths) > 1 and all(path.parent == folder for path in paths):
        save_srt_if_missing(folder / "transcript.srt", cues)
    return sorted(cues)


def word_counts(text):
    text = unicodedata.normalize("NFKC", text.lower())
    result = Counter(
        token for token in re.findall(r"[a-z]{3,}", text)
        if token not in STOPWORDS
    )
    # Chinese has no whitespace word boundaries. Character bigrams provide
    # a lightweight, language-independent estimate of topic continuity.
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return result


def cosine_distance(left, right):
    if not left or not right:
        return 0.0
    dot = sum(count * right.get(key, 0) for key, count in left.items())
    norm_a = math.sqrt(sum(count * count for count in left.values()))
    norm_b = math.sqrt(sum(count * count for count in right.values()))
    return 1 - dot / max(1.0, norm_a * norm_b)


def explicit_heading(text):
    return bool(CHAPTER_ANNOUNCEMENT.match(text.strip()))


def boundaries(cues, duration, minimum=MIN_CHAPTER, target=TARGET_CHAPTER, maximum=MAX_CHAPTER):
    """Only spoken headings create chapter boundaries; duration targets are ignored."""
    return [item['start'] for item in spoken_chapters.detect(cues,duration)['accepted'] if 0<item['start']<duration]


def chapter_title(cues, start, number):
    headings=spoken_chapters.detect(cues,max((b for _,b,_ in cues),default=0))['accepted']
    candidate=next((h for h in headings if abs(h['start']-start)<=.5),None)
    return candidate['title'] if candidate else '未识别到朗读章标题'


def make_chapters(cues, duration, toc=()):
    return spoken_chapters.detect(cues,duration,toc)['chapters']


def evidence_path(item_id):
    return DATA/'chapter-evidence'/(hashlib.sha256(item_id.encode()).hexdigest()+'.json')


def save_evidence(item_id, report):
    file=evidence_path(item_id);file.parent.mkdir(parents=True,exist_ok=True)
    temp=file.with_suffix('.tmp');temp.write_text(json.dumps(report,ensure_ascii=False,indent=2));temp.replace(file)


def preview_headings(book):
    """Preview currently available text without ASR or changing ABS chapters."""
    paths=book_tracks(book);lengths=[probe_duration(path) for path in paths]
    complete=all(read_srt(path.with_suffix('.srt')) for path in paths)
    if complete:
        cues=transcript(paths,lengths)
    else:
        cues=[];offset=0.0
        for path,duration in zip(paths,lengths):
            cues.extend((a+offset,b+offset,text) for a,b,text in read_srt(path.with_suffix('.srt')))
            offset+=duration
    toc=spoken_chapters.read_toc(paths[0].parent) if paths else []
    report=spoken_chapters.detect(cues,sum(lengths),toc)
    report.update(complete=complete,missing_tracks=sum(not bool(read_srt(p.with_suffix('.srt'))) for p in paths),toc_titles=len(toc))
    save_evidence(book['id'],report)
    return report


def has_chapters(book, duration, paths=(), durations=()):
    existing = (book.get("media") or {}).get("chapters") or []
    if len(existing) > 1:
        return not is_file_split_chapters(existing, paths, durations)
    if len(existing) == 1:
        entry = existing[0]
        # Audiobookshelf sometimes generates a single placeholder spanning
        # the entire recording, which provides no useful navigation.
        full = entry.get("start", 0) <= 3 and entry.get("end", 0) >= duration * 0.95
        return not full
    return False



def previous_generated_backup(book):
    audit=DATA/'chapter-migration-audit.json'
    if not audit.is_file():return None
    normalized=lambda values:[(round(float(c['start']),2),round(float(c['end']),2),c['title']) for c in values]
    current=(book.get('media') or {}).get('chapters') or []
    for entry in json.loads(audit.read_text()):
        if entry.get('item_id')==book['id'] and entry.get('verified_ours') and normalized(current)==normalized(entry['current_chapters']):return entry
    return None


def restore_previous_chapters(book):
    backup=previous_generated_backup(book)
    if not backup:raise ValueError('当前章节已变化，无法安全恢复旧备份')
    reply=request('/api/items/'+urllib.parse.quote(book['id'],safe='')+'/chapters','POST',{'chapters':backup['old_chapters']})
    if not reply.get('success'):raise RuntimeError('Audiobookshelf 未接受恢复操作')
    return len(backup['old_chapters'])


def process_book(book, state, dry_run=False):
    item_id = book["id"]
    media = book.get("media") or {}
    if is_excluded(book):
        return False
    paths = book_tracks(book)
    # Focus on books spread across several unchaptered MP3 recordings.
    if not paths or (len(paths) < 2 and not ALLOW_SINGLE):
        return False
    if any(path.suffix.lower() not in {".mp3", ".m4a", ".m4b", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aac"} for path in paths):
        return False
    if is_excluded(book, paths) or is_explicit_chapter_files(paths):
        return False
    signature = fingerprint(paths)+":"+spoken_chapters.ALGORITHM
    if state.get(item_id) == signature:
        return False
    if REQUIRE_SUBTITLES and not (all(read_srt(path.with_suffix(".srt")) for path in paths)
                                 or read_srt(paths[0].parent / "transcript.srt")):
        return False
    lengths = [probe_duration(path) for path in paths]
    duration = sum(lengths)
    if has_chapters(book, duration, paths, lengths) and not previous_generated_backup(book):
        return False
    if any(time.time() - path.stat().st_mtime < SETTLE_SECONDS for path in paths):
        LOG.info("Waiting for import to settle: %s", item_id)
        return False
    signature = fingerprint(paths)+":"+spoken_chapters.ALGORITHM
    if state.get(item_id) == signature:
        return False
    title = (media.get("metadata") or {}).get("title") or item_id
    LOG.info("Analyzing '%s' (%d tracks, %.1f min)", title, len(paths), duration / 60)
    cues = transcript(paths, lengths)
    toc=spoken_chapters.read_toc(paths[0].parent)
    report=spoken_chapters.detect(cues,duration,toc)
    report.update(complete=True,missing_tracks=0,toc_titles=len(toc))
    save_evidence(item_id,report)
    chapters = report['chapters']
    if not chapters:
        LOG.warning("No reliable spoken chapter heading found: %s", title)
        state[item_id] = signature
        return True
    LOG.info("Proposed %d chapters for '%s': %s", len(chapters), title,
             ", ".join(f"{c['start']:.0f}s {c['title'][:40]}" for c in chapters))
    if not dry_run:
        # Re-fetch before writing: manual chapters added during transcription win.
        latest = request("/api/items/" + urllib.parse.quote(item_id, safe=""))
        if has_chapters(latest, duration, paths, lengths) and not previous_generated_backup(latest):
            LOG.info("Chapters were added by someone else; preserving them")
            return False
        backup_dir = DATA / "chapter-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / (hashlib.sha256(item_id.encode()).hexdigest()+"-"+str(time.time_ns())+".json")
        backup.write_text(json.dumps({"item_id":item_id,"chapters":(latest.get("media") or {}).get("chapters",[])},ensure_ascii=False),encoding="utf-8")
        answer = request("/api/items/" + urllib.parse.quote(item_id, safe="") +
                         "/chapters", method="POST", body={"chapters": chapters})
        if not answer.get("success"):
            raise RuntimeError(f"Audiobookshelf rejected chapters for {title}")
        verified = request("/api/items/" + urllib.parse.quote(item_id, safe=""))
        if len(((verified.get("media") or {}).get("chapters") or [])) != len(chapters):
            raise RuntimeError(f"Chapter verification failed for {title}")
        state[item_id] = signature
        LOG.info("Published chapters: %s (%d)", title, len(chapters))
    return True


def read_state():
    path = DATA / "state.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text("utf-8"))
    return data if isinstance(data, dict) else {}


def write_state(state):
    path = DATA / "state.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(path)


def scan(dry_run=False, book_dir=None):
    """Scan one newly imported ABS book (or all books if manually requested).

    Returns (number of matching items, number of processing errors).
    """
    controls_file = Path(os.environ.get("AUTOMATION_CONTROLS_FILE", str(DATA / "library-automation-controls.json")))
    if controls_file.is_file() and not json.loads(controls_file.read_text()).get("auto_chapters", True):
        LOG.info("Automatic chapters paused in extension settings")
        return 0, 0
    libraries = request("/api/libraries").get("libraries", [])
    state = read_state()
    changed, matched, errors = 0, 0, 0
    for library in libraries:
        if library.get("mediaType") != "book":
            continue
        library_id = urllib.parse.quote(library["id"], safe="")
        response = request(f"/api/libraries/{library_id}/items?limit=0&minified=0")
        for item in response.get("results", []):
            if item.get("mediaType") != "book" or item.get("isMissing") or item.get("isInvalid"):
                continue
            if book_dir and str(item.get("path", "")).rstrip("/") != book_dir.rstrip("/"):
                continue
            matched += 1
            if is_excluded(item):
                continue
            try:
                # Library listing entries can be minified and omit audioFiles
                # and chapters. Always fetch the complete item before deciding.
                detail = request("/api/items/" + urllib.parse.quote(item["id"], safe=""))
                if process_book(detail, state, dry_run):
                    changed += 1
                    if not dry_run:
                        write_state(state)
            except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
                errors += 1
                LOG.error("Cannot process %s: %s", item.get("id"), error)
    LOG.info("Scan complete: %d matched, %d processed, %d failed", matched, changed, errors)
    return matched, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changing ABS chapters")
    parser.add_argument("--book-dir", help="Only process the new ABS item at this directory")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    DATA.mkdir(parents=True, exist_ok=True)
    if not TOKEN:
        LOG.warning("ABS_API_TOKEN is not configured; set it in .env then restart this service")
        if args.once:
            return 2
    if args.once:
        try:
            if args.book_dir and not any(
                args.book_dir.startswith(str(root) + "/") for root in ROOTS
            ):
                LOG.error("Requested book folder is outside configured roots")
                return 2
            matched, errors = scan(args.dry_run, args.book_dir)
            if errors:
                return 1
            if args.book_dir and not matched:
                LOG.warning("New book has not appeared in ABS yet: %s", args.book_dir)
                return 3
            return 0
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            LOG.error("Scan failed: %s", error)
            return 1
    LOG.info("Event-driven mode ready; scans run only when the host watcher reports a new book")
    while True:
        signal.pause()


if __name__ == "__main__":
    raise SystemExit(main())
