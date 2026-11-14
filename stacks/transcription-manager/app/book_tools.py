"""Book-scoped transcription, manuscript viewing and metadata matching."""
import hashlib
import json
import math
import os
import secrets
import shutil
import threading
import time
import urllib.parse
from pathlib import Path
import abs_api
import transcription_manager as m

LOCK = threading.RLock()
CACHE = (0, [])
# 字幕存在性缓存：每条音轨都要在机械盘上 stat 三个候选路径，
# 全库 5000+ 条音轨会把 catalog 拖到一分钟。TTL 取得短，新字幕最迟 20 秒可见。
SUBTITLE_CACHE = {}
SUBTITLE_TTL = 600
CATALOG_CACHE = (0, None)
CATALOG_TTL = 20          # 新鲜期；过期后后台刷新，仍然立刻返回旧数据
CATALOG_REFRESHING = None
JOBS = {}
MATCH_SEARCHES = {}
PROVIDERS = {
    'audible': 'Audible 美国', 'audible.uk': 'Audible 英国',
    'audible.ca': 'Audible 加拿大', 'audible.au': 'Audible 澳大利亚',
    'audible.de': 'Audible 德国', 'audible.fr': 'Audible 法国',
    'audible.jp': 'Audible 日本', 'audible.it': 'Audible 意大利',
    'audible.in': 'Audible 印度', 'audible.es': 'Audible 西班牙',
    'google': 'Google Books', 'openlibrary': 'Open Library',
    'itunes': 'iTunes', 'fantlab': 'FantLab',
}


def match_providers():
    """Use Audiobookshelf's installed providers, including user-defined ones."""
    result = abs_api.request('/api/search/providers')
    providers = (result.get('providers') or {}).get('books') if isinstance(result, dict) else None
    if not isinstance(providers, list):
        raise ValueError('Audiobookshelf 未返回可用的书籍匹配来源')
    available = [{'value': source['value'], 'text': source.get('text') or source['value']}
                 for source in providers if isinstance(source, dict)
                 and isinstance(source.get('value'), str) and source['value']]
    return {'providers': available}


def load_jobs():
    global JOBS
    path = m.DATA / 'book-match-jobs.json'
    if path.exists():
        JOBS = json.loads(path.read_text())
        for value in JOBS.values():
            if value.get('status') == 'running':
                value.update(status='failed', error='服务已重启，请重新点击匹配', stage='任务中断')


def save_jobs():
    with LOCK:
        path = m.DATA / 'book-match-jobs.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(JOBS, ensure_ascii=False)); temp.replace(path)


load_jobs()


def endpoint(item):
    return '/api/items/' + urllib.parse.quote(item, safe='')


def inventory(force=False):
    global CACHE
    with LOCK:
        if not force and time.monotonic() - CACHE[0] < 60:
            return CACHE[1]
        books = []
        for lib in abs_api.request('/api/libraries').get('libraries', []):
            if lib.get('mediaType') != 'book':
                continue
            for item in abs_api.request('/api/libraries/' + urllib.parse.quote(lib['id'], safe='') + '/items?limit=0&minified=0').get('results', []):
                if not item.get('isMissing') and not item.get('isInvalid'):
                    books.append(dict(abs_api.request(endpoint(item['id'])), library_name=lib['name']))
        CACHE = (time.monotonic(), books)
        return books


def book(item_id):
    if not isinstance(item_id, str) or not item_id or len(item_id) > 200:
        raise ValueError('请选择当前书籍')
    result = abs_api.request(endpoint(item_id))
    if not tracks(result):  # Validate all paths before any mutation.
        raise ValueError("书籍没有可识别的音轨")
    return result


def relative(path):
    for key, root in m.ROOTS.items():
        if path.is_relative_to(root.resolve()):
            return key + '/' + path.relative_to(root.resolve()).as_posix()
    raise ValueError('书籍不在已挂载的媒体库内')


