#!/usr/bin/env python3
"""Audiobookshelf 元数据匹配服务（独立于转写服务）。

- 元数据来源：ABS 内置的全部书籍来源（Audible 各区、iTunes、Google Books、Open Library…）
- 章节来源：Audnexus（Audible 官方章节时间轴）或“不替换”
- 应用时逐步执行并返回每一步结果，便于前端显示进度和错误
"""
import json, os, re, secrets, threading, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ABS_BASE = os.environ.get('ABS_BASE_URL', 'http://audiobookshelf:80').rstrip('/')
ABS_TOKEN = os.environ.get('ABS_API_TOKEN', '')
META_TOKEN = os.environ.get('META_TOKEN', '')
PORT = int(os.environ.get('PORT', '13382'))
BACKUP_DIR = os.environ.get('BACKUP_DIR', '/data/metadata-backups')
AUDNEXUS = 'https://api.audnex.us'

SEARCHES = {}
LOCK = threading.Lock()
SEARCH_TTL = 1800
LABELS = {
    'audible': 'Audible（美国站）', 'audible.uk': 'Audible（英国站）', 'audible.ca': 'Audible（加拿大站）',
    'audible.au': 'Audible（澳洲站）', 'audible.de': 'Audible（德国站）', 'audible.fr': 'Audible（法国站）',
    'audible.jp': 'Audible（日本站）', 'audible.it': 'Audible（意大利站）', 'audible.in': 'Audible（印度站）',
    'audible.es': 'Audible（西班牙站）', 'itunes': 'iTunes / Apple Books', 'google': 'Google Books',
    'openlibrary': 'Open Library', 'fantlab': 'FantLab',
}

def req(url, method='GET', body=None, headers=None, timeout=25):
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
        return req(AUDNEXUS + path, timeout=10)
    except Exception:
        return None

def item_duration(item):
    media = item.get('media') or {}
    return media.get('duration') or sum((f.get('duration') or 0) for f in media.get('audioFiles') or [])

def chapters_for(asin):
    data = audnexus(f'/books/{asin}/chapters') or {}
    out = []
    for ch in data.get('chapters') or []:
        start = ch.get('startOffsetSec')
        if start is None and ch.get('startOffsetMs') is not None:
            start = ch['startOffsetMs'] / 1000.0
        if start is None: continue
        length = (ch.get('lengthMs') or 0) / 1000.0
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
    year = c.get('publishedYear')
    if year: md['publishedYear'] = str(year)[:4]
    authors = split_names(c.get('author'))
    if authors: md['authors'] = [{'name': n} for n in authors]
    narrators = split_names(c.get('narrator'))
    if narrators: md['narrators'] = narrators
    genres = c.get('genres')
    if isinstance(genres, list): md['genres'] = [str(g) for g in genres if g]
    series = c.get('series')
    if isinstance(series, list):
        out = [{'name': s['series'], 'sequence': str(s.get('sequence') or '')}
               for s in series if isinstance(s, dict) and s.get('series')]
        if out: md['series'] = out
    return md

