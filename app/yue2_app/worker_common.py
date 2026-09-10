from __future__ import annotations

import os
import time
import traceback
from pathlib import Path

from .io import atomic_json


class Cancelled(InterruptedError):
    pass


class JobContext:
    def __init__(self, job_dir: Path):
        self.job_dir = job_dir.resolve()
        self.status_path = self.job_dir / "status.json"
        self.cancel_path = self.job_dir / "cancel.requested"
        self.started = time.time()
        self.last_token_update = 0.0
        self.token_phase: str | None = None
        self.token_count = 0

    def cancelled(self) -> bool:
        return self.cancel_path.exists()

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled("用户已取消任务")

    def update(self, stage: str, **extra) -> None:
        current = {}
        try:
            import json
            current = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
        current.update({"status": "running", "stage": stage, "updated_at": time.time(), **extra})
        atomic_json(self.status_path, current)

    def token(self, phase: str, _token: int) -> None:
        if phase != self.token_phase:
            self.token_phase = phase
            self.token_count = 0
        self.token_count += 1
        now = time.monotonic()
        if now - self.last_token_update >= 0.5:
            self.last_token_update = now
            self.update("planning" if phase == "abc" else "semantic", tokens=self.token_count)

    def finish(self, **extra) -> None:
        self.check_cancelled()
        current = {}
        try:
            import json
            current = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
        current.update({"status": "complete", "stage": "complete", "progress": 1.0,
                        "updated_at": time.time(),
                        "finished_at": time.time(), **extra})
        atomic_json(self.status_path, current)

    def fail(self, exc: BaseException) -> None:
        status = "cancelled" if isinstance(exc, (Cancelled, InterruptedError, KeyboardInterrupt)) else "failed"
        current = {}
        try:
            import json
            current = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
        current.update({"status": status, "stage": status, "updated_at": time.time(),
                        "finished_at": time.time(), "error_type": type(exc).__name__, "error": str(exc),
                        "traceback": traceback.format_exc()[-12000:]})
        atomic_json(self.status_path, current)


def configure_environment(root: Path) -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HOME", str(root / "cache" / "huggingface"))
    os.environ.setdefault("HF_MODULES_CACHE", str(root / "cache" / "huggingface" / "modules"))
