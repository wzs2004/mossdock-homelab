#!/usr/bin/env python3
"""Audiobookshelf 元数据匹配服务（独立于转写服务）。

- 搜索：调用 ABS 的 /api/search/books（Audible / iTunes 等来源）
- 预览：按 ASIN 从 Audnexus 取章节时间轴
- 应用：把选中结果写入 ABS（标题/作者/旁白/系列/简介/ASIN）+ 可选替换章节
"""
import json, os, re, secrets, threading, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ABS_BASE = os.environ.get('ABS_BASE_URL', 'http://audiobookshelf:80').rstrip('/')
ABS_TOKEN = os.environ.get('ABS_API_TOKEN', '')
META_TOKEN = os.environ.get('META_TOKEN', '')
PORT = int(os.environ.get('PORT', '13382'))
AUDNEXUS = 'https://api.audnex.us'

SEARCHES = {}          # token -> {at, item_id, provider, candidate}
LOCK = threading.Lock()
SEARCH_TTL = 1800

def req(url, method='GET', body=None, headers=None, timeout=20):
    data = None if body is None else json.dumps(body).encode()
    h = {'Content-Type': 'application/json'}
    if headers: h.update(headers)
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None

def abs_api(path, method='GET', body=None):
    return req(ABS_BASE + path, method, body, {'Authorization': 'Bearer ' + ABS_TOKEN})

def audnexus(path):
    try:
        return req(AUDNEXUS + path, timeout=12)
    except Exception:
        return None

def chapters_for(asin):
    if not asin: return []
    data = audnexus(f'/books/{asin}/chapters') or {}
    out = []
    for ch in data.get('chapters') or []:
        start = ch.get('startOffsetSec')
        length = (ch.get('lengthMs') or 0) / 1000.0
        if start is None and ch.get('startOffsetMs') is not None:
            start = ch['startOffsetMs'] / 1000.0
        if start is None: continue
        out.append({'start': round(float(start), 3), 'end': round(float(start) + length, 3),
                    'title': (ch.get('title') or '').strip() or f'Chapter {len(out)+1}'})
    return out

def split_names(value):
    if not value: return []
    if isinstance(value, list):
        return [str(v.get('name') if isinstance(v, dict) else v).strip() for v in value if v]
    return [p.strip() for p in re.split(r'\s*(?:,|&|、)\s*', str(value)) if p.strip()]

def selected_metadata(c):
    md = {}
    for key in ('title', 'subtitle', 'publisher', 'description', 'isbn', 'asin', 'language'):
        v = c.get(key)
        if v: md[key] = str(v)
    year = c.get('publishedYear') or c.get('publishedDate')
    if year: md['publishedYear'] = str(year)[:4]
    authors = split_names(c.get('author') or c.get('authors'))
    if authors: md['authors'] = [{'name': n} for n in authors]
    narrators = split_names(c.get('narrator') or c.get('narrators'))
    if narrators: md['narrators'] = narrators
    genres = c.get('genres')
    if isinstance(genres, list): md['genres'] = [str(g) for g in genres if g]
    series = c.get('series')
    if isinstance(series, list):
        out = []
        for s in series:
            if isinstance(s, dict) and s.get('series'):
                out.append({'name': s['series'], 'sequence': str(s.get('sequence') or '')})
        if out: md['series'] = out
    return md

