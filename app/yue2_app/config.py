from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path

from .model_verify import PINNED_MODELS
from .settings import model_directory, settings_info


def kit_root() -> Path:
    configured = os.environ.get("YUE2_HOME") or os.environ.get("YUE2_KIT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]


ROOT = kit_root()
OUTPUTS = ROOT / "outputs" / "jobs"
UPLOADS = ROOT / "uploads"
LOGS = ROOT / "logs"
CACHE = ROOT / "cache"
RUNTIME = ROOT / "runtime"
PYTHON = RUNTIME / "python.exe"
CORE_PYTHON = TRANSCRIBE_PYTHON = VOICE_PYTHON = PYTHON
UPSTREAM = ROOT / "vendor"


def ensure_layout() -> None:
    for path in (OUTPUTS, UPLOADS, LOGS, CACHE / "huggingface"):
        path.mkdir(parents=True, exist_ok=True)


def upstream_path(root: Path | None = None) -> Path:
    base = root or ROOT
    candidates = (base / "vendor", base / "research" / "wheel-0.1.5")
    return next((path for path in candidates if (path / "yue2").is_dir()), candidates[0])


def model_paths(root: Path | None = None, *, strict: bool = True) -> dict[str, Path]:
    models = model_directory((root or ROOT).resolve(), strict=strict)
    return {
        "model": models / "YuE2-3B",
        "vae": models / "YuE2-Vae",
        "sheetsage": models / "SheetSage2",
        "mert": models / "MERT-v2-FullSong",
    }


def voice_model_paths(root: Path | None = None, *, strict: bool = True) -> dict[str, Path]:
    models = model_directory((root or ROOT).resolve(), strict=strict)
    return {"seed_vc": models / "Seed-VC", "demucs": models / "Demucs"}


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
    configured = settings_info(base)
    paths = model_paths(base, strict=False)
    voice_paths = voice_model_paths(base, strict=False)
    models_root = Path(str(configured["model_directory"]))
    required = {
        "model": ("model.safetensors", "config.json", "qwen.tiktoken", "yue2_generation_config.json"),
        "vae": ("model.safetensors", "config.json", "modeling_vae.py"),
        "sheetsage": ("model.safetensors", "config.json", "modeling_sheetsage2.py", "processor_config.json"),
        "mert": ("model.safetensors", "config.json", "modeling_mert2.py", "preprocessor_config.json"),
    }
    identities = {"model": "YuE2-3B", "vae": "YuE2-Vae",
                  "sheetsage": "SheetSage2", "mert": "MERT-v2-FullSong"}
    models = {}
    for name in required:
        path = paths[name]
        weight = path / "model.safetensors"
        expected_size = PINNED_MODELS[identities[name]]["size"]
        models[name] = (_expected_size(weight, expected_size)
                        and all(_nonempty(path / filename) for filename in required[name] if filename != "model.safetensors"))
    source = upstream_path(base) / "yue2"
    transcribe_python = _nonempty(runtime / "python.exe")
    renderer = bool(transcribe_python and _nonempty(
        runtime / "Lib" / "site-packages" / "playwright" / "__init__.py")
        and any(_nonempty(path) for path in (runtime / "playwright").glob(
            "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"))
        and _render_assets_ready(paths["sheetsage"] / "render_assets"))
    voice_python = _nonempty(runtime / "python.exe")
    voice_files = (
        voice_paths["seed_vc"] / "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
        voice_paths["seed_vc"] / "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
        voice_paths["seed_vc"] / "rmvpe.pt",
        voice_paths["seed_vc"] / "campplus_cn_common.bin",
        voice_paths["seed_vc"] / "whisper-small" / "model.safetensors",
        voice_paths["seed_vc"] / "bigvgan_v2_44khz_128band_512x" / "config.json",
        voice_paths["seed_vc"] / "bigvgan_v2_44khz_128band_512x" / "bigvgan_generator.pt",
        voice_paths["demucs"] / "955717e8.safetensors",
        voice_paths["demucs"] / "955717e8.json",
        voice_paths["demucs"] / "htdemucs.yaml",
        models_root / "VOICE_MODEL_MANIFEST.json",
    )
    voice_models = all(_nonempty(path) for path in voice_files)
    voice_source = all(_nonempty(base / "vendor" / "seed-vc" / filename)
                       for filename in ("inference.py", "hf_utils.py", "LICENSE"))
    result = {
        "core_python": _nonempty(runtime / "python.exe"),
        "transcribe_python": transcribe_python,
        "voice_python": voice_python,
        "ffmpeg": _nonempty(runtime / "ffmpeg" / "ffmpeg.exe"),
        "renderer": renderer,
        "models": models,
        "voice_models": voice_models,
        "voice_source": voice_source,
        "upstream_source": all(_nonempty(source / filename) for filename in ("__init__.py", "pipeline.py")),
        "model_directory": str(models_root),
        "model_repository": configured["model_repository"],
        "settings_error": configured["error"],
    }
    result["capabilities"] = {
        "generation": bool(not configured["error"] and result["core_python"] and result["upstream_source"]
                           and models["model"] and models["vae"]),
        "transcription": bool(not configured["error"] and result["transcribe_python"] and result["ffmpeg"]
                              and models["sheetsage"] and models["mert"]),
        "score_renderer": bool(not configured["error"] and result["renderer"]),
        "voice_conversion": bool(not configured["error"] and voice_python and voice_models and voice_source
                                 and result["ffmpeg"]),
    }
    rvc_assets = models_root / "RVC"
    rvc_source = all(_nonempty(base / "vendor/rvc" / file) for file in (
        "UPSTREAM.json", "infer/cli.py", "train/train.py"))
    rvc_inference = bool(result["core_python"] and rvc_source and all(_nonempty(rvc_assets / file) for file in (
        "hubert_base/config.json", "hubert_base/pytorch_model.bin", "rmvpe.pt")))
    result["capabilities"]["rvc_inference"] = rvc_inference
    result["capabilities"]["rvc_training"] = bool(rvc_inference and all(_nonempty(rvc_assets / file) for file in (
        "pretrained_v2/f0G48k.pth", "pretrained_v2/f0D48k.pth")))
    result["capabilities"]["vocal_separation"] = bool(result["core_python"] and result["ffmpeg"] and all(
        _nonempty(voice_paths['demucs'] / file) for file in ('955717e8.safetensors', '955717e8.json', 'htdemucs.yaml')))
    return result


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
