from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from .artifacts import (
    assert_provenance,
    generation_provenance,
    manifest_reference,
    verify_artifact_manifest,
    verify_hash_manifest,
    write_artifact_manifest,
)
from .config import model_paths, upstream_path
from .io import atomic_json, within
from .worker_common import JobContext, configure_environment


def add_upstream(root: Path) -> None:
    source = upstream_path(root)
    if not (source / "yue2").is_dir():
        raise FileNotFoundError(f"找不到随节点发布的 YuE2 推理源码：{source}")
    sys.path.insert(0, str(source))


def create_pipe(root: Path, request: dict):
    from yue2 import YuE2Pipeline

    paths = model_paths(root)
    backend = request.get("backend", "torch-eager")
    budget = float(request.get("memory_budget_gib", 23.5))
    return YuE2Pipeline.from_pretrained(
        str(paths["model"]), vae=str(paths["vae"]), device="cuda",
        memory_budget_gib=budget, backend=backend, quantization="none",
        offload_ar=bool(request.get("offload_ar", False)), local_files_only=True,
        verify_hashes=bool(request.get("verify_hashes", False)), progress=False,
    )


def generation_kwargs(request: dict, seed: int | None = None) -> dict:
    result = {
        "style": str(request.get("style", "")),
        "lyrics": str(request.get("lyrics", "")),
        "cot": request.get("cot", "full"),
        "seed": int(request.get("seed", 831001) if seed is None else seed),
    }
    if request.get("abc"):
        result["abc"] = str(request["abc"])
    if request.get("cfg_scale") is not None:
        result["cfg_scale"] = float(request["cfg_scale"])
    if request.get("abc_sampling"):
        result["abc_sampling"] = dict(request["abc_sampling"])
    if request.get("semantic_sampling"):
        result["semantic_sampling"] = dict(request["semantic_sampling"])
    return result


def generate_one(pipe, ctx: JobContext, request: dict, destination: Path, seed: int):
    from yue2.pipeline import SongResult
    from yue2.storage import identity

    kwargs = generation_kwargs(request, seed)
    request_only = {k: v for k, v in kwargs.items() if k not in {"abc_sampling", "semantic_sampling"}}
    song_request = pipe._request(**request_only)
    config = pipe.effective_config(song_request, kwargs.get("abc_sampling"), kwargs.get("semantic_sampling"))
    request_identity = identity({"request": song_request.to_dict(), "config": config, "weights": pipe.weights})
    started = time.perf_counter()
    ctx.check_cancelled()
    ctx.update("planning", seed=seed, tokens=0)
    plan = pipe.plan(request=song_request, abc_sampling=kwargs.get("abc_sampling"),
                     cancelled=ctx.cancelled, on_token=ctx.token)
    ctx.check_cancelled()
    ctx.update("semantic", seed=seed, tokens=0, abc_truncated=bool(plan.truncated))
    semantic = pipe.generate_semantic(plan, sampling=kwargs.get("semantic_sampling"),
                                      cancelled=ctx.cancelled, on_token=ctx.token)
    ctx.check_cancelled()
    nar_start = time.perf_counter()
    ctx.update("synthesis", seed=seed, semantic_truncated=bool(semantic.truncated))
    latents = pipe.synthesize(semantic, cancelled=ctx.cancelled)
    nar_seconds = time.perf_counter() - nar_start
    ctx.check_cancelled()
    vae_start = time.perf_counter()
    ctx.update("decoding", seed=seed)
    audio = pipe.decode(latents)
    ctx.check_cancelled()
    timing = {
        "abc": plan.timing,
        "semantic": semantic.timing,
        "nar_seconds": nar_seconds,
        "vae_seconds": time.perf_counter() - vae_start,
        "load": dict(pipe.load_timing),
        "e2e_seconds": time.perf_counter() - started,
    }
    result = SongResult(audio, 48000, semantic, latents, config, pipe.weights, timing, request_identity)
    receipt = result.save_artifacts(destination)
    ctx.check_cancelled()
    return receipt, result


def generation_result(completed: list[dict], requested: int, failures: list[dict] | None = None) -> dict:
    failures = failures or []
    first = completed[0]
    return {
        "candidates": completed,
        "audio": first["audio"],
        "artifact_dir": first["directory"],
        "truncated": first["truncated"],
        "requested_candidates": requested,
        "completed_candidates": len(completed),
        "failures": failures,
        "partial": bool(failures or len(completed) < requested),
    }


