from __future__ import annotations

import json
from pathlib import Path

from . import __version__
from .io import atomic_json, sha256, within
from .model_verify import pinned_entries
from .settings import model_directory

ARTIFACT_SCHEMA = 1


def declared_model_provenance(root: Path, names) -> dict:
    manifest_path = model_directory(root.resolve(), strict=True) / "MODEL_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    return {"bundle": manifest.get("bundle"), "models": pinned_entries(manifest, names)}


def generation_provenance(root: Path, runtime_weights: dict) -> dict:
    manifest_path = model_directory(root.resolve(), strict=True) / "MODEL_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    selected = pinned_entries(manifest, ("YuE2-3B", "YuE2-Vae"))
    expected = {
        "YuE2-3B": runtime_weights.get("mot", {}).get("files", {}).get("model.safetensors", {}).get("sha256"),
        "YuE2-Vae": runtime_weights.get("vae", {}).get("files", {}).get("model.safetensors", {}).get("sha256"),
    }
    for name, digest in expected.items():
        if not digest or str(selected[name].get("sha256", "")).lower() != str(digest).lower():
            raise ValueError(f"{name} 运行时权重与模型来源清单不一致")
    return {
        "bundle": manifest["bundle"],
        "models": selected,
        "runtime_identity": runtime_weights,
    }


def write_artifact_manifest(directory: Path, filename: str, kind: str, files: list[str], *,
                            models: dict, source: dict | None = None,
                            config: dict | None = None) -> tuple[Path, dict]:
    directory = directory.resolve()
    records = {}
    for name in files:
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(f"无效的工件清单路径：{name}")
        path = within(directory, directory / relative)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"工件缺失或是符号链接：{name}")
        records[relative.as_posix()] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    value = {
        "schema": ARTIFACT_SCHEMA,
        "kind": kind,
        "producer": {"name": "yue2-t8", "version": __version__},
        "models": models,
        "source": source or {},
        "config": config or {},
        "files": records,
    }
    path = directory / filename
    atomic_json(path, value)
    return path, value


def verify_artifact_manifest(directory: Path, filename: str, kind: str,
                             required: set[str]) -> dict:
    directory = directory.resolve()
    path = within(directory, directory / filename)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"缺少 {filename}；旧版或不完整的高级工件不能继续推理")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schema") != ARTIFACT_SCHEMA or value.get("kind") != kind:
        raise ValueError(f"{filename} 的格式或工件类型不正确")
    records = value.get("files")
    if not isinstance(records, dict) or not required <= set(records):
        raise ValueError(f"{filename} 缺少必要文件记录")
    for name, expected in records.items():
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(f"{filename} 包含越界路径")
        candidate = within(directory, directory / relative)
        if candidate.is_symlink() or not candidate.is_file():
            raise FileNotFoundError(f"清单文件缺失：{name}")
        if candidate.stat().st_size != int(expected.get("bytes", -1)) or sha256(candidate) != expected.get("sha256"):
            raise ValueError(f"高级工件完整性校验失败：{name}")
    if not isinstance(value.get("models"), dict):
        raise ValueError(f"{filename} 缺少模型来源")
    return value


def manifest_reference(path: Path) -> dict:
    return {"file": path.name, "sha256": sha256(path), "bytes": path.stat().st_size}


def verify_hash_manifest(directory: Path, filename: str, required: set[str]) -> dict:
    directory = directory.resolve()
    path = within(directory, directory / filename)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"缺少 {filename}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or not required <= set(value):
        raise ValueError(f"{filename} 缺少必要文件记录")
    for name, expected in value.items():
        relative = Path(name)
        if relative.is_absolute() or len(relative.parts) != 1 or not isinstance(expected, str):
            raise ValueError(f"{filename} 包含无效文件记录")
        candidate = within(directory, directory / relative)
        if candidate.is_symlink() or not candidate.is_file() or sha256(candidate) != expected:
            raise ValueError(f"谱系完整性校验失败：{name}")
    return value


def assert_provenance(manifest: dict, root: Path, runtime_weights: dict) -> None:
    current = generation_provenance(root, runtime_weights)
    recorded = manifest.get("models")
    if not isinstance(recorded, dict):
        raise ValueError("高级工件缺少模型来源")
    compatible = {key: recorded.get(key) for key in ("bundle", "models", "runtime_identity")}
    if compatible != current:
        raise ValueError("高级工件的模型来源与当前 YuE2 权重不一致")
