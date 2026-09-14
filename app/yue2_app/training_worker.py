"""Serial worker entry points for optional YuE2 style training."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .asset_library import AssetLibrary
from .io import within
from .worker_common import JobContext, configure_environment


def install_assets(root: Path, ctx: JobContext, _request: dict) -> dict:
    from .training_resources import install
    return install(root, ctx)


def prepare(root: Path, ctx: JobContext, request: dict) -> dict:
    from .yue2_training_data import prepare_snapshot
    library = AssetLibrary(root)
    run = library.get_training_run(str(request.get("run_id", "")))
    if run["training_kind"] != "yue2_style":
        raise ValueError("当前训练记录不是 YuE2 歌曲风格训练")
    output = library.home / "training" / run["id"] / "prepared"
    library.update_training_run(run["id"], state="preparing", current_job_id=ctx.job_dir.name)
    try:
        dataset = prepare_snapshot(root, run["snapshot_id"], output, ctx)
        library.update_training_run(run["id"], state="draft",
                                    config={**run["config"], "prepared": str(output),
                                            "dataset_identity": dataset["identity"]})
        return {"run_id": run["id"], "prepared": str(output), "dataset": dataset}
    except BaseException:
        library.update_training_run(run["id"], state="failed")
        raise


def train_style(root: Path, ctx: JobContext, request: dict) -> dict:
    from .yue2_trainer import train
    library = AssetLibrary(root)
    run = library.get_training_run(str(request.get("run_id", "")))
    if run["training_kind"] != "yue2_style":
        raise ValueError("当前训练记录不是 YuE2 歌曲风格训练")
    prepared_value = request.get("prepared") or run["config"].get("prepared")
    if not prepared_value:
        raise ValueError("请先完成训练素材预处理")
    prepared = within(library.home / "training", Path(prepared_value))
    library.update_training_run(run["id"], state="running", current_job_id=ctx.job_dir.name)
    try:
        return train(root, run, prepared, ctx)
    except BaseException:
        library.update_training_run(run["id"], state="cancelled" if ctx.cancelled() else "failed")
        raise


def preview(root: Path, ctx: JobContext, request: dict) -> dict:
    """Preview is its own queue item, so a paused trainer never waits on itself."""
    from .core_worker import add_upstream, run_generate
    library = AssetLibrary(root)
    run = library.get_training_run(str(request.get("run_id", "")))
    selected_step = int(request.get("checkpoint_step") or 0)
    model_asset_id = "" if selected_step else str(request.get("model_asset_id") or run.get("model_asset_id") or "")
    if not model_asset_id:
        from .training_resources import manifest as resource_manifest
        from .yue2_trainer import inspect_training_checkpoint
        checkpoint_value = str((library.home / "training" / run["id"] / "checkpoints" /
                                f"step-{selected_step:08d}") if selected_step else
                               (run.get("config", {}).get("last_checkpoint") or ""))
        if not checkpoint_value:
            raise ValueError("训练尚未保存可试听的检查点")
        checkpoints = library.home / "training" / run["id"] / "checkpoints"
        checkpoint = within(checkpoints, Path(checkpoint_value))
        adapter = checkpoint / "adapter.safetensors"
        inspected_checkpoint = inspect_training_checkpoint(
            checkpoint, identity=str(run.get("config", {}).get("training_identity") or ""),
            step=int(checkpoint.name.removeprefix("step-") or 0))
        inspected = inspected_checkpoint["adapter"]
        resources = resource_manifest()
        step = int(checkpoint.name.removeprefix("step-") or 0)
        existing = next((item for item in library.list_assets(kind="model", limit=500)
                         if item.get("metadata", {}).get("model_type") == "yue2_ar_lora"
                         and (item.get("metadata", {}).get("training_run_id") == run["id"]
                              or item.get("provenance", {}).get("training_run_id") == run["id"])
                         and int(item.get("metadata", {}).get("checkpoint_step") or -1) == step), None)
        if existing:
            model_asset_id = existing["id"]
        else:
            model = library.import_file(
                adapter, kind="model", title=f"{run['title']} · 第 {step} 步检查点",
                tags=["YuE2", "AR LoRA", "训练检查点"],
                provenance={"training_run_id": run["id"], "snapshot_id": run["snapshot_id"],
                            "checkpoint": checkpoint.name},
                metadata={"model_type": "yue2_ar_lora", "rank": inspected["rank"],
                          "adapter_content_sha256": inspected["content_sha256"], "supported_cot": ["off"],
                          "nar_companion_sha256": resources["files"]["nar_lora_joint_v4.pt"]["sha256"],
                          "checkpoint_step": step, "training_run_id": run["id"],
                          "training_incomplete": True},
            )
            model_asset_id = model["id"]
        current_checkpoint = Path(str(run.get("config", {}).get("last_checkpoint") or "")).name
        if not run.get("model_asset_id") or checkpoint.name == current_checkpoint:
            run = library.update_training_run(run["id"], model_asset_id=model_asset_id)
    generated = dict(request.get("generate") or {})
    generated.update(style_model_asset_id=model_asset_id,
                     style_model_scale=float(request.get("style_model_scale", 1.0)), candidates=1)
    add_upstream(root)
    result = run_generate(root, ctx, generated)
    return {**result, "run_id": run["id"], "model_asset_id": model_asset_id}


def migrate_existing(root: Path, ctx: JobContext, _request: dict) -> dict:
    """Journaled copy of existing uploads and finished audio into durable assets."""
    from .io import atomic_json, sha256
    library = AssetLibrary(root)
    journal_path = library.home / "migration-v1.json"
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        journal = {"schema": 1, "files": {}}
    candidates = []
    for path in sorted((root / "uploads").glob("*")):
        if path.is_file() and path.suffix.lower() in {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac"}:
            candidates.append((path, "song", "历史上传"))
    for path in sorted((root / "outputs" / "jobs").rglob("*")):
        if path.is_file() and path.suffix.lower() in {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac"}:
            name = path.stem.lower()
            kind = "vocal" if "vocal" in name else "instrumental" if "accompaniment" in name or "backing" in name else "work"
            candidates.append((path, kind, "历史任务结果"))
    imported, reused = [], 0
    for index, (path, kind, label) in enumerate(candidates):
        ctx.progress("workbench_migrate", index, len(candidates))
        relative = path.relative_to(root).as_posix()
        digest = sha256(path)
        previous = journal["files"].get(relative)
        if previous and previous.get("sha256") == digest:
            try:
                library.get_asset(previous["asset_id"]); reused += 1; continue
            except (KeyError, ValueError):
                pass
        asset = library.import_file(path, kind=kind, title=f"{label} · {path.name}",
                                    provenance={"migration": 1, "source": relative},
                                    metadata={"legacy_source": relative})
        journal["files"][relative] = {"sha256": digest, "asset_id": asset["id"]}
        atomic_json(journal_path, journal)
        imported.append(asset["id"])
    ctx.progress("workbench_migrate", len(candidates), len(candidates))
    return {"imported": len(imported), "reused": reused, "total": len(candidates), "asset_ids": imported}


HANDLERS = {"yue2_training_assets": install_assets, "yue2_prepare": prepare,
            "yue2_train": train_style, "yue2_preview": preview,
            "workbench_migrate": migrate_existing}


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
        result = HANDLERS[job["kind"]](root, ctx, job.get("request", {}))
        if result.get("paused"):
            ctx.pause(result=result)
        else:
            ctx.finish(result=result)
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 130 if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