def run_generate(root: Path, ctx: JobContext, request: dict) -> dict:
    artifacts = ctx.job_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    count = max(1, min(8, int(request.get("candidates", 1))))
    base_seed = int(request.get("seed", 831001))
    seeds = request.get("seeds") or [base_seed + index for index in range(count)]
    if len(seeds) != count:
        raise ValueError("seeds 数量必须与 candidates 一致")
    pipe = create_pipe(root, request)
    completed = []
    failures = []
    try:
        for index, seed in enumerate(seeds):
            ctx.check_cancelled()
            destination = artifacts / ("song" if count == 1 else f"candidate-{index + 1:03d}")
            ctx.update("candidate", candidate=index + 1, candidates=count, seed=int(seed))
            try:
                receipt, result = generate_one(pipe, ctx, request, destination, int(seed))
                completed.append({
                    "index": index + 1,
                    "seed": int(seed),
                    "directory": str(destination),
                    "audio": str(destination / "audio.flac"),
                    "abc": str(destination / "score.abc") if result.abc is not None else None,
                    "result": str(destination / "result.json"),
                    "truncated": result.truncated,
                    "identity": receipt["identity"],
                    "audio_seconds": receipt["audio_seconds"],
                })
                ctx.update("candidate", candidate=index + 1, candidates=count, seed=int(seed),
                           result=generation_result(completed, count, failures))
            except BaseException as exc:
                failure = {"index": index + 1, "status": "failed", "type": type(exc).__name__,
                           "error": str(exc), "seed": int(seed)}
                atomic_json(destination / "failure.json", failure)
                if isinstance(exc, (InterruptedError, KeyboardInterrupt)) or not completed:
                    raise
                failures.append(failure)
                return generation_result(completed, count, failures)
    finally:
        pipe.close()
    return generation_result(completed, count, failures)


def run_plan(root: Path, ctx: JobContext, request: dict) -> dict:
    destination = ctx.job_dir / "artifacts" / "plan"
    pipe = create_pipe(root, request)
    try:
        kwargs = generation_kwargs(request)
        kwargs.pop("semantic_sampling", None)
        ctx.update("planning", tokens=0)
        plan = pipe.plan(**kwargs, cancelled=ctx.cancelled, on_token=ctx.token)
        ctx.check_cancelled()
        plan.save(destination)
        return {"plan_dir": str(destination), "abc": plan.abc, "truncated": bool(plan.truncated),
                "request": plan.request.to_dict()}
    finally:
        pipe.close()


def load_semantic(source: Path):
    from yue2 import SymbolicPlan
    from yue2.pipeline import SemanticResult

    manifest_path = source / "semantic_manifest.json"
    manifest = verify_artifact_manifest(
        source, manifest_path.name, "yue2-semantic-v1",
        {"semantic.npy", "semantic.json", "plan_manifest.json"},
    )
    verify_hash_manifest(
        source, "plan_manifest.json", {"plan.json", "abc_tokens.npy", "prefix.npy"},
    )
    plan = SymbolicPlan.load(source)
    info = json.loads((source / "semantic.json").read_text(encoding="utf-8"))
    array = np.load(source / "semantic.npy", allow_pickle=False)
    if array.ndim != 1 or array.dtype.kind not in "iu":
        raise ValueError("无效的 semantic.npy")
    semantic = SemanticResult(plan, [int(value) for value in array], info.get("timing", {}),
                              bool(info.get("truncated")))
    return semantic, manifest, manifest_path


def save_semantic(destination: Path, semantic, *, models: dict,
                  semantic_sampling: dict | None = None,
                  source: dict | None = None) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    semantic.plan.save(destination)
    np.save(destination / "semantic.npy", np.asarray(semantic.tokens, dtype=np.int32))
    atomic_json(destination / "semantic.json", {
        "timing": semantic.timing,
        "truncated": semantic.truncated,
        "semantic_sampling": semantic_sampling,
    })
    path, _ = write_artifact_manifest(
        destination, "semantic_manifest.json", "yue2-semantic-v1",
        ["semantic.npy", "semantic.json", "plan_manifest.json"],
        models=models, source=source,
        config={"semantic_sampling": semantic_sampling},
    )
    return path


