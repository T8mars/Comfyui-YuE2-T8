"""Explicit, reference-aware disposal of training records and private caches."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from .asset_library import _blob_locked, _ident
from .library_cleanup import strings
from .io import within


def selected_ids(value) -> list[str]:
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("每次最多选择 500 项训练记录或快照")
    return list(dict.fromkeys(_ident(item, "训练记录或快照 ID") for item in value))


def cache_size(home: Path, run_id: str) -> tuple[Path, int, int]:
    """Reject links anywhere in the deletion tree, including Windows junctions."""
    directory = home / "training" / _ident(run_id, "训练 ID")
    for parent in (home.parent, home, directory.parent, directory):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("训练目录是链接，不能自动清理")
    within(home, directory)
    size = checkpoints = 0
    if directory.exists() and not directory.is_dir():
        raise ValueError("训练目录异常，不能自动清理")
    def fail(error):
        raise error
    for current, dirs, files in os.walk(directory, followlinks=False, onerror=fail):
        for name in [*dirs, *files]:
            path = Path(current) / name
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("训练缓存包含链接，不能自动清理")
            within(directory, path)
            if name in files:
                size += path.stat().st_size
            elif Path(current).name == "checkpoints" and name.startswith("step-"):
                checkpoints += 1
    return directory, size, checkpoints


class TrainingCleanup:
    def __init__(self, library, finished_jobs=None):
        self.library, self.home = library, library.home
        self.finished_jobs = dict(finished_jobs or {})

    def inventory(self, *, kind="runs", offset=0, limit=10) -> dict:
        if kind not in {"runs", "snapshots"}:
            raise ValueError("清理类型无效")
        offset, limit = max(0, int(offset)), min(100, max(1, int(limit)))
        with self.library.reading() as db:
            if kind == "runs":
                rows = db.execute("SELECT * FROM training_runs ORDER BY updated_at DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
                total = db.execute("SELECT COUNT(*) FROM training_runs").fetchone()[0]
            else:
                rows = db.execute("SELECT ds.*, (SELECT COUNT(*) FROM training_runs tr WHERE tr.snapshot_id=ds.id) AS run_count "
                                  "FROM dataset_snapshots ds ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
                total = db.execute("SELECT COUNT(*) FROM dataset_snapshots").fetchone()[0]
        items = []
        for row in rows:
            item = dict(row)
            if kind == "runs":
                config = json.loads(item.pop("config_json"))
                item.update(steps=config.get("completed_training_steps", config.get("steps", 0)),
                            cleanup_pending=bool(config.get("cleanup_pending")))
                try:
                    path, size, checkpoints = cache_size(self.home, item["id"])
                    item.update(path=str(path), bytes=size, checkpoints=checkpoints)
                except (OSError, ValueError) as exc:
                    item.update(bytes=0, checkpoints=0, path_error=str(exc))
            else:
                item.pop("manifest_json")
            items.append(item)
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def _preview(self, db, data, protected) -> dict:
        runs = selected_ids(data.get("run_ids", []))
        snapshots = selected_ids(data.get("snapshot_ids", []))
        if not runs and not snapshots:
            raise ValueError("请选择要清理的训练记录或快照")
        for name in ("discard_paused", "include_unused_snapshots"):
            if not isinstance(data.get(name, False), bool):
                raise ValueError("清理选项必须是布尔值")
        pinned = set(protected)
        records = db.execute("SELECT * FROM training_runs").fetchall()
        configs = {row["id"]: set(strings(json.loads(row["config_json"]))) for row in records}
        eligible, skipped, paths, titles, candidates, size = [], [], [], [], set(snapshots), 0
        for run_id in runs:
            row = next((row for row in records if row["id"] == run_id), None)
            reason = ""
            try:
                path, amount, checkpoints = cache_size(self.home, run_id)
            except (OSError, ValueError) as exc:
                reason, path, amount, checkpoints = str(exc), self.home / "training" / run_id, 0, 0
            # References are strings, so also cover a descendant cache/checkpoint path.
            references = pinned | set().union(*(refs for ident, refs in configs.items() if ident != run_id))
            path_text = str(path.resolve()).replace("\\", "/").casefold().rstrip("/")
            referenced = any(value == run_id or value.replace("\\", "/").casefold().rstrip("/") == path_text
                             or value.replace("\\", "/").casefold().startswith(path_text + "/") for value in references)
            if row is None:
                reason = "训练记录已不存在"
            elif "*" in references or referenced:
                reason = "运行任务、草稿或其他训练仍在使用"
            elif row["state"] in {"queued", "preparing", "running", "pausing"} and (not row["current_job_id"] or self.finished_jobs.get(run_id, "") != row["current_job_id"]):
                reason = "训练仍在运行，请先暂停或取消并等待任务结束"
            elif row["state"] == "paused" and not data.get("discard_paused"):
                reason = "训练已暂停；勾选放弃继续训练后才能清理"
            if reason:
                skipped.append({"id": run_id, "kind": "runs", "title": row["title"] if row else run_id, "reason": reason})
            else:
                eligible.append(run_id)
                titles.append(row["title"])
                paths.append({"id": run_id, "path": str(path), "bytes": amount, "checkpoints": checkpoints})
                size += amount
                if data.get("include_unused_snapshots") and row["snapshot_id"]:
                    candidates.add(row["snapshot_id"])
        # Only snapshots that will have no remaining runs may be removed.
        remaining_configs = pinned | set().union(*(refs for ident, refs in configs.items() if ident not in eligible))
        deletable_snapshots, snapshot_titles, kept_snapshots = [], [], []
        for snapshot_id in sorted(candidates):
            row = db.execute("SELECT title FROM dataset_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            linked = [item for item in records if item["snapshot_id"] == snapshot_id and item["id"] not in eligible]
            reason = "快照仍关联其他训练记录" if linked else ""
            if "*" in remaining_configs or snapshot_id in remaining_configs:
                reason = "任务、草稿或其他训练仍在使用快照"
            if not row:
                reason = "快照已不存在"
            if reason:
                entry = {"id": snapshot_id, "kind": "snapshots", "title": row["title"] if row else snapshot_id, "reason": reason}
                (skipped if snapshot_id in snapshots else kept_snapshots).append(entry)
            else:
                deletable_snapshots.append(snapshot_id)
                snapshot_titles.append(row["title"])
        return {"run_ids": eligible, "snapshot_ids": deletable_snapshots, "titles": titles,
                "snapshot_titles": snapshot_titles, "skipped": skipped, "kept_snapshots": kept_snapshots, "paths": paths, "bytes": size}

    def preview(self, data, protected=()) -> dict:
        with self.library.reading() as db:
            return self._preview(db, data, protected)

    @_blob_locked
    def purge(self, data, protected=()) -> dict:
        if data.get("confirmed") is not True:
            raise ValueError("请先确认训练清理预览")
        # Execution uses precisely the approved IDs; it cannot add snapshots later.
        data = {**data, "include_unused_snapshots": False}
        with self.library.transaction() as db:
            preview = self._preview(db, data, protected)
            for run_id in preview["run_ids"]:
                row = db.execute("SELECT config_json FROM training_runs WHERE id=?", (run_id,)).fetchone()
                config = json.loads(row[0])
                config["cleanup_pending"] = True
                db.execute("UPDATE training_runs SET state='failed',config_json=?,updated_at=? WHERE id=?",
                           (json.dumps(config, ensure_ascii=False), time.time(), run_id))
        deleted, pending, errors, released = [], [], [], 0
        for run_id in preview["run_ids"]:
            before = 0
            try:
                directory, before, _ = cache_size(self.home, run_id)
                if directory.exists():
                    shutil.rmtree(directory)
                with self.library.transaction() as db:
                    db.execute("DELETE FROM training_runs WHERE id=?", (run_id,))
                deleted.append(run_id)
                released += before
            except (OSError, ValueError) as exc:
                pending.append(run_id)
                try:
                    released += max(0, before - cache_size(self.home, run_id)[1])
                except (OSError, ValueError):
                    pass
                errors.append({"id": run_id, "reason": "部分缓存未能删除，记录已禁止继续训练；解除文件占用后再次清理。" + str(exc)})
        removed_snapshots = []
        with self.library.transaction() as db:
            for snapshot_id in preview["snapshot_ids"]:
                if not db.execute("SELECT 1 FROM training_runs WHERE snapshot_id=?", (snapshot_id,)).fetchone():
                    db.execute("DELETE FROM dataset_snapshots WHERE id=?", (snapshot_id,))
                    removed_snapshots.append(snapshot_id)
        return {"deleted_runs": deleted, "deleted_snapshots": removed_snapshots, "pending_runs": pending,
                "released_bytes": released, "skipped": preview["skipped"], "errors": errors}
