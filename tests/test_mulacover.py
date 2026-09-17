from __future__ import annotations

import importlib.util
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.yue2_app import service
from app.yue2_app.mulacover_core import normalize_request, style_tags
from app.yue2_app.mulacover_models import SOURCE_FILES, readiness


class MuLaCoverRequestTests(unittest.TestCase):
    def test_normalizes_audio_request_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upload = root / "uploads" / "song.wav"
            upload.parent.mkdir(parents=True)
            upload.write_bytes(b"not decoded during admission")
            raw = {
                "source_mode": "audio", "source_path": str(upload),
                "lyrics": "[Verse]\nhello", "topic": "Longing", "genre": "country",
                "instrument": "guitar, strings", "mood": "hopeful",
                "duration_seconds": 30, "semitone_shift": -2, "octave_shift": -1,
                "seed": 42, "decode_seed": 43, "cfg_scale": 1.5,
                "temperature": 1.0, "topk": 250,
            }
            normalized = normalize_request(root, raw)
            self.assertEqual(normalized, normalize_request(root, normalized))
            self.assertEqual(normalized["source"]["ref_audio"], str(upload.resolve()))
            self.assertEqual(normalized["tags"], "topic:[Longing]; genre:[country]; instrument:[guitar, strings]; mood:[hopeful]")

    def test_rejects_source_outside_managed_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            outside = Path(temporary) / "song.wav"
            root.mkdir(); outside.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "必须来自整合包"):
                normalize_request(root, {"source_path": str(outside), "lyrics": "x", "genre": "pop"})

    def test_style_requires_one_value(self):
        with self.assertRaisesRegex(ValueError, "至少填写"):
            style_tags({"topic": "", "genre": "", "instrument": "", "mood": ""})


class MuLaCoverServiceTests(unittest.TestCase):
    def _store(self):
        store = service.JobStore.__new__(service.JobStore)
        store.lock = threading.RLock()
        store.storage_lock = threading.RLock()
        store.jobs = {}
        store.pending = queue.Queue()
        store.updating = False
        return store

    def test_job_is_persisted_for_remix_panel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs = root / "outputs" / "jobs"
            upload = root / "uploads" / "song.flac"
            outputs.mkdir(parents=True); upload.parent.mkdir(); upload.write_bytes(b"x")
            request = {"source_path": str(upload), "lyrics": "[Verse]\nhello", "genre": "pop"}
            with patch.object(service, "ROOT", root), patch.object(service, "OUTPUTS", outputs), \
                    patch.object(service, "runtime_ready", return_value={"capabilities": {"mulacover": True}}):
                job = self._store()._create("mulacover_remix", request, source="webui")
            self.assertEqual(job["result_panel"], "remix")
            self.assertEqual(job["project_id"], "")
            self.assertTrue((outputs / job["id"] / "job.json").is_file())

    def test_remix_style_tags_are_promoted_to_asset_library(self):
        source = Path(service.__file__).read_text(encoding="utf-8")
        self.assertIn('generation.get("tags")', source)


class MuLaCoverUiAndNodeTests(unittest.TestCase):
    def test_vendored_model_source_is_complete(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue(readiness(root)["source_ready"], readiness(root)["source_missing"])

    def test_missing_codec_or_torchtune_source_is_not_ready(self):
        for missing in ("src/mulacover/_codec/models/flow_matching.py",
                        "src/mulacover/_codec/models/sq_codec.py",
                        "compat/torchtune/models/llama3_2/__init__.py"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for relative in SOURCE_FILES:
                    if relative != missing:
                        path = root / "vendor/mulacover" / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.touch()
                state = readiness(root)
                self.assertFalse(state["source_ready"])
                self.assertFalse(state["ready"])
                self.assertEqual(state["source_missing"], [missing])

    def test_workbench_exposes_remix_and_persistence(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "app" / "web" / "index.html").read_text(encoding="utf-8")
        script = (root / "app" / "web" / "mulacover.js").read_text(encoding="utf-8")
        self.assertIn('data-tab="remix"', html)
        self.assertIn('id="remix-result"', html)
        self.assertIn("remix-draft:", script)
        self.assertIn("mulacover_remix", script)

    def test_native_node_package_has_no_service_bridge(self):
        root = Path(__file__).resolve().parents[2] / "Comfyui-Mulacover-T8"
        if not (root / "__init__.py").is_file():
            self.skipTest("Native nodes are validated in their separately released repository")
        source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
        self.assertNotIn("127.0.0.1", source)
        self.assertNotIn("/api/jobs", source)
        spec = importlib.util.spec_from_file_location(
            "comfyui_mulacover_t8_test", root / "__init__.py",
            submodule_search_locations=[str(root)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertIn("T8MuLaCoverGenerate", module.NODE_CLASS_MAPPINGS)
        self.assertIn("T8MuLaCoverAudioCondition", module.NODE_CLASS_MAPPINGS)


if __name__ == "__main__":
    unittest.main()
