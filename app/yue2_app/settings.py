from __future__ import annotations

import json
import os
from pathlib import Path

from .io import atomic_json

SETTINGS_SCHEMA = 1
MODEL_REPOSITORY = "https://huggingface.co/t8star/YuE2-Comfy"


def default_model_directory(root: Path) -> Path:
    return root.resolve() / "models"


def _resolve_directory(root: Path, value: object) -> Path:
    text = str(value or "").strip()
    if not text:
        return default_model_directory(root)
    expanded = Path(os.path.expandvars(text)).expanduser()
    if not expanded.is_absolute():
        expanded = root.resolve() / expanded
    return expanded.resolve()


def settings_info(root: Path) -> dict[str, object]:
    root = root.resolve()
    default = default_model_directory(root)
    path = root / "settings.json"
    error = ""
    raw: object = "models"
    try:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("settings.json 必须是 JSON 对象")
            if value.get("schema", SETTINGS_SCHEMA) != SETTINGS_SCHEMA:
                raise ValueError("settings.json 版本不受支持")
            raw = value.get("model_directory", "models")
        directory = _resolve_directory(root, raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        directory = default
        error = str(exc)
    return {
        "schema": SETTINGS_SCHEMA,
        "model_directory": str(directory),
        "default_model_directory": str(default),
        "using_default": directory == default,
        "model_repository": MODEL_REPOSITORY,
        "settings_file": str(path),
        "error": error,
    }


def model_directory(root: Path, *, strict: bool = False) -> Path:
    info = settings_info(root)
    if strict and info["error"]:
        raise ValueError(str(info["error"]))
    return Path(str(info["model_directory"]))


def save_model_directory(root: Path, value: object) -> dict[str, object]:
    root = root.resolve()
    directory = _resolve_directory(root, value)
    if directory.exists() and not directory.is_dir():
        raise ValueError("模型路径必须是文件夹")
    directory.mkdir(parents=True, exist_ok=True)
    stored = "models" if directory == default_model_directory(root) else str(directory)
    atomic_json(root / "settings.json", {
        "schema": SETTINGS_SCHEMA,
        "model_directory": stored,
    })
    return settings_info(root)
