"""Keep physical SRT sidecars visible in the ABS media-file index."""
import hashlib
import json
import threading
import time
import urllib.parse
from pathlib import Path
import abs_api

DATA=abs_api.DATA
WAKE=threading.Event();FORCE=threading.Event()
STATE={'running':False,'current':'','last_scan':0,'error':''}


def load():
    file=DATA/'media-sync.json'
    return json.loads(file.read_text()) if file.is_file() else {}


def save(values):
    file=DATA/'media-sync.json';temp=file.with_suffix('.tmp')
    temp.write_text(json.dumps(values,ensure_ascii=False,indent=2));temp.replace(file)


def status():
    values=load()
    return {**STATE,'synced_books':sum(bool(v.get('synced')) for v in values.values()),
            'subtitle_files':sum(v.get('count',0) for v in values.values() if v.get('synced')),
            'pending_books':sum(not v.get('synced') for v in values.values())}


def request_sync():
    FORCE.set();WAKE.set()
    return {'ok':True,'message':'已请求同步字幕到媒体库，可在任务中心查看结果'}


def subtitle_files(item):
    path=Path(item.get('path','')).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in abs_api.ROOTS):return []
    folder=path.parent if item.get('isFile') else path
    files=[path.with_suffix('.srt')] if item.get('isFile') else list(folder.rglob('*.srt'))
    return sorted(p for p in files if p.is_file() and not p.is_symlink() and p.stat().st_size>0)


def indexed_files(book):
    folder=Path(book['path']).parent if book.get('isFile') else Path(book['path'])
    paths=set()
    for entry in book.get('libraryFiles') or []:
        metadata=entry.get('metadata') or {}
        path=metadata.get('path') or str(folder/metadata.get('filename',''))
        if path.lower().endswith(('.srt', '.txt')):paths.add(str(Path(path).resolve()))
    return paths


def run_once(force=False):
    values=load();library_scans=set()
    for library in abs_api.request('/api/libraries').get('libraries',[]):
        if library.get('mediaType')!='book':continue
        items=abs_api.request('/api/libraries/'+urllib.parse.quote(library['id'],safe='')+'/items?limit=0&minified=0').get('results',[])
        for item in items:
            files=subtitle_files(item)
            if not files:continue
            item_id=item['id'];fingerprint=hashlib.sha256(json.dumps([(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in files]).encode()).hexdigest()
            previous=values.get(item_id,{})
            if not force and previous.get('signature')==fingerprint and previous.get('synced'):continue
            name=(item.get('media') or {}).get('metadata',{}).get('title',item_id)
            STATE['current']='同步字幕 · '+name
            record={'title':name,'signature':fingerprint,'count':len(files),'checked_at':time.time(),'synced':False}
            try:
                endpoint='/api/items/'+urllib.parse.quote(item_id,safe='')
                book=abs_api.request(endpoint)
                wanted={str(p) for p in files}
                indexed = indexed_files(book)
                if not wanted.issubset(indexed) or any(not Path(p).is_file() for p in indexed):
                    if book.get('isFile'):
                        if library['id'] not in library_scans:
                            abs_api.request('/api/libraries/'+urllib.parse.quote(library['id'],safe='')+'/scan','POST',{})
                            library_scans.add(library['id'])
                        record['message']='等待媒体库扫描完成'
                    else:
                        abs_api.request(endpoint+'/scan','POST',{})
                    book=abs_api.request(endpoint)
                indexed = indexed_files(book)
                missing=wanted-indexed
                stale={p for p in indexed if not Path(p).is_file()}
                record.update(synced=not missing and not stale,indexed=len(wanted)-len(missing),message='字幕已出现在媒体文件列表' if not missing else '等待索引更新')
            except Exception as exc:
                record['error']=str(exc);STATE['error']=name+'：'+str(exc)
            values[item_id]=record;save(values)
    STATE.update(last_scan=time.time(),current='')
    return values


def run():
    while True:
        WAKE.clear();force=FORCE.is_set();FORCE.clear()
        STATE.update(running=True,error='')
        try:run_once(force)
        except Exception as exc:STATE['error']=str(exc)
        finally:STATE.update(running=False,current='')
        WAKE.wait(60)
