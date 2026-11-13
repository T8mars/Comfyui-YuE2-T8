"""Optional YuE2-VAE-RS x3 audio decoders.

The decoders read the same YuE2 latents as the pinned YuE2-Vae; only the latent-to-audio step changes. YuE2-Vae stays the
pinned bundle model for everything else. Weights: CC BY-NC 4.0, by Ruach Studio and Viktor Zhuromskyy, from m-a-p's
YuE2-Vae (https://huggingface.co/goldhub/YuE2-RS-Finetunes/tree/main/VAE-RS, DOI 10.5281/zenodo.24167962).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from .model_verify import sha256
from .settings import model_directory

STANDARD = "standard"
DIRECTORY = "YuE2-VAE-RS"
SOURCE = "goldhub/YuE2-RS-Finetunes"
REVISION = "b371be93fe7320c7084813b4f5967a6820756a06"
# x3 needs 60 latent frames of context on each side of a tile; the loader refuses a shorter halo.
HALO_FRAMES = 60
DECODER_TENSORS = 427
DECODERS = {
    "rs_x3_v1": {"step": "161953", "files": {
        "YuE2-VAE-RS-x3_v1.safetensors": {
            "size": 803071224, "sha256": "a88c8da0d0913115953196b01aaf770ea128ab224bde090a8f34ee46db3bb039"},
        "YuE2-VAE-RS-x3_v1-full.safetensors": {
            "size": 1068062656, "sha256": "6a946cc308dc580220d77420408cdd8c4854bbb12e5705c4c83f5c829fa1b5ba"},
    }},
    "rs_x3_v2": {"step": "155030", "files": {
        "YuE2-VAE-RS-x3_v2.safetensors": {
            "size": 803071224, "sha256": "114da484c564a265a694f019d0715cde2f87da88815a054f72ce77c5250f1a0c"},
        "YuE2-VAE-RS-x3_v2-full.safetensors": {
            "size": 1068062656, "sha256": "230052bb633cbc98f5d80565b62ea2a8f95a6856c749c16aa4c9050df5fbef2e"},
    }},
}
CHOICES = (STANDARD, *DECODERS)
_DECODER_PARTS = {"conv1", "pre", "blocks", "snake1", "conv2"}


def normalize(value) -> str:
    name = STANDARD if value is None else value
    if not isinstance(name, str) or name not in CHOICES:
        raise ValueError("音频解码器必须是以下之一：" + "、".join(CHOICES))
    return name


def download_url(filename: str) -> str:
    return f"https://huggingface.co/{SOURCE}/resolve/{REVISION}/VAE-RS/{filename}"


def directory(root: Path) -> Path:
    return model_directory(root.resolve(), strict=True) / DIRECTORY


def readiness(root: Path) -> dict[str, bool]:
    """Cheap presence check for the UI: a regular file of the released size. Jobs verify SHA-256."""
    try:
        base = directory(root)
    except (OSError, ValueError):
        return {name: False for name in DECODERS}
    ready = {}
    for name, spec in DECODERS.items():
        ready[name] = False
        for filename, entry in spec["files"].items():
            path = base / filename
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size == entry["size"]:
                    ready[name] = True
                    break
            except OSError:
                pass
    return ready


def _diagnose(path: Path, name: str) -> str:
    """Why a file under a release name is not that release, from its safetensors header only."""
    try:
        with path.open("rb") as stream:
            length = struct.unpack("<Q", stream.read(8))[0]
            if not 0 < length <= 64 * 2**20:
                raise ValueError
            header = json.loads(stream.read(length))
        metadata = header.pop("__metadata__", None) or {}
        tensors = {key: value for key, value in header.items() if key.split(".")[0] in _DECODER_PARTS}
    except (OSError, ValueError, struct.error, AttributeError, UnicodeDecodeError):
        return "不是可读取的 safetensors 文件（下载不完整或已损坏）"
    if not tensors:
        return "文件中没有 YuE2-VAE-RS 解码器张量；原版 YuE2-Vae 请放在 YuE2-Vae 目录"
    dtypes = sorted({str(value.get("dtype")) for value in tensors.values()})
    if dtypes != ["F32"]:
        return (f"解码器张量精度为 {'、'.join(dtypes)}；VAE-RS 只以 float32 发布和验证，"
                "半精度或量化副本不能按发布效果解码")
    if len(tensors) != DECODER_TENSORS:
        return f"解码器张量为 {len(tensors)} 个，发布版为 {DECODER_TENSORS} 个；文件不完整或被裁剪"
    step = str(metadata.get("step", ""))
    for other, spec in DECODERS.items():
        if other != name and step == spec["step"]:
            return f"这是 {other} 的文件，不是 {name}"
    if step != DECODERS[name]["step"]:
        return f"训练步数 {step or '未知'} 与 {name} 发布版 {DECODERS[name]['step']} 不符"
    return "内容与发布版不一致（下载不完整或已损坏）"


def resolve(root: Path, value, *, verify: bool = True) -> dict | None:
    """The selected release as a verified file identity, or None for the pinned YuE2-Vae decoder.

    The decoder-only file is preferred; the -full file (Standard encoder plus this decoder) is accepted in its place.
    verify=False checks presence and size only, for fast request validation; jobs always verify SHA-256."""
    name = normalize(value)
    if name == STANDARD:
        return None
    base = directory(root)
    files = DECODERS[name]["files"]
    present = [base / filename for filename in files if (base / filename).exists() or (base / filename).is_symlink()]
    if not present:
        first, entry = next(iter(files.items()))
        raise FileNotFoundError(f"未找到音频解码器 {name}：请下载 {first} 放到 {base}；"
                                f"下载地址：{download_url(first)}；SHA-256：{entry['sha256']}")
    path = present[0]
    entry = files[path.name]
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"音频解码器 {name} 必须是普通文件，不接受符号链接或文件夹：{path}")
    size = path.stat().st_size
    if size != entry["size"] or (verify and sha256(path) != entry["sha256"]):
        raise ValueError(f"音频解码器 {name} 不是发布版文件：{path}；{_diagnose(path, name)}；"
                         f"期望 SHA-256：{entry['sha256']}；重新下载：{download_url(path.name)}")
    return {"release": name, "file": f"{DIRECTORY}/{path.name}", "path": str(path), "bytes": size,
            "sha256": entry["sha256"], "source": SOURCE, "revision": REVISION, "halo_frames": HALO_FRAMES}
