"""Explicit asset cleanup with immutable-reference checks and retryable blob GC."""
from __future__ import annotations

import json
import time

from .asset_library import _blob_locked, _ident
from .io import within


def batch_ids(value) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 500:
        raise ValueError("请选择 1–500 项资产")
    return list(dict.fromkeys(_ident(item, "素材 ID") for item in value))


def strings(value):
    if isinstance(value, str):
        yield value
        if len(value) <= 65536 and value[:1] in {"{", "["}:
            try:
                nested = json.loads(value)
            except ValueError:
                return
            yield from strings(nested)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class LibraryCleanup:
    def __init__(self, library):
        self.library = library
        self.home = library.home

    def move(self, ids, status: str) -> dict:
        ids = batch_ids(ids)
        if status not in {"active", "trashed"}:
            raise ValueError("素材状态无效")
        changed, skipped = [], []
        with self.library.transaction() as db:
            for asset_id in ids:
                if db.execute("UPDATE assets SET status=?,updated_at=? WHERE id=?",
                              (status, time.time(), asset_id)).rowcount:
                    changed.append(asset_id)
                else:
                    skipped.append({"id": asset_id, "reason": "素材已不存在"})
        return {"changed": changed, "skipped": skipped}

    def _selection(self, data) -> list[str]:
        if data.get("mode") == "empty_trash":
            with self.library.reading() as db:
                return [row[0] for row in db.execute("SELECT id FROM assets WHERE status='trashed' ORDER BY updated_at DESC,id")]
        if data.get("ids") == []:
            return []  # Retry a previously committed deletion's occupied payload.
        return batch_ids(data.get("ids"))

    def _blob(self, digest, suffix):
        path = self.library._blob_path(digest, suffix)
        raw = self.library.blobs / digest[:2] / f"{digest}{suffix}"
        if raw.is_symlink() or raw.parent.is_symlink():
            raise ValueError("素材存储路径是链接，不能自动清理")
        return path

    def _preview(self, db, ids, protected, detach, limit=None) -> dict:
        pinned = set(protected)
        for row in db.execute("SELECT manifest_json FROM dataset_snapshots"):
            manifest = json.loads(row[0])  # Corrupt snapshots must block destructive cleanup.
            pinned.update(strings(manifest))
        # MIDI source revisions and generation recipes remain immutable references.
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('midi_documents', 'midi_versions', 'midi_snapshots'):
            if table in tables:
                for row in db.execute(f'SELECT data_json FROM {table}'):
                    pinned.update(strings(json.loads(row[0])))
        deletable, titles, skipped, projects = [], [], [], {}
        for asset_id in ids:
            if limit is not None and len(deletable) >= limit:
                break
            row = db.execute("SELECT title,status FROM assets WHERE id=?", (asset_id,)).fetchone()
            reason = ""
            revisions = db.execute("SELECT id,blob_sha256,blob_suffix FROM revisions WHERE asset_id=?", (asset_id,)).fetchall()
            links = db.execute("SELECT DISTINCT p.id,p.title FROM project_assets pa JOIN projects p ON p.id=pa.project_id WHERE pa.asset_id=?", (asset_id,)).fetchall()
            if row is None:
                reason = "素材已不存在"
            elif row["status"] != "trashed":
                reason = "请先移入回收站"
            elif "*" in protected or asset_id in pinned or any(item["id"] in pinned for item in revisions):
                reason = "训练快照、运行任务或草稿仍在使用"
            elif db.execute("SELECT 1 FROM training_runs WHERE model_asset_id=?", (asset_id,)).fetchone():
                reason = "训练记录仍关联此模型"
            elif db.execute("SELECT 1 FROM collection_assets WHERE asset_id=?", (asset_id,)).fetchone():
                reason = "分类收藏仍在使用"
            elif db.execute("SELECT 1 FROM revisions WHERE asset_id<>? AND parent_revision_id IN (SELECT id FROM revisions WHERE asset_id=?)", (asset_id, asset_id)).fetchone():
                reason = "其他素材版本仍在使用"
            elif links and not detach:
                reason = "项目仍在使用：" + "、".join(item["title"] for item in links)[:200]
            else:
                try:
                    for item in revisions:
                        self._blob(item["blob_sha256"], item["blob_suffix"])
                except ValueError as exc:
                    reason = str(exc)
            if reason:
                skipped.append({"id": asset_id, "title": row["title"] if row else asset_id, "reason": reason})
            else:
                deletable.append(asset_id)
                titles.append(row["title"])
                for link in links:
                    projects[link["id"]] = link["title"]
        blobs = set(tuple(row) for row in db.execute("SELECT blob_sha256,blob_suffix FROM blob_gc WHERE NOT EXISTS "
                        "(SELECT 1 FROM revisions r WHERE r.blob_sha256=blob_gc.blob_sha256 AND r.blob_suffix=blob_gc.blob_suffix)"))
        if deletable:
            placeholders = ",".join("?" for _ in deletable)
            blobs.update(tuple(row) for row in db.execute(
                f"SELECT DISTINCT blob_sha256,blob_suffix FROM revisions WHERE asset_id IN ({placeholders}) "
                f"AND NOT EXISTS (SELECT 1 FROM revisions other WHERE other.blob_sha256=revisions.blob_sha256 "
                f"AND other.blob_suffix=revisions.blob_suffix AND other.asset_id NOT IN ({placeholders}))",
                (*deletable, *deletable)))
        size = 0
        cached_digests = set()
        for digest, suffix in blobs:
            path = self._blob(digest, suffix)
            if path.is_file():
                size += path.stat().st_size
            if digest not in cached_digests:
                cached_digests.add(digest)
                remaining = db.execute("SELECT asset_id FROM revisions WHERE blob_sha256=?", (digest,)).fetchall()
                if all(row[0] in deletable for row in remaining):
                    for cache in self.library.waveforms.glob(f"{digest}-*.json"):
                        if not cache.is_symlink() and cache.is_file():
                            size += within(self.library.waveforms, cache).stat().st_size
        pending_gc = db.execute("SELECT COUNT(*) FROM blob_gc").fetchone()[0]
        return {"ids": [*deletable, *(item['id'] for item in skipped)], "deletable": deletable, "skipped": skipped,
                "bytes": size, "pending_gc": pending_gc, "projects": list(projects.values()), "titles": titles}

    def preview(self, data, protected=()) -> dict:
        if not isinstance(data.get("detach_projects", False), bool):
            raise ValueError("移出项目选项必须是布尔值")
        ids = self._selection(data)
        with self.library.reading() as db:
            return self._preview(db, ids, protected, data.get("detach_projects", False),
                                 500 if data.get("mode") == "empty_trash" else None)

    @_blob_locked
    def purge(self, data, protected=()) -> dict:
        if data.get("confirmed") is not True:
            raise ValueError("彻底删除前必须确认清理预览")
        if not isinstance(data.get("detach_projects", False), bool):
            raise ValueError("移出项目选项必须是布尔值")
        ids = self._selection(data)
        with self.library.transaction() as db:
            preview = self._preview(db, ids, protected, data.get("detach_projects", False),
                                    500 if data.get("mode") == "empty_trash" else None)
            for asset_id in preview["deletable"]:
                db.execute("INSERT OR IGNORE INTO blob_gc SELECT blob_sha256,blob_suffix FROM revisions WHERE asset_id=?", (asset_id,))
                if data.get("detach_projects"):
                    for row in db.execute("SELECT p.id,p.metadata_json FROM projects p WHERE p.id IN (SELECT project_id FROM project_assets WHERE asset_id=?)", (asset_id,)).fetchall():
                        metadata = json.loads(row["metadata_json"])
                        if metadata.get("master_asset_id") == asset_id:
                            metadata.pop("master_asset_id", None)
                            metadata.pop("master_revision_id", None)
                        db.execute("UPDATE projects SET metadata_json=?,updated_at=? WHERE id=?",
                                   (json.dumps(metadata, ensure_ascii=False), time.time(), row["id"]))
                    db.execute("DELETE FROM project_assets WHERE asset_id=?", (asset_id,))
                db.execute("UPDATE revisions SET parent_revision_id=NULL WHERE asset_id=?", (asset_id,))
                db.execute("DELETE FROM revisions WHERE asset_id=?", (asset_id,))
                db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        released, errors = 0, []
        # Journal first, unlink after commit. A crash leaves only recoverable garbage,
        # never a missing payload still referenced by a live database revision.
        with self.library.transaction() as db:
            for row in db.execute("SELECT blob_sha256,blob_suffix FROM blob_gc").fetchall():
                digest, suffix = tuple(row)
                try:
                    if not db.execute("SELECT 1 FROM revisions WHERE blob_sha256=? AND blob_suffix=?", (digest, suffix)).fetchone():
                        path = self._blob(digest, suffix)
                        size = path.stat().st_size if path.is_file() else 0
                        path.unlink(missing_ok=True)
                        released += size
                    if not db.execute("SELECT 1 FROM revisions WHERE blob_sha256=?", (digest,)).fetchone():
                        for cache in self.library.waveforms.glob(f"{digest}-*.json"):
                            if cache.is_symlink():
                                continue
                            cache = within(self.library.waveforms, cache)
                            size = cache.stat().st_size if cache.is_file() else 0
                            cache.unlink(missing_ok=True)
                            released += size
                    db.execute("DELETE FROM blob_gc WHERE blob_sha256=? AND blob_suffix=?", (digest, suffix))
                except (OSError, ValueError):
                    errors.append("部分文件被占用或路径异常，请再次清空回收站重试")
        return {"deleted": preview["deletable"], "skipped": preview["skipped"],
                "released_bytes": released, "errors": list(dict.fromkeys(errors))}
