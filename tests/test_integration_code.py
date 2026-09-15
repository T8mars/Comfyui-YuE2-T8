import hashlib
import json
import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.yue2_app.artifacts import (
    assert_provenance,
    generation_provenance,
    verify_artifact_manifest,
    verify_hash_manifest,
    write_artifact_manifest,
)
from app.yue2_app.config import ROOT, model_paths, runtime_ready
from app.yue2_app.core_worker import add_upstream, generation_kwargs, generation_result, run_decode, run_doctor, run_generate
from app.yue2_app.io import atomic_json, public_job, within
from app.yue2_app.model_verify import PINNED_MODELS, REQUIRED_FILES, verify_bundle
from app.yue2_app.retention import RetentionManager
from app.yue2_app.settings import model_directory, save_model_directory, settings_info
from app.yue2_app.service import (
    JobStore,
    acquire_instance_lock,
    is_loopback_host,
    job_directory,
    retention_references,
    worker_failure_message,
)
from app.yue2_app import service
from app.yue2_app.worker_common import Cancelled, JobContext
from app.yue2_app.voice_worker import gate_converted_vocal, remix_audio


class IntegrationCodeTests(unittest.TestCase):
    def test_saved_korean_plan_loads_on_non_utf8_windows_locale(self):
        add_upstream(ROOT)
        from yue2.pipeline import SymbolicPlan
        from yue2.protocol import SongRequest

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            plan = SymbolicPlan(
                SongRequest(style="한국 동요", lyrics="아침 햇살", cot="off"),
                None, [], [1, 2, 3],
            )
            plan.save(directory)
            restored = SymbolicPlan.load(directory)
            self.assertEqual(restored.request.style, "한국 동요")
            self.assertEqual(restored.request.lyrics, "아침 햇살")

    def test_rvc_activity_gate_suppresses_hallucinated_tone_during_silent_source(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            reference_rate = 44100
            converted_rate = 48000
            seconds = 2
            reference = np.zeros(reference_rate * seconds, dtype=np.float32)
            reference[reference_rate:] = 0.08 * np.sin(
                2 * np.pi * 220 * np.arange(reference_rate) / reference_rate)
            converted = 0.08 * np.sin(
                2 * np.pi * 330 * np.arange(converted_rate * seconds) / converted_rate).astype(np.float32)
            reference_path = root / "separated.wav"
            converted_path = root / "converted.wav"
            sf.write(reference_path, reference, reference_rate, subtype="FLOAT")
            sf.write(converted_path, converted, converted_rate, subtype="FLOAT")

            report = gate_converted_vocal(reference_path, converted_path)
            gated, rate = sf.read(converted_path, dtype="float32")
            silent_rms = float(np.sqrt(np.mean(np.square(gated[:converted_rate * 3 // 4]))))
            active_rms = float(np.sqrt(np.mean(np.square(gated[converted_rate * 5 // 4:]))))

            self.assertEqual(rate, converted_rate)
            self.assertEqual(report["version"], "rms-v2")
            self.assertLess(silent_rms, 1e-5)
            self.assertGreater(active_rms, 0.04)
            self.assertGreater(report["muted_fraction"], 0.35)

    def test_model_directory_setting_supports_another_drive_layout(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            external = root / "external-models"
            info = save_model_directory(root, external)
            self.assertEqual(model_directory(root), external.resolve())
            self.assertEqual(model_paths(root)["model"], external.resolve() / "YuE2-3B")
            self.assertFalse(info["using_default"])
            self.assertEqual(info["model_repository"], "https://huggingface.co/t8star/YuE2-Comfy")
            self.assertEqual(json.loads((root / "settings.json").read_text())["schema"], 1)
            default = save_model_directory(root, "")
            self.assertTrue(default["using_default"])
            self.assertEqual(model_directory(root), (root / "models").resolve())

    def test_invalid_model_directory_setting_is_visible_and_disables_capabilities(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            (root / "settings.json").write_text("[]", encoding="utf-8")
            info = settings_info(root)
            ready = runtime_ready(root)
            self.assertIn("JSON 对象", info["error"])
            self.assertTrue(ready["settings_error"])
            self.assertFalse(any(ready["capabilities"].values()))

    def test_worker_failure_uses_final_exception_message(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            log = Path(directory) / "job.log"
            log.write_text("Traceback (most recent call last):\nFileNotFoundError: 缺少 YuE2 推理源码\n",
                           encoding="utf-8")
            self.assertEqual(worker_failure_message(log, 1), "缺少 YuE2 推理源码")

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

    def test_active_duplicate_request_reuses_existing_job(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            outputs = Path(directory) / "jobs"
            outputs.mkdir()
            store = object.__new__(JobStore)
            store.updating = False
            store.storage_lock = threading.RLock()
            store.lock = threading.RLock()
            store.jobs = {}
            store.pending = queue.Queue()
            store.current_id = None
            store.current_process = None
            request = {"style": "Mandarin pop", "lyrics": "同一首歌", "seed": 42}
            with mock.patch.object(service, "OUTPUTS", outputs), \
                    mock.patch.object(service, "runtime_ready", return_value={
                        "capabilities": {"generation": True}
                    }):
                first = store.create("generate", request, source="webui", client_request_id="click-1")
                duplicate = store.create("generate", request, source="webui", client_request_id="click-2")
            self.assertEqual(duplicate["id"], first["id"])
            self.assertTrue(duplicate["deduplicated"])
            self.assertEqual(store.pending.qsize(), 1)
            self.assertEqual(first["summary"], "Mandarin pop")

    def test_reference_voice_job_is_accepted_and_named(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            outputs = Path(directory) / "jobs"
            outputs.mkdir()
            store = object.__new__(JobStore)
            store.updating = False
            store.storage_lock = threading.RLock()
            store.lock = threading.RLock()
            store.jobs = {}
            store.pending = queue.Queue()
            store.current_id = None
            store.current_process = None
            import numpy as np
            import soundfile as sf
            uploads = Path(directory) / "uploads"
            uploads.mkdir()
            for name in ("song.flac", "voice.wav"):
                sf.write(uploads / name, np.zeros(32000), 16000)
            request = {"source_path": str(uploads / "song.flac"), "reference_path": str(uploads / "voice.wav")}
            with mock.patch.object(service, "ROOT", Path(directory)), mock.patch.object(service, "OUTPUTS", outputs), \
                    mock.patch.object(service, "runtime_ready", return_value={
                        "capabilities": {"voice_conversion": True}
                    }):
                created = store.create("voice_convert", request, source="comfyui")
            self.assertEqual(created["kind"], "voice_convert")
            self.assertEqual(created["summary"], "参考音色翻唱 · voice.wav")

    def test_result_panel_survives_resume_and_legacy_cover_is_restored(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            outputs = Path(directory) / "jobs"
            outputs.mkdir()
            store = object.__new__(JobStore)
            store.updating = False
            store.storage_lock = threading.RLock()
            store.lock = threading.RLock()
            store.jobs = {}
            store.pending = queue.Queue()
            with mock.patch.object(service, "OUTPUTS", outputs), mock.patch.object(
                    service, "runtime_ready", return_value={"capabilities": {"generation": True}}):
                created = store.create("generate", {"abc": "X:1\nK:C\nC4|"}, source="webui", result_panel="cover")
                self.assertEqual(created["result_panel"], "cover")
                status_path = outputs / created["id"] / "status.json"
                legacy = {**created, "status": "failed"}
                legacy.pop("result_panel")
                atomic_json(status_path, legacy)
                self.assertEqual(store.get(created["id"])["result_panel"], "cover")
                resumed = store.resume(created["id"], {"memory_budget_gib": 8})
                self.assertEqual(resumed["result_panel"], "cover")
                resumed_job = json.loads((outputs / resumed["id"] / "job.json").read_text())
                self.assertEqual(resumed_job["request"]["memory_budget_gib"], 8)

    def test_voice_remix_outputs_finite_48khz_stereo(self):
        import importlib.util
        if importlib.util.find_spec("scipy") is None:
            self.skipTest("Voice runtime owns the SciPy resampler")
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            vocal = root / "vocal.wav"
            backing = root / "backing.wav"
            output = root / "audio.flac"
            timeline = np.arange(8000, dtype=np.float32) / 8000
            sf.write(vocal, np.sin(2 * np.pi * 220 * timeline)[:, None] * 0.1, 8000)
            sf.write(backing, np.column_stack([np.sin(2 * np.pi * 110 * timeline) * 0.1] * 2), 8000)
            info = remix_audio(vocal, backing, output)
            data, rate = sf.read(output, dtype="float32", always_2d=True)
            self.assertEqual((rate, data.shape[1]), (48000, 2))
            self.assertTrue(np.isfinite(data).all())
            self.assertEqual(info["channels"], 2)

    def test_cancelling_queued_job_updates_logical_queue_immediately(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            outputs = Path(directory) / "jobs"
            outputs.mkdir()
            store = object.__new__(JobStore)
            store.updating = False
            store.storage_lock = threading.RLock()
            store.lock = threading.RLock()
            store.jobs = {}
            store.pending = queue.Queue()
            store.current_id = None
            store.current_process = None
            with mock.patch.object(service, "OUTPUTS", outputs):
                created = store.create("doctor", {"verify_hashes": True}, source="webui")
                cancelled = store.cancel(created["id"])
                state = store.state()
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(state["queued"], 0)

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
                entries[name] = {"source": f"source/{name}", "revision": name.lower(),
                                 "file": f"{name}/model.safetensors", "size": len(payload),
                                 "sha256": hashlib.sha256(payload).hexdigest()}
            atomic_json(models / "MODEL_MANIFEST.json", {"bundle": "t8star/YuE2-Comfy", "models": entries})
            with mock.patch.dict(PINNED_MODELS, entries, clear=True):
                self.assertEqual(set(verify_bundle(root, progress=False)), set(REQUIRED_FILES))
            (models / "YuE2-3B" / "model.safetensors").write_bytes(b"changed")
            with mock.patch.dict(PINNED_MODELS, entries, clear=True), self.assertRaises(ValueError):
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
            pins = json.loads(json.dumps(json.loads((models / "MODEL_MANIFEST.json").read_text())["models"]))
            with mock.patch.dict(PINNED_MODELS, pins, clear=True):
                provenance = generation_provenance(root, weights)
                original_manifest = (models / "MODEL_MANIFEST.json").read_text(encoding="utf-8")
                (models / "MODEL_MANIFEST.json").write_text(original_manifest + "  \n", encoding="utf-8")
                self.assertEqual(generation_provenance(root, weights), provenance)
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
            with mock.patch.dict(PINNED_MODELS, pins, clear=True):
                assert_provenance(manifest, root, weights)
                legacy = json.loads(json.dumps(manifest))
                legacy["models"]["bundle_manifest_sha256"] = "legacy-audit-only-value"
                assert_provenance(legacy, root, weights)
            wrong_weights = json.loads(json.dumps(weights))
            wrong_weights["mot"]["files"]["model.safetensors"]["sha256"] = "3" * 64
            with mock.patch.dict(PINNED_MODELS, pins, clear=True), self.assertRaises(ValueError):
                assert_provenance(manifest, root, wrong_weights)
            changed_manifest = json.loads((models / "MODEL_MANIFEST.json").read_text())
            changed_manifest["models"]["YuE2-3B"]["source"] = "untrusted/source"
            atomic_json(models / "MODEL_MANIFEST.json", changed_manifest)
            with mock.patch.dict(PINNED_MODELS, pins, clear=True), self.assertRaises(ValueError):
                generation_provenance(root, weights)
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

    def test_retention_keeps_active_dependencies(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for relative in ("outputs/jobs", "uploads", "logs", "exports"):
                (root / relative).mkdir(parents=True)
            old_id = "20260910-120000-00000001"
            old = root / "outputs" / "jobs" / old_id
            old.mkdir()
            atomic_json(old / "status.json", {"id": old_id, "status": "complete", "finished_at": 1})
            (old / "artifact.bin").write_bytes(b"x")
            upload = root / "uploads" / "source.wav"
            upload.write_bytes(b"audio")
            os.utime(upload, (1, 1))
            atomic_json(root / "retention.json", {
                "enabled": True, "cleanup_interval_hours": 6,
                "jobs": {"max_age_days": 0, "max_count": 0, "max_bytes_gib": 1 / 2**30},
                "uploads": {"max_age_days": 0, "max_bytes_gib": 1 / 2**30},
                "logs": {"max_age_days": 0, "max_bytes_gib": 1},
            })
            jobs, uploads = retention_references(
                [{"plan_dir": str(old / "artifacts"), "source_path": str(upload)}],
                root / "outputs" / "jobs", root / "uploads",
            )
            report = RetentionManager(root).cleanup(
                force=True, protected_jobs=jobs, protected_uploads=uploads,
            )
            self.assertTrue(old.is_dir())
            self.assertTrue(upload.is_file())
            self.assertEqual(report["deleted"]["jobs"], [])
            self.assertEqual(report["deleted"]["uploads"], [])

    def test_decode_rejects_untracked_file_before_loading_model(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "outputs" / "jobs" / "20260910-120000-00000001" / "artifacts" / "synthesis"
            source.mkdir(parents=True)
            (source / "unverified.npy").write_bytes(b"not trusted")
            job = root / "outputs" / "jobs" / "20260910-120001-00000002"
            job.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "latent.npy"):
                run_decode(root, JobContext(job), {"latent": str(source / "unverified.npy")})

    def test_recursive_plan_manifest_detects_tamper(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            files = {"plan.json": b"plan", "abc_tokens.npy": b"abc", "prefix.npy": b"prefix", "score.abc": b"score"}
            for name, payload in files.items():
                (root / name).write_bytes(payload)
            atomic_json(root / "plan_manifest.json", {
                name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()
            })
            verify_hash_manifest(root, "plan_manifest.json", set(files))
            (root / "score.abc").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify_hash_manifest(root, "plan_manifest.json", set(files))
            (root / "score.abc").unlink()
            atomic_json(root / "plan_manifest.json", {
                name: hashlib.sha256(payload).hexdigest()
                for name, payload in files.items() if name != "score.abc"
            })
            verify_hash_manifest(
                root, "plan_manifest.json", {"plan.json", "abc_tokens.npy", "prefix.npy"},
            )

    def test_loopback_host_validation(self):
        for value in ("127.0.0.1:8189", "localhost", "[::1]:8189"):
            self.assertTrue(is_loopback_host(value), value)
        for value in ("attacker.example:8189", "localhost@attacker.example", "127.0.0.1:bad", ""):
            self.assertFalse(is_loopback_host(value), value)

    def test_instance_lock_rejects_duplicate_service(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            first = acquire_instance_lock(root)
            try:
                with self.assertRaises(RuntimeError):
                    acquire_instance_lock(root)
            finally:
                first.close()
            acquire_instance_lock(root).close()

    def test_runtime_ready_rejects_placeholder_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for relative in ("runtime/python.exe", "runtime/ffmpeg/ffmpeg.exe",
                             "models/YuE2-3B/model.safetensors", "models/YuE2-Vae/model.safetensors",
                             "models/SheetSage2/model.safetensors", "models/MERT-v2-FullSong/model.safetensors"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            ready = runtime_ready(root)
            self.assertFalse(ready["core_python"])
            self.assertFalse(ready["ffmpeg"])
            self.assertFalse(any(ready["models"].values()))
            self.assertFalse(ready["capabilities"]["generation"])
            browser = root / "runtime" / "playwright" / "chromium_headless_shell-1" / "chrome-headless-shell-win64" / "chrome-headless-shell.exe"
            browser.parent.mkdir(parents=True)
            browser.write_bytes(b"browser")
            (root / "runtime" / "python.exe").write_bytes(b"python")
            self.assertFalse(runtime_ready(root)["capabilities"]["score_renderer"])

    def test_doctor_fails_without_cuda(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                mock.patch("torch.cuda.is_available", return_value=False):
            job = Path(directory)
            atomic_json(job / "status.json", {"status": "running"})
            with self.assertRaisesRegex(RuntimeError, "CUDA"):
                run_doctor(ROOT, JobContext(job), {})

    def test_export_is_complete_and_collision_safe(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            outputs = root / "outputs" / "jobs"
            job_id = "20260910-120000-00000001"
            artifact = outputs / job_id / "artifacts"
            artifact.mkdir(parents=True)
            atomic_json(outputs / job_id / "status.json", {"id": job_id, "status": "complete"})
            (artifact / "one.bin").write_bytes(b"one")
            store = object.__new__(JobStore)
            store.updating = False
            store.storage_lock = threading.RLock()
            store.lock = threading.RLock()
            store.jobs = {}
            results = []
            errors = []
            def export():
                try:
                    results.append(store.export(job_id))
                except BaseException as exc:
                    errors.append(exc)
            with mock.patch.object(service, "ROOT", root), mock.patch.object(service, "OUTPUTS", outputs):
                threads = [threading.Thread(target=export) for _ in range(2)]
                for thread in threads: thread.start()
                for thread in threads: thread.join()
            self.assertEqual(errors, [])
            first, second = results
            self.assertNotEqual(first, second)
            self.assertEqual((first / "one.bin").read_bytes(), b"one")
            self.assertEqual((second / "one.bin").read_bytes(), b"one")
            def fail_copy(_source, temporary):
                Path(temporary).mkdir()
                (Path(temporary) / "partial.bin").write_bytes(b"partial")
                raise OSError("copy interrupted")
            with mock.patch.object(service, "ROOT", root), mock.patch.object(service, "OUTPUTS", outputs), \
                    mock.patch.object(service.shutil, "copytree", side_effect=fail_copy), \
                    self.assertRaises(OSError):
                store.export(job_id)
            self.assertEqual(list((root / "exports").glob(".*.tmp")), [])

    def test_completed_yue2_training_exports_model_package(self):
        from app.yue2_app.asset_library import AssetLibrary

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            outputs = root / "outputs" / "jobs"
            job_id = "20260915-173543-00000002"
            model_source = root / "adapter.safetensors"
            model_source.write_bytes(b"trained-yue2-adapter")
            asset = AssetLibrary(root).import_file(
                model_source, kind="model", title='我的歌曲风格: 200 步',
                metadata={"model_type": "yue2_ar_lora", "completed_training_steps": 200,
                          "selected_validation_step": 200, "rank": 8},
                provenance={"training_run_id": "run-1"},
            )
            atomic_json(outputs / job_id / "status.json", {
                "id": job_id, "kind": "yue2_train", "status": "complete",
                "result": {"model_asset": {"id": asset["id"]}},
            })
            store = object.__new__(JobStore)
            store.updating = False
            store.storage_lock = threading.RLock()
            store.lock = threading.RLock()
            store.jobs = {}
            with mock.patch.object(service, "ROOT", root), mock.patch.object(service, "OUTPUTS", outputs):
                destination = store.export(job_id)
            exported_model = destination / "我的歌曲风格_ 200 步.safetensors"
            self.assertEqual(exported_model.read_bytes(), b"trained-yue2-adapter")
            manifest = json.loads((destination / "model.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["asset_id"], asset["id"])
            self.assertEqual(manifest["model_file"], exported_model.name)
            self.assertEqual(manifest["metadata"]["completed_training_steps"], 200)
            self.assertEqual(manifest["sha256"], hashlib.sha256(b"trained-yue2-adapter").hexdigest())

    def test_training_model_and_history_pagination_are_bounded(self):
        web = Path(__file__).resolve().parents[1] / "app" / "web"
        javascript = (web / "workbench.js").read_text(encoding="utf-8")
        app_javascript = (web / "app.js").read_text(encoding="utf-8")
        html = (web / "index.html").read_text(encoding="utf-8")
        self.assertIn("const trainingModelPageSize = 3", javascript)
        self.assertIn("const historyPageSize = 10", app_javascript)
        self.assertIn('id="training-model-prev"', html)
        self.assertIn('id="training-model-next"', html)
        self.assertNotIn('class="ghost compact" type="button" data-copy-trained-model-path', javascript)

    def test_visible_project_and_creator_links_use_expected_destinations(self):
        web = Path(__file__).resolve().parents[1] / "app" / "web"
        html = (web / "index.html").read_text(encoding="utf-8")
        css = (web / "workbench.css").read_text(encoding="utf-8")
        for value in (
            "https://github.com/T8mars/Comfyui-YuE2-T8",
            "https://registry.comfy.org/nodes/yue2-t8",
            "https://huggingface.co/t8star/YuE2-Comfy",
            "https://space.bilibili.com/385085361",
            "https://www.youtube.com/@T8star-Aix/",
        ):
            self.assertIn(f'href="{value}"', html)
        self.assertNotIn("body > header .lead, body > header .project-meta { display: none; }", css)
        self.assertIn("body > header .project-meta", css)

    def test_workflows_are_well_formed(self):
        workflows = list((Path(__file__).resolve().parents[1] / "workflows").glob("*.json"))
        self.assertEqual({path.name[:2] for path in workflows}, {"01", "02", "03", "04"})
        for path in workflows:
            data = json.loads(path.read_text(encoding="utf-8"))
            node_ids = {node["id"] for node in data["nodes"]}
            self.assertIn("YuE2ModelLoader", {node["type"] for node in data["nodes"]})
            for link in data["links"]:
                self.assertIn(link[1], node_ids)
                self.assertIn(link[3], node_ids)

    def test_ci_and_registry_gate_use_current_actions_and_explicit_python(self):
        github = Path(__file__).resolve().parents[1] / ".github/workflows"
        quality = (github / "quality.yml").read_text(encoding="utf-8")
        publish = (github / "publish.yml").read_text(encoding="utf-8")
        for workflow in (quality, publish):
            self.assertIn("actions/checkout@v7", workflow)
            self.assertIn("actions/setup-python@v7", workflow)
        self.assertIn("python scripts/ui_browser_smoke.py", quality)
        self.assertIn("actions/upload-artifact@v7", quality)
        self.assertTrue((Path(__file__).resolve().parents[1] / "scripts/ui_browser_smoke.py").is_file())
        self.assertIn('python-version: "3.12"', publish)
        self.assertEqual(publish.count('"${{ steps.python.outputs.python-path }}" - <<\'PY\''), 2)


if __name__ == "__main__":
    unittest.main()
