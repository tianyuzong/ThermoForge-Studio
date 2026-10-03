"""Hash actual immutable results, with bounded cache invalidated by file identity."""
import hashlib,json
from functools import lru_cache
from pathlib import Path


def identity(path):
    stat=Path(path).stat()
    return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)


@lru_cache(maxsize=128)
def _hash_unchanged(path,stamp):
    with Path(path).open('rb') as stream:value=hashlib.file_digest(stream,'sha256').hexdigest()
    if identity(path)!=stamp:raise ValueError('读取期间文件发生变化')
    return value


def file_digest(path):
    path=str(Path(path).resolve())
    return _hash_unchanged(path,identity(path))


def checked_raw_fields(folder,expected=None):
    folder=Path(folder)
    manifest=json.loads((folder/'raw-fields.sha256.json').read_text(encoding='utf-8'))
    actual=file_digest(folder/'fields.h5')
    if manifest.get('sha256')!=actual or expected is not None and expected!=actual:raise ValueError('原始场文件与保存的 SHA-256 不一致')
    return actual
