"""Secure update discovery and staging for the local WebUI."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


MANIFEST_URL = "https://github.com/T8mars/Comfyui-YuE2-T8/releases/latest/download/update-manifest.json"
REPOSITORY = "T8mars/Comfyui-YuE2-T8"
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 160 * 1024 * 1024
MAX_ARCHIVE_FILES = 5000


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(str(value))
    if not match:
        raise ValueError("更新清单中的版本号无效")
    return tuple(map(int, match.groups()))


def _read_url(url: str, maximum: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "YuE2-T8-Updater/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != "https" or not (
            final.hostname == "github.com" or (final.hostname or "").endswith(".githubusercontent.com")
        ):
            raise ValueError("更新下载被重定向到了不受信任的地址")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > maximum:
            raise ValueError("更新文件超过允许大小")
        content = response.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("更新文件超过允许大小")
    return content


def validate_manifest(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("channel") != "stable":
        raise ValueError("更新清单格式不受支持")
    version = str(value.get("version", ""))
    version_tuple(version)
    tag = f"v{version}"
    asset = f"Comfyui-YuE2-T8-{tag}-code.zip"
    archive_root = f"Comfyui-YuE2-T8-{tag}"
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset}"
    if value.get("tag") != tag or value.get("asset") != asset:
        raise ValueError("更新清单的版本与文件名不一致")
    if value.get("archive_root") != archive_root or value.get("download_url") != expected_url:
        raise ValueError("更新清单的下载地址无效")
    digest = str(value.get("sha256", "")).lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise ValueError("更新清单缺少有效的 SHA256")
    result = dict(value)
    result["sha256"] = digest
    return result


def fetch_manifest() -> dict:
    try:
        content = _read_url(MANIFEST_URL, MAX_MANIFEST_BYTES)
        return validate_manifest(json.loads(content.decode("utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 GitHub 更新清单，请检查网络后重试") from exc


def public_update(manifest: dict, current_version: str) -> dict:
    available = version_tuple(manifest["version"]) > version_tuple(current_version)
    return {
        "current_version": current_version,
        "latest_version": manifest["version"],
        "update_available": available,
        "published_at": manifest.get("published_at"),
        "release_url": f"https://github.com/{REPOSITORY}/releases/tag/{manifest['tag']}",
    }


def check_update(current_version: str) -> tuple[dict, dict]:
    manifest = fetch_manifest()
    return public_update(manifest, current_version), manifest


def _safe_extract(archive_path: Path, destination: Path, archive_root: str) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_FILES or sum(item.file_size for item in entries) > MAX_EXTRACTED_BYTES:
            raise ValueError("更新压缩包的内容超过允许大小")
        prefix = archive_root + "/"
        for item in entries:
            normalized = item.filename.replace("\\", "/")
            path = Path(normalized)
            is_symlink = (item.external_attr >> 16) & 0o170000 == 0o120000
            if (is_symlink or normalized.startswith("/") or ".." in path.parts
                    or (normalized != archive_root + "/" and not normalized.startswith(prefix))):
                raise ValueError("更新压缩包包含不安全的路径")
        archive.extractall(destination)
    return destination / archive_root


def _source_version(source: Path) -> str:
    try:
        project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        package_version = project["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("更新包缺少有效的项目版本") from exc
    marker = (source / "app/yue2_app/__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{package_version}"' not in marker:
        raise ValueError("更新包内部版本不一致")
    for required in ("app/yue2_app/service.py", "app/yue2_app/updater.py", "scripts/apply_update.py"):
        if not (source / required).is_file():
            raise ValueError(f"更新包缺少必要文件：{required}")
    return str(package_version)


def prepare_update(root: Path, current_version: str) -> dict:
    info, manifest = check_update(current_version)
    if not info["update_available"]:
        raise ValueError("当前已经是最新版本")
    update_root = root.resolve() / "cache" / "updates"
    update_root.mkdir(parents=True, exist_ok=True)
    archive_path = update_root / manifest["asset"]
    content = _read_url(manifest["download_url"], MAX_ARCHIVE_BYTES)
    if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
        raise ValueError("更新包 SHA256 校验失败，已拒绝安装")
    with tempfile.NamedTemporaryFile(dir=update_root, prefix="download-", suffix=".tmp", delete=False) as stream:
        temporary_archive = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_archive, archive_path)
    staging = update_root / (manifest["version"] + "-" + manifest["sha256"][:12])
    temporary_directory = update_root / ("extract-" + next(tempfile._get_candidate_names()))
    try:
        temporary_directory.mkdir()
        source = _safe_extract(archive_path, temporary_directory, manifest["archive_root"])
        if _source_version(source) != manifest["version"]:
            raise ValueError("更新包版本与清单不一致")
        if staging.exists():
            shutil.rmtree(staging)
        os.replace(source, staging)
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)
    manifest_path = staging / ".update-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**info, "source": str(staging), "manifest": str(manifest_path)}


def launch_update(root: Path, prepared: dict, pid: int, host: str, port: int) -> dict:
    source = Path(prepared["source"]).resolve()
    helper = source / "scripts" / "apply_update.py"
    python = root.resolve() / "runtime" / "core" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)
    status_path = root.resolve() / "logs" / "update-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps({
        "state": "waiting_for_service", "current_version": prepared["current_version"],
        "target_version": prepared["latest_version"], "message": "更新包已校验，正在重启并安装",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [str(python), "-X", "utf8", str(helper), "--target", str(root.resolve()),
               "--source", str(source), "--manifest", str(prepared["manifest"]),
               "--pid", str(pid), "--host", host, "--port", str(port)]
    creationflags = 0
    if os.name == "nt":
        creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                         | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with (root / "logs/update.stdout.log").open("ab") as stdout, (root / "logs/update.stderr.log").open("ab") as stderr:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   creationflags=creationflags, close_fds=True)
    return {"accepted": True, "updater_pid": process.pid, "target_version": prepared["latest_version"]}


def update_status(root: Path) -> dict:
    path = root.resolve() / "logs" / "update-status.json"
    if not path.is_file():
        return {"state": "idle"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "error", "message": "更新状态文件无法读取"}
    return value if isinstance(value, dict) else {"state": "error", "message": "更新状态无效"}
