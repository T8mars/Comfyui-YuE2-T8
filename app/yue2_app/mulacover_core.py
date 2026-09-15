from __future__ import annotations

import gc
import json
import math
import sys
from pathlib import Path
from typing import Any

from .io import atomic_json
from .mulacover_models import UPSTREAM_COMMIT, readiness
from .settings import model_directory


AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac"}
MIDI_SUFFIXES = {".mid", ".midi"}


def prepare_imports(root: Path) -> None:
    vendor = root.resolve() / "vendor" / "mulacover"
    for directory in (vendor / "compat", vendor / "src"):
        value = str(directory)
        if value not in sys.path:
            sys.path.insert(0, value)


def _text(value: Any, name: str, *, required: bool = False, limit: int = 30_000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name}必须是文本")
    result = value.strip()
    if required and not result:
        raise ValueError(f"请填写{name}")
    if len(result) > limit:
        raise ValueError(f"{name}不能超过 {limit} 个字符")
    return result


def _number(request: dict, key: str, default: float, low: float, high: float) -> float:
    raw = request.get(key, default)
    try:
        if isinstance(raw, bool):
            raise ValueError()
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{key} 必须是数字") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{key} 必须在 {low}–{high} 之间")
    return value


def _integer(request: dict, key: str, default: int, low: int, high: int) -> int:
    raw = request.get(key, default)
    if isinstance(raw, bool):
        raise ValueError(f"{key} 必须是整数")
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{key} 必须是整数") from exc
    if value != raw and not (isinstance(raw, str) and raw.strip() == str(value)):
        raise ValueError(f"{key} 必须是整数")
    if not low <= value <= high:
        raise ValueError(f"{key} 必须在 {low}–{high} 之间")
    return value


def _input_path(root: Path, value: Any, name: str, suffixes: set[str]) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    allowed = tuple((root / relative).resolve() for relative in ("uploads", "outputs", "userdata"))
    if not any(path == base or base in path.parents for base in allowed):
        raise ValueError(f"{name}必须来自整合包上传、任务输出或资产库")
    if not path.is_file() or path.suffix.lower() not in suffixes:
        raise ValueError(f"{name}文件不存在或格式不受支持")
    return path


def style_tags(request: dict) -> str:
    def clean(key: str, fallback: str) -> str:
        value = _text(request.get(key, ""), fallback, limit=1_000)
        return " ".join(value.replace("[", "(").replace("]", ")").split())

    values = {
        "topic": clean("topic", "主题"),
        "genre": clean("genre", "流派"),
        "instrument": clean("instrument", "乐器"),
        "mood": clean("mood", "情绪"),
    }
    if not any(values.values()):
        raise ValueError("请至少填写流派、乐器、情绪或主题中的一项")
    return "; ".join(f"{key}:[{value or 'unspecified'}]" for key, value in values.items())


def normalize_request(root: Path, request: dict) -> dict:
    if not isinstance(request, dict):
        raise ValueError("request 必须是对象")
    lyrics = _text(request.get("lyrics", ""), "歌词", required=True)
    source_mode = str(request.get("source_mode", "audio"))
    if source_mode not in {"audio", "midi"}:
        raise ValueError("素材方式必须是 audio 或 midi")
    source: dict[str, Any]
    supplied_source = request.get("source") if isinstance(request.get("source"), dict) else {}
    if source_mode == "audio":
        raw_audio = supplied_source.get("ref_audio") or request.get("source_path")
        ref_audio = _input_path(root, raw_audio, "参考歌曲", AUDIO_SUFFIXES)
        try:
            import soundfile as sf
            if float(sf.info(str(ref_audio)).duration) > 900:
                raise ValueError("参考歌曲不能超过 15 分钟；请先裁剪需要重新编曲的部分")
        except ValueError:
            raise
        except Exception:
            pass
        source = {"ref_audio": str(ref_audio)}
        bpm = supplied_source.get("bpm", request.get("bpm"))
        if bpm not in (None, ""):
            source["bpm"] = _number({"bpm": bpm}, "bpm", 120, 30, 300)
    else:
        source = {
            "melody_midi": str(_input_path(root, supplied_source.get("melody_midi") or request.get("melody_midi"), "旋律 MIDI", MIDI_SUFFIXES)),
            "chord_midi": str(_input_path(root, supplied_source.get("chord_midi") or request.get("chord_midi"), "和弦 MIDI", MIDI_SUFFIXES)),
        }
        raw_drum = supplied_source.get("drum_midi") or request.get("drum_midi")
        if raw_drum:
            source["drum_midi"] = str(_input_path(root, raw_drum, "鼓组 MIDI", MIDI_SUFFIXES))
    return {
        "source_mode": source_mode,
        "source": source,
        "lyrics": lyrics,
        "tags": str(request.get("tags") or style_tags(request)),
        "topic": _text(request.get("topic", ""), "主题", limit=1_000),
        "genre": _text(request.get("genre", ""), "流派", limit=1_000),
        "instrument": _text(request.get("instrument", ""), "乐器", limit=1_000),
        "mood": _text(request.get("mood", ""), "情绪", limit=1_000),
        "duration_seconds": _integer(request, "duration_seconds", 30, 5, 300),
        "semitone_shift": _integer(request, "semitone_shift", 0, -12, 12),
        "octave_shift": _integer(request, "octave_shift", 0, -2, 2),
        "seed": _integer(request, "seed", 831001, 0, 2**63 - 1),
        "decode_seed": _integer(request, "decode_seed", 831002, 0, 2**63 - 1),
        "cfg_scale": _number(request, "cfg_scale", 1.5, 0.1, 5.0),
        "temperature": _number(request, "temperature", 1.0, 0.1, 2.0),
        "topk": _integer(request, "topk", 250, 1, 8191),
        "project_id": str(request.get("project_id") or "")[:128],
    }


