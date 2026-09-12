"""Hash-verified, bounded Demucs cache shared by both voice conversion backends."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from filelock import FileLock

from .artifacts import verify_artifact_manifest, write_artifact_manifest

FILES = ('separated_vocal.wav', 'accompaniment.wav')
MANIFEST = 'separation_manifest.json'
KIND = 'yue2-voice-separation-v1'
MAX_BYTES = 5 * 2**30


def identity(models: dict, song_sha256: str) -> tuple[dict, dict]:
    # Conversion models/settings do not change the separated input tracks.
    demucs = {'schema': 1, 'components': {'Demucs': models['components']['Demucs']}}
    source = {'song_sha256': song_sha256, 'pipeline': 'demucs-cuda-shifts1-overlap025-split-v1'}
    return demucs, source


def _plain(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction()


def _base(root: Path) -> Path:
    base = root.resolve()
    for name in ('cache', 'voice-separation'):
        base = base / name
        if not _plain(base):
            raise ValueError('分离缓存目录不能是链接')
        base.mkdir(exist_ok=True)
    if not _plain(base / '.lock'):
        raise ValueError('分离缓存锁不能是链接')
    return base


def _key(models: dict, source: dict) -> str:
    value = json.dumps([models, source], sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(value.encode()).hexdigest()


def _valid(entry: Path, models: dict, source: dict) -> bool:
    if not _plain(entry) or not entry.is_dir():
        return False
    if any(not _plain(entry / name) for name in (*FILES, MANIFEST)):
        return False
    manifest = verify_artifact_manifest(entry, MANIFEST, KIND, set(FILES))
    return (set(manifest['files']) == set(FILES) and manifest['models'] == models
            and manifest['source'] == source)


def _remove_entry(entry: Path) -> bool:
    # Never recurse into a cache directory, nor delete unexpected user files.
    if not _plain(entry) or not entry.is_dir():
        return False
    paths = list(entry.iterdir())
    if any(p.name not in (*FILES, MANIFEST) or not _plain(p) or not p.is_file() for p in paths):
        return False
    for path in paths:
        path.unlink()
    entry.rmdir()
    return True


def restore(root: Path, output: Path, models: dict, source: dict, ctx) -> bool:
    try:
        base = _base(root)
        with FileLock(str(base / '.lock'), timeout=30):
            entry = base / _key(models, source)
            if not _valid(entry, models, source):
                return False
            ctx.check_cancelled()
            for name in FILES:
                shutil.copy2(entry / name, output / name)
            os.utime(entry, None)
            return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # A disposable cache cannot invalidate otherwise usable source audio.
        if isinstance(exc, InterruptedError):
            raise
        print(f'Separation cache miss: {exc}', flush=True)
        return False


def save(root: Path, output: Path, models: dict, source: dict, ctx,
         *, max_bytes: int = MAX_BYTES) -> bool:
    stage = None
    try:
        size = sum((output / name).stat().st_size for name in FILES)
        if size > max_bytes:
            return False
        base = _base(root)
        with FileLock(str(base / '.lock'), timeout=30):
            ctx.check_cancelled()
            entry = base / _key(models, source)
            stage = base / ('.stage-' + uuid.uuid4().hex)
            stage.mkdir()
            for name in FILES:
                ctx.check_cancelled()
                shutil.copy2(output / name, stage / name)
            write_artifact_manifest(stage, MANIFEST, KIND, list(FILES), models=models, source=source)
            size = sum(path.stat().st_size for path in stage.iterdir())
            if size > max_bytes:
                return False
            ctx.check_cancelled()
            if entry.exists() and not _remove_entry(entry):
                return False
            entries = [p for p in base.iterdir() if len(p.name) == 64
                       and all(c in '0123456789abcdef' for c in p.name)
                       and _plain(p) and p.is_dir()]
            sizes = {p: sum(f.stat().st_size for f in p.iterdir() if _plain(f) and f.is_file()) for p in entries}
            total = sum(sizes.values())
            for old in sorted(entries, key=lambda p: p.stat().st_mtime):
                if total + size <= max_bytes:
                    break
                if _remove_entry(old):
                    total -= sizes[old]
            if total + size > max_bytes:
                return False
            ctx.check_cancelled()
            stage.rename(entry)
            stage = None
            return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, InterruptedError):
            raise
        print(f'Separation cache not saved: {exc}', flush=True)
        return False
    finally:
        if stage is not None:
            _remove_entry(stage)
