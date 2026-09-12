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
import uuid
from pathlib import Path


PRESERVE = {
    "models", "runtime", "downloads", "outputs", "uploads", "exports", "logs", "cache", "userdata",
    "settings.json", "retention.json", "server.json", "service.lock", "yue2_home.txt", "roadmap.md",
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


def update_requirements(target: Path, source: Path) -> tuple[bool, bool]:
    lock = source / 'requirements-unified.lock.txt'
    if not lock.is_file():
        return False, False
    try:
        installed = json.loads((target / 'runtime/installed.json').read_text(encoding='utf-8-sig'))
        if not isinstance(installed, dict):
            installed = {}
    except (OSError, ValueError):
        installed = {}
    runtime = (not (target / 'runtime/python.exe').is_file() or installed.get('layout') != 'unified'
               or installed.get('runtime_lock_sha256') != digest(lock))
    crt = source / 'vendor/msvc-runtime/manifest.json'
    if crt.is_file():
        runtime = runtime or installed.get('msvc_runtime_manifest_sha256') != digest(crt)
    new_models, old_models = source / 'app/yue2_app/rvc_assets.json', target / 'app/yue2_app/rvc_assets.json'
    models = new_models.is_file() and (not old_models.is_file() or digest(new_models) != digest(old_models))
    return runtime, models


def source_files(source: Path):
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source)
        if relative.parts[0].lower() in PRESERVE or relative.name.lower() in {".update-manifest.json", "roadmap.md"}:
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        yield path, relative


def invalidate_bytecode(destination: Path) -> None:
    if destination.suffix != '.py':
        return
    cache = destination.parent / '__pycache__'
    if cache.is_symlink() or (hasattr(cache, 'is_junction') and cache.is_junction()):
        raise ValueError('Python 缓存目录不能是外部链接')
    for compiled in cache.glob(destination.stem + '.*.pyc'):
        compiled.unlink()
    destination.with_suffix('.pyc').unlink(missing_ok=True)


def apply_files(source: Path, target: Path, version: str) -> tuple[Path, list[dict]]:
    backup = target / "logs" / "backups" / ("before-" + version + "-" + time.strftime("%Y%m%d-%H%M%S") + '-' + uuid.uuid4().hex[:8])
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
            expected = digest(path)
            # Record before replacement so a failure during copy/hash verification rolls this file back too.
            records.append({"file": relative.as_posix(), "sha256": expected, "existed": existed})
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".update-tmp")
            shutil.copy2(path, temporary)
            os.replace(temporary, destination)
            if digest(destination) != expected:
                raise RuntimeError(f"复制校验失败：{relative.as_posix()}")
            invalidate_bytecode(destination)
    except BaseException:
        for record in reversed(records):
            destination = target / record["file"]
            if record["existed"]:
                shutil.copy2(backup / record["file"], destination)
            else:
                destination.unlink(missing_ok=True)
            invalidate_bytecode(destination)
        raise
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "update_manifest.json").write_text(json.dumps({
        "version": version, "source": str(source), "target": str(target), "files": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup, records


def rollback(target: Path, backup: Path, records: list[dict]) -> None:
    for record in reversed(records):
        destination = (target / record["file"]).resolve()
        saved = (backup / record["file"]).resolve()
        if target not in destination.parents or backup not in saved.parents:
            raise ValueError('恢复文件超出备份目录')
        if record["existed"]:
            shutil.copy2(saved, destination)
        else:
            destination.unlink(missing_ok=True)
        invalidate_bytecode(destination)


def start_service(target: Path, host: str, port: int) -> subprocess.Popen:
    python = target / "runtime" / "python.exe"
    if not python.is_file():
        python = target / 'runtime/core/python.exe'
    if not python.is_file():
        raise FileNotFoundError('No installed studio Python runtime')
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
    parser.add_argument("--files-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--restore-backup", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--prepared-runtime", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--no-progress-browser", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    target = args.target.resolve(strict=True)
    source = args.source.resolve(strict=True)
    if target not in source.parents or source.parts[len(target.parts):len(target.parts) + 2] != ("cache", "updates"):
        raise ValueError("暂存更新必须位于安装目录的 cache/updates 中")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    version = str(manifest["version"])
    status_path = target / "logs/update-status.json"
    if args.restore_backup:
        saved = args.restore_backup.resolve(strict=True)
        if (target / 'logs/backups').resolve() not in saved.parents:
            raise ValueError('恢复备份必须位于当前整合包的 logs/backups')
        records = json.loads((saved / 'update_manifest.json').read_text(encoding='utf-8'))['files']
        rollback(target, saved, records)
        return 0
    needs_runtime, needs_models = update_requirements(target, source)
    if (needs_runtime or needs_models) and not args.no_restart and not args.files_only:
        if os.name != 'nt':
            raise RuntimeError('Unified runtime migration supports Windows only')
        coordinator = source / 'scripts/apply_unified_update.ps1'
        if not coordinator.is_file():
            raise FileNotFoundError('更新包缺少统一环境迁移程序')
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(coordinator),
                   '-Target', str(target), '-Source', str(source), '-Manifest', str(args.manifest.resolve()),
                   '-ServicePid', str(args.pid), '-BootstrapPid', str(os.getpid()), '-Port', str(args.port)]
        if args.prepared_runtime:
            command.extend(['-PreparedRuntime', str(args.prepared_runtime.resolve())])
        if args.no_progress_browser:
            command.append('-NoBrowser')
        if not needs_runtime:
            command.append('-ReuseRuntime')
        with (target / 'logs/update.stdout.log').open('ab') as stdout, (target / 'logs/update.stderr.log').open('ab') as stderr:
            subprocess.Popen(command, cwd=target, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                             close_fds=True)
        # Exit this old Python before the coordinator promotes/removes its runtime.
        return 0
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
        if args.no_restart or args.files_only:
            atomic_status(status_path, {"state": "files_staged" if args.files_only else "complete", "version": version, "backup": str(backup),
                                        "message": "代码已备份并安装" if args.files_only else "更新安装完成"})
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
