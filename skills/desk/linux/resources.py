"""Bound generated diagnostics; current click evidence is kept separately."""
from pathlib import Path
import re
import time


def prune_shots(directory, protected=(), max_bytes=512*1024*1024, max_age=86400, now=None, keep_recent=3):
    now=time.time() if now is None else now
    protected={Path(p).name[:32] for p in protected}
    files=[]
    for p in Path(directory).glob('*.png'):
        if not re.fullmatch(r'[0-9a-f]{32}(?:-marked|-shown)?\.png',p.name):continue
        try:s=p.stat();files.append((s.st_mtime,p,s.st_size))
        except FileNotFoundError:pass
    total=sum(size for _,_,size in files)
    recent=[]
    for _,p,_ in sorted(files,reverse=True):
        if p.name[:32] not in recent:recent.append(p.name[:32])
    protected.update(recent[:keep_recent])
    for modified,p,size in sorted(files):
        if p.name[:32] in protected:continue
        if now-modified>max_age or total>max_bytes:
            p.unlink(missing_ok=True);total-=size


def rotate_log(path, max_bytes=8*1024*1024):
    path=Path(path)
    if path.exists() and path.stat().st_size>=max_bytes:
        path.replace(path.with_name(path.name+'.1'))
