import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.yue2_app.artifacts import (
    assert_provenance,
    generation_provenance,
    verify_artifact_manifest,
    write_artifact_manifest,
)
from app.yue2_app.config import ROOT, model_paths
from app.yue2_app.core_worker import generation_kwargs, generation_result, run_generate
from app.yue2_app.io import atomic_json, public_job, within
from app.yue2_app.model_verify import REQUIRED_FILES, verify_bundle
from app.yue2_app.retention import RetentionManager
from app.yue2_app.service import job_directory
from app.yue2_app.worker_common import Cancelled, JobContext


class IntegrationCodeTests(unittest.TestCase):
    def test_required_model_files_exist(self):
        if not (ROOT / "models").is_dir():
            self.skipTest("Models are downloaded by the post-install setup")
        for name, directory in model_paths().items():
            self.assertTrue((directory / "model.safetensors").is_file(), name)
            self.assertTrue((directory / "config.json").is_file(), name)

    def test_atomic_json_preserves_chinese(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "中文 状态.json"
            atomic_json(path, {"歌词": "晚风穿过城市的灯"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["歌词"], "晚风穿过城市的灯")

    def test_atomic_json_allows_concurrent_writers(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "status.json"
            errors = []
            def write(value):
                try:
                    atomic_json(path, {"value": value})
                except BaseException as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=write, args=(value,)) for value in range(20)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(errors, [])
            self.assertIn(json.loads(path.read_text(encoding="utf-8"))["value"], range(20))
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_path_boundary(self):
        base = ROOT / "outputs"
        self.assertEqual(within(base, base / "jobs"), (base / "jobs").resolve())
        with self.assertRaises(ValueError):
            within(base, ROOT / "models")
        self.assertEqual(job_directory("20260910-120000-deadbeef").name, "20260910-120000-deadbeef")
        with self.assertRaises(ValueError):
            job_directory("../../outside-job")

    def test_cancelled_job_cannot_finish(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            job = Path(directory)
            atomic_json(job / "status.json", {"status": "cancelling"})
            (job / "cancel.requested").touch()
            with self.assertRaises(Cancelled):
                JobContext(job).finish(result={"ok": True})
            self.assertNotEqual(json.loads((job / "status.json").read_text())["status"], "complete")

    def test_public_status_hides_command(self):
        self.assertNotIn("command", public_job({"id": "x", "command": ["secret"]}))

    def test_generation_request_modes(self):
        value = generation_kwargs({"style": "爵士", "lyrics": "词", "cot": "melody", "seed": 42})
        self.assertEqual(value["seed"], 42)
        self.assertEqual(value["cot"], "melody")
        partial = generation_result([{"audio": "a.flac", "directory": "song", "truncated": {}}], 2,
                                    [{"error": "candidate failed"}])
        self.assertTrue(partial["partial"])
        self.assertEqual(partial["completed_candidates"], 1)

    def test_later_candidate_failure_preserves_completed_result(self):
        class FakePipe:
            def close(self): pass
        class FakeResult:
            abc = None
            truncated = {"abc": False, "semantic": False}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            job = Path(directory)
            atomic_json(job / "status.json", {"status": "running"})
            calls = 0
            def generate(*_args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("candidate failed")
                return ({"identity": "one", "audio_seconds": 1.0}, FakeResult())
            with mock.patch("app.yue2_app.core_worker.create_pipe", return_value=FakePipe()), \
                    mock.patch("app.yue2_app.core_worker.generate_one", side_effect=generate):
                result = run_generate(ROOT, JobContext(job), {"candidates": 3, "seed": 1})
            self.assertTrue(result["partial"])
            self.assertEqual(result["completed_candidates"], 1)
            self.assertEqual(result["failures"][0]["seed"], 2)

    def test_model_bundle_verifier(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            models = root / "models"
            entries = {}
            for name, required in REQUIRED_FILES.items():
                model_dir = models / name
                model_dir.mkdir(parents=True)
                weight = model_dir / "model.safetensors"
                payload = name.encode("utf-8")
                weight.write_bytes(payload)
                for filename in required:
                    (model_dir / filename).write_text("{}", encoding="utf-8")
                entries[name] = {"file": f"{name}/model.safetensors", "size": len(payload),
                                 "sha256": hashlib.sha256(payload).hexdigest()}
            atomic_json(models / "MODEL_MANIFEST.json", {"bundle": "t8star/YuE2-Comfy", "models": entries})
            self.assertEqual(set(verify_bundle(root, progress=False)), set(REQUIRED_FILES))
            (models / "YuE2-3B" / "model.safetensors").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify_bundle(root, progress=False)

    def test_staged_manifest_verifies_hashes_and_model_provenance(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            models = root / "models"
            models.mkdir()
            mot_hash = "1" * 64
            vae_hash = "2" * 64
            atomic_json(models / "MODEL_MANIFEST.json", {
                "bundle": "t8star/YuE2-Comfy",
                "models": {
                    "YuE2-3B": {"source": "source/mot", "revision": "a", "file": "mot", "size": 1,
                                 "sha256": mot_hash},
                    "YuE2-Vae": {"source": "source/vae", "revision": "b", "file": "vae", "size": 1,
                                 "sha256": vae_hash},
                },
            })
            weights = {
                "mot": {"files": {"model.safetensors": {"sha256": mot_hash, "bytes": 1}}},
                "vae": {"files": {"model.safetensors": {"sha256": vae_hash, "bytes": 1}}},
            }
            provenance = generation_provenance(root, weights)
            artifact = root / "artifact"
            artifact.mkdir()
            (artifact / "semantic.npy").write_bytes(b"tokens")
            (artifact / "semantic.json").write_text("{}", encoding="utf-8")
            (artifact / "plan_manifest.json").write_text("{}", encoding="utf-8")
            semantic_manifest_path, _ = write_artifact_manifest(
                artifact, "semantic_manifest.json", "yue2-semantic-v1",
                ["semantic.npy", "semantic.json", "plan_manifest.json"], models=provenance,
            )
            manifest = verify_artifact_manifest(
                artifact, "semantic_manifest.json", "yue2-semantic-v1",
                {"semantic.npy", "semantic.json", "plan_manifest.json"},
            )
            assert_provenance(manifest, root, weights)
            wrong_weights = json.loads(json.dumps(weights))
            wrong_weights["mot"]["files"]["model.safetensors"]["sha256"] = "3" * 64
            with self.assertRaises(ValueError):
                assert_provenance(manifest, root, wrong_weights)
            (artifact / "latent.npy").write_bytes(b"latents")
            write_artifact_manifest(
                artifact, "latent_manifest.json", "yue2-latent-v1",
                ["latent.npy", "semantic_manifest.json"], models=provenance,
            )
            verify_artifact_manifest(
                artifact, "latent_manifest.json", "yue2-latent-v1",
                {"latent.npy", "semantic_manifest.json"},
            )
            original_semantic_manifest = semantic_manifest_path.read_text(encoding="utf-8")
            semantic_manifest_path.write_text(original_semantic_manifest + " ", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_artifact_manifest(
                    artifact, "latent_manifest.json", "yue2-latent-v1",
                    {"latent.npy", "semantic_manifest.json"},
                )
            semantic_manifest_path.write_text(original_semantic_manifest, encoding="utf-8")
            (artifact / "semantic.npy").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify_artifact_manifest(
                    artifact, "semantic_manifest.json", "yue2-semantic-v1",
                    {"semantic.npy", "semantic.json", "plan_manifest.json"},
                )

    def test_retention_prunes_terminal_jobs_and_keeps_exports(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for relative in ("outputs/jobs", "uploads", "logs", "exports"):
                (root / relative).mkdir(parents=True)
            policy = {
                "enabled": True,
                "cleanup_interval_hours": 6,
                "jobs": {"max_age_days": 0, "max_count": 1, "max_bytes_gib": 0},
                "uploads": {"max_age_days": 0, "max_bytes_gib": 2 / 2**30},
                "logs": {"max_age_days": 0, "max_bytes_gib": 2 / 2**30},
            }
            atomic_json(root / "retention.json", policy)
            job_ids = ["20260910-120000-00000001", "20260910-120001-00000002"]
            for index, job_id in enumerate(job_ids):
                job = root / "outputs" / "jobs" / job_id
                job.mkdir()
                atomic_json(job / "status.json", {"id": job_id, "status": "complete",
                                                    "finished_at": index + 1})
                (job / "data.bin").write_bytes(b"x")
            running_id = "20260910-120002-00000003"
            running = root / "outputs" / "jobs" / running_id
            running.mkdir()
            atomic_json(running / "status.json", {"id": running_id, "status": "running"})
            for folder, names in (("uploads", ("old.wav", "new.wav")), ("logs", ("old.log", "new.log"))):
                for index, name in enumerate(names):
                    path = root / folder / name
                    path.write_bytes(b"xx")
                    os.utime(path, (index + 1, index + 1))
            (root / "exports" / "keep.flac").write_bytes(b"permanent")
            report = RetentionManager(root).cleanup(current_job=running_id, force=True)
            self.assertFalse((root / "outputs" / "jobs" / job_ids[0]).exists())
            self.assertTrue((root / "outputs" / "jobs" / job_ids[1]).exists())
            self.assertTrue(running.exists())
            self.assertEqual(len(report["deleted"]["uploads"]), 1)
            self.assertEqual(len(report["deleted"]["logs"]), 1)
            self.assertTrue((root / "exports" / "keep.flac").is_file())

    def test_workflows_are_well_formed(self):
        workflows = list((ROOT / "workflows").glob("*.json"))
        self.assertEqual(len(workflows), 3)
        for path in workflows:
            data = json.loads(path.read_text(encoding="utf-8"))
            node_ids = {node["id"] for node in data["nodes"]}
            self.assertIn("YuE2ModelLoader", {node["type"] for node in data["nodes"]})
            for link in data["links"]:
                self.assertIn(link[1], node_ids)
                self.assertIn(link[3], node_ids)


if __name__ == "__main__":
    unittest.main()