def run_semantic(root: Path, ctx: JobContext, request: dict) -> dict:
    from yue2 import SymbolicPlan

    plan_path = within(root / "outputs" / "jobs", Path(request["plan_dir"]))
    verify_hash_manifest(
        plan_path, "plan_manifest.json", {"plan.json", "abc_tokens.npy", "prefix.npy"},
    )
    plan = SymbolicPlan.load(plan_path)
    destination = ctx.job_dir / "artifacts" / "semantic"
    pipe = create_pipe(root, request)
    try:
        ctx.update("semantic", tokens=0)
        semantic = pipe.generate_semantic(plan, sampling=request.get("semantic_sampling"),
                                          cancelled=ctx.cancelled, on_token=ctx.token)
        ctx.check_cancelled()
        manifest = save_semantic(
            destination, semantic,
            models=generation_provenance(root, pipe.weights),
            semantic_sampling=request.get("semantic_sampling"),
            source={"plan_manifest": manifest_reference(plan_path / "plan_manifest.json")},
        )
        return {"semantic_dir": str(destination), "truncated": bool(semantic.truncated),
                "tokens": len(semantic.tokens), "manifest": str(manifest)}
    finally:
        pipe.close()


def run_synthesize(root: Path, ctx: JobContext, request: dict) -> dict:
    source = within(root / "outputs" / "jobs", Path(request["semantic_dir"]))
    semantic, semantic_manifest, semantic_manifest_path = load_semantic(source)
    destination = ctx.job_dir / "artifacts" / "synthesis"
    destination.mkdir(parents=True, exist_ok=True)
    pipe = create_pipe(root, request)
    try:
        assert_provenance(semantic_manifest, root, pipe.weights)
        ctx.update("synthesis")
        latents = pipe.synthesize(semantic, cancelled=ctx.cancelled)
        ctx.check_cancelled()
        np.save(destination / "latent.npy", np.asarray(latents, dtype=np.float32))
        provenance = generation_provenance(root, pipe.weights)
        local_semantic_manifest = save_semantic(
            destination, semantic, models=provenance,
            semantic_sampling=semantic_manifest.get("config", {}).get("semantic_sampling"),
            source={"input_semantic_manifest": manifest_reference(semantic_manifest_path)},
        )
        latent_manifest, _ = write_artifact_manifest(
            destination, "latent_manifest.json", "yue2-latent-v1",
            ["latent.npy", "semantic_manifest.json"], models=provenance,
            source={"semantic_manifest": manifest_reference(local_semantic_manifest)},
            config={"effective_generation": pipe.effective_config(
                semantic.plan.request, None,
                semantic_manifest.get("config", {}).get("semantic_sampling"),
            )},
        )
        return {"latent_dir": str(destination), "latent": str(destination / "latent.npy"),
                "frames": int(latents.shape[0]), "manifest": str(latent_manifest)}
    finally:
        pipe.close()


def run_decode(root: Path, ctx: JobContext, request: dict) -> dict:
    import soundfile as sf

    source = within(root / "outputs" / "jobs",
                    Path(request.get("latent") or Path(request["latent_dir"]) / "latent.npy"))
    canonical_source = within(root / "outputs" / "jobs", source.parent / "latent.npy")
    if source != canonical_source:
        raise ValueError("解码只接受 latent_manifest.json 记录的 latent.npy")
    latent_manifest_path = source.parent / "latent_manifest.json"
    latent_manifest = verify_artifact_manifest(
        source.parent, latent_manifest_path.name, "yue2-latent-v1",
        {"latent.npy", "semantic_manifest.json"},
    )
    verify_artifact_manifest(
        source.parent, "semantic_manifest.json", "yue2-semantic-v1",
        {"semantic.npy", "semantic.json", "plan_manifest.json"},
    )
    verify_hash_manifest(
        source.parent, "plan_manifest.json", {"plan.json", "abc_tokens.npy", "prefix.npy"},
    )
    latents = np.load(canonical_source, allow_pickle=False)
    destination = ctx.job_dir / "artifacts" / "decode"
    destination.mkdir(parents=True, exist_ok=True)
    pipe = create_pipe(root, request)
    try:
        assert_provenance(latent_manifest, root, pipe.weights)
        ctx.update("decoding")
        audio = pipe.decode(latents)
        ctx.check_cancelled()
        path = destination / "audio.flac"
        sf.write(path, audio, 48000, subtype="PCM_24")
        ctx.check_cancelled()
        decode_manifest, _ = write_artifact_manifest(
            destination, "decode_manifest.json", "yue2-decode-v1",
            ["audio.flac"], models=generation_provenance(root, pipe.weights),
            source={"latent_manifest": manifest_reference(latent_manifest_path)},
            config={"sample_rate": 48000, "vae_decode": "halo_crop"},
        )
        return {"audio": str(path), "artifact_dir": str(destination),
                "sample_rate": 48000, "audio_seconds": len(audio) / 48000,
                "manifest": str(decode_manifest)}
    finally:
        pipe.close()