def tracks(item):
    paths = [p.resolve() for p in abs_api.book_tracks(item)]
    for path in paths:
        m.safe_audio(relative(path))
    return paths


def document_path(item):
    source = Path(item['path']).resolve()
    folder = source.parent if item.get('isFile') else source
    relative(tracks(item)[0])
    if not any(folder.is_relative_to(root.resolve()) for root in m.ROOTS.values()):
        raise ValueError('无效的书籍目录')
    return source.with_suffix('.srt') if item.get('isFile') else folder / 'transcript.srt'


def track_duration(item, path):
    stat = path.stat()
    for entry in (item.get('media') or {}).get('audioFiles') or []:
        metadata = entry.get('metadata') or {}
        filename = metadata.get('path') or str(Path(item['path']) / metadata.get('filename', ''))
        value = entry.get('duration')
        if Path(filename).resolve() == path and isinstance(value, (float, int)) and math.isfinite(value) and value > 0:
            if metadata.get('size') == stat.st_size and abs(metadata.get('mtimeMs', 0) - stat.st_mtime * 1000) < 2:
                return float(value)
    return abs_api.probe_duration(path)


def timeline(item):
    entries = []; offset = 0; complete = True
    for path in tracks(item):
        length = track_duration(item, path)
        source = m.subtitle(path)
        part = m.cues(source.read_text(encoding='utf-8-sig', errors='replace'), 0) if source else []
        part = [(a, z, t) for a, z, t in part if 0 <= a < length and a < z <= length + 2]
        complete = complete and bool(part)
        for start, end, text in part:
            if 0 <= start < length and start < end <= length + 2:
                entries.append({'id': len(entries), 'start': round(offset + start, 3), 'end': round(min(offset + end, offset + length), 3), 'text': text})
        offset += length
    revision = hashlib.sha256(json.dumps([entries, offset], ensure_ascii=False).encode()).hexdigest()
    return dict(cues=entries, duration=offset, complete=complete, revision=revision)


def manuscript(item):
    info = timeline(item)
    if not info['cues']:
        return info
    text = m.render_srt([(c['start'], c['end'], c['text']) for c in info['cues']])
    target = document_path(item)
    with LOCK:
        if not target.exists() or target.read_text(encoding='utf-8-sig') != text:
            if target.exists():
                folder = m.DATA / 'book-srt-backups'; folder.mkdir(exist_ok=True)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                backup = folder / (hashlib.sha256(item['id'].encode()).hexdigest() + '-' + digest + '.srt')
                if not backup.exists(): backup.write_bytes(target.read_bytes())
            temp = target.with_suffix('.srt.tmp')
            temp.write_text(text, encoding='utf-8'); temp.replace(target)
            __import__('media_sync').WAKE.set()
        if not item.get('isFile'):
            # Both consumers validate this contract before splitting the full timeline.
            order = {'version': 1, 'item_id': item['id'], 'complete': info['complete'],
                     'srt_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                     'tracks': [{'path': p.relative_to(target.parent).as_posix(),
                                 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns,
                                 'duration': track_duration(item, p)} for p in tracks(item)]}
            marker = target.parent / 'transcript.order.json'
            encoded = json.dumps(order, ensure_ascii=False, sort_keys=True)
            if not marker.exists() or marker.read_text() != encoded:
                temp = marker.with_suffix('.json.tmp'); temp.write_text(encoded); temp.replace(marker)
    return dict(info, document=target.name, srt=text)


def update_manuscripts():
    for item in inventory():
        try:
            # List responses may omit audioFiles; fetch the full current book.
            manuscript(book(item['id']))
        except Exception as exc:
            m.AUTO_STATE['error'] = str(exc)


def subtitle_exists(path):
    key = str(path)
    now = time.monotonic()
    hit = SUBTITLE_CACHE.get(key)
    if hit and now - hit[0] < SUBTITLE_TTL:
        return hit[1]
    value = bool(m.subtitle(path))
    SUBTITLE_CACHE[key] = (now, value)
    return value


def _build_catalog():
    tasks = {t['path']: t for t in m.rows('SELECT * FROM tasks')}
    output = []
    forced={r['path'] for r in m.rows('SELECT path FROM quality_requests')}
    for item in inventory():
        try:
            paths = tracks(item)
        except (OSError, ValueError) as exc:
            output.append(dict(id=item['id'],name=(item.get('media') or {}).get('metadata',{}).get('title',item['id']),library=item.get('library_name',''),status='failed',progress=0,eta_seconds=None,errors=['源文件不可用：'+str(exc)],stage='',tracks=[],has_transcript=False,complete=False,source_missing=True))
            continue
        if not paths:
            continue
        states = []
        for path in paths:
            key = relative(path); task = tasks.get(key, {})
            exists = subtitle_exists(path); state = task.get('status', 'missing')
            if state != 'running' and exists and not (key in forced and state in {'queued','failed'}): state = 'done'
            if state == 'done' and not exists: state = 'missing'
            states.append(dict(path=key, status=state, progress=100 if state=='done' else task.get('progress', 0), error=task.get('error',''), started=task.get('started',0), stage=task.get('stage','')))
        progress = sum(t['progress'] for t in states) / len(states)
        active = [t for t in states if t['status']=='running']
        eta = None
        if active and active[0]['progress'] >= 3:
            current=active[0]; elapsed=max(0,time.time()-current['started'])
            eta=int(elapsed/current['progress'] * sum(100-t['progress'] for t in states))
        status = next((s for s in ('running','queued','failed','missing','cancelled') if any(t['status']==s for t in states)), 'done')
        output.append(dict(id=item['id'], name=(item.get('media') or {}).get('metadata',{}).get('title',item['id']), library=item.get('library_name',''), status=status, progress=round(progress,1), eta_seconds=eta, errors=[t['error'] for t in states if t['error'] and t['status']=='failed'], stage=active[0]['stage'] if active else '', tracks=states, has_transcript=any(t['status']=='done' for t in states), complete=all(t['status']=='done' for t in states)))
    return {'books':output, 'model_error':m.model_error(), 'auto_transcribe':False,'quality_profile':__import__('asr_quality').PROFILE,'model':m.MODEL.name}


def _refresh_catalog_async():
    """后台重建目录；同一时刻只允许一个重建线程。"""
    global CATALOG_REFRESHING
    if CATALOG_REFRESHING is not None and CATALOG_REFRESHING.is_alive():
        return
    def run():
        try:
            _store_catalog(_build_catalog())
        except Exception:
            pass
    CATALOG_REFRESHING = threading.Thread(target=run, daemon=True)
    CATALOG_REFRESHING.start()


def _store_catalog(result):
    global CATALOG_CACHE
    CATALOG_CACHE = (time.monotonic(), result)
    return result


def catalog(lite=False):
    """目录接口。

    过期不阻塞：有旧数据就立刻返回并在后台刷新，避免转写占满磁盘时
    一次重建要几十秒、把面板卡住。
    lite=True 去掉每本书的音轨明细（响应体从 ~1 MB 降到几十 KB），
    面板列表只需要总数/完成数。
    """
    cached_at, cached = CATALOG_CACHE
    if cached is None:
        full = _store_catalog(_build_catalog())      # 首次（启动预热通常已填好）
    else:
        full = cached
        if time.monotonic() - cached_at >= CATALOG_TTL:
            _refresh_catalog_async()
    if not lite:
        return full
    books = []
    for item in full['books']:
        row = {k: v for k, v in item.items() if k != 'tracks'}
        tracks = item.get('tracks') or []
        row['tracks_total'] = len(tracks)
        row['tracks_done'] = sum(1 for t in tracks if t.get('status') == 'done')
        books.append(row)
    return {**{k: v for k, v in full.items() if k != 'books'}, 'books': books}


