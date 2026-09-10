from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from .config import model_paths
from .io import atomic_json, within
from .worker_common import JobContext, configure_environment


def add_upstream(root: Path) -> None:
    source = root / "vendor"
    if not (source / "yue2").is_dir():
        raise FileNotFoundError(f"找不到随节点发布的 YuE2 推理源码：{source}")
    sys.path.insert(0, str(source))


def create_pipe(root: Path, request: dict):
    from yue2 import YuE2Pipeline

    paths = model_paths()
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
    return receipt, result


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
            except BaseException as exc:
                atomic_json(destination / "failure.json", {"status": "failed", "type": type(exc).__name__,
                            "error": str(exc), "seed": int(seed)})
                raise
    finally:
        pipe.close()
    return {"candidates": completed, "audio": completed[0]["audio"],
            "artifact_dir": completed[0]["directory"], "truncated": completed[0]["truncated"]}


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

    plan = SymbolicPlan.load(source)
    info = json.loads((source / "semantic.json").read_text(encoding="utf-8"))
    array = np.load(source / "semantic.npy", allow_pickle=False)
    if array.ndim != 1 or array.dtype.kind not in "iu":
        raise ValueError("无效的 semantic.npy")
    return SemanticResult(plan, [int(value) for value in array], info.get("timing", {}), bool(info.get("truncated")))


def save_semantic(destination: Path, semantic) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    semantic.plan.save(destination)
    np.save(destination / "semantic.npy", np.asarray(semantic.tokens, dtype=np.int32))
    atomic_json(destination / "semantic.json", {"timing": semantic.timing, "truncated": semantic.truncated})


def run_semantic(root: Path, ctx: JobContext, request: dict) -> dict:
    from yue2 import SymbolicPlan

    plan_path = within(root / "outputs" / "jobs", Path(request["plan_dir"]))
    plan = SymbolicPlan.load(plan_path)
    destination = ctx.job_dir / "artifacts" / "semantic"
    pipe = create_pipe(root, request)
    try:
        ctx.update("semantic", tokens=0)
        semantic = pipe.generate_semantic(plan, sampling=request.get("semantic_sampling"),
                                          cancelled=ctx.cancelled, on_token=ctx.token)
        ctx.check_cancelled()
        save_semantic(destination, semantic)
        return {"semantic_dir": str(destination), "truncated": bool(semantic.truncated),
                "tokens": len(semantic.tokens)}
    finally:
        pipe.close()


def run_synthesize(root: Path, ctx: JobContext, request: dict) -> dict:
    source = within(root / "outputs" / "jobs", Path(request["semantic_dir"]))
    semantic = load_semantic(source)
    destination = ctx.job_dir / "artifacts" / "synthesis"
    destination.mkdir(parents=True, exist_ok=True)
    pipe = create_pipe(root, request)
    try:
        ctx.update("synthesis")
        latents = pipe.synthesize(semantic, cancelled=ctx.cancelled)
        ctx.check_cancelled()
        np.save(destination / "latent.npy", np.asarray(latents, dtype=np.float32))
        save_semantic(destination, semantic)
        return {"latent_dir": str(destination), "latent": str(destination / "latent.npy"),
                "frames": int(latents.shape[0])}
    finally:
        pipe.close()


def run_decode(root: Path, ctx: JobContext, request: dict) -> dict:
    import soundfile as sf

    source = within(root / "outputs" / "jobs",
                    Path(request.get("latent") or Path(request["latent_dir"]) / "latent.npy"))
    latents = np.load(source, allow_pickle=False)
    destination = ctx.job_dir / "artifacts" / "decode"
    destination.mkdir(parents=True, exist_ok=True)
    pipe = create_pipe(root, request)
    try:
        ctx.update("decoding")
        audio = pipe.decode(latents)
        path = destination / "audio.flac"
        sf.write(path, audio, 48000, subtype="PCM_24")
        return {"audio": str(path), "artifact_dir": str(destination),
                "sample_rate": 48000, "audio_seconds": len(audio) / 48000}
    finally:
        pipe.close()


def run_render_plan(root: Path, ctx: JobContext, request: dict) -> dict:
    from yue2 import SymbolicPlan

    plan_dir = within(root / "outputs" / "jobs", Path(request["plan_dir"]))
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
        config = pipe.effective_config(plan.request, None, request.get("semantic_sampling"))
        request_identity = identity({"request": plan.request.to_dict(), "config": config, "weights": pipe.weights})
        result = SongResult(audio, 48000, semantic, latents, config, pipe.weights,
                            {"abc": plan.timing, "semantic": semantic.timing, "nar_seconds": nar_seconds,
                             "vae_seconds": time.perf_counter() - start, "load": dict(pipe.load_timing)},
                            request_identity)
        result.save_artifacts(destination)
        return {"audio": str(destination / "audio.flac"), "artifact_dir": str(destination),
                "truncated": result.truncated, "abc": result.abc}
    finally:
        pipe.close()


def run_doctor(root: Path, ctx: JobContext, request: dict) -> dict:
    import importlib.metadata
    import torch
    from yue2.storage import model_identity

    ctx.update("doctor")
    paths = model_paths()
    packages = {name: importlib.metadata.version(name) for name in
                ("torch", "transformers", "huggingface-hub", "safetensors", "tiktoken", "soundfile")}
    result = {
        "versions": packages,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "bf16_supported": torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "model": model_identity(paths["model"], verify=bool(request.get("verify_hashes", True))),
        "vae": model_identity(paths["vae"], verify=bool(request.get("verify_hashes", True))),
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
    root, job_dir = args.root.resolve(), args.job_dir.resolve()
    configure_environment(root)
    add_upstream(root)
    ctx = JobContext(job_dir)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8-sig"))
    try:
        ctx.update("starting", pid=__import__("os").getpid())
        result = HANDLERS[job["kind"]](root, ctx, job.get("request", {}))
        ctx.finish(result=result)
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 130 if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
