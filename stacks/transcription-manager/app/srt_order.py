"""Shared full-book SRT timeline contract for ASR and the Language Reactor bridge."""
import hashlib
import json
from pathlib import Path


def read_order(source, paths):
    """Return validated audio order and legacy/partial/complete state."""
    marker = source.parent / 'transcript.order.json'
    paths = [Path(p).resolve() for p in paths]
    if not marker.exists():
        return paths, 'legacy'
    value = json.loads(marker.read_text(encoding='utf-8'))
    if value.get('version') != 1 or type(value.get('complete')) is not bool:
        raise ValueError('整书 SRT 时间轴索引格式无效')
    if value.get('srt_sha256') != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError('整书 SRT 已变化，需重新生成时间轴索引')
    ordered = []
    for entry in value.get('tracks', []):
        path = (source.parent / entry['path']).resolve()
        if not path.is_relative_to(source.parent.resolve()) or path not in paths:
            raise ValueError('整书 SRT 不属于当前音轨')
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (entry.get('size'), entry.get('mtime_ns')):
            raise ValueError('音轨已变化，整书 SRT 时间轴需更新')
        ordered.append(path)
    if len(ordered) != len(paths) or set(ordered) != set(paths):
        raise ValueError('整书 SRT 音轨列表不完整或重复')
    return ordered, 'complete' if value['complete'] else 'partial'
