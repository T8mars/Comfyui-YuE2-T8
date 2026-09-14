"""Pinned optional resources for YuE2 artist/style training."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .settings import model_directory


def manifest() -> dict:
    path = Path(__file__).with_name("yue2_training_assets.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or not isinstance(value.get("files"), dict):
        raise ValueError("YuE2 训练资源清单无效")
    return value


def resource_directory(root: Path) -> Path:
    return model_directory(root, strict=True) / "YuE2-training"


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def status(root: Path) -> dict:
    specification = manifest()
    directory = resource_directory(root)
    files = {}
    for name, expected in specification["files"].items():
        path = directory / name
        complete = (path.is_file() and path.stat().st_size == int(expected["bytes"])
                    and _digest(path) == expected["sha256"])
        partial = path.with_suffix(path.suffix + ".part")
        files[name] = {"ready": complete, "bytes": path.stat().st_size if path.is_file() else 0,
                       "expected_bytes": int(expected["bytes"]),
                       "partial_bytes": partial.stat().st_size if partial.is_file() else 0}
    return {"ready": all(item["ready"] for item in files.values()), "files": files,
            "directory": str(directory), "source": specification["source"],
            "revision": specification["revision"], "license": specification["license"],
            "download_bytes": sum(int(value["bytes"]) for value in specification["files"].values())}


def _download_one(url: str, destination: Path, expected_size: int, expected_hash: str, ctx=None) -> None:
    if destination.is_file() and destination.stat().st_size == expected_size and _digest(destination) == expected_hash:
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists() and partial.stat().st_size > expected_size:
        partial.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": "YuE2-T8/1.4 training-assets"})
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if offset and exc.code == 416:
            offset = 0
            partial.unlink(missing_ok=True)
            response = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "YuE2-T8/1.4 training-assets"}), timeout=60)
        else:
            raise
    if offset and response.status != 206:
        offset = 0
        partial.unlink(missing_ok=True)
    mode = "ab" if offset else "wb"
    with response, partial.open(mode) as stream:
        while True:
            if ctx:
                ctx.check_cancelled()
            block = response.read(4 * 1024 * 1024)
            if not block:
                break
            stream.write(block)
            offset += len(block)
            if ctx:
                ctx.update("yue2_training_assets", current_file=destination.name,
                           file_completed=offset, file_total=expected_size)
        stream.flush()
        os.fsync(stream.fileno())
    if partial.stat().st_size != expected_size or _digest(partial) != expected_hash:
        raise ValueError(f"训练资源下载校验失败：{destination.name}")
    os.replace(partial, destination)


def install(root: Path, ctx=None) -> dict:
    specification = manifest()
    destination = resource_directory(root)
    destination.mkdir(parents=True, exist_ok=True)
    for name, expected in specification["files"].items():
        if ctx:
            ctx.update("yue2_training_assets", current_file=name)
        _download_one(expected["url"], destination / name, int(expected["bytes"]), expected["sha256"], ctx)
    installed = status(root)
    if not installed["ready"]:
        raise RuntimeError("YuE2 训练资源未完整安装")
    return installed

