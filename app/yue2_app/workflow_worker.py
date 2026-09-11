"""Durable reference-cover orchestration. Only one GPU child runs at a time."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from .io import atomic_json, within
from .worker_common import JobContext, configure_environment


def run_stage(root: Path, ctx: JobContext, name: str, kind: str, request: dict) -> dict:
    directory = ctx.job_dir / "artifacts" / "stages" / name
    directory.mkdir(parents=True, exist_ok=True)
    job_id = ctx.job_dir.name
    atomic_json(directory / "job.json", {"id": job_id, "kind": kind, "request": request})
    atomic_json(directory / "status.json", {"id": job_id, "kind": kind, "status": "queued",
                                           "stage": "starting", "created_at": time.time()})
    runtime, module = ("voice", "voice_worker") if kind == "voice_convert" else ("core", "core_worker")
    command = [str(root / "runtime" / runtime / "python.exe"), "-X", "utf8", "-m",
               "app.yue2_app." + module, "--root", str(root), "--job-dir", str(directory)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, cwd=root, creationflags=flags)
    last_update = None
    try:
        while True:
            if ctx.cancelled():
                (directory / "cancel.requested").touch()
            try:
                state = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            if state.get("status") != "complete" and state.get("updated_at") != last_update:
                last_update = state.get("updated_at")
                fields = {key: state[key] for key in (
                    "tokens", "progress", "completed", "total", "checkpoint", "resumed_stage",
                    "resumable", "candidate", "candidates") if key in state}
                ctx.update(state.get("failed_stage") or state.get("stage", "starting"),
                           workflow_stage=name, child_pid=process.pid, **fields)
            if process.poll() is not None:
                break
            time.sleep(0.5)
        ctx.check_cancelled()
        if process.returncode != 0 or state.get("status") != "complete":
            # Re-read after exit: the final atomic status can race the last poll.
            state = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if process.returncode != 0 or state.get("status") != "complete":
                raise RuntimeError(state.get("error") or f"{name} worker exited: {process.returncode}")
        return state["result"]
    finally:
        if process.poll() is None:
            from .service import terminate_process_tree
            terminate_process_tree(process.pid)
            process.wait(timeout=30)


def run_workflow(root: Path, ctx: JobContext, request: dict) -> dict:
    generate = dict(request["generate"])
    previous = request.get("resume_from")
    if previous:
        previous = within(root / "outputs" / "jobs", Path(previous))
        generate["resume_from"] = str(previous / "artifacts" / "stages" / "generate")
    ctx.update("starting", resumable=False, workflow_stage="generate")
    generated = run_stage(root, ctx, "generate", "generate", generate)
    ctx.check_cancelled()
    voice = dict(request["voice"])
    if previous:
        voice["resume_from"] = str(previous / "artifacts" / "stages" / "voice")
    voice["source_path"] = generated["audio"]
    ctx.update("separating_vocals", generated_result=generated, workflow_stage="voice")
    converted = run_stage(root, ctx, "voice", "voice_convert", voice)
    result = {**converted, "generated_result": generated,
              "artifact_dir": str(ctx.job_dir / "artifacts"),
              "workflow": {"generate": "complete", "voice": "complete"}}
    atomic_json(ctx.job_dir / "artifacts" / "workflow_result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    directory = within(root / "outputs" / "jobs", args.job_dir)
    configure_environment(root)
    ctx = JobContext(directory)
    try:
        job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
        ctx.finish(result=run_workflow(root, ctx, job["request"]))
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
