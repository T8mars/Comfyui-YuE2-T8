from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path

from .model_verify import PINNED_MODELS


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


def _nonempty(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def _expected_size(path: Path, size: int) -> bool:
    try:
        return _nonempty(path) and path.stat().st_size == size
    except OSError:
        return False


def _render_assets_ready(directory: Path) -> bool:
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8-sig"))
        files = manifest.get("files")
        required = {"abcjs-basic-min.js", "renderer.js", "DejaVuSans.ttf", "LICENSE.font"}
        if not isinstance(files, dict) or not required <= set(files):
            return False
        base = directory.resolve()
        for relative, digest in files.items():
            if str(relative).replace("\\", "/").startswith("soundfonts/"):
                continue
            path = (base / str(relative)).resolve()
            if (base not in path.parents or not _nonempty(path)
                    or hashlib.sha256(path.read_bytes()).hexdigest() != str(digest).lower()):
                return False
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def runtime_ready(root: Path | None = None) -> dict[str, object]:
    base = root.resolve() if root is not None else ROOT
    runtime = base / "runtime"
    paths = model_paths(base)
    required = {
        "model": ("model.safetensors", "config.json", "qwen.tiktoken", "yue2_generation_config.json"),
        "vae": ("model.safetensors", "config.json", "modeling_vae.py"),
        "sheetsage": ("model.safetensors", "config.json", "modeling_sheetsage2.py", "processor_config.json"),
        "mert": ("model.safetensors", "config.json", "modeling_mert2.py", "preprocessor_config.json"),
    }
    identities = {"model": "YuE2-3B", "vae": "YuE2-Vae",
                  "sheetsage": "SheetSage2", "mert": "MERT-v2-FullSong"}
    models = {}
    for name, path in paths.items():
        weight = path / "model.safetensors"
        expected_size = PINNED_MODELS[identities[name]]["size"]
        models[name] = (_expected_size(weight, expected_size)
                        and all(_nonempty(path / filename) for filename in required[name] if filename != "model.safetensors"))
    source = upstream_path(base) / "yue2"
    transcribe_python = _nonempty(runtime / "transcribe" / "python.exe")
    renderer = bool(transcribe_python and _nonempty(
        runtime / "transcribe" / "Lib" / "site-packages" / "playwright" / "__init__.py")
        and any(_nonempty(path) for path in (runtime / "playwright").glob(
            "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"))
        and _render_assets_ready(paths["sheetsage"] / "render_assets"))
    result = {
        "core_python": _nonempty(runtime / "core" / "python.exe"),
        "transcribe_python": transcribe_python,
        "ffmpeg": _nonempty(runtime / "ffmpeg" / "ffmpeg.exe"),
        "renderer": renderer,
        "models": models,
        "upstream_source": all(_nonempty(source / filename) for filename in ("__init__.py", "pipeline.py")),
    }
    result["capabilities"] = {
        "generation": bool(result["core_python"] and result["upstream_source"]
                           and models["model"] and models["vae"]),
        "transcription": bool(result["transcribe_python"] and result["ffmpeg"]
                              and models["sheetsage"] and models["mert"]),
        "score_renderer": bool(result["renderer"]),
    }
    return result


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
