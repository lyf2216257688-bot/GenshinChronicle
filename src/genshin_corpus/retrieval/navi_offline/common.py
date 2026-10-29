"""Create-only evidence IO and verifiable completion records."""
import hashlib
import json
import os
from pathlib import Path

from ..navi_f2_preflight import strict_json


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return strict_json(Path(path).read_text(encoding='utf8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Path(source).open('rb') as src, target.open('xb') as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())


def inside(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    require(path.is_relative_to(root), 'Path escapes artifact root')
    return path


def inventory(root, exclude=()):
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.relative_to(root).as_posix() not in exclude}


def seal(root, metadata=None):
    write(Path(root) / 'complete.json', {'files': inventory(root), 'metadata': metadata or {}})


def verify(root):
    value = read(Path(root) / 'complete.json')
    require(inventory(root, ('complete.json',)) == value['files'], 'Artifact set/hash changed')
    for name in value['files']:
        inside(root, name)
    return value['metadata']