def _transpose(condition, semitones: int):
    if not semitones:
        return condition
    from mulacover.symbolic import SymbolicCondition

    melody = condition.melody.clone()
    melody[:, 1] = (melody[:, 1] + semitones).clamp(0, 127)
    chords = condition.chords.clone()
    if len(chords):
        chords[:, 1] = (chords[:, 1] + semitones) % 12
    return SymbolicCondition(melody=melody, chords=chords, drums=condition.drums.clone(), bpm=condition.bpm)


def run(root: Path, job_dir: Path, request: dict, ctx) -> dict:
    root, job_dir = root.resolve(), job_dir.resolve()
    prepared = normalize_request(root, request)
    state = readiness(root)
    if not state["ready"]:
        missing = [name for name, item in state["components"].items() if not item["ready"]]
        raise RuntimeError("MuLaCover 模型尚未安装完整：" + "、".join(missing))
    prepare_imports(root)
    import soundfile as sf
    import torch
    from mulacover import MuLaCoverGenPipeline

    if not torch.cuda.is_available():
        raise RuntimeError("MuLaCover 需要可用的 NVIDIA CUDA 显卡")
    device = torch.device("cuda:0")
    dtypes = {
        "mulacover": torch.bfloat16,
        "codec": torch.float32,
        "qwen": torch.float32,
        "transcriptor": torch.float32,
    }
    artifact_dir = job_dir / "artifacts" / "mulacover"
    condition_dir = artifact_dir / "condition"
    audio_path = artifact_dir / "audio.flac"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    pipe = None
    try:
        ctx.update("mulacover_loading", message="正在加载重新编曲模型")
        pipe = MuLaCoverGenPipeline.from_pretrained(
            str(model_directory(root)), device=device, dtype=dtypes, lazy_load=True,
        )
        ctx.check_cancelled()
        ctx.update("mulacover_transcribing", message=(
            "正在从参考歌曲提取旋律与和弦" if prepared["source_mode"] == "audio" else "正在读取旋律与和弦 MIDI"
        ))
        condition = pipe._symbolic_condition(prepared["source"])
        semitones = prepared["semitone_shift"] + prepared["octave_shift"] * 12
        condition = _transpose(condition, semitones)
        midi_paths = condition.save_midi(condition_dir)
        ctx.check_cancelled()
        ctx.update("mulacover_style", message="正在编码歌词与曲风")
        model_inputs = pipe.preprocess(
            {
                "lyrics": prepared["lyrics"], "tags": prepared["tags"],
                "melody_midi": str(midi_paths["melody"]),
                "chord_midi": str(midi_paths["chord"]),
                "drum_midi": str(midi_paths["drums"]),
            },
            cfg_scale=prepared["cfg_scale"],
            max_audio_length_ms=prepared["duration_seconds"] * 1000,
        )
        ctx.check_cancelled()

        def progress(stage: str, completed: int, total: int) -> None:
            mapped = "mulacover_decoding" if stage == "decoding" else "mulacover_generating"
            ctx.check_cancelled()
            message = "正在解码完整歌曲" if mapped == "mulacover_decoding" else "正在生成重新编曲版本"
            ctx.update(mapped, message=message, completed=completed, total=total,
                       progress=completed / max(1, total))

        device_index = device.index if device.index is not None else torch.cuda.current_device()
        with torch.random.fork_rng(devices=[device_index]):
            torch.manual_seed(prepared["seed"])
            model_outputs = pipe._forward(
                model_inputs,
                max_audio_length_ms=prepared["duration_seconds"] * 1000,
                temperature=prepared["temperature"], topk=prepared["topk"],
                cfg_scale=prepared["cfg_scale"], disable_progress=True,
                cancelled=ctx.cancelled, on_progress=progress,
            )
        ctx.check_cancelled()
        ctx.update("mulacover_decoding", message="正在解码完整歌曲")
        pipe.postprocess(
            model_outputs, save_path=audio_path, disable_progress=True,
            cancelled=ctx.cancelled, on_progress=progress,
            decode_seed=prepared["decode_seed"],
        )
        info = sf.info(audio_path)
        if info.frames <= 0 or info.duration <= 0:
            raise RuntimeError("MuLaCover 没有生成可播放的音频")
        metadata = {
            "schema": 1,
            "engine": "MuLaCover",
            "upstream_commit": UPSTREAM_COMMIT,
            "model_revision": state["components"]["MuLaCover"]["revision"],
            "request": prepared,
            "audio": {"sample_rate": info.samplerate, "channels": info.channels, "seconds": info.duration},
        }
        atomic_json(artifact_dir / "metadata.json", metadata)
        return {
            "audio": str(audio_path),
            "audio_seconds": info.duration,
            "sample_rate": info.samplerate,
            "seed": prepared["seed"],
            "decode_seed": prepared["decode_seed"],
            "style": prepared["tags"],
            "semitone_shift": semitones,
            "condition_dir": str(condition_dir),
            "melody_midi": str(midi_paths["melody"]),
            "chord_midi": str(midi_paths["chord"]),
            "drum_midi": str(midi_paths["drums"]),
            "metadata": str(artifact_dir / "metadata.json"),
        }
    finally:
        pipe = None
        gc.collect()
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
