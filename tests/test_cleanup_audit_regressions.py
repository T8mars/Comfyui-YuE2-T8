"""Independent regressions from the v1.5.11 ten-round cleanup audit."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from app.yue2_app.io import atomic_json
from app.yue2_app.retention import RetentionManager
import test_manual_cleanup as cleanup_fixtures


class TrashBoundaryAudit(unittest.TestCase):
    setUp = cleanup_fixtures.AssetCleanupTest.setUp
    tearDown = cleanup_fixtures.AssetCleanupTest.tearDown
    asset = cleanup_fixtures.AssetCleanupTest.asset
    trash = cleanup_fixtures.AssetCleanupTest.trash

    def test_empty_trash_reaches_disposable_asset_after_500_protected_assets(self):
        disposable = self.asset("shared fixture bytes", "old disposable")
        self.trash(disposable)
        protected = []
        with self.library.transaction() as db:
            for index in range(500):
                asset_id = f"{index + 1:032x}"
                revision_id = f"{index + 1001:032x}"
                protected.append({"asset_id": asset_id, "revision_id": revision_id})
                db.execute(
                    "INSERT INTO assets SELECT ?,kind,?,status,tags_json,rights_json,"
                    "created_at,?,? FROM assets WHERE id=?",
                    (asset_id, f"protected-{index}", time.time() + index + 1,
                     revision_id, disposable["id"]),
                )
                db.execute(
                    "INSERT INTO revisions SELECT ?,?,NULL,blob_sha256,blob_suffix,"
                    "mime,size,metadata_json,provenance_json,created_at "
                    "FROM revisions WHERE id=?",
                    (revision_id, asset_id, disposable["current_revision_id"]),
                )
            db.execute("INSERT INTO dataset_snapshots VALUES(?,?,?,?,?,?)", (
                "f" * 32, "fixed protected assets", "yue2_style",
                json.dumps({"schema": 1, "items": protected}), "f" * 64, 1,
            ))
        self.assertEqual(self.library.count_assets(status="trashed"), 501)
        preview = self.cleanup.preview({"mode": "empty_trash"})
        self.assertEqual(preview["deletable"], [disposable["id"]])
        self.assertIn(disposable["id"], preview["ids"])
        self.assertLessEqual(len(preview["deletable"]), 500)
        self.assertEqual(len(preview["skipped"]), 500)
        report = self.cleanup.purge({"ids": preview["deletable"], "confirmed": True})
        self.assertEqual(report["deleted"], [disposable["id"]])
        self.assertEqual(self.library.count_assets(status="trashed"), 500)
        survivor = protected[0]
        self.assertEqual(self.library.revision_file(survivor["asset_id"])[0].read_text(),
                         "shared fixture bytes")

    def test_cache_only_gc_remains_visible_and_retryable_with_no_assets(self):
        asset = self.asset()
        blob, revision = self.library.revision_file(asset["id"])
        cache = self.library.waveforms / (revision["blob_sha256"] + "-64.json")
        cache.write_bytes(b"cache")
        self.trash(asset)
        original_unlink = Path.unlink

        def occupied(path, *args, **kwargs):
            if path == cache:
                raise PermissionError("independent occupied waveform cache")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", occupied):
            report = self.cleanup.purge({"ids": [asset["id"]], "confirmed": True})
        self.assertEqual(report["deleted"], [asset["id"]])
        self.assertTrue(report["errors"])
        self.assertFalse(blob.exists())
        self.assertTrue(cache.exists())
        preview = self.cleanup.preview({"mode": "empty_trash"})
        self.assertEqual(preview["ids"], [])
        self.assertEqual(preview["deletable"], [])
        self.assertEqual(preview["bytes"], 5)
        self.assertGreater(preview["pending_gc"], 0)
        cache.write_bytes(b"")
        zero_bytes = self.cleanup.preview({"mode": "empty_trash"})
        self.assertEqual(zero_bytes["bytes"], 0)
        self.assertGreater(zero_bytes["pending_gc"], 0)
        retry = self.cleanup.purge({"ids": [], "confirmed": True})
        self.assertFalse(retry["errors"])
        self.assertFalse(cache.exists())
        self.assertEqual(self.cleanup.preview({"mode": "empty_trash"})["pending_gc"], 0)


class PortableOccupiedJobAudit(unittest.TestCase):
    setUp = cleanup_fixtures.JobCleanupTest.setUp
    tearDown = cleanup_fixtures.JobCleanupTest.tearDown
    job = cleanup_fixtures.JobCleanupTest.job

    def prepare_occupied_job(self):
        job_id = self.job(1)
        directory = self.outputs / job_id
        atomic_json(directory / "job.json", {"id": job_id, "kind": "generate", "request": {}})
        status = {**self.store.jobs[job_id], "resumable": True, "finished_at": 1,
                  "result": {"audio": "artifact.txt"}}
        self.store.jobs[job_id] = status
        atomic_json(directory / "status.json", status)
        blocked = directory / "zz-blocked.txt"
        blocked.write_text("portable simulated occupied file")
        real_unlink = Path.unlink

        def occupied_unlink(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError("independent occupied-file simulation")
            return real_unlink(path, *args, **kwargs)

        return job_id, directory, occupied_unlink

    def test_partial_payload_deletion_blocks_execution_but_can_retry_cleanup(self):
        job_id, directory, occupied_unlink = self.prepare_occupied_job()
        with patch.object(Path, "unlink", occupied_unlink):
            report = self.store.job_cleanup({"ids": [job_id], "confirmed": True}, execute=True)
        self.assertTrue(report["errors"])
        self.assertFalse(report["deleted"])
        self.assertFalse((directory / "artifact.txt").exists())
        self.assertTrue((directory / "job.json").is_file())
        self.assertTrue((directory / "status.json").is_file())
        remaining = self.store.get(job_id)
        self.assertEqual(remaining["status"], "failed")
        self.assertTrue(remaining["cleanup_pending"])
        self.assertFalse(remaining["resumable"])
        self.assertIsNone(remaining["result"])
        with patch.object(self.store, "create") as create:
            with self.assertRaisesRegex(ValueError, "部分清理"):
                self.store.resume(job_id)
            with self.assertRaisesRegex(ValueError, "部分清理"):
                self.store.retry_assistant(job_id, {})
            create.assert_not_called()
        self.assertEqual(self.store.job_cleanup({"ids": [job_id]})["deletable"], [job_id])
        retried = self.store.job_cleanup({"ids": [job_id], "confirmed": True}, execute=True)
        self.assertEqual(retried["deleted"], [job_id])
        self.assertFalse(directory.exists())

    def test_policy_partial_failure_updates_cache_before_status_filter(self):
        job_id, directory, occupied_unlink = self.prepare_occupied_job()
        self.store.retention = RetentionManager(self.root)
        with patch.object(Path, "unlink", occupied_unlink):
            report = self.store.cleanup_retention(force=True)
        self.assertTrue(report["errors"])
        self.assertTrue(directory.is_dir())
        self.assertEqual(self.store.jobs[job_id]["status"], "failed")
        self.assertTrue(self.store.jobs[job_id]["cleanup_pending"])
        complete, complete_total = self.store.list_page(status="complete")
        failed, failed_total = self.store.list_page(status="failed")
        self.assertEqual((complete, complete_total), ([], 0))
        self.assertEqual([item["id"] for item in failed], [job_id])
        self.assertEqual(failed_total, 1)
        retry = self.store.cleanup_retention(force=True)
        self.assertFalse(retry["errors"])
        self.assertFalse(directory.exists())
        self.assertNotIn(job_id, self.store.jobs)


class WindowsOccupiedJobAudit(unittest.TestCase):
    setUp = cleanup_fixtures.JobCleanupTest.setUp
    tearDown = cleanup_fixtures.JobCleanupTest.tearDown
    job = cleanup_fixtures.JobCleanupTest.job

    @unittest.skipUnless(os.name == "nt", "requires real Windows file sharing semantics")
    def test_partial_cleanup_keeps_control_files_visible_and_can_retry(self):
        job_id = self.job(1)
        directory = self.outputs / job_id
        original_job = {"id": job_id, "kind": "generate", "request": {}, "created_at": 1}
        atomic_json(directory / "job.json", original_job)
        status = {**self.store.jobs[job_id], "result": {"audio": "artifact.txt"}, "resumable": True}
        self.store.jobs[job_id] = status
        atomic_json(directory / "status.json", status)
        blocked = directory / "zz-blocked.txt"
        blocked.write_text("independent actual occupied payload")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        handle = kernel.CreateFileW(str(blocked), 0x80000000, 0, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            report = self.store.job_cleanup({"ids": [job_id], "confirmed": True},
                                            execute=True)
            self.assertEqual(report["deleted"], [])
            self.assertTrue(report["errors"])
            self.assertTrue((directory / "job.json").is_file())
            self.assertTrue((directory / "status.json").is_file())
            self.assertEqual(json.loads((directory / "job.json").read_text()), original_job)
            remaining = self.store.get(job_id)
            self.assertEqual(remaining["status"], "failed")
            self.assertTrue(remaining["cleanup_pending"])
            self.assertFalse(remaining["resumable"])
            self.assertIsNone(remaining.get("result"))
            with patch.object(self.store, "create") as create:
                with self.assertRaisesRegex(ValueError, "部分清理"):
                    self.store.resume(job_id)
                with self.assertRaisesRegex(ValueError, "部分清理"):
                    self.store.retry_assistant(job_id, {})
                create.assert_not_called()
            self.assertTrue(blocked.is_file())
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(self.store.job_cleanup({"ids": [job_id]})["deletable"], [job_id])
        retry = self.store.job_cleanup({"ids": [job_id], "confirmed": True}, execute=True)
        self.assertEqual(retry["deleted"], [job_id])
        self.assertFalse(directory.exists())
        self.assertFalse((self.logs / f"{job_id}.log").exists())
        with self.assertRaises(KeyError):
            self.store.get(job_id)


if __name__ == "__main__":
    unittest.main()
