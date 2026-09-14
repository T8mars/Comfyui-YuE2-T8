"""Validated YuE2 training dataset preparation.

The community regularizer is converted once from its pinned, verified pickle
container into primitive NumPy arrays plus JSON. User-controlled pickle files
are never accepted by this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np

from .asset_library import AssetLibrary
from .io import atomic_json, sha256
from .training_resources import manifest as resource_manifest, resource_directory, status as resource_status

REGULARIZER_SCHEMA = 1
DATASET_SCHEMA = 1
CODEC_SIZE = 32768


def _identity(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_regularizer_paths(root: Path) -> tuple[Path, Path, Path]:
    directory = resource_directory(root) / "regularizer-safe-v1"
    return directory / "metadata.json", directory / "codec.npy", directory / "offsets.npy"


def convert_regularizer(root: Path) -> dict:
    """Convert the one pinned pack. This is the only intentional pickle boundary."""
    import torch

    installed = resource_status(root)
    if not installed["ready"]:
        raise ValueError("请先安装并校验 YuE2 训练资源")
    expected = resource_manifest()["files"]["minted_regularizer_pack.pt"]
    source = resource_directory(root) / "minted_regularizer_pack.pt"
    if source.stat().st_size != int(expected["bytes"]) or sha256(source) != expected["sha256"]:
        raise ValueError("regularizer pack 身份与固定资源清单不一致")
    metadata_path, codec_path, offsets_path = _safe_regularizer_paths(root)
    manifest_path = metadata_path.parent / "manifest.json"
    try:
        converted = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (converted.get("source_sha256") == expected["sha256"] and metadata_path.is_file()
                and codec_path.is_file() and offsets_path.is_file()
                and converted.get("files", {}).get("metadata.json") == sha256(metadata_path)
                and converted.get("files", {}).get("codec.npy") == sha256(codec_path)
                and converted.get("files", {}).get("offsets.npy") == sha256(offsets_path)):
            return converted
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    # The exact hash above is the trust gate. No user path reaches weights_only=False.
    records = torch.load(source, map_location="cpu", weights_only=False)
    if not isinstance(records, list) or not records:
        raise ValueError("regularizer pack 不是非空记录列表")
    clean, codecs, offsets = [], [], [0]
    allowed = {"name", "src", "style", "lyrics", "codec"}
    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != allowed:
            raise ValueError(f"regularizer 第 {index + 1} 条字段不符合固定 schema")
        name, src, style, lyrics = (record[key] for key in ("name", "src", "style", "lyrics"))
        if not all(isinstance(value, str) for value in (name, src, style, lyrics)):
            raise ValueError("regularizer 文本字段类型无效")
        if src not in {"minted", "minted_val"}:
            raise ValueError("regularizer split 无效")
        codec = np.asarray(record["codec"])
        if codec.ndim != 1 or codec.size < 1 or codec.size > 200000 or codec.dtype.kind not in "iu":
            raise ValueError("regularizer codec 数组形状或类型无效")
        codec = codec.astype(np.int32, copy=False)
        if int(codec.min()) < 0 or int(codec.max()) >= CODEC_SIZE:
            raise ValueError("regularizer codec token 越界")
        clean.append({"name": name[:200], "src": src, "style": style, "lyrics": lyrics})
        codecs.append(codec)
        offsets.append(offsets[-1] + len(codec))
    directory = metadata_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory.with_name(directory.name + f".{os.getpid()}.tmp")
    if temporary.exists():
        import shutil
        shutil.rmtree(temporary)
    temporary.mkdir()
    try:
        atomic_json(temporary / "metadata.json", {"schema": REGULARIZER_SCHEMA, "records": clean})
        np.save(temporary / "codec.npy", np.concatenate(codecs).astype(np.int32, copy=False), allow_pickle=False)
        np.save(temporary / "offsets.npy", np.asarray(offsets, dtype=np.int64), allow_pickle=False)
        converted = {
            "schema": REGULARIZER_SCHEMA, "source_sha256": expected["sha256"], "records": len(clean),
            "files": {name: sha256(temporary / name) for name in ("metadata.json", "codec.npy", "offsets.npy")},
        }
        atomic_json(temporary / "manifest.json", converted)
        if directory.exists():
            import shutil
            shutil.rmtree(directory)
        os.replace(temporary, directory)
    finally:
        if temporary.exists():
            import shutil
            shutil.rmtree(temporary)
    return converted


class TokenizerHead:
    """Factory namespace to avoid importing Torch in the web service."""

    @staticmethod
    def build():
        import torch
        from torch import nn
        class Model(nn.Module):
            def __init__(self):
                super().__init__()
                self.inp = nn.Linear(1024, 512)
                self.pos = nn.Parameter(torch.zeros(1, 512, 512))
                layer = nn.TransformerEncoderLayer(512, 8, 2048, dropout=.1, batch_first=True,
                                                   norm_first=True, activation="gelu")
                self.enc = nn.TransformerEncoder(layer, 8)
                self.norm = nn.LayerNorm(512)
                self.head = nn.Linear(512, CODEC_SIZE)

            def forward(self, value):
                return self.head(self.norm(self.enc(self.inp(value) + self.pos[:, :value.shape[1]])))
        return Model()


def load_tokenizer_head(root: Path, device="cuda"):
    import torch
    head = TokenizerHead.build().to(device).eval()
    checkpoint = resource_directory(root) / "tokenizer_head_joint_v4.pt"
    payload = torch.load(checkpoint, map_location=device, weights_only=True)
    if not isinstance(payload, dict) or set(payload) != {"model", "cfg"}:
        raise ValueError("tokenizer head checkpoint schema 无效")
    head.load_state_dict(payload["model"], strict=True)
    head.requires_grad_(False)
    return head


def _read_text_revision(library: AssetLibrary, revision_id: str | None, fallback: str) -> str:
    if not revision_id:
        return fallback
    with library.reading() as db:
        row = db.execute("SELECT asset_id FROM revisions WHERE id=?", (revision_id,)).fetchone()
    if row is None:
        raise ValueError("训练素材关联的文本版本不存在")
    path, info = library.revision_file(row["asset_id"], revision_id)
    if sha256(path) != info["blob_sha256"]:
        raise ValueError("固定文本素材内容与版本摘要不一致")
    return path.read_text(encoding="utf-8").strip()


def _load_snapshot(library: AssetLibrary, snapshot_id: str) -> dict:
    with library.reading() as db:
        row = db.execute("SELECT * FROM dataset_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if row is None:
        raise ValueError("训练素材快照不存在")
    manifest = json.loads(row["manifest_json"])
    if _identity(manifest) != row["manifest_sha256"]:
        raise ValueError("训练素材快照内容已改变")
    return {**dict(row), **manifest}


def _mert_features(model, processor, waveform, device):
    import torch
    waveform = waveform.contiguous()
    chunks, seconds = [], waveform.numel() / 24000
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        for start in range(0, waveform.numel(), 24000 * 30):
            chunk = waveform[start:start + 24000 * 30]
            # MERT needs a usable analysis window. Pad the final partial second
            # instead of dropping it: dropping it and then stretching earlier
            # features to the full duration silently shifts lyric alignment.
            actual_samples = chunk.numel()
            if actual_samples < 24000:
                chunk = torch.nn.functional.pad(chunk, (0, 24000 - actual_samples))
            inputs = processor(chunk.cpu().numpy(), sampling_rate=24000, return_tensors="pt")
            inputs = {key: value.to(device) for key, value in inputs.items()}
            output = model(**inputs, output_hidden_states=True).hidden_states[20][0]
            if actual_samples < 24000:
                keep = max(1, int(round(output.shape[0] * actual_samples / 24000)))
                output = output[:keep]
            chunks.append(output.float().cpu())
    if not chunks:
        raise ValueError("训练片段至少需要 1 秒可解码音频")
    hidden = torch.cat(chunks, 0)
    frames = max(1, int(round(seconds * 25)))
    hidden = torch.nn.functional.interpolate(hidden.T[None], size=frames, mode="linear",
                                              align_corners=False)[0].T
    return hidden.numpy().astype(np.float16, copy=False)


def _semantic_tokens(head, features, device):
    import numpy as np
    import torch
    value = features.astype(np.float32)
    value = (value - value.mean(0)) / (value.std(0) + 1e-5)
    total, window, overlap = len(value), 512, 256
    result = np.zeros(total, dtype=np.int32)
    starts = list(range(0, max(1, total - window + 1), overlap))
    if starts[-1] + window < total:
        starts.append(max(0, total - window))
    with torch.inference_mode():
        for start in starts:
            block, count = value[start:start + window], min(window, total - start)
            if len(block) < window:
                block = np.pad(block, ((0, window - len(block)), (0, 0)))
            with torch.autocast("cuda", dtype=torch.bfloat16):
                predicted = head(torch.from_numpy(block[None]).to(device))[0, :count].float().argmax(-1).cpu().numpy()
            left = start + (0 if start == 0 else window // 4)
            right = start + count - (0 if start + count >= total else window // 4)
            result[left:right] = predicted[left - start:right - start]
    return result


def prepare_snapshot(root: Path, snapshot_id: str, output: Path, ctx=None) -> dict:
    """Create safe AR records from immutable assets. Each song remains a full aligned unit."""
    import numpy as np
    import torch
    from transformers import AutoFeatureExtractor, AutoModel
    from .audio_decode import load_paper_audio
    from .config import model_paths, upstream_path
    from .model_verify import PINNED_MODELS

    installed = resource_status(root)
    if not installed["ready"]:
        raise ValueError("YuE2 训练资源尚未安装")
    convert_regularizer(root)
    library, device = AssetLibrary(root), "cuda"
    snapshot = _load_snapshot(library, snapshot_id)
    if snapshot.get("training_kind") != "yue2_style":
        raise ValueError("这个素材快照不是 YuE2 歌曲风格训练")
    options = snapshot.get("options", {})
    default_style = str(options.get("default_style", "")).strip()
    default_lyrics = str(options.get("default_lyrics", "[instrumental]")).strip() or "[instrumental]"
    if not torch.cuda.is_available():
        raise RuntimeError("YuE2 训练数据准备需要 NVIDIA CUDA")
    models = model_paths(root, strict=True)
    processor = AutoFeatureExtractor.from_pretrained(models["mert"], trust_remote_code=True,
                                                     local_files_only=True)
    mert = AutoModel.from_pretrained(models["mert"], trust_remote_code=True,
                                     local_files_only=True, torch_dtype=torch.bfloat16).to(device).eval()
    mert.requires_grad_(False)
    head = load_tokenizer_head(root, device)
    # Use reviewed in-repository code instead of model-directory Python files.
    import sys
    vendor = str(upstream_path(root))
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from yue2.protocol import SongRequest, token_prefixes
    from yue2.tokenization_yue2 import YuE2TextTokenizer
    tokenizer = YuE2TextTokenizer(models["model"] / "qwen.tiktoken")
    output.mkdir(parents=True, exist_ok=True)
    records, total_seconds, files = [], 0.0, {}
    for index, item in enumerate(snapshot["items"]):
        if ctx:
            ctx.progress("yue2_prepare", index, len(snapshot["items"]))
        audio_path, audio_info = library.revision_file(item["asset_id"], item["revision_id"])
        if sha256(audio_path) != item["blob_sha256"]:
            raise ValueError("固定训练音频内容与快照摘要不一致")
        duration = float(audio_info and json.loads(audio_info["metadata_json"]).get("duration", 0))
        start, end = float(item["start"]), float(item["end"])
        if start < 0 or end > duration + .05 or end - start < 1:
            raise ValueError("训练片段边界与固定音频版本不一致")
        waveform = load_paper_audio(audio_path, max_seconds=end)
        waveform = waveform[round(start * 24000):round(end * 24000)]
        style = _read_text_revision(library, item.get("style_revision_id"), default_style)
        lyrics = _read_text_revision(library, item.get("lyrics_revision_id"), default_lyrics)
        if not style:
            raise ValueError("每条 YuE2 训练素材都需要曲风描述，或设置公共曲风")
        features = _mert_features(mert, processor, waveform, device)
        codec = _semantic_tokens(head, features, device)
        request = SongRequest(style=style[:1500], lyrics=lyrics, cot="off", seed=1,
                              id=f"asset-{index:04d}")
        prefix = np.asarray(token_prefixes(request, tokenizer), dtype=np.int64)
        if len(prefix) + len(codec) + 1 > 12288:
            raise ValueError("素材与完整歌词超过首期训练上下文；请提供同步片段歌词或缩短素材，系统不会静默截断")
        record_id = f"record-{index:04d}"
        np.save(output / f"{record_id}-codec.npy", codec, allow_pickle=False)
        np.save(output / f"{record_id}-prefix.npy", prefix, allow_pickle=False)
        files[f"{record_id}-codec.npy"] = sha256(output / f"{record_id}-codec.npy")
        files[f"{record_id}-prefix.npy"] = sha256(output / f"{record_id}-prefix.npy")
        records.append({"id": record_id, "asset_id": item["asset_id"], "revision_id": item["revision_id"],
                        "track_group_id": item["track_group_id"], "split": item["split"],
                        "style": style[:1500], "lyrics": lyrics, "seconds": end - start,
                        "codec": f"{record_id}-codec.npy", "prefix": f"{record_id}-prefix.npy"})
        total_seconds += end - start
        del waveform, features, codec
        torch.cuda.empty_cache()
    dataset = {"schema": DATASET_SCHEMA, "snapshot_id": snapshot_id,
               "snapshot_sha256": snapshot["manifest_sha256"], "records": records,
               "seconds": total_seconds, "model": {"mert": PINNED_MODELS["MERT-v2-FullSong"]["revision"],
               "head_sha256": resource_manifest()["files"]["tokenizer_head_joint_v4.pt"]["sha256"]},
               "files": files}
    dataset["identity"] = _identity({key: value for key, value in dataset.items() if key != "identity"})
    atomic_json(output / "dataset.json", dataset)
    if ctx:
        ctx.progress("yue2_prepare", len(records), len(records))
        ctx.memory("yue2_prepare_complete", records=len(records), seconds=total_seconds)
    return dataset


def load_regularizer(root: Path) -> list[dict]:
    """Load primitive arrays from the converted cache (allow_pickle remains false)."""
    import numpy as np
    convert_regularizer(root)
    metadata_path, codec_path, offsets_path = _safe_regularizer_paths(root)
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    codec = np.load(codec_path, allow_pickle=False, mmap_mode="r")
    offsets = np.load(offsets_path, allow_pickle=False)
    if len(offsets) != len(meta["records"]) + 1 or offsets[-1] != len(codec):
        raise ValueError("转换后的 regularizer 索引无效")
    return [{**record, "codec": codec[offsets[i]:offsets[i + 1]]}
            for i, record in enumerate(meta["records"])]
