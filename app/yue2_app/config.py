from __future__ import annotations

import json
import os
from pathlib import Path


def kit_root() -> Path:
    configured = os.environ.get("YUE2_HOME") or os.environ.get("YUE2_KIT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]


ROOT = kit_root()
MODELS = ROOT / "models"
OUTPUTS = ROOT / "outputs" / "jobs"
UPLOADS = ROOT / "uploads"
LOGS = ROOT / "logs"
CACHE = ROOT / "cache"
RUNTIME = ROOT / "runtime"
CORE_PYTHON = RUNTIME / "core" / "python.exe"
TRANSCRIBE_PYTHON = RUNTIME / "transcribe" / "python.exe"
UPSTREAM = ROOT / "vendor"


def ensure_layout() -> None:
    for path in (OUTPUTS, UPLOADS, LOGS, CACHE / "huggingface"):
        path.mkdir(parents=True, exist_ok=True)


def model_paths() -> dict[str, Path]:
    return {
        "model": MODELS / "YuE2-3B",
        "vae": MODELS / "YuE2-Vae",
        "sheetsage": MODELS / "SheetSage2",
        "mert": MODELS / "MERT-v2-FullSong",
    }


def runtime_ready() -> dict[str, object]:
    paths = model_paths()
    return {
        "core_python": CORE_PYTHON.is_file(),
        "transcribe_python": TRANSCRIBE_PYTHON.is_file(),
        "models": {name: (path / "model.safetensors").is_file() for name, path in paths.items()},
        "upstream_source": (UPSTREAM / "yue2").is_dir(),
    }


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
