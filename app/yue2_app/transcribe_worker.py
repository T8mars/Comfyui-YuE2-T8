from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import model_paths
from .io import atomic_json, within
from .worker_common import JobContext, configure_environment


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
    request = job.get("request", {})
    try:
        ctx.update("starting", pid=os.getpid())
        paths = model_paths(root)
        sys.path.insert(0, str(paths["sheetsage"]))
        import torch
        from transformers import AutoModel

        if not torch.cuda.is_available():
            raise RuntimeError("转谱运行时未检测到 NVIDIA CUDA")
        source = within(root / "uploads", Path(request["source_path"]))
        if not source.is_file():
            raise FileNotFoundError(f"找不到音频：{source}")
        output = job_dir / "artifacts" / "transcription"
        output.mkdir(parents=True, exist_ok=True)
        ctx.update("loading_transcriber", gpu=torch.cuda.get_device_name(0))
        model = AutoModel.from_pretrained(
            str(paths["sheetsage"]), base_model_path=str(paths["mert"]),
            local_files_only=True, trust_remote_code=True,
            torch_dtype=torch.float32, attn_implementation="sdpa",
        ).eval().to("cuda")

        def progress(value):
            ctx.check_cancelled()
            stage = value.get("stage", "transcribing")
            ctx.update(stage, window=value.get("window"), windows=value.get("windows"))

        ctx.update("transcribing")
        result = model.transcribe(
            str(source), output_dir=output, melody_only=bool(request.get("melody_only", True)),
            dtype=request.get("dtype", "bf16"), preset=request.get("preset", "default"),
            max_seconds=request.get("max_seconds"),
            render_audio=bool(request.get("render_audio", False)),
            render_score=request.get("render_score", False),
            render_parts=tuple(request.get("render_parts", ["mix"])), progress=progress,
        )
        ctx.check_cancelled()
        abc_path = output / "score.abc"
        abc = abc_path.read_text(encoding="utf-8") if abc_path.is_file() else result.get("abc")
        if request.get("melody_only", True) and (not abc or result.get("abc_error")):
            raise RuntimeError(result.get("abc_error") or "转谱没有产生可用的旋律 ABC")
        public = {
            "transcription_dir": str(output),
            "abc": abc,
            "abc_path": str(abc_path) if abc_path.is_file() else None,
            "midi": str(output / "transcription.mid") if (output / "transcription.mid").is_file() else None,
            "duration_seconds": result.get("duration_seconds"),
            "warnings": result.get("warnings", []),
            "melody_only": bool(request.get("melody_only", True)),
            "rendered": result.get("rendered"),
            "render_error": result.get("render_error"),
        }
        atomic_json(output / "job_result.json", public)
        del model
        torch.cuda.empty_cache()
        ctx.finish(result=public)
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 130 if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