def run_render_plan(root: Path, ctx: JobContext, request: dict) -> dict:
    from yue2 import SymbolicPlan

    plan_dir = within(root / "outputs" / "jobs", Path(request["plan_dir"]))
    verify_hash_manifest(
        plan_dir, "plan_manifest.json", {"plan.json", "abc_tokens.npy", "prefix.npy"},
    )
    exact = bool(request.get("exact", True)) and not request.get("abc")
    if not exact:
        generated = dict(request)
        if not generated.get("abc"):
            generated["abc"] = (plan_dir / "score.abc").read_text(encoding="utf-8")
        generated["candidates"] = 1
        return run_generate(root, ctx, generated)

    plan = SymbolicPlan.load(plan_dir)
    destination = ctx.job_dir / "artifacts" / "song"
    pipe = create_pipe(root, request)
    try:
        from yue2.pipeline import SongResult
        from yue2.storage import identity
        ctx.update("semantic", tokens=0)
        semantic = pipe.generate_semantic(plan, sampling=request.get("semantic_sampling"),
                                          cancelled=ctx.cancelled, on_token=ctx.token)
        ctx.check_cancelled()
        ctx.update("synthesis")
        start = time.perf_counter()
        latents = pipe.synthesize(semantic, cancelled=ctx.cancelled)
        nar_seconds = time.perf_counter() - start
        ctx.check_cancelled()
        ctx.update("decoding")
        start = time.perf_counter()
        audio = pipe.decode(latents)
        ctx.check_cancelled()
        config = pipe.effective_config(plan.request, None, request.get("semantic_sampling"))
        request_identity = identity({"request": plan.request.to_dict(), "config": config, "weights": pipe.weights})
        result = SongResult(audio, 48000, semantic, latents, config, pipe.weights,
                            {"abc": plan.timing, "semantic": semantic.timing, "nar_seconds": nar_seconds,
                             "vae_seconds": time.perf_counter() - start, "load": dict(pipe.load_timing)},
                            request_identity)
        result.save_artifacts(destination)
        ctx.check_cancelled()
        return {"audio": str(destination / "audio.flac"), "artifact_dir": str(destination),
                "truncated": result.truncated, "abc": result.abc}
    finally:
        pipe.close()


def run_doctor(root: Path, ctx: JobContext, request: dict) -> dict:
    import importlib.metadata

    import torch

    from .model_verify import verify_bundle

    ctx.update("doctor")
    if not torch.cuda.is_available():
        raise RuntimeError("自检失败：未检测到 NVIDIA CUDA")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("自检失败：GPU 不支持 BF16")
    verified = verify_bundle(root, progress=False)
    packages = {name: importlib.metadata.version(name) for name in
                ("torch", "transformers", "huggingface-hub", "safetensors", "tiktoken", "soundfile")}
    result = {
        "versions": packages,
        "torch_cuda": torch.version.cuda,
        "cuda_available": True,
        "bf16_supported": True,
        "gpu": torch.cuda.get_device_name(0),
        "model": verified["YuE2-3B"],
        "vae": verified["YuE2-Vae"],
        "sheetsage": verified["SheetSage2"],
        "mert": verified["MERT-v2-FullSong"],
    }
    atomic_json(ctx.job_dir / "artifacts" / "doctor.json", result)
    return result


HANDLERS = {
    "generate": run_generate,
    "plan": run_plan,
    "render_plan": run_render_plan,
    "semantic": run_semantic,
    "synthesize": run_synthesize,
    "decode": run_decode,
    "doctor": run_doctor,
}


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
    try:
        ctx.update("starting", pid=os.getpid())
        add_upstream(root)
        result = HANDLERS[job["kind"]](root, ctx, job.get("request", {}))
        ctx.finish(result=result)
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 130 if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
