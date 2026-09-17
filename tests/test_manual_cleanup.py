from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.yue2_app.asset_library import AssetLibrary
from app.yue2_app.library_cleanup import LibraryCleanup
from app.yue2_app import service
from app.yue2_app.io import atomic_json


class AssetCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.library = AssetLibrary(self.root)
        self.cleanup = LibraryCleanup(self.library)

    def tearDown(self):
        self.temp.cleanup()

    def asset(self, text="original", title="test"):
        return self.library.create_text(kind="lyrics", title=title, text=text)

    def trash(self, *assets):
        self.cleanup.move([item["id"] for item in assets], "trashed")

    def purge(self, *assets, **options):
        return self.cleanup.purge({"ids": [item["id"] for item in assets], "confirmed": True, **options})

    def test_trash_restore_preserves_versions_and_project_links(self):
        asset = self.asset()
        project = self.library.create_project("project")
        self.library.add_to_project(project["id"], asset["id"])
        new = self.library.create_text(kind="lyrics", title="test", text="edited", asset_id=asset["id"])
        self.trash(asset)
        self.assertEqual(self.library.count_assets(), 0)
        self.assertEqual(self.library.count_assets(status="trashed"), 1)
        self.assertEqual(self.library.get_project(project["id"])["assets"][0]["revision_id"], asset["current_revision_id"])
        self.assertEqual(self.library.revision_file(asset["id"], asset["current_revision_id"])[0].read_text(), "original")
        self.cleanup.move([asset["id"]], "active")
        self.assertEqual(self.library.get_asset(asset["id"])["current_revision_id"], new["current_revision_id"])
        with self.assertRaises(ValueError):
            self.library.list_assets(status="invalid")

    def test_project_references_need_explicit_detach_and_clear_master(self):
        asset = self.asset()
        project = self.library.create_project("archived", metadata={"master_asset_id": asset["id"], "master_revision_id": asset["current_revision_id"], "keep": 1})
        self.library.add_to_project(project["id"], asset["id"])
        self.library.update_project(project["id"], status="archived")
        self.trash(asset)
        preview = self.cleanup.preview({"ids": [asset["id"]]})
        self.assertFalse(preview["deletable"])
        self.assertIn("archived", preview["skipped"][0]["reason"])
        preview = self.cleanup.preview({"ids": [asset["id"]], "detach_projects": True})
        self.assertEqual(preview["projects"], ["archived"])
        self.assertEqual(self.purge(asset, detach_projects=True)["deleted"], [asset["id"]])
        kept = self.library.get_project(project["id"])
        self.assertEqual(kept["metadata"], {"keep": 1})
        self.assertEqual(kept["assets"], [])

    def test_shared_payload_is_released_only_after_last_asset(self):
        first, second = self.asset("same"), self.asset("same")
        blob = self.library.revision_file(second["id"])[0]
        self.trash(first)
        self.assertEqual(self.cleanup.preview({"ids": [first["id"]]})["bytes"], 0)
        self.assertEqual(self.purge(first)["released_bytes"], 0)
        self.assertEqual(blob.read_text(), "same")
        self.trash(second)
        self.assertEqual(self.purge(second)["released_bytes"], 4)
        self.assertFalse(blob.exists())

    def test_batch_estimate_counts_shared_blob_once_and_preserves_sources(self):
        first, second = self.asset("same"), self.asset("same")
        source = self.root / "source.txt"
        exported = self.root / "exports" / "keep.txt"
        exported.parent.mkdir()
        source.write_text("source")
        exported.write_text("export")
        self.trash(first, second)
        preview = self.cleanup.preview({"mode": "empty_trash"})
        self.assertEqual(preview["bytes"], 4)
        self.assertEqual(len(self.purge(first, second)["deleted"]), 2)
        self.assertEqual(source.read_text(), "source")
        self.assertEqual(exported.read_text(), "export")

    def test_snapshot_and_draft_references_are_protected_even_when_detaching(self):
        asset = self.asset()
        with self.library.transaction() as db:
            db.execute("INSERT INTO dataset_snapshots VALUES(?,?,?,?,?,?)", ("a" * 32, "fixed", "yue2_style", json.dumps({"items": [{"revision_id": asset["current_revision_id"]}]}), "b" * 64, 1))
        self.trash(asset)
        self.assertFalse(self.purge(asset, detach_projects=True)["deleted"])
        other = self.asset("draft")
        self.trash(other)
        for protected in ({other["id"]}, {other["current_revision_id"]}, {"*"}):
            self.assertFalse(self.cleanup.purge({"ids": [other["id"]], "confirmed": True}, protected)["deleted"])

    def test_new_reference_after_preview_is_checked_again(self):
        asset = self.asset()
        self.trash(asset)
        self.assertEqual(self.cleanup.preview({"ids": [asset["id"]]})["deletable"], [asset["id"]])
        project = self.library.create_project("new reference")
        # A fixed reference may already be in another process's transaction.
        with self.library.transaction() as db:
            db.execute("INSERT INTO project_assets VALUES(?,?,?,?,?)", (project["id"], asset["id"], asset["current_revision_id"], "lyrics", 1))
        self.assertFalse(self.purge(asset)["deleted"])

    def test_failed_payload_unlink_is_retryable_and_reimport_stays_intact(self):
        asset = self.asset()
        blob = self.library.revision_file(asset["id"])[0]
        self.trash(asset)
        original_unlink = Path.unlink

        def busy(path, *args, **kwargs):
            if path == blob:
                raise PermissionError("in use")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", busy):
            result = self.purge(asset)
        self.assertEqual(result["deleted"], [asset["id"]])
        self.assertTrue(result["errors"])
        self.assertTrue(blob.exists())
        reimport = self.asset()
        retried = self.cleanup.purge({"ids": [], "confirmed": True})
        self.assertEqual(retried["released_bytes"], 0)
        self.assertEqual(self.library.revision_file(reimport["id"])[0].read_text(), "original")
        self.trash(reimport)
        self.assertTrue(self.purge(reimport)["released_bytes"])

    def test_occupied_payload_can_be_retried_with_an_empty_recycle_bin(self):
        asset = self.asset()
        blob = self.library.revision_file(asset["id"])[0]
        cache = self.library.waveforms / f'{asset["blob_sha256"]}-64.json'
        cache.write_bytes(b"waveform")
        self.trash(asset)
        original_unlink = Path.unlink

        def busy(path, *args, **kwargs):
            if path == blob:
                raise PermissionError("in use")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", busy):
            self.assertTrue(self.purge(asset)["errors"])
        self.assertEqual(self.library.count_assets(status="trashed"), 0)
        self.assertEqual(self.cleanup.preview({"mode": "empty_trash"})["bytes"], 16)
        retried = self.cleanup.purge({"ids": [], "confirmed": True})
        self.assertFalse(retried["errors"])
        self.assertEqual(retried["released_bytes"], 16)
        self.assertFalse(blob.exists())
        self.assertFalse(cache.exists())

    def test_training_model_link_is_protected_while_unrelated_asset_is_deleted(self):
        model = self.asset("trained model placeholder")
        unused = self.asset("unused")
        with self.library.transaction() as db:
            db.execute("INSERT INTO training_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("c" * 32, "trained", "yue2_style", None, "complete", "{}", None, model["id"], 1, 1))
        self.trash(model, unused)
        report = self.purge(model, unused, detach_projects=True)
        self.assertEqual(report["deleted"], [unused["id"]])
        self.assertIn("训练记录", report["skipped"][0]["reason"])

    def test_validation_and_corrupt_snapshot_do_not_delete_files(self):
        asset = self.asset()
        self.trash(asset)
        for data in ({"ids": [asset["id"]]}, {"ids": ["../escape"], "confirmed": True}, {"ids": [asset["id"]], "confirmed": True, "detach_projects": "false"}):
            with self.assertRaises(ValueError):
                self.cleanup.purge(data)
        with self.library.transaction() as db:
            db.execute("INSERT INTO dataset_snapshots VALUES(?,?,?,?,?,?)", ("a" * 32, "broken", "yue2_style", "{broken", "b" * 64, 1))
        with self.assertRaises(ValueError):
            self.purge(asset)
        self.assertTrue(self.library.revision_file(asset["id"])[0].is_file())


class JobCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.outputs, self.logs, self.uploads = (self.root / name for name in ("outputs/jobs", "logs", "uploads"))
        for directory in (self.outputs, self.logs, self.uploads):
            directory.mkdir(parents=True)
        self.patches = [patch.object(service, name, value) for name, value in (("ROOT", self.root), ("OUTPUTS", self.outputs), ("LOGS", self.logs), ("UPLOADS", self.uploads))]
        for patched in self.patches:
            patched.start()
        self.store = service.JobStore.__new__(service.JobStore)
        self.store.lock = threading.RLock()
        self.store.storage_lock = threading.RLock()
        self.store.updating = False
        self.store.current_id = None
        self.store.jobs = {}

    def tearDown(self):
        for patched in reversed(self.patches):
            patched.stop()
        self.temp.cleanup()

    def job(self, index, status="complete", request=None):
        job_id = f"20260918-120000-{index:08x}"
        directory = self.outputs / job_id
        directory.mkdir()
        atomic_json(directory / "job.json", {"request": request or {}})
        (directory / "artifact.txt").write_text("keep")
        (self.logs / f"{job_id}.log").write_text("log")
        self.store.jobs[job_id] = {"id": job_id, "kind": "generate", "status": status, "created_at": index, "summary": "fixture", "project_id": ""}
        atomic_json(directory / "status.json", self.store.jobs[job_id])
        return job_id

    def test_preview_then_selected_delete_leaves_other_jobs_and_assets(self):
        first, second = self.job(1), self.job(2, "failed")
        library = AssetLibrary(self.root)
        asset = library.create_text(kind="lyrics", title="keep", text="keep")
        data = {"ids": [first]}
        preview = self.store.job_cleanup(data)
        self.assertEqual(preview["deletable"], [first])
        self.assertTrue((self.outputs / first).is_dir())
        result = self.store.job_cleanup({**data, "confirmed": True}, execute=True)
        self.assertEqual(result["released_bytes"], preview["bytes"])
        self.assertFalse((self.outputs / first).exists())
        self.assertFalse((self.logs / f"{first}.log").exists())
        self.assertIn(second, self.store.jobs)
        self.assertTrue(library.revision_file(asset["id"])[0].is_file())

    def test_paused_queued_running_current_and_draft_jobs_are_protected(self):
        first = self.job(1)
        statuses = [self.job(index, state) for index, state in enumerate(("paused", "queued", "running"), 2)]
        self.store.current_id = first
        ids = [first, *statuses]
        result = self.store.job_cleanup({"ids": ids, "confirmed": True}, execute=True)
        self.assertEqual(len(result["skipped"]), 4)
        self.assertFalse(result["deleted"])
        self.store.current_id = None
        draft = {"local": {"values": {"source": json.dumps({"$job_file": {"job_id": first, "relative": "artifact.txt"}})}}}
        with patch.object(service.assistant_data, "all_drafts", return_value={"scope": draft}):
            self.assertFalse(self.store.job_cleanup({"ids": [first]})["deletable"])

    def test_new_active_dependency_blocks_deletion_after_preview(self):
        first = self.job(1)
        self.assertEqual(self.store.job_cleanup({"ids": [first]})["deletable"], [first])
        self.job(2, "running", {"source": str(self.outputs / first / "artifact.txt")})
        self.assertFalse(self.store.job_cleanup({"ids": [first], "confirmed": True}, execute=True)["deleted"])

    def test_failed_cleanup_filters_and_preview_ids_exclude_later_jobs(self):
        failed, complete = self.job(1, "failed"), self.job(2)
        preview = self.store.job_cleanup({"mode": "failed_cancelled", "filters": {"query": failed}})
        self.assertEqual(preview["deletable"], [failed])
        later = self.job(3, "failed")
        result = self.store.job_cleanup({"ids": preview["deletable"], "confirmed": True}, execute=True)
        self.assertEqual(result["deleted"], [failed])
        self.assertEqual(set(self.store.jobs), {complete, later})

    def test_validation_and_internal_log_alias_do_not_delete(self):
        first, second = self.job(1), self.job(2)
        with self.assertRaises(ValueError):
            self.store.job_cleanup({"ids": [first]}, execute=True)
        with self.assertRaises(ValueError):
            self.store.job_cleanup({"ids": ["../escape"], "confirmed": True}, execute=True)
        alias = self.logs / f"{first}.log"
        alias.unlink()
        try:
            alias.symlink_to(self.logs / f"{second}.log")
        except OSError:
            self.skipTest("symbolic links unavailable")
        result = self.store.job_cleanup({"ids": [first], "confirmed": True}, execute=True)
        self.assertFalse(result["deleted"])
        self.assertTrue((self.outputs / first).exists())
        self.assertEqual((self.logs / f"{second}.log").read_text(), "log")

    def test_active_asset_descriptor_and_invalid_drafts_are_protected(self):
        asset_id, revision_id = "a" * 32, "b" * 32
        self.job(1, "running", {"source": {"$asset": {"asset_id": asset_id, "revision_id": revision_id}}})
        self.assertTrue({asset_id, revision_id} <= self.store.cleanup_asset_references())
        with patch.object(service.assistant_data, "all_drafts", return_value={"scope": {"assistant": {"error": "old schema"}}}):
            self.assertEqual(self.store.cleanup_asset_references(), {"*"})


if __name__ == "__main__":
    unittest.main()
