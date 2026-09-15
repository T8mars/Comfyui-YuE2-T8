from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .settings import model_directory


UPSTREAM_COMMIT = "a2ddf7ff655b2a2bbe11795a07f5ab293e383786"
MODEL_REPOSITORIES = {
    "MuLaCover": {
        "repo": "HeartMuLa/MuLaCover",
        "revision": "bbbaef2b31835c2ef5172ff528dbe325230d46fb",
        "required": {
            "config.json": 367,
            "gen_config.json": 246,
            "tokenizer.json": 9_085_657,
            "model-00001-of-00005.safetensors": 1_994_713_352,
            "model-00002-of-00005.safetensors": 1_981_941_248,
            "model-00003-of-00005.safetensors": 1_963_054_632,
            "model-00004-of-00005.safetensors": 1_999_477_480,
            "model-00005-of-00005.safetensors": 809_619_536,
            "model.safetensors.index.json": 56_360,
        },
    },
    "HeartCodec-oss": {
        "repo": "HeartMuLa/HeartCodec-oss-20260123",
        "revision": "f889dab0532cfa4bf459f2a3367eb6d346b8eeda",
        "required": {
            "config.json": 981,
            "model-00001-of-00002.safetensors": 4_930_472_472,
            "model-00002-of-00002.safetensors": 1_707_911_436,
            "model.safetensors.index.json": 83_875,
        },
    },
    "Qwen3-Embedding-0.6B": {
        "repo": "Qwen/Qwen3-Embedding-0.6B",
        "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "required": {
            "config.json": 727,
            "model.safetensors": 1_191_586_416,
            "tokenizer.json": 11_423_705,
            "tokenizer_config.json": 9_706,
        },
    },
}

YOURMT3 = {
    "revision": "5e66c1ea173a8186e0d20432b841d3180cc015b5",
    "relative": "yourmt3/last.ckpt",
    "bytes": 561_544_628,
    "url": (
        "https://huggingface.co/spaces/mimbres/YourMT3/resolve/"
        "5e66c1ea173a8186e0d20432b841d3180cc015b5/amt/logs/2024/"
        "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/"
        "checkpoints/last.ckpt"
    ),
}
CHORD_COMMIT = "481f4ce703f8822b99f4037e9104ba1760e21ea3"
CHORD_NAMES = tuple(
    f"joint_chord_net_ismir_naive_v1.0_reweight(0.0,10.0)_s{fold}.best.sdict"
    for fold in range(5)
)
CHORD_SIZES = {
    CHORD_NAMES[0]: 5_746_183,
    CHORD_NAMES[1]: 5_746_175,
    CHORD_NAMES[2]: 5_746_179,
    CHORD_NAMES[3]: 5_746_175,
    CHORD_NAMES[4]: 5_746_227,
}


def paths(root: Path, *, strict: bool = False) -> dict[str, Path]:
    base = model_directory(root.resolve(), strict=strict)
    return {name: base / name for name in (*MODEL_REPOSITORIES, "SymbolicTranscriptor")}


def _file_status(path: Path, expected: int | None = None) -> dict:
    try:
        actual = path.stat().st_size if path.is_file() and not path.is_symlink() else 0
    except OSError:
        actual = 0
    return {"path": str(path), "bytes": actual, "expected_bytes": expected,
            "ready": bool(actual and (expected is None or actual == expected))}


def readiness(root: Path) -> dict:
    locations = paths(root)
    components: dict[str, dict] = {}
    for name, spec in MODEL_REPOSITORIES.items():
        files = {relative: _file_status(locations[name] / relative, size)
                 for relative, size in spec["required"].items()}
        components[name] = {"directory": str(locations[name]),
                            "repo": spec["repo"], "revision": spec["revision"],
                            "ready": all(entry["ready"] for entry in files.values()),
                            "files": files}
    transcriptor = locations["SymbolicTranscriptor"]
    files = {YOURMT3["relative"]: _file_status(transcriptor / YOURMT3["relative"], YOURMT3["bytes"])}
    for filename in CHORD_NAMES:
        files[f"chord/{filename}"] = _file_status(
            transcriptor / "chord" / filename, CHORD_SIZES[filename]
        )
    components["SymbolicTranscriptor"] = {
        "directory": str(transcriptor), "ready": all(entry["ready"] for entry in files.values()),
        "files": files,
    }
    core = root / "vendor" / "mulacover"
    source_ready = all((core / relative).is_file() for relative in (
        "src/mulacover/pipeline.py", "src/mulacover/modeling.py",
        "compat/torchtune/modules/transformer.py", "compat/vector_quantize_pytorch/__init__.py",
    ))
    return {"ready": bool(source_ready and all(item["ready"] for item in components.values())),
            "source_ready": source_ready, "upstream_commit": UPSTREAM_COMMIT,
            "components": components}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(root: Path) -> Path:
    state = readiness(root)
    state["schema"] = 1
    destination = paths(root)["MuLaCover"].parent / "MULACOVER_MODEL_MANIFEST.json"
    destination.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination
