import json
import tempfile
import unittest
import sys
import numpy as np
from pathlib import Path
from unittest.mock import patch

from app.yue2_app import workflow_worker
from app.yue2_app.core_worker import atomic_stage, generate_one
from app.yue2_app.io import atomic_json
from app.yue2_app.worker_common import JobContext
from app.yue2_app.voice_worker import restore_voice_stage
from app.yue2_app.artifacts import write_artifact_manifest


class WorkflowRecoveryTests(unittest.TestCase):
    def test_failed_decode_resumes_latents_without_resampling(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))
        from yue2.pipeline import SymbolicPlan, SemanticResult
        from yue2.protocol import SongRequest
        request = {"style": "test", "lyrics": "test", "cot": "off", "seed": 42}
        calls = []
        class Pipe:
            device = "cpu"
            weights = {"fixture": True}
            load_timing = {}
            nar_stats = {"math": 1}
            fail_decode = True
            def _request(self, **kwargs):
                return SongRequest(**kwargs)
            def effective_config(self, *args):
                return {"generation": {"ode_steps": 32}}
            def plan(self, request, **kwargs):
                calls.append("plan")
                return SymbolicPlan(request, None, [], [1, 2])
            def generate_semantic(self, plan, **kwargs):
                calls.append("semantic")
                return SemanticResult(plan, [5, 6, 7], {"seconds": 1}, False)
            def synthesize(self, semantic, **kwargs):
                calls.append("synthesize")
                return np.zeros((3, 64), dtype=np.float32)
            def decode(self, latents):
                calls.append("decode")
                if self.fail_decode:
                    raise RuntimeError("decode failed")
                return np.full((480, 2), 0.1, dtype=np.float32)
        with tempfile.TemporaryDirectory() as temporary, \
                patch("app.yue2_app.core_worker.generation_provenance", return_value={"fixture": True}), \
                patch("app.yue2_app.core_worker.assert_provenance"), \
                patch.object(JobContext, "memory"):
            root = Path(temporary)
            first = root / "outputs/jobs/20260911-000000-11111111"
            second = root / "outputs/jobs/20260911-000000-22222222"
            first.mkdir(parents=True); second.mkdir()
            pipe = Pipe()
            with self.assertRaisesRegex(RuntimeError, "decode failed"):
                generate_one(pipe, JobContext(first), request, first / "artifacts/song", 42)
            pipe.fail_decode = False
            receipt, result = generate_one(pipe, JobContext(second), {**request, "resume_from": str(first)}, second / "artifacts/song", 42)
            self.assertEqual(calls, ["plan", "semantic", "synthesize", "decode", "decode"])
            self.assertTrue((second / "artifacts/song/audio.flac").is_file())
            self.assertEqual(receipt["audio_seconds"], 0.01)

    def test_atomic_checkpoint_never_exposes_partial_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "semantic"
            def interrupted(path):
                (path / "semantic.npy").write_bytes(b"partial")
                raise InterruptedError("cancel")
            with self.assertRaises(InterruptedError):
                atomic_stage(destination, interrupted)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])
            atomic_stage(destination, lambda path: (path / "manifest.json").write_text("{}"))
            self.assertTrue((destination / "manifest.json").is_file())

    def test_workflow_runs_voice_only_after_generated_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "outputs/jobs/20260911-000000-11111111"
            directory.mkdir(parents=True)
            ctx = JobContext(directory)
            calls = []
            def stage(root, ctx, name, kind, request):
                calls.append((name, request.copy()))
                if name == "generate":
                    return {"audio": str(directory / "generated.flac")}
                self.assertEqual(request["source_path"], str(directory / "generated.flac"))
                return {"audio": str(directory / "final.flac")}
            with patch.object(workflow_worker, "run_stage", side_effect=stage):
                result = workflow_worker.run_workflow(root, ctx, {"generate": {"seed": 1}, "voice": {"reference_path": "ref.wav"}})
            self.assertEqual([name for name, _ in calls], ["generate", "voice"])
            self.assertEqual(result["workflow"], {"generate": "complete", "voice": "complete"})
            self.assertTrue((directory / "artifacts/workflow_result.json").is_file())

    def test_generation_failure_never_starts_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "outputs/jobs/20260911-000000-11111111"
            directory.mkdir(parents=True)
            with patch.object(workflow_worker, "run_stage", side_effect=RuntimeError("OOM")) as stage:
                with self.assertRaisesRegex(RuntimeError, "OOM"):
                    workflow_worker.run_workflow(root, JobContext(directory), {"generate": {}, "voice": {}})
                self.assertEqual(stage.call_count, 1)

    def test_final_child_status_is_read_after_process_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "outputs/jobs/20260911-000000-11111111"
            directory.mkdir(parents=True)
            child_status = directory / "artifacts/stages/generate/status.json"
            class Process:
                pid = 123
                returncode = 0
                def poll(self):
                    atomic_json(child_status, {"status": "complete", "stage": "complete", "result": {"audio": "ok"}})
                    return 0
            with patch.object(workflow_worker.subprocess, "Popen", return_value=Process()):
                result = workflow_worker.run_stage(root, JobContext(directory), "generate", "generate", {})
            self.assertEqual(result, {"audio": "ok"})

    def test_voice_resume_checks_source_models_and_file_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            previous = Path(temporary) / "previous"
            output = Path(temporary) / "output"
            previous.mkdir(); output.mkdir()
            (previous / "vocal.wav").write_bytes(b"audio")
            models, source = {"model": "revision"}, {"song_sha256": "abc"}
            write_artifact_manifest(previous, "separation_manifest.json", "yue2-voice-separation-v1",
                                    ["vocal.wav"], models=models, source=source)
            self.assertFalse(restore_voice_stage(previous, output, "separation", ["vocal.wav"], models, {"song_sha256": "other"}))
            self.assertTrue(restore_voice_stage(previous, output, "separation", ["vocal.wav"], models, source))
            (previous / "vocal.wav").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                restore_voice_stage(previous, output, "separation", ["vocal.wav"], models, source)


if __name__ == "__main__":
    unittest.main()
