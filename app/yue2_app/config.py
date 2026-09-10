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


def upstream_path(root: Path | None = None) -> Path:
    base = root or ROOT
    candidates = (base / "vendor", base / "research" / "wheel-0.1.5")
    return next((path for path in candidates if (path / "yue2").is_dir()), candidates[0])


def model_paths(root: Path | None = None) -> dict[str, Path]:
    models = (root / "models") if root is not None else MODELS
    return {
        "model": models / "YuE2-3B",
        "vae": models / "YuE2-Vae",
        "sheetsage": models / "SheetSage2",
        "mert": models / "MERT-v2-FullSong",
    }


def runtime_ready() -> dict[str, object]:
    paths = model_paths()
    required = {
        "model": ("model.safetensors", "config.json", "qwen.tiktoken", "yue2_generation_config.json"),
        "vae": ("model.safetensors", "config.json", "modeling_vae.py"),
        "sheetsage": ("model.safetensors", "config.json", "modeling_sheetsage2.py", "processor_config.json"),
        "mert": ("model.safetensors", "config.json", "modeling_mert2.py", "preprocessor_config.json"),
    }
    return {
        "core_python": CORE_PYTHON.is_file(),
        "transcribe_python": TRANSCRIBE_PYTHON.is_file(),
        "models": {name: all((path / filename).is_file() for filename in required[name])
                   for name, path in paths.items()},
        "upstream_source": (upstream_path() / "yue2").is_dir(),
    }


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
