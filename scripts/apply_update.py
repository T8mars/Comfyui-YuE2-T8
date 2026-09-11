"""Apply a staged YuE2 update after the WebUI service exits."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PRESERVE = {
    "models", "runtime", "downloads", "outputs", "uploads", "exports", "logs", "cache", "userdata",
    "settings.json", "retention.json", "server.json", "service.lock", "yue2_home.txt",
}


def atomic_status(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def process_running(pid: int) -> bool:
    if os.name == "nt":
        process = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not process:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(code))) and code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def source_files(source: Path):
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source)
        if relative.parts[0] in PRESERVE or relative.name == ".update-manifest.json":
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        yield path, relative


def apply_files(source: Path, target: Path, version: str) -> tuple[Path, list[dict]]:
    backup = target / "logs" / "backups" / ("before-" + version + "-" + time.strftime("%Y%m%d-%H%M%S"))
    records = []
    try:
        for path, relative in source_files(source):
            destination = (target / relative).resolve()
            if target not in destination.parents:
                raise ValueError("更新文件超出了安装目录")
            existed = destination.is_file()
            if existed:
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".update-tmp")
            shutil.copy2(path, temporary)
            os.replace(temporary, destination)
            expected = digest(path)
            if digest(destination) != expected:
                raise RuntimeError(f"复制校验失败：{relative.as_posix()}")
            records.append({"file": relative.as_posix(), "sha256": expected, "existed": existed})
    except BaseException:
        for record in reversed(records):
            destination = target / record["file"]
            if record["existed"]:
                shutil.copy2(backup / record["file"], destination)
            else:
                destination.unlink(missing_ok=True)
        raise
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "update_manifest.json").write_text(json.dumps({
        "version": version, "source": str(source), "target": str(target), "files": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup, records


def rollback(target: Path, backup: Path, records: list[dict]) -> None:
    for record in reversed(records):
        destination = target / record["file"]
        if record["existed"]:
            shutil.copy2(backup / record["file"], destination)
        else:
            destination.unlink(missing_ok=True)


def start_service(target: Path, host: str, port: int) -> subprocess.Popen:
    python = target / "runtime" / "core" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)
    environment = os.environ.copy()
    environment.update({
        "YUE2_HOME": str(target), "YUE2_KIT": str(target), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "HF_HOME": str(target / "cache/huggingface"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "PLAYWRIGHT_BROWSERS_PATH": str(target / "runtime/playwright"),
    })
    environment["PATH"] = str(target / "runtime/ffmpeg") + os.pathsep + environment.get("PATH", "")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    stdout = (target / "logs/server.stdout.log").open("ab")
    stderr = (target / "logs/server.stderr.log").open("ab")
    try:
        return subprocess.Popen([str(python), "-X", "utf8", "-m", "app.yue2_app.service", "--host", host,
                                 "--port", str(port)], cwd=target, env=environment, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=stderr, creationflags=flags, close_fds=True)
    finally:
        stdout.close()
        stderr.close()


def wait_for_health(target: Path, host: str, port: int, version: str, process: subprocess.Popen) -> None:
    import urllib.request
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"更新后的服务启动失败（代码 {process.returncode}）")
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2) as response:
                health = json.load(response)
            if (health.get("ok") is True and health.get("version") == version
                    and Path(health.get("root", "")).resolve() == target):
                return
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(.4)
    raise RuntimeError("更新后的服务未能在 40 秒内启动")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8189)
    parser.add_argument("--no-restart", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    target = args.target.resolve(strict=True)
    source = args.source.resolve(strict=True)
    if target not in source.parents or source.parts[len(target.parts):len(target.parts) + 2] != ("cache", "updates"):
        raise ValueError("暂存更新必须位于安装目录的 cache/updates 中")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    version = str(manifest["version"])
    status_path = target / "logs/update-status.json"
    backup = None
    records = []
    updated_service = None
    try:
        atomic_status(status_path, {"state": "waiting_for_service", "target_version": version,
                                    "message": "正在等待本地服务安全退出"})
        deadline = time.monotonic() + 90
        while process_running(args.pid) and time.monotonic() < deadline:
            time.sleep(.25)
        if process_running(args.pid):
            raise RuntimeError("本地服务未能在 90 秒内退出，更新尚未安装")
        atomic_status(status_path, {"state": "installing", "target_version": version,
                                    "message": "正在备份并安装新版代码"})
        backup, records = apply_files(source, target, version)
        if args.no_restart:
            atomic_status(status_path, {"state": "complete", "version": version, "backup": str(backup),
                                        "message": "更新安装完成"})
            return 0
        updated_service = start_service(target, args.host, args.port)
        wait_for_health(target, args.host, args.port, version, updated_service)
        atomic_status(status_path, {"state": "complete", "version": version, "backup": str(backup),
                                    "message": f"已更新到 v{version}"})
        return 0
    except BaseException as exc:
        if backup and records:
            try:
                if updated_service and updated_service.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill.exe", "/PID", str(updated_service.pid), "/T", "/F"], check=False,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    else:
                        updated_service.terminate()
                    try:
                        updated_service.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        updated_service.kill()
                rollback(target, backup, records)
                previous = start_service(target, args.host, args.port) if not args.no_restart else None
                message = f"更新失败，已恢复旧版本：{exc}"
                if previous and previous.poll() is not None:
                    message += "；旧版本服务需要手动启动"
            except BaseException as rollback_error:
                message = f"更新失败且自动恢复失败：{exc}；{rollback_error}"
        else:
            message = f"更新失败：{exc}"
        atomic_status(status_path, {"state": "error", "target_version": version, "message": message})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