BACKUP_TTL_DAYS = int(os.environ.get('BACKUP_TTL_DAYS', '30'))
BACKUP_MAX_GB = float(os.environ.get('BACKUP_MAX_GB', '2'))


def prune_data(ttl_days=None):
    """清理占用空间且可再生的数据。

    - book-srt-backups：发布字幕前的自动备份，只保留最近 N 天
    - asr-*：转写临时分段目录，进程崩溃时会残留
    - legacy-transcripts：整期字幕拆分的缓存，同样只留 N 天
    """
    days = ttl_days if ttl_days is not None else BACKUP_TTL_DAYS
    cutoff = time.time() - days * 86400
    removed = 0
    folders = [m.DATA / 'book-srt-backups', m.DATA / 'legacy-transcripts']
    for folder in folders:
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
                elif entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    stale_cutoff = time.time() - 86400
    for entry in m.DATA.glob('asr-*'):
        try:
            if entry.is_dir() and entry.stat().st_mtime < stale_cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    # 再按总容量兜底：超过上限就从最旧的开始删，防止连续大批转写把盘占满
    folder = m.DATA / 'book-srt-backups'
    if folder.is_dir():
        files = sorted((f for f in folder.iterdir() if f.is_file()),
                       key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        limit = BACKUP_MAX_GB * 1024 ** 3
        for entry in files:
            if total <= limit:
                break
            total -= entry.stat().st_size
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass

    if removed:
        print(f'[prune] 清理 {removed} 项过期数据（保留 {days} 天 / 上限 {BACKUP_MAX_GB} GB）', flush=True)
    return removed


def match_status(item_id):
    with LOCK:
        value=dict(JOBS.get(item_id, {'status':'idle', 'progress':0}))
    if value.get('status')=='running':
        elapsed=int(time.time()-value['started'])
        value.update(elapsed_seconds=elapsed, eta_seconds=max(0,40-elapsed), eta_estimated=True)
    return value


def match_candidates(item_id, provider, title='', author=''):
    if provider not in {entry['value'] for entry in match_providers()['providers']}:
        raise ValueError('不支持该匹配来源')
    current = book(item_id)
    metadata = (current.get('media') or {}).get('metadata') or {}
    title = (title if title else metadata.get('title') or '').strip()
    author = (author if author else metadata.get('authorName') or '').strip()
    if not title or len(title) > 300 or len(author) > 300:
        raise ValueError('请输入不超过 300 字的书名和作者')
    params = urllib.parse.urlencode({'title': title, 'author': author, 'provider': provider})
    results = abs_api.request('/api/search/books?' + params)
    if not isinstance(results, list):
        raise ValueError('匹配来源返回的候选格式无效')
    results = [result for result in results if isinstance(result, dict) and result.get('title')]
    token = secrets.token_urlsafe(24)
    with LOCK:
        now = time.time()
        for old in list(MATCH_SEARCHES):
            if now - MATCH_SEARCHES[old]['at'] > 1200:
                del MATCH_SEARCHES[old]
        MATCH_SEARCHES[token] = {'at': now, 'item_id': item_id, 'provider': provider, 'results': results}
    return {'token': token, 'provider': provider, 'title': title, 'author': author,
            'results': [{**r, 'index': index} for index, r in enumerate(results)]}


def selected_metadata(candidate):
    allowed = ('title', 'subtitle', 'publisher', 'description', 'isbn', 'asin', 'language', 'publishedYear')
    metadata = {key: str(candidate[key]).strip() for key in allowed
                if candidate.get(key) is not None and str(candidate[key]).strip()}
    for source, dest in [('author', 'authors'), ('narrator', 'narrators')]:
        value = candidate.get(source)
        if isinstance(value, str) and value.strip():
            metadata[dest] = [{'name': value.strip()}] if source == 'author' else [value.strip()]
    if isinstance(candidate.get('authors'), list):
        authors = [item.get('name') if isinstance(item, dict) else item for item in candidate['authors']]
        authors = [name.strip() for name in authors if isinstance(name, str) and name.strip()]
        if authors: metadata['authors'] = [{'name': name} for name in authors]
    if isinstance(candidate.get('narrators'), list):
        narrators = [name.strip() for name in candidate['narrators'] if isinstance(name, str) and name.strip()]
        if narrators: metadata['narrators'] = narrators
    if isinstance(candidate.get('genres'), list):
        metadata['genres'] = [x for x in candidate['genres'] if isinstance(x, str) and x.strip()]
    if isinstance(candidate.get('series'), list):
        series = [{'name': item['series'], 'sequence': str(item.get('sequence') or '')}
                  for item in candidate['series'] if isinstance(item, dict)
                  and isinstance(item.get('series'), str) and item['series'].strip()]
        if series: metadata['series'] = series
    return metadata


def match(item_id, token=None, index=None):
    with LOCK:
        if JOBS.get(item_id,{}).get('status')=='running': return match_status(item_id)
        search = MATCH_SEARCHES.get(token)
        if not search or search['item_id'] != item_id or time.time() - search['at'] > 1200:
            raise ValueError('匹配候选已失效，请重新搜索')
        if type(index) is not int or index < 0 or index >= len(search['results']):
            raise ValueError('请选择列表中的匹配结果')
        candidate = search['results'][index]
        payload = selected_metadata(candidate)
        if not payload.get('title'):
            raise ValueError('匹配结果缺少书名')
        JOBS[item_id]={'status':'running','progress':0,'stage':'读取当前书籍','started':time.time()}
        save_jobs()
    def run():
        try:
            current=book(item_id)
            folder=m.DATA/'metadata-backups';folder.mkdir(exist_ok=True)
            backup=folder/(hashlib.sha256(item_id.encode()).hexdigest()+f'-{time.time_ns()}.json')
            backup.write_text(json.dumps(current,ensure_ascii=False))
            with LOCK: JOBS[item_id].update(progress=45,stage='应用所选 '+search['provider']+' 元数据（保留现有封面）')
            result=abs_api.request(endpoint(item_id)+'/media','PATCH',{'metadata':payload})
            if result.get('error') or result.get('warning'): raise ValueError(str(result.get('error') or result['warning']))
            latest = abs_api.request(endpoint(item_id))
            if ((latest.get('media') or {}).get('metadata') or {}).get('title') != payload['title']:
                raise RuntimeError('元数据写入后未通过校验，旧数据已备份')
            with LOCK: JOBS[item_id].update(status='done',progress=100,stage='已应用所选匹配结果',result={'provider':search['provider'],'title':payload['title']})
            global CACHE
            CACHE=(0,[])
        except Exception as exc:
            with LOCK: JOBS[item_id].update(status='failed',error=str(exc),stage='匹配失败，可重试')
        finally:
            save_jobs()
    threading.Thread(target=run,daemon=True).start()
    return match_status(item_id)


def clear_and_retranscribe(item_id):
    current = book(item_id)
    paths = tracks(current)
    keys = [relative(path) for path in paths]
    with m.LOCK:
        if m.model_error(): raise ValueError(m.model_error())
        if any(key not in m.scan() for key in keys): raise ValueError('音轨不在当前扫描的书库中')
        states = {r['path']: r['status'] for r in m.rows('SELECT path,status FROM tasks')}
        if any(states.get(key) in {'running', 'queued'} for key in keys):
            raise ValueError('请等待当前任务完成后再清除并重新识别')
        # queue() touches several tables. Restore their exact previous rows if any
        # track fails to queue, including cancelled/failed tasks and retry counts.
        tables = ('tasks', 'observations', 'quality_requests', 'reset_requests')
        previous = {table: {key: next(iter(m.rows(f'SELECT * FROM {table} WHERE path=?', (key,))), None)
                            for key in keys} for table in tables}
        sources = {}
        for path in paths:
            digest = m.fingerprint(path)
            for file in (path.with_suffix('.srt'), m.DATA/'transcripts'/(digest+'.srt'),
                         m.DATA/'transcripts'/(digest+'.words.json'), m.DATA/'quality'/(digest+'.json'),
                         m.DATA/'segments'/digest):
                if file.exists() or file.is_symlink(): sources[file] = None
        manuscript_file = document_path(current)
        for file in (manuscript_file, manuscript_file.parent/'transcript.order.json'):
            if file.exists() or file.is_symlink(): sources[file] = None
        if any(file.is_symlink() for file in sources):
            raise ValueError('字幕或缓存包含符号链接，请先人工检查')
        if any(child.is_symlink() for file in sources if file.is_dir() for child in file.rglob('*')):
            raise ValueError('分段缓存包含符号链接，请先人工检查')
        backup = m.DATA/'book-reset-backups'/hashlib.sha256(item_id.encode()).hexdigest()/str(time.time_ns())
        backup.mkdir(parents=True, exist_ok=False)
        for number, file in enumerate(sources):
            dest = backup/str(number)
            if file.is_dir(): shutil.copytree(file, dest)
            else: shutil.copy2(file, dest)
            sources[file] = dest
        (backup/'manifest.json').write_text(json.dumps({'book_id':item_id,'files':
            [{'original':str(source),'backup':dest.name} for source,dest in sources.items()]},ensure_ascii=False,indent=2))
        try:
            for key in keys:
                m.execute('INSERT OR REPLACE INTO reset_requests(path,requested) VALUES(?,?)',(key,time.time()))
            for file in sources:
                if file.is_dir(): shutil.rmtree(file)
                else: file.unlink()
            for path in paths:
                m.COMBINED_CACHE.pop(path.parent, None)
            for key in keys:
                if not m.queue(key, force=True):
                    raise RuntimeError('音轨无法加入重新识别队列')
        except Exception:
            for source,dest in sources.items():
                if dest.is_dir(): shutil.copytree(dest,source,dirs_exist_ok=True)
                else: shutil.copy2(dest,source)
            for table, by_key in previous.items():
                for key, row in by_key.items():
                    if row is None:
                        m.execute(f'DELETE FROM {table} WHERE path=?', (key,))
                    else:
                        columns = tuple(row)
                        placeholders = ','.join('?' for _ in columns)
                        m.execute(f'INSERT OR REPLACE INTO {table} ({",".join(columns)}) VALUES ({placeholders})',
                                  tuple(row[column] for column in columns))
            raise
    return {'ok':True,'queued':len(keys),'backup':str(backup)}


def get(url):
    params=urllib.parse.parse_qs(url.query)
    item_id=params.get('path',[''])[0]
    if url.path=='/api/books/catalog': return catalog(lite=params.get('lite',['0'])[0]=='1')
    if url.path=='/api/books/match-status': return match_status(item_id)
    if url.path=='/api/books/match-providers': return match_providers()
    if url.path=='/api/books/match-candidates':
        return match_candidates(item_id,params.get('provider',['audible'])[0],
                                params.get('title',[''])[0],params.get('author',[''])[0])
    if url.path=='/api/books/transcript':
        return manuscript(book(item_id))
    raise ValueError('接口不存在')


def post(path,payload):
    item_id=payload.get('item_id')
    if path=='/api/books/match':
        if not isinstance(item_id,str) or not item_id or len(item_id)>200: raise ValueError('请选择当前书籍')
        return match(item_id,payload.get('token'),payload.get('index'))
    if path=='/api/books/reset-transcript': return clear_and_retranscribe(item_id)
    if path=='/api/books/queue':
        force=payload.get('retranscribe',False)
        if type(force) is not bool: raise ValueError('重转写选项必须为布尔值')
        current=book(item_id); count=0
        for track in tracks(current):
            if force or not m.subtitle(track): count+=int(m.queue(relative(track),force=force))
        return {'ok':True,'queued':count}
    raise ValueError('接口不存在')
