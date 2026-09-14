from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from app.yue2_app.asset_library import AssetLibrary
from app.yue2_app import service
from app.yue2_app.io import atomic_json
from app.yue2_app.workbench_api import parse_byte_range, waveform


ROOT = Path(__file__).resolve().parents[1]


class AssetLibraryTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "cache").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="asset-test-", dir=ROOT / "cache")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source.wav"
        signal = (.2 * np.sin(np.arange(48000, dtype=np.float32) * .02)).astype(np.float32)
        sf.write(self.source, signal, 48000, subtype="FLOAT")
        self.library = AssetLibrary(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_is_durable_and_blob_is_deduplicated_without_merging_assets(self):
        first = self.library.import_file(self.source, kind="reference_voice", title="参考 A",
                                         provenance={"source": "test-a"})
        second = self.library.import_file(self.source, kind="reference_voice", title="参考 B",
                                          provenance={"source": "test-b"})
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["blob_sha256"], second["blob_sha256"])
        self.assertEqual(first["metadata"]["duration"], 1.0)
        blobs = [item for item in self.library.blobs.rglob("*") if item.is_file()]
        self.assertEqual(len(blobs), 1)
        self.source.unlink()
        stored, _ = self.library.revision_file(first["id"])
        self.assertTrue(stored.is_file())

    def test_text_revision_and_project_reference_are_immutable(self):
        project = self.library.create_project("夜航")
        lyrics = self.library.create_text(kind="lyrics", title="夜航歌词", text="[Verse]\n第一版")
        original_revision = lyrics["current_revision_id"]
        self.library.add_to_project(project["id"], lyrics["id"], revision_id=original_revision, role="lyrics")
        updated = self.library.create_text(kind="lyrics", title="夜航歌词", text="[Verse]\n第二版",
                                           asset_id=lyrics["id"])
        self.assertNotEqual(updated["current_revision_id"], original_revision)
        referenced = self.library.get_project(project["id"])["assets"][0]
        self.assertEqual(referenced["revision_id"], original_revision)

    def test_text_revision_rejects_parent_from_another_asset(self):
        first = self.library.create_text(kind="lyrics", title="歌词 A", text="第一版")
        second = self.library.create_text(kind="lyrics", title="歌词 B", text="另一首")
        with self.assertRaisesRegex(ValueError, "父版本不属于"):
            self.library.create_text(kind="lyrics", title="歌词 A", text="第二版",
                                     asset_id=first["id"], parent_revision_id=second["current_revision_id"])

    def test_snapshot_rejects_track_group_leakage(self):
        asset = self.library.import_file(self.source, kind="song", title="歌曲")
        base = {"asset_id": asset["id"], "revision_id": asset["current_revision_id"],
                "start": 0, "end": 1, "track_group_id": "same-song"}
        with self.assertRaisesRegex(ValueError, "有权使用"):
            self.library.create_snapshot(title="未确认权利", training_kind="yue2_style",
                                         items=[{**base, "split": "train"}])
        with self.assertRaisesRegex(ValueError, "跨训练集"):
            self.library.create_snapshot(title="错误划分", training_kind="yue2_style",
                                         items=[{**base, "split": "train"},
                                                {**base, "split": "validation"}],
                                         options={"rights_confirmed": True})
        snapshot = self.library.create_snapshot(title="正确划分", training_kind="yue2_style",
                                                items=[{**base, "split": "train"}],
                                                options={"rights_confirmed": True})
        self.assertEqual(snapshot["item_count"] if "item_count" in snapshot else len(snapshot["items"]), 1)
        listed = self.library.list_snapshots("yue2_style")
        self.assertEqual(listed[0]["manifest_sha256"], snapshot["manifest_sha256"])

    def test_waveform_cache_and_ranges(self):
        asset = self.library.import_file(self.source, kind="song", title="波形")
        result = waveform(self.library, asset["id"], bins=64)
        self.assertEqual(result["bins"], 64)
        self.assertEqual(len(result["peaks"]), 64)
        self.assertEqual(parse_byte_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(parse_byte_range("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_byte_range("bytes=-10", 100), (90, 99))
        with self.assertRaises(ValueError):
            parse_byte_range("bytes=100-120", 100)
        cache = next(self.library.waveforms.glob("*.json"))
        self.assertEqual(json.loads(cache.read_text())["sha256"], asset["blob_sha256"])

    def test_training_run_tracks_immutable_snapshot(self):
        asset = self.library.import_file(self.source, kind="song", title="训练歌曲")
        snapshot = self.library.create_snapshot(
            title="数据集", training_kind="yue2_style",
            items=[{"asset_id": asset["id"], "revision_id": asset["current_revision_id"],
                    "start": 0, "end": 1, "track_group_id": "song-a", "split": "train"}],
            options={"default_style": "warm jazz", "rights_confirmed": True})
        run = self.library.create_training_run(title="风格模型", training_kind="yue2_style",
                                               snapshot_id=snapshot["id"], config={"rank": 16})
        self.assertEqual(run["snapshot_id"], snapshot["id"])
        self.assertEqual(run["config"]["rank"], 16)
        updated = self.library.update_training_run(run["id"], state="queued", current_job_id="job-1")
        self.assertEqual(updated["state"], "queued")
        self.assertEqual(self.library.list_training_runs("yue2_style")[0]["id"], run["id"])

    def test_completed_job_is_promoted_once_and_malformed_nested_request_is_safe(self):
        outputs = self.root / "outputs"
        outputs.mkdir()
        job_id = "20260914-120000-deadbeef"
        directory = outputs / job_id
        audio = directory / "artifacts" / "result.wav"
        audio.parent.mkdir(parents=True)
        sf.write(audio, np.zeros(4800, dtype=np.float32), 48000)
        atomic_json(directory / "job.json", {
            "kind": "generate",
            "request": {"generate": "invalid-but-harmless"},
        })
        status = {"status": "complete", "result": {"audio": str(audio)}, "summary": "测试作品"}
        store = service.JobStore.__new__(service.JobStore)
        store.lock = __import__("threading").RLock()
        store.jobs = {job_id: status}
        with patch.object(service, "ROOT", self.root), patch.object(service, "OUTPUTS", outputs):
            store._promote_completed_result(job_id, status)
            first_ids = list(status["asset_ids"])
            store._promote_completed_result(job_id, status)
        self.assertEqual(status["asset_ids"], first_ids)
        promoted = AssetLibrary(self.root).get_asset(first_ids[0])
        self.assertEqual(promoted["kind"], "work")
        self.assertEqual(promoted["provenance"]["job_id"], job_id)


if __name__ == "__main__":
    unittest.main()