class Handler(BaseHTTPRequestHandler):
    server_version = 'meta-bridge/1.1'
    def log_message(self, fmt, *args): print(fmt % args, flush=True)

    def send(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        return (not META_TOKEN) or self.headers.get('X-Meta-Token') == META_TOKEN

    def do_GET(self):
        try:
            url = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(url.query)
            if url.path == '/api/health':
                return self.send(200, {'status': 'ok'})
            if not self.authorized(): return self.send(403, {'error': '元数据服务密钥无效'})
            if url.path == '/api/books/match-providers': return self.providers()
            if url.path == '/api/books/match-status': return self.send(200, {'status': 'idle', 'progress': 0})
            if url.path == '/api/books/match-candidates': return self.candidates(q)
            return self.send(404, {'error': '接口不存在'})
        except Exception as exc:
            return self.send(400, {'error': str(exc)})

    def do_POST(self):
        try:
            url = urllib.parse.urlparse(self.path)
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            if not self.authorized(): return self.send(403, {'error': '元数据服务密钥无效'})
            if url.path == '/api/books/match': return self.apply(payload)
            return self.send(404, {'error': '接口不存在'})
        except Exception as exc:
            return self.send(400, {'error': str(exc)})

    def providers(self):
        chapter = [{'value': 'audnexus', 'text': 'Audnexus（Audible 官方章节）'},
                   {'value': 'none', 'text': '不替换章节'}]
        try:
            data = abs_api('/api/search/providers') or {}
            books = (data.get('providers') or {}).get('books') or []
        except Exception:
            books = []
        out = [{'value': b['value'], 'text': LABELS.get(b['value'], b.get('text') or b['value'])}
               for b in books if b.get('value')]
        out = [{'value': 'all', 'text': '聚合搜索（Audible US/UK + iTunes）'}] + out
        if len(out) == 1:
            out += [{'value': 'audible', 'text': LABELS['audible']},
                   {'value': 'audible.uk', 'text': LABELS['audible.uk']},
                   {'value': 'itunes', 'text': LABELS['itunes']}]
        return self.send(200, {'providers': out, 'chapterProviders': chapter,
                               'currentDuration': None})

    def candidates(self, q):
        item_id = (q.get('path') or [''])[0]
        provider = (q.get('provider') or ['audible'])[0]
        if not item_id: raise ValueError('缺少书籍')
        book = abs_api('/api/items/' + urllib.parse.quote(item_id, safe=''))
        md = (book.get('media') or {}).get('metadata') or {}
        title = (q.get('title') or [''])[0].strip() or (md.get('title') or '')
        author = (q.get('author') or [''])[0].strip() or ' '.join(a.get('name', '') for a in md.get('authors') or [])
        if not title: raise ValueError('缺少书名')
        def search_one(prov):
            params = urllib.parse.urlencode({'provider': prov, 'title': title, 'author': author})
            try:
                found = abs_api('/api/search/books?' + params) or []
            except Exception:
                return []
            out = []
            for r in found:
                if isinstance(r, dict) and r.get('title'):
                    item = dict(r); item['source'] = LABELS.get(prov, prov); out.append(item)
            return out
        if provider == 'all':
            merged, seen = [], set()
            for prov in ('audible', 'audible.uk', 'itunes'):
                for r in search_one(prov):
                    key = (r.get('asin') or '') + '|' + (r.get('title') or '').lower() + '|' + str(r.get('publishedYear'))
                    if key in seen: continue
                    seen.add(key); merged.append(r)
            results = merged
        else:
            results = search_one(provider)
        english = [r for r in results if str(r.get('language') or 'English').lower().startswith('english')]
        if english: results = english
        current_min = round(item_duration(book) / 60.0, 1)

        def enrich(pair):
            idx, r = pair
            item = dict(r); item['index'] = idx
            minutes = r.get('duration')
            item['durationMinutes'] = minutes if isinstance(minutes, (int, float)) and minutes else None
            if item['durationMinutes'] and current_min:
                item['durationDiffPct'] = round(abs(current_min - item['durationMinutes']) / item['durationMinutes'] * 100, 1)
            if r.get('asin') and provider != 'itunes':
                item['chapterCount'] = len(chapters_for(r['asin']))
            return item
        results = results[:12]
        with ThreadPoolExecutor(max_workers=8) as pool:
            enriched_results = list(pool.map(enrich, enumerate(results)))
        token = secrets.token_urlsafe(18)
        with LOCK:
            now = time.time()
            for k in [k for k, v in SEARCHES.items() if now - v['at'] > SEARCH_TTL]: SEARCHES.pop(k, None)
            SEARCHES[token] = {'at': now, 'item_id': item_id, 'provider': provider, 'results': enriched_results,
                               'currentDuration': current_min}
        return self.send(200, {'token': token, 'provider': provider, 'title': title, 'author': author,
                               'currentDuration': current_min, 'results': enriched_results})

    def apply(self, payload):
        item_id = payload.get('item_id'); token = payload.get('token'); index = payload.get('index')
        chapter_source = payload.get('chapter_provider', 'audnexus')
        with LOCK:
            search = SEARCHES.get(token)
        if not search or search['item_id'] != item_id:
            raise ValueError('匹配结果已过期，请重新搜索')
        if not isinstance(index, int) or not (0 <= index < len(search['results'])):
            raise ValueError('请选择列表中的结果')
        candidate = search['results'][index]
        metadata = selected_metadata(candidate)
        if not metadata.get('title'): raise ValueError('结果缺少书名')
        book = abs_api('/api/items/' + urllib.parse.quote(item_id, safe=''))
        current_min = round(item_duration(book) / 60.0, 1)
        steps = []
        def step(name, status, detail=''):
            steps.append({'name': name, 'status': status, 'detail': detail})
        # 1) 备份
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            with open(os.path.join(BACKUP_DIR, f'{item_id}-{int(time.time())}.json'), 'w') as f:
                json.dump(book, f, ensure_ascii=False)
            step('备份原数据', 'ok')
        except Exception as exc:
            step('备份原数据', 'warn', f'备份失败（继续执行）：{exc}')
        # 2) 写入元数据
        try:
            abs_api('/api/items/' + urllib.parse.quote(item_id, safe='') + '/media', 'PATCH', {'metadata': metadata})
            step('写入元数据', 'ok', metadata['title'])
        except Exception as exc:
            step('写入元数据', 'fail', str(exc))
            return self.send(200, {'status': 'failed', 'progress': 100, 'steps': steps,
                                   'stage': '元数据写入失败', 'error': str(exc)})
        # 3) 校验元数据
        try:
            latest = abs_api('/api/items/' + urllib.parse.quote(item_id, safe=''))
            md = (latest.get('media') or {}).get('metadata') or {}
            problems = []
            if (md.get('title') or '') != metadata.get('title'): problems.append('标题')
            want_authors = [a['name'] for a in metadata.get('authors') or []]
            got_authors = [a.get('name') for a in md.get('authors') or []]
            if want_authors and got_authors and {x.lower() for x in want_authors} != {x.lower() for x in got_authors}:
                problems.append('作者')
            want_series = [(s['name'], s.get('sequence')) for s in metadata.get('series') or []]
            got_series = [(s.get('name'), s.get('sequence')) for s in md.get('series') or []]
            if want_series and got_series and want_series != got_series: problems.append('系列')
            if metadata.get('publishedYear') and md.get('publishedYear') != metadata['publishedYear']:
                problems.append('年份')
            if problems:
                step('校验元数据', 'warn', '部分字段未写入：' + '、'.join(problems))
            else:
                step('校验元数据', 'ok', '标题/作者/系列/年份一致')
        except Exception as exc:
            step('校验元数据', 'warn', f'无法校验：{exc}')
        # 4) 章节
        chapter_count = 0
        if chapter_source == 'none':
            step('替换章节', 'skip', '按设置保留现有章节')
        elif not candidate.get('asin'):
            step('替换章节', 'warn', '该来源没有 ASIN，无法获取章节')
        elif candidate.get('durationMinutes') and current_min and \
                abs(current_min - candidate['durationMinutes']) / candidate['durationMinutes'] > 0.20:
            step('替换章节', 'skip',
                 f"时长差太大（音频 {current_min} 分 / 来源 {candidate['durationMinutes']} 分），保留现有章节")
        else:
            try:
                chapters = chapters_for(candidate['asin'])
                if not chapters:
                    step('替换章节', 'warn', '来源没有返回章节')
                else:
                    abs_api('/api/items/' + urllib.parse.quote(item_id, safe='') + '/chapters',
                            'POST', {'chapters': chapters})
                    latest = abs_api('/api/items/' + urllib.parse.quote(item_id, safe=''))
                    chapter_count = len((latest.get('media') or {}).get('chapters') or [])
                    diff = (abs(current_min - candidate['durationMinutes']) / candidate['durationMinutes'] * 100
                            if candidate.get('durationMinutes') and current_min else 0)
                    note = f'（时长差 {diff:.1f}%，章节可能与音频有偏差）' if diff > 3 else ''
                    if chapter_count >= len(chapters) * 0.9:
                        step('替换章节', 'warn' if diff > 3 else 'ok', f'{chapter_count} 章{note}')
                    else:
                        step('替换章节', 'warn', f'写入后只读到 {chapter_count} 章')
            except Exception as exc:
                step('替换章节', 'fail', str(exc))
        # 5) 封面
        cover = candidate.get('cover')
        if cover:
            try:
                abs_api('/api/items/' + urllib.parse.quote(item_id, safe='') + '/cover', 'POST', {'url': cover})
                step('更新封面', 'ok')
            except Exception as exc:
                step('更新封面', 'warn', f'下载封面失败，保留原封面：{exc}')
        else:
            step('更新封面', 'skip', '来源没有封面')
        status = 'partial' if any(s['status'] in ('warn', 'fail') for s in steps) else 'done'
        return self.send(200, {'status': status, 'progress': 100, 'steps': steps,
                               'chapters': chapter_count, 'cover': steps[-1]['status'] == 'ok',
                               'stage': f"已应用：{metadata['title']}"})

if __name__ == '__main__':
    print(f'meta-bridge on :{PORT} abs={ABS_BASE}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
