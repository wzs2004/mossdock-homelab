"""Persistent controls and results for shared-queue ABS matching and chapters."""
import hashlib
import json
import os
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
import chapter_worker as chapters

DATA = Path(os.environ.get('DATA_DIR', '/data'))
AUTO_MATCH = os.environ.get('AUTO_MATCH', '1') == '1'
AUTO_CHAPTERS = os.environ.get('AUTO_CHAPTERS', '1') == '1'
STATE = {'last_scan': 0, 'current': '', 'matched': 0, 'chaptered': 0,
         'waiting_subtitles': 0, 'error': '', 'running': False}
CONTROL_LOCK = threading.RLock()
WAKE = threading.Event()


def atomic_json(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(file)


def load_state():
    file = DATA / 'library-automation.json'
    state = json.loads(file.read_text()) if file.exists() else {}
    for key in ('metadata', 'chapters', 'records'):
        state.setdefault(key, {})
    audit=DATA/'chapter-migration-audit.json'
    if audit.is_file():
        for prior in json.loads(audit.read_text()):
            entry=state['records'].get(prior['item_id'])
            if entry and prior.get('verified_ours') and not entry.get('chapter_algorithm') and entry.get('chapter_status')!='restored':
                entry.update(chapter_legacy=True,chapter_status='legacy',chapter_message='旧版推测结果，需手动按朗读章标题复核或恢复备份')
    return state


def save_state(state):
    atomic_json(DATA / 'library-automation.json', state)


def controls():
    with CONTROL_LOCK:
        file = DATA / 'library-automation-controls.json'
        value = json.loads(file.read_text()) if file.exists() else {}
        return {'auto_match': AUTO_MATCH, 'auto_chapters': AUTO_CHAPTERS, 'commands': [], **value}


def update_settings(values):
    if not values or set(values) - {'auto_match', 'auto_chapters'} or any(type(v) is not bool for v in values.values()):
        raise ValueError('设置仅接受自动匹配和自动分章的开关')
    with CONTROL_LOCK:
        value = controls()
        value.update(values)
        atomic_json(DATA / 'library-automation-controls.json', value)
    WAKE.set()
    return {'ok': True, 'message': '设置已保存，重启后仍然生效', 'settings': values}


def note(message):
    with (DATA / 'library-automation.log').open('a') as log:
        log.write(time.strftime('%F %T') + ' ' + message + '\n')


def action(payload):
    kind = payload.get('action')
    if kind == 'log':
        file = DATA / 'library-automation.log'
        if not file.exists(): return {'log': '暂无自动整理日志'}
        with file.open('rb') as stream:
            stream.seek(max(0, file.stat().st_size - 16000))
            return {'log': stream.read().decode(errors='replace')[-12000:]}
    if kind not in {'scan', 'retry_failed', 'match', 'chapters', 'preview_chapters', 'restore_chapters'}:
        raise ValueError('无效的自动整理操作')
    item_id = payload.get('item_id')
    state = load_state()
    if kind in {'match', 'chapters', 'preview_chapters', 'restore_chapters'} and (not isinstance(item_id, str) or item_id not in state['records']):
        raise ValueError('找不到这本书，请先检查书库')
    with CONTROL_LOCK:
        value = controls()
        command = {'id': uuid.uuid4().hex, 'action': kind, 'item_id': item_id, 'created': time.time()}
        if any(c['action'] == kind and c.get('item_id') == item_id for c in value['commands']):
            return {'ok': True, 'message': '该操作已在等待执行'}
        if len(value['commands']) >= 100: raise ValueError('待执行操作过多，请稍后再试')
        value['commands'].append(command)
        atomic_json(DATA / 'library-automation-controls.json', value)
    WAKE.set()
    return {'ok': True, 'message': '已加入自动整理队列，可在下方查看进度'}


def status(live_audio_paths=None):
    config = controls()
    state = load_state()
    records = []
    for item_id, entry in state['records'].items():
        if live_audio_paths is not None:
            # The persistent state intentionally keeps deleted books for audit
            # and recovery, but the live UI must only show scanned audio.
            book_path = str(entry.get('path') or '').lstrip('/').rstrip('/')
            if not book_path or not any(p == book_path or p.startswith(book_path + '/')
                                        for p in live_audio_paths):
                continue
        metadata = state['metadata'].get(item_id, {})
        record = dict(entry, item_id=item_id)
        record['match_status'] = ('failed' if metadata.get('error') else 'done') if metadata else 'pending'
        record['match_error'] = metadata.get('error', '')
        record['match_updated'] = metadata.get('updated', False)
        record['retry_at'] = metadata.get('retry_at', 0)
        record['pending_actions'] = [c['action'] for c in config['commands'] if c.get('item_id') == item_id]
        records.append(record)
    totals = ({'matched': sum(not bool(x.get('error')) for x in state['metadata'].values()),
               'updated': sum(bool(x.get('updated')) for x in state['metadata'].values()),
               'unmatched': sum(bool(x.get('error')) for x in state['metadata'].values())}
              if live_audio_paths is None else
              {'matched': sum(x['match_status'] == 'done' for x in records),
               'updated': sum(bool(x['match_updated']) for x in records),
               'unmatched': sum(x['match_status'] == 'failed' for x in records)})
    return {**STATE, 'metadata_errors': totals['unmatched'],
            'settings': {k: config[k] for k in ('auto_match', 'auto_chapters')},
            'pending_commands': len(config['commands']),
            'totals': {**totals,
                       'chaptered': sum(x.get('chapter_status') == 'done' for x in records),
                       'waiting': sum(x.get('chapter_status') == 'waiting' for x in records)},
            'records': sorted(records, key=lambda r: r.get('title', '').casefold())}


def record_book(state, book):
    entry = state['records'].setdefault(book['id'], {})
    entry.update(title=((book.get('media') or {}).get('metadata') or {}).get('title', book['id']),
                 path=book.get('path', ''), library_id=book.get('libraryId', ''), checked_at=time.time())
    return entry


def match_book(book, provider, state, now, force=False):
    meta = (book.get('media') or {}).get('metadata') or {}
    signature = hashlib.sha256(json.dumps([meta.get('title'), meta.get('authors')], sort_keys=True).encode()).hexdigest()
    previous = state['metadata'].get(book['id'], {})
    if not force and previous.get('signature') == signature and now < previous.get('retry_at', 0): return
    folder = DATA / 'metadata-backups'; folder.mkdir(parents=True, exist_ok=True)
    backup = folder / (hashlib.sha256(book['id'].encode()).hexdigest() + '.json')
    if not backup.exists(): backup.write_text(json.dumps(book, ensure_ascii=False))
    if not meta.get('title'): return
    record = {'signature': signature, 'retry_at': now + 3600, 'checked_at': now}
    try:
        result = chapters.request('/api/items/' + urllib.parse.quote(book['id'], safe='') + '/match', 'POST',
                                  {'provider': provider or 'audible', 'overrideDetails': False, 'overrideCover': False})
        if result.get('warning') or result.get('error'):
            record.update(error=result.get('warning') or str(result.get('error')), retry_at=now + 86400)
        else:
            record.update(updated=bool(result.get('updated')), retry_at=now + 30 * 86400)
            STATE['matched'] += 1
    except Exception as exc:
        record['error'] = str(exc)
    state['metadata'][book['id']] = record
    save_state(state)
    note('匹配 · ' + meta['title'] + ' · ' + (record.get('error') or ('已补充资料' if record.get('updated') else '资料完整')))


def chapter_book(book, state, preview_only=False):
    entry = record_book(state, book)
    entry['chapter_legacy'] = bool(chapters.previous_generated_backup(book))
    try:
        if preview_only:
            report=chapters.preview_headings(book)
            entry.update(chapter_algorithm=report['algorithm'],chapter_preview=report['accepted'],chapter_review=report['review'],
                         chapter_message=report['message']+(' · 字幕未齐，当前仅预览' if not report['complete'] else ' · 预览未写入媒体库'),
                         chapter_status='preview',chapter_error='')
            note('章标题预览 · '+entry['title']+' · '+report['message'])
            return
        paths = chapters.book_tracks(book)
        ready = paths and (all(chapters.read_srt(p.with_suffix('.srt')) for p in paths)
                           or chapters.read_srt(paths[0].parent / 'transcript.srt'))
        if not ready:
            entry.update(chapter_status='waiting', chapter_message='等待全书字幕齐全，随后自动分章', chapter_error='')
            return
        STATE['current'] = '内容分章 · ' + entry['title']
        changed = chapters.process_book(book, state['chapters'])
        latest = chapters.request('/api/items/' + urllib.parse.quote(book['id'], safe='')) if changed else book
        count = len((latest.get('media') or {}).get('chapters') or [])
        entry['chapter_legacy']=bool(chapters.previous_generated_backup(latest))
        report_file=chapters.evidence_path(book['id'])
        report=json.loads(report_file.read_text()) if report_file.is_file() else {}
        if report:
            entry.update(chapter_algorithm=report.get('algorithm'),chapter_preview=report.get('accepted',[]),chapter_review=report.get('review',[]))
        if book['id'] in state['chapters']:
            label = 'done' if report.get('accepted') and count > 0 else 'no_headings'
            message = f"已按朗读章标题生成 {count} 项目录" if label=='done' else '未识别到可靠章标题，未强行切章'
        else:
            label, message = ('preserved', f'保留已有 {count} 个正式章节') if count > 1 else ('skipped', '时长较短或当前不适合分章')
        previous = entry.get('chapter_message')
        entry.update(chapter_status=label, chapter_count=count, chapter_message=message, chapter_error='')
        if changed:
            STATE['chaptered'] += 1
        if previous != message: note('分章 · ' + entry['title'] + ' · ' + message)
    except Exception as exc:
        entry.update(chapter_status='failed', chapter_error=str(exc), chapter_message='分章失败，可重试')
        note('分章失败 · ' + entry['title'] + ' · ' + str(exc))
    finally:
        save_state(state)


def process_commands():
    did_scan = False
    # Commands remain on disk until completion, so restart recovery is idempotent.
    for command in controls()['commands']:
        STATE['current'] = '执行操作 · ' + command['action']
        state = load_state()
        try:
            kind = command['action']
            if kind == 'scan':
                run_once()
                did_scan = True
            elif kind == 'retry_failed':
                failed_matches=[item_id for item_id,value in state['metadata'].items() if value.get('error')]
                failed_chapters=[item_id for item_id,value in state['records'].items() if value.get('chapter_status')=='failed']
                for item_id in dict.fromkeys(failed_matches+failed_chapters):
                    book=chapters.request('/api/items/'+urllib.parse.quote(item_id,safe=''))
                    record_book(state,book)
                    if item_id in failed_matches: match_book(book,'audible',state,time.time(),force=True)
                    if item_id in failed_chapters: chapter_book(book,state)
                save_state(state)
            elif kind in {'match', 'chapters', 'preview_chapters', 'restore_chapters'}:
                book = chapters.request('/api/items/' + urllib.parse.quote(command['item_id'], safe=''))
                record_book(state, book)
                if kind == 'match': match_book(book, 'audible', state, time.time(), force=True)
                elif kind == 'restore_chapters':
                    count=chapters.restore_previous_chapters(book)
                    state['chapters'].pop(book['id'],None)
                    state['records'][book['id']].update(chapter_status='restored',chapter_message=f'已恢复修改前目录（{count} 项）',chapter_legacy=False)
                else: chapter_book(book, state, preview_only=(kind=='preview_chapters'))
                state['records'][book['id']].pop('operation_error',None)
                save_state(state)
            note('操作完成 · ' + kind)
        except Exception as exc:
            STATE['error'] = str(exc)
            if command.get('item_id') in state['records']:
                entry = state['records'][command['item_id']]
                entry['operation_error'] = str(exc)
                save_state(state)
            note('操作失败 · ' + command['action'] + ' · ' + str(exc))
        finally:
            with CONTROL_LOCK:
                config = controls()
                config['commands'] = [c for c in config['commands'] if c['id'] != command['id']]
                atomic_json(DATA / 'library-automation-controls.json', config)

    return did_scan


def run_once():
    state = load_state(); waiting = 0; match_candidates = []
    libraries = chapters.request('/api/libraries').get('libraries', [])
    for library in libraries:
        if library.get('mediaType') != 'book': continue
        items = chapters.request('/api/libraries/' + urllib.parse.quote(library['id'], safe='') + '/items?limit=0&minified=0').get('results', [])
        for item in items:
            if item.get('isMissing') or item.get('isInvalid') or chapters.is_excluded(item): continue
            STATE['current'] = (item.get('media') or {}).get('metadata', {}).get('title', item['id'])
            try:
                book = chapters.request('/api/items/' + urllib.parse.quote(item['id'], safe=''))
                record_book(state, book)
                config = controls()
                if config['auto_chapters']:
                    chapter_book(book, state)
                    waiting += int(state['records'][book['id']].get('chapter_status') == 'waiting')
                if config['auto_match']:
                    provider = library.get('provider') or 'audible'
                    match_candidates.append((book, 'audible' if provider == 'google' else provider))
            except Exception as exc:
                STATE['error'] = f"{item['id']}: {exc}"
                note(STATE['error'])
    started = time.monotonic(); attempted = 0
    for book, provider in match_candidates:
        if not controls()['auto_match'] or attempted >= 8 or time.monotonic() - started > 120: break
        before = state['metadata'].get(book['id'])
        STATE['current'] = '匹配书籍资料 · ' + ((book.get('media') or {}).get('metadata') or {}).get('title', book['id'])
        match_book(book, provider, state, time.time())
        if state['metadata'].get(book['id']) is not before: attempted += 1
    STATE.update(last_scan=time.time(), current='', waiting_subtitles=waiting,
                 metadata_errors=sum(bool(x.get('error')) for x in state['metadata'].values()))
    save_state(state)


def run():
    while True:
        WAKE.clear()
        STATE.update(error='', running=True)
        try:
            did_scan = process_commands()
            if not did_scan and (controls()['auto_match'] or controls()['auto_chapters']): run_once()
        except Exception as exc:
            STATE['error'] = str(exc)
        finally:
            STATE.update(running=False, current='')
        WAKE.wait(120)
