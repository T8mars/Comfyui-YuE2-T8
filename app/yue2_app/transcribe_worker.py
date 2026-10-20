from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import model_paths
from .artifacts import declared_model_provenance, write_artifact_manifest
from .io import atomic_json, sha256, within
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
            raise RuntimeError("转谱运行时未检测到 CUDA/HIP 兼容 GPU")
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
        audio_input, input_options = str(source), {}
        if request.get("preset", "default") == "paper":
            from .audio_decode import load_paper_audio
            audio_input = load_paper_audio(source, max_seconds=request.get("max_seconds"))
            input_options["sampling_rate"] = 24000
        result = model.transcribe(
            audio_input, output_dir=output, melody_only=bool(request.get("melody_only", True)),
            dtype=request.get("dtype", "bf16"), preset=request.get("preset", "default"),
            max_seconds=request.get("max_seconds"),
            render_audio=False, render_score=False,
            render_parts=tuple(request.get("render_parts", ["mix"])), progress=progress, **input_options,
        )
        ctx.check_cancelled()
        # Native notation inserts pickup padding and averages tempo; it is a
        # musical score, not an audio-clock-aligned conditioning sequence.
        # Rebuild from the saved raw seconds, retaining the native score too.
        from importlib import import_module
        from .transcription_timing import align_abc_to_audio
        try:
            notation = import_module(model.__class__.__module__.rsplit('.', 1)[0] + '.notation_sheetsage2')
            result['melody_only'] = bool(request.get('melody_only', True))
            align_abc_to_audio(output, result, notation.generate_abc_from_data)
        except (ValueError, OSError, TypeError) as exc:
            # A paid/expensive successful recognition is still usable. Never
            # silently present fallback notation as audio-aligned.
            result.setdefault('warnings', []).append(f'音频时间对齐未完成，已保留原转谱：{exc}')
        if request.get('render_audio') or request.get('render_score'):
            try:
                rendering = import_module(model.__class__.__module__.rsplit('.', 1)[0] + '.rendering_sheetsage2')
                result['rendered'] = rendering.render_outputs(
                    input_dir=output, output_dir=output,
                    audio=bool(request.get('render_audio', False)),
                    score=request.get('render_score') or (),
                    parts=tuple(request.get('render_parts', ['mix'])),
                    assets_dir=paths['sheetsage']/'render_assets')
            except Exception as exc:
                result['render_error'] = str(exc)
                result.setdefault('warnings', []).append(f'转谱已保留，预览渲染未完成：{exc}')
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
            "abc_error": result.get("abc_error"),
            "timing": result.get("timing"),
            "notation_abc": str(output / "score.notation.abc") if (output / "score.notation.abc").is_file() else None,
            "source_audio": str(source),
        }
        atomic_json(output / "job_result.json", public)
        artifact_files = [
            path.relative_to(output).as_posix() for path in output.rglob("*")
            if path.is_file() and not path.is_symlink()
            and path.name not in {"job_result.json", "transcription_manifest.json"}
        ]
        manifest_path, _ = write_artifact_manifest(
            output, "transcription_manifest.json", "yue2-transcription-v1", artifact_files,
            models=declared_model_provenance(root, ("SheetSage2", "MERT-v2-FullSong")),
            source={"audio": {"file": source.name, "sha256": sha256(source),
                              "bytes": source.stat().st_size}},
            config={"melody_only": bool(request.get("melody_only", True)),
                    "dtype": request.get("dtype", "bf16"),
                    "preset": request.get("preset", "default"),
                    "max_seconds": request.get("max_seconds")},
        )
        public["manifest"] = str(manifest_path)
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
