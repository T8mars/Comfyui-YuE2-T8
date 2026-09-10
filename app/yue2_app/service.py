from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .config import (
    CACHE,
    CORE_PYTHON,
    LOGS,
    OUTPUTS,
    ROOT,
    TRANSCRIBE_PYTHON,
    UPLOADS,
    ensure_layout,
    runtime_ready,
)
from .io import atomic_json, public_job, within
from .retention import RetentionManager

TERMINAL = {"complete", "failed", "cancelled"}
CORE_KINDS = {"generate", "plan", "render_plan", "semantic", "synthesize", "decode", "doctor"}
TRANSCRIBE_KINDS = {"transcribe"}
JOB_ID_PATTERN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{8}")
_LOG_LOCK = threading.Lock()
SERVER_LOG_MAX_BYTES = 20 * 1024 * 1024
SERVER_LOG_BACKUPS = 3


def job_directory(job_id: str) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("无效的任务 ID")
    return within(OUTPUTS, OUTPUTS / job_id)


def terminate_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def terminate_recorded_worker(status: dict, job_id: str) -> None:
    if os.name != "nt" or not JOB_ID_PATTERN.fullmatch(job_id):
        return
    try:
        pid = int(status.get("worker_pid") or status.get("pid") or 0)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    script = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' -ErrorAction SilentlyContinue;"
        f"if($p -and $p.CommandLine -match 'app\\.yue2_app\\.(core_worker|transcribe_worker)' "
        f"-and $p.CommandLine -like '*{job_id}*'){{taskkill.exe /PID {pid} /T /F | Out-Null}}"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class JobStore:
    def __init__(self):
        ensure_layout()
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.pending: queue.Queue[str] = queue.Queue()
        self.current_id: str | None = None
        self.current_process: subprocess.Popen | None = None
        self.stopping = threading.Event()
        self.retention = RetentionManager(ROOT)
        self._restore()
        self.cleanup_retention(force=True)
        self.thread = threading.Thread(target=self._scheduler, name="yue2-scheduler", daemon=True)
        self.thread.start()

    def _restore(self) -> None:
        for directory in sorted(OUTPUTS.glob("*")):
            if not directory.is_dir():
                continue
            try:
                job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
                status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if (not JOB_ID_PATTERN.fullmatch(directory.name) or job.get("id") != directory.name
                    or status.get("id") != directory.name):
                continue
            if status.get("status") not in TERMINAL:
                terminate_recorded_worker(status, directory.name)
                status.update({"status": "failed", "stage": "failed", "finished_at": time.time(),
                               "error": "服务重启时任务仍未结束，请重新提交"})
                atomic_json(directory / "status.json", status)
            self.jobs[job["id"]] = status

    def create(self, kind: str, request: dict) -> dict:
        if kind not in CORE_KINDS | TRANSCRIBE_KINDS:
            raise ValueError(f"不支持的任务类型：{kind}")
        if not isinstance(request, dict):
            raise ValueError("request 必须是对象")
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        directory = OUTPUTS / job_id
        directory.mkdir(parents=True)
        now = time.time()
        job = {"id": job_id, "kind": kind, "request": request, "created_at": now}
        status = {"id": job_id, "kind": kind, "status": "queued", "stage": "queued",
                  "created_at": now, "updated_at": now, "job_dir": str(directory)}
        atomic_json(directory / "job.json", job)
        atomic_json(directory / "status.json", status)
        with self.lock:
            self.jobs[job_id] = status
            self.pending.put(job_id)
        return public_job(status)

    def get(self, job_id: str) -> dict:
        directory = job_directory(job_id)
        path = directory / "status.json"
        if not path.is_file():
            raise KeyError(job_id)
        status = json.loads(path.read_text(encoding="utf-8"))
        if status.get("id") != job_id:
            raise ValueError("任务状态 ID 与目录不一致")
        with self.lock:
            self.jobs[job_id] = status
        return public_job(status)

    def list(self, limit: int = 100) -> list[dict]:
        with self.lock:
            ids = sorted(self.jobs, key=lambda value: self.jobs[value].get("created_at", 0), reverse=True)
        result = []
        for job_id in ids[:max(1, min(limit, 500))]:
            try:
                result.append(self.get(job_id))
            except KeyError:
                continue
        return result

    def cancel(self, job_id: str, force: bool = False) -> dict:
        status = self.get(job_id)
        if status["status"] in TERMINAL:
            return status
        directory = job_directory(job_id)
        (directory / "cancel.requested").touch()
        status.update({"status": "cancelling", "stage": "cancelling", "updated_at": time.time()})
        atomic_json(directory / "status.json", status)
        with self.lock:
            self.jobs[job_id] = status
            process = self.current_process if self.current_id == job_id else None
        if force and process and process.poll() is None:
            terminate_process_tree(process.pid)
        return public_job(status)

    def state(self) -> dict:
        with self.lock:
            current = self.current_id
            queued = self.pending.qsize()
        return {"current_job": current, "queued": queued}

    def cleanup_retention(self, *, force: bool = False) -> dict:
        with self.lock:
            current = self.current_id
        report = self.retention.cleanup(current, force=force)
        deleted = {item["id"] for item in report.get("deleted", {}).get("jobs", [])}
        if deleted:
            with self.lock:
                for job_id in deleted:
                    self.jobs.pop(job_id, None)
        return report

    def stop(self) -> None:
        self.stopping.set()
        with self.lock:
            process = self.current_process
        if process and process.poll() is None:
            terminate_process_tree(process.pid)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=15)

    def _mark(self, job_id: str, **values) -> None:
        directory = job_directory(job_id)
        status = self.get(job_id)
        status.update(values, updated_at=time.time())
        atomic_json(directory / "status.json", status)
        with self.lock:
            self.jobs[job_id] = status

    def _scheduler(self) -> None:
        while not self.stopping.is_set():
            try:
                job_id = self.pending.get(timeout=0.25)
            except queue.Empty:
                try:
                    self.cleanup_retention()
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    with (LOGS / "retention.log").open("a", encoding="utf-8") as stream:
                        stream.write(f"{time.time()} {type(exc).__name__}: {exc}\n")
                continue
            process = None
            log = None
            try:
                status = self.get(job_id)
                directory = OUTPUTS / job_id
                if status["status"] == "cancelling" or (directory / "cancel.requested").exists():
                    self._mark(job_id, status="cancelled", stage="cancelled", finished_at=time.time(),
                               error="任务在排队阶段被取消")
                    continue
                job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
                kind = job["kind"]
                python = TRANSCRIBE_PYTHON if kind in TRANSCRIBE_KINDS else CORE_PYTHON
                module = "app.yue2_app.transcribe_worker" if kind in TRANSCRIBE_KINDS else "app.yue2_app.core_worker"
                if not python.is_file():
                    raise RuntimeError(f"运行时未安装：{python}。请先运行 install_runtime.bat")
                command = [str(python), "-X", "utf8", "-m", module, "--root", str(ROOT), "--job-dir", str(directory)]
                environment = os.environ.copy()
                environment.update({
                    "YUE2_HOME": str(ROOT), "YUE2_KIT": str(ROOT), "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                    "HF_HOME": str(CACHE / "huggingface"),
                    "HF_MODULES_CACHE": str(CACHE / "huggingface" / "modules"),
                    "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "runtime" / "playwright"),
                })
                environment["PATH"] = str(ROOT / "runtime" / "ffmpeg") + os.pathsep + environment.get("PATH", "")
                log_path = LOGS / f"{job_id}.log"
                log = log_path.open("ab", buffering=0)
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._mark(job_id, status="running", stage="starting", started_at=time.time(),
                           log=str(log_path), command=command)
                process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=flags)
                with self.lock:
                    self.current_id, self.current_process = job_id, process
                self._mark(job_id, worker_pid=process.pid)
                return_code = process.wait()
                log.close()
                log = None
                latest = self.get(job_id)
                if latest.get("status") not in TERMINAL:
                    if latest.get("status") == "cancelling" or (directory / "cancel.requested").exists():
                        self._mark(job_id, status="cancelled", stage="cancelled", finished_at=time.time(),
                                   error="任务已终止", return_code=return_code)
                    else:
                        self._mark(job_id, status="failed", stage="failed", finished_at=time.time(),
                                   error=f"worker 已退出，返回码 {return_code}；请查看日志", return_code=return_code)
            except BaseException as exc:
                if process and process.poll() is None:
                    terminate_process_tree(process.pid)
                try:
                    self._mark(job_id, status="failed", stage="failed", finished_at=time.time(),
                               error=str(exc), traceback=traceback.format_exc()[-12000:])
                except BaseException:
                    pass
            finally:
                if log is not None:
                    log.close()
                with self.lock:
                    self.current_id, self.current_process = None, None
                self.pending.task_done()


def rotate_server_log(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < SERVER_LOG_MAX_BYTES:
        return
    oldest = path.with_name(path.name + f".{SERVER_LOG_BACKUPS}")
    oldest.unlink(missing_ok=True)
    for index in range(SERVER_LOG_BACKUPS - 1, 0, -1):
        source = path.with_name(path.name + f".{index}")
        if source.exists():
            source.replace(path.with_name(path.name + f".{index + 1}"))
    path.replace(path.with_name(path.name + ".1"))


STORE: JobStore | None = None
WEB_ROOT = ROOT / "app" / "web"


class Handler(BaseHTTPRequestHandler):
    server_version = "YuE2Local/" + __version__

    def log_message(self, format, *args):
        line = f"{self.log_date_time_string()} {self.client_address[0]} {format % args}\n"
        with _LOG_LOCK:
            path = LOGS / "server.log"
            rotate_server_log(path)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)

    def _json(self, status: int, value: object):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    def _body_json(self, maximum: int = 4 * 1024 * 1024):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("请求必须使用 application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > maximum:
            raise ValueError("请求正文大小无效")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _static(self, path: Path):
        if not path.is_file():
            return self._error(404, "文件不存在")
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime.endswith("javascript") else ""))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        assert STORE is not None
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True, "version": __version__, "root": str(ROOT),
                                        "ready": runtime_ready(), **STORE.state()})
            if path == "/api/jobs":
                limit = int(urllib.parse.parse_qs(parsed.query).get("limit", ["100"])[0])
                return self._json(200, {"jobs": STORE.list(limit)})
            if path == "/api/retention":
                return self._json(200, STORE.retention.status())
            if path.startswith("/api/jobs/"):
                return self._json(200, STORE.get(path.split("/")[3]))
            if path.startswith("/api/files/"):
                pieces = path.split("/")
                if len(pieces) < 5:
                    return self._error(400, "缺少文件路径")
                job_id = pieces[3]
                STORE.get(job_id)
                relative = Path(urllib.parse.unquote("/".join(pieces[4:])))
                directory = job_directory(job_id)
                file = within(directory, directory / relative)
                return self._static(file)
            if path == "/" or path == "/index.html":
                return self._static(WEB_ROOT / "index.html")
            if path.startswith("/static/"):
                file = within(WEB_ROOT, WEB_ROOT / path.removeprefix("/static/"))
                return self._static(file)
            return self._error(404, "接口不存在")
        except KeyError:
            return self._error(404, "任务不存在")
        except (ValueError, OSError) as exc:
            return self._error(400, str(exc))

    def do_POST(self):
        assert STORE is not None
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlparse(origin).netloc.lower() != self.headers.get("Host", "").lower():
                return self._error(403, "拒绝跨站请求")
            if path == "/api/jobs":
                data = self._body_json()
                return self._json(202, STORE.create(data.get("kind", "generate"), data.get("request", {})))
            if path == "/api/retention/cleanup":
                return self._json(200, STORE.cleanup_retention(force=True))
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                data = self._body_json(1024) if int(self.headers.get("Content-Length", "0")) else {}
                return self._json(200, STORE.cancel(job_id, bool(data.get("force", False))))
            if path == "/api/uploads":
                params = urllib.parse.parse_qs(parsed.query)
                original = params.get("filename", ["upload.wav"])[0]
                suffix = Path(original).suffix.lower()
                if suffix not in {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac"}:
                    raise ValueError("支持 WAV、FLAC、MP3、M4A、OGG、AAC")
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024 * 1024:
                    raise ValueError("音频大小必须在 1GB 以内")
                destination = UPLOADS / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + suffix)
                remaining = length
                try:
                    with destination.open("wb") as stream:
                        while remaining:
                            block = self.rfile.read(min(8 * 1024 * 1024, remaining))
                            if not block:
                                raise ValueError("上传中断")
                            stream.write(block)
                            remaining -= len(block)
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
                return self._json(201, {"path": str(destination), "bytes": length, "name": Path(original).name})
            if path == "/api/export":
                data = self._body_json()
                job_id = str(data["job_id"])
                status = STORE.get(job_id)
                source = within(OUTPUTS, job_directory(job_id) / "artifacts")
                if not source.is_dir():
                    raise FileNotFoundError("任务没有可导出的工件")
                export_root = (ROOT / "exports").resolve()
                requested = Path(str(data.get("destination") or ""))
                base = within(export_root, requested if requested.is_absolute() else export_root / requested)
                base.mkdir(parents=True, exist_ok=True)
                destination = within(export_root, base / status["id"])
                if destination.exists():
                    destination = within(export_root, base / f"{status['id']}-{time.strftime('%H%M%S')}")
                shutil.copytree(source, destination)
                return self._json(200, {"destination": str(destination)})
            if path == "/api/unload":
                data = self._body_json(1024) if int(self.headers.get("Content-Length", "0")) else {}
                state = STORE.state()
                if data.get("cancel_current") and state["current_job"]:
                    STORE.cancel(state["current_job"], force=bool(data.get("force", False)))
                return self._json(200, {"ok": True, "message": "worker 按任务隔离，空闲时不占用模型显存", **STORE.state()})
            return self._error(404, "接口不存在")
        except KeyError as exc:
            return self._error(400, f"缺少字段：{exc}")
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return self._error(400, str(exc))


def main(argv=None) -> int:
    global STORE
    parser = argparse.ArgumentParser(description="YuE2 本地整合包服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8189)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("YuE2 service only supports a loopback host")
    ensure_layout()
    STORE = JobStore()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    state_path = ROOT / "server.json"
    state_path.write_text(json.dumps({"host": args.host, "port": args.port,
                                      "pid": os.getpid(), "started_at": time.time()}, indent=2), encoding="utf-8")
    print(f"YuE2 本地整合包：http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        STORE.stop()
        server.server_close()
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("pid") == os.getpid():
                state_path.unlink(missing_ok=True)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