class Handler(BaseHTTPRequestHandler):
    server_version = 'meta-bridge/1.0'
    def log_message(self, fmt, *args): print(fmt % args, flush=True)

    def send(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        if not META_TOKEN: return True
        return self.headers.get('X-Meta-Token') == META_TOKEN

    def do_GET(self):
        try:
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path == '/api/health':
                return self.send(200, {'status': 'ok'})
            if not self.authorized():
                return self.send(403, {'error': '元数据服务密钥无效'})
            if url.path == '/api/books/match-providers':
                return self.send(200, {'providers': [
                    {'value': 'audible', 'text': 'Audible（美国站）'},
                    {'value': 'audible.uk', 'text': 'Audible（英国站）'},
                    {'value': 'itunes', 'text': 'iTunes（无章节）'},
                ]})
            if url.path == '/api/books/match-status':
                return self.send(200, {'status': 'idle', 'progress': 0})
            if url.path == '/api/books/match-candidates':
                return self.candidates(q)
            return self.send(404, {'error': '接口不存在'})
        except Exception as exc:
            return self.send(400, {'error': str(exc)})

    def do_POST(self):
        try:
            url = urllib.parse.urlparse(self.path)
            length = int(self.headers.get('Content-Length') or 0)
            payload = json.loads(self.rfile.read(length) or b'{}')
            if not self.authorized():
                return self.send(403, {'error': '元数据服务密钥无效'})
            if url.path == '/api/books/match':
                return self.apply(payload)
            return self.send(404, {'error': '接口不存在'})
        except Exception as exc:
            return self.send(400, {'error': str(exc)})

    def candidates(self, q):
        item_id = (q.get('path') or [''])[0]
        provider = (q.get('provider') or ['audible'])[0]
        if not item_id: raise ValueError('缺少书籍')
        if provider not in ('audible', 'audible.uk', 'audible.ca', 'audible.au', 'audible.de', 'audible.fr', 'itunes'):
            raise ValueError('不支持的来源')
        book = abs_api('/api/items/' + urllib.parse.quote(item_id, safe=''))
        md = (book.get('media') or {}).get('metadata') or {}
        title = (q.get('title') or [''])[0].strip() or (md.get('title') or '')
        author = (q.get('author') or [''])[0].strip() or ' '.join(a.get('name', '') for a in md.get('authors') or [])
        if not title: raise ValueError('缺少书名')
        params = urllib.parse.urlencode({'provider': provider, 'title': title, 'author': author})
        results = abs_api('/api/search/books?' + params) or []
        results = [r for r in results if isinstance(r, dict) and r.get('title')]
        english = [r for r in results if str(r.get('language') or 'English').lower().startswith('english')]
        if english: results = english
        results = results[:8]
        def enrich(pair):
            idx, r = pair
            item = dict(r); item['index'] = idx
            if r.get('asin') and provider != 'itunes':
                item['chapterCount'] = len(chapters_for(r['asin']))
            return item
        with ThreadPoolExecutor(max_workers=6) as pool:
            enriched = list(pool.map(enrich, enumerate(results)))
        token = secrets.token_urlsafe(18)
        with LOCK:
            now = time.time()
            for k in [k for k, v in SEARCHES.items() if now - v['at'] > SEARCH_TTL]: SEARCHES.pop(k, None)
            SEARCHES[token] = {'at': now, 'item_id': item_id, 'provider': provider, 'results': results}
        return self.send(200, {'token': token, 'provider': provider, 'title': title,
                               'author': author, 'results': enriched})

    def apply(self, payload):
        item_id = payload.get('item_id')
        token = payload.get('token')
        index = payload.get('index')
        apply_chapters = payload.get('chapters', True)
        with LOCK:
            search = SEARCHES.get(token)
        if not search or search['item_id'] != item_id:
            raise ValueError('匹配结果已过期，请重新搜索')
        if not isinstance(index, int) or not (0 <= index < len(search['results'])):
            raise ValueError('请选择列表中的结果')
        candidate = search['results'][index]
        # 备份当前书籍
        book = abs_api('/api/items/' + urllib.parse.quote(item_id, safe=''))
        metadata = selected_metadata(candidate)
        if not metadata.get('title'): raise ValueError('结果缺少书名')
        backup_dir = os.environ.get('BACKUP_DIR', '/data/metadata-backups')
        try:
            os.makedirs(backup_dir, exist_ok=True)
            with open(os.path.join(backup_dir, f'{item_id}-{int(time.time())}.json'), 'w') as f:
                json.dump(book, f, ensure_ascii=False)
        except Exception:
            pass
        abs_api('/api/items/' + urllib.parse.quote(item_id, safe='') + '/media', 'PATCH', {'metadata': metadata})
        chapters = []
        chapters_skipped = ''
        if apply_chapters and candidate.get('asin'):
            info = audnexus('/books/' + candidate['asin']) or {}
            minutes = info.get('runtimeLengthMin') or 0
            media = book.get('media') or {}
            dur_sec = media.get('duration') or sum((f.get('duration') or 0) for f in media.get('audioFiles') or [])
            duration = dur_sec / 60.0
            if minutes and duration and abs(duration - minutes) / minutes > 0.03:
                chapters_skipped = f'；时长不符（音频 {duration:.0f} 分 / 来源 {minutes} 分），未替换章节'
            else:
                chapters = chapters_for(candidate['asin'])
        if chapters:
            abs_api('/api/items/' + urllib.parse.quote(item_id, safe='') + '/chapters', 'POST', {'chapters': chapters})
        cover = candidate.get('cover')
        cover_ok = False
        if cover:
            try:
                abs_api('/api/items/' + urllib.parse.quote(item_id, safe='') + '/cover', 'POST', {'url': cover})
                cover_ok = True
            except Exception:
                pass
        return self.send(200, {'status': 'done', 'progress': 100,
                               'stage': f"已应用：{metadata['title']}" + (f"，替换 {len(chapters)} 章" if chapters else '') + chapters_skipped,
                               'chapters': len(chapters), 'cover': cover_ok, 'chapters_skipped': chapters_skipped})

if __name__ == '__main__':
    print(f'meta-bridge on :{PORT} abs={ABS_BASE}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
