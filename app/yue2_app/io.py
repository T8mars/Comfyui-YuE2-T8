from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

_ATOMIC_WRITE_LOCK = threading.Lock()


@contextmanager
def json_file_lock(path: Path, timeout: float = 30):
    """Serialize read/modify/write cycles for one JSON file across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=timeout):
        yield


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve a unique sibling without repeating the target name, PID and UUID.
    # Deep workflow paths can otherwise exceed Windows MAX_PATH even when the
    # final JSON path is well within the limit.  A sibling keeps replace atomic.
    descriptor, name = tempfile.mkstemp(prefix="j-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        with _ATOMIC_WRITE_LOCK:
            for attempt in range(20):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.01)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def within(base: Path, candidate: Path) -> Path:
    base = base.resolve()
    candidate = candidate.resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"Path is outside allowed directory: {candidate}")
    return candidate


def public_job(status: dict) -> dict:
    result = dict(status)
    result.pop("command", None)
    return result


def remove_job_payload(directory: Path) -> None:
    """Keep retryable control records until every task payload has been removed."""
    if not directory.exists():
        return
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
        raise ValueError("任务目录是链接，不能自动清理")
    job_path, status_path = directory / "job.json", directory / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8-sig"))
    job = (json.loads(job_path.read_text(encoding="utf-8-sig")) if job_path.exists()
           else {"id": status["id"], "kind": status.get("kind", "unknown"), "request": {}})
    status.update(status="failed", cleanup_pending=True, resumable=False, result=None,
                  error="任务清理尚未完成，部分文件可能已删除；请关闭占用文件后重新清理。")
    atomic_json(status_path, status)
    try:
        for child in directory.iterdir():
            if child.name in {"job.json", "status.json"}:
                continue
            if child.is_symlink():
                child.unlink()
            elif getattr(child, "is_junction", lambda: False)():
                raise ValueError("任务内容包含目录链接，不能自动清理")
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        job_path.unlink(missing_ok=True)
        status_path.unlink()
        directory.rmdir()
    except (OSError, ValueError):
        if not job_path.exists():
            atomic_json(job_path, job)
        if not status_path.exists():
            atomic_json(status_path, status)
        raise
