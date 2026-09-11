from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from .artifacts import write_artifact_manifest, verify_artifact_manifest
from .io import atomic_json, sha256, within
from .settings import model_directory
from .worker_common import JobContext, configure_environment


def _audio_path(root: Path, value: object, *, reference: bool = False) -> Path:
    candidate = Path(str(value or "")).expanduser().resolve()
    allowed = (root / "uploads",) if reference else (root / "uploads", root / "outputs" / "jobs")
    for base in allowed:
        try:
            resolved = within(base, candidate)
        except ValueError:
            continue
        if resolved.is_file() and not resolved.is_symlink():
            return resolved
    label = "参考音色" if reference else "待转换歌曲"
    raise ValueError(f"{label}文件不在允许的本地目录中：{candidate}")


def _number(request: dict, name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(request.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 必须在 {low} 到 {high} 之间")
    return value


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if audio.size == 0 or sample_rate <= 0 or not np.isfinite(audio).all():
        raise ValueError(f"音频无效：{path.name}")
    return audio, int(sample_rate)


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    from scipy.signal import resample_poly
    divisor = math.gcd(source_rate, target_rate)
    return resample_poly(audio, target_rate // divisor, source_rate // divisor, axis=0).astype(np.float32)


def remix_audio(converted_vocal: Path, accompaniment: Path, destination: Path, *,
                vocal_gain_db: float = 0.0, accompaniment_gain_db: float = 0.0,
                sample_rate: int = 48000) -> dict:
    vocal, vocal_rate = _read_audio(converted_vocal)
    backing, backing_rate = _read_audio(accompaniment)
    vocal = _resample(vocal.mean(axis=1, keepdims=True), vocal_rate, sample_rate)
    backing = _resample(backing, backing_rate, sample_rate)
    if backing.shape[1] == 1:
        backing = np.repeat(backing, 2, axis=1)
    elif backing.shape[1] > 2:
        backing = backing[:, :2]
    vocal = np.repeat(vocal, 2, axis=1)
    frames = max(len(vocal), len(backing))
    mixed = np.zeros((frames, 2), dtype=np.float32)
    mixed[:len(vocal)] += vocal * (10.0 ** (vocal_gain_db / 20.0))
    mixed[:len(backing)] += backing * (10.0 ** (accompaniment_gain_db / 20.0))
    peak_before = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    limiter_gain = min(1.0, 0.98 / peak_before) if peak_before > 0 else 1.0
    mixed *= limiter_gain
    if not np.isfinite(mixed).all() or float(np.max(np.abs(mixed))) <= 1e-7:
        raise RuntimeError("混音结果为空或包含无效采样")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, mixed, sample_rate, format="FLAC", subtype="PCM_24")
    return {
        "sample_rate": sample_rate,
        "channels": 2,
        "duration_seconds": round(frames / sample_rate, 3),
        "peak_before_limit": round(peak_before, 6),
        "limiter_gain": round(limiter_gain, 6),
    }


def _run_seed_vc(root: Path, source: Path, reference: Path, output: Path, request: dict,
                 ctx: JobContext | None = None) -> Path:
    source_root = root / "vendor" / "seed-vc"
    model_root = model_directory(root, strict=True) / "Seed-VC"
    os.environ["SEED_VC_MODEL_ROOT"] = str(model_root)
    sys.path.insert(0, str(source_root))
    previous = Path.cwd()
    try:
        os.chdir(source_root)
        import inference

        args = SimpleNamespace(
            source=str(source),
            target=str(reference),
            output=str(output),
            diffusion_steps=int(request["diffusion_steps"]),
            length_adjust=1.0,
            inference_cfg_rate=float(request["cfg_rate"]),
            f0_condition=True,
            auto_f0_adjust=bool(request["auto_f0_adjust"]),
            semi_tone_shift=int(request["semi_tone_shift"]),
            checkpoint=str(model_root / "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"),
            config=str(model_root / "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml"),
            fp16=True,
            progress_callback=(lambda completed, total: ctx.progress("converting_voice", completed, total)) if ctx else None,
            cancelled=ctx.cancelled if ctx else None,
        )
        inference.main(args)
    finally:
        os.chdir(previous)
    candidates = sorted(output.glob("vc_*.wav"), key=lambda path: path.stat().st_mtime)
    if len(candidates) != 1:
        raise RuntimeError(f"Seed-VC 未产生唯一的转换音轨（找到 {len(candidates)} 个）")
    return candidates[0]


def restore_voice_stage(previous: Path, output: Path, stage: str, files: list[str],
                        models: dict, source: dict) -> bool:
    if not (previous / (stage + "_manifest.json")).is_file():
        return False
    manifest = verify_artifact_manifest(previous, stage + "_manifest.json",
                                        "yue2-voice-" + stage + "-v1", set(files))
    if manifest["models"] != models or manifest.get("source") != source:
        return False
    for name in files:
        if previous.resolve() != output.resolve():
            shutil.copy2(previous / name, output / name)
    return True


def _separate_vocals(root: Path, source: Path, ctx: JobContext,
                     vocals_path: Path, accompaniment_path: Path) -> None:
    import torch
    import yaml
    from demucs.api import Separator
    from demucs.apply import BagOfModels
    from demucs.hf import load_safetensors_model

    model_root = model_directory(root, strict=True) / "Demucs"
    bag_config = yaml.safe_load((model_root / "htdemucs.yaml").read_text(encoding="utf-8"))
    model = load_safetensors_model(model_root / "955717e8.safetensors")
    bag = BagOfModels([model], bag_config.get("weights"), bag_config.get("segment"))
    separator = Separator.__new__(Separator)
    separator._model = bag
    separator._audio_channels = bag.audio_channels
    separator._samplerate = bag.samplerate

    def progress(value: dict) -> None:
        ctx.check_cancelled()
        length = max(1, int(value.get("audio_length") or 1))
        offset = max(0, int(value.get("segment_offset") or 0))
        ctx.update("separating_vocals", progress=min(0.99, offset / length))

    separator.update_parameter(
        device="cuda", shifts=1, overlap=0.25, split=True, segment=None,
        jobs=0, progress=False, callback=progress, callback_arg=None,
    )
    origin, stems = separator.separate_audio_file(source)
    vocal = stems["vocals"].detach().float().cpu()
    accompaniment = (origin - stems["vocals"]).detach().float().cpu()
    sf.write(vocals_path, vocal.numpy().T, bag.samplerate, subtype="FLOAT")
    sf.write(accompaniment_path, accompaniment.numpy().T, bag.samplerate, subtype="FLOAT")
    del stems, origin, separator, bag, model
    torch.cuda.empty_cache()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    job_dir = within(root / "outputs" / "jobs", args.job_dir)
    configure_environment(root)
    ctx = JobContext(job_dir)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8-sig"))
    raw = job.get("request", {})
    try:
        request = {
            "source_path": raw.get("source_path"),
            "reference_path": raw.get("reference_path"),
            "diffusion_steps": int(_number(raw, "diffusion_steps", 30, 4, 50)),
            "cfg_rate": _number(raw, "cfg_rate", 0.7, 0.0, 1.5),
            "auto_f0_adjust": bool(raw.get("auto_f0_adjust", False)),
            "semi_tone_shift": int(_number(raw, "semi_tone_shift", 0, -12, 12)),
            "vocal_gain_db": _number(raw, "vocal_gain_db", 0, -18, 12),
            "accompaniment_gain_db": _number(raw, "accompaniment_gain_db", 0, -18, 12),
        }
        source = _audio_path(root, request["source_path"])
        reference = _audio_path(root, request["reference_path"], reference=True)
        reference_audio, reference_rate = _read_audio(reference)
        reference_seconds = len(reference_audio) / reference_rate
        if not 1.0 <= reference_seconds <= 30.0:
            raise ValueError("参考音色需要 1–30 秒的清晰干声，推荐 5–25 秒")

        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("参考音色运行时未检测到 NVIDIA CUDA")
        ctx.update("separating_vocals", pid=os.getpid(), gpu=torch.cuda.get_device_name(0))
        ctx.memory("voice_start")
        ctx.check_cancelled()
        output = job_dir / "artifacts" / "reference_cover"
        converted = output / "converted"
        output.mkdir(parents=True, exist_ok=True)
        separated_vocal = output / "separated_vocal.wav"
        backing_path = output / "accompaniment.wav"
        voice_manifest = json.loads(
            (model_directory(root, strict=True) / "VOICE_MODEL_MANIFEST.json").read_text(encoding="utf-8-sig"))
        previous = output
        if raw.get("resume_from"):
            previous = within(root / "outputs" / "jobs", Path(raw["resume_from"])) / "artifacts" / "reference_cover"
        separation_source = {"song_sha256": sha256(source)}
        separation_files = ["separated_vocal.wav", "accompaniment.wav"]
        if not restore_voice_stage(previous, output, "separation", separation_files, voice_manifest, separation_source):
            _separate_vocals(root, source, ctx, separated_vocal, backing_path)
        else:
            ctx.update("separating_vocals", resumed_stage="separation")
        write_artifact_manifest(output, "separation_manifest.json", "yue2-voice-separation-v1",
                                separation_files, models=voice_manifest, source=separation_source)
        ctx.update("separating_vocals", resumable=True)
        ctx.memory("separation_saved")

        ctx.update("loading_voice_model")
        ctx.check_cancelled()
        ctx.update("converting_voice", diffusion_steps=request["diffusion_steps"])
        converted_vocal = output / "converted_vocal.wav"
        conversion_source = {**separation_source, "reference_sha256": sha256(reference),
                             "settings": {k: request[k] for k in (
                                 "diffusion_steps", "cfg_rate", "auto_f0_adjust", "semi_tone_shift")}}
        if not restore_voice_stage(previous, output, "conversion", ["converted_vocal.wav"], voice_manifest, conversion_source):
            raw_converted = _run_seed_vc(root, separated_vocal, reference, converted, request, ctx)
            shutil.copy2(raw_converted, converted_vocal)
        else:
            ctx.update("converting_voice", resumed_stage="conversion")
        write_artifact_manifest(output, "conversion_manifest.json", "yue2-voice-conversion-v1",
                                ["converted_vocal.wav"], models=voice_manifest, source=conversion_source)
        ctx.memory("conversion_saved")
        ctx.check_cancelled()

        ctx.update("remixing")
        final_audio = output / "audio.flac"
        audio_info = remix_audio(
            converted_vocal, backing_path, final_audio,
            vocal_gain_db=request["vocal_gain_db"],
            accompaniment_gain_db=request["accompaniment_gain_db"],
        )
        manifest_source = {
            "song": {"file": source.name, "sha256": sha256(source), "bytes": source.stat().st_size},
            "reference_voice": {
                "file": reference.name, "sha256": sha256(reference), "bytes": reference.stat().st_size,
                "duration_seconds": round(reference_seconds, 3),
            },
        }
        voice_manifest = json.loads(
            (model_directory(root, strict=True) / "VOICE_MODEL_MANIFEST.json").read_text(encoding="utf-8-sig")
        )
        manifest_path, _ = write_artifact_manifest(
            output, "reference_cover_manifest.json", "yue2-reference-cover-v1",
            ["audio.flac", "converted_vocal.wav", "separated_vocal.wav", "accompaniment.wav"],
            models=voice_manifest, source=manifest_source, config={key: value for key, value in request.items()
                                                                  if not key.endswith("_path")},
        )
        result = {
            "audio": str(final_audio),
            "converted_vocal": str(converted_vocal),
            "separated_vocal": str(separated_vocal),
            "accompaniment": str(backing_path),
            "artifact_dir": str(output),
            "manifest": str(manifest_path),
            "audio_info": audio_info,
            "settings": {key: value for key, value in request.items() if not key.endswith("_path")},
        }
        atomic_json(output / "job_result.json", result)
        ctx.memory("voice_complete")
        ctx.finish(result=result)
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 130 if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
