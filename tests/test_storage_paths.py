"""Checkpoint I/O under the legacy Windows path budget, without GPU work."""
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import numpy as np

from app.yue2_app.core_worker import atomic_stage
from app.yue2_app.io import atomic_json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("yue2_storage_paths", ROOT / "vendor/yue2/storage.py")
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


@contextmanager
def legacy_path_budget(seen):
    """Enforce MAX_PATH even on hosts whose OS/Python already allow long paths."""
    def guarded(function, positions):
        def call(*args, **kwargs):
            for index in positions:
                if len(args) > index and not isinstance(args[index], int):
                    path = os.path.abspath(os.fsdecode(args[index]))
                    seen.append(path)
                    if len(path) >= 260:
                        raise FileNotFoundError(2, "Legacy Windows path limit", path)
            return function(*args, **kwargs)
        return call
    with patch("os.open", guarded(os.open, (0,))), \
            patch("io.open", guarded(io.open, (0,))), \
            patch("os.mkdir", guarded(os.mkdir, (0,))), \
            patch("os.replace", guarded(os.replace, (0, 1))):
        yield


class StoragePathTests(unittest.TestCase):
    def checkpoint_parent(self, root, length):
        suffix = Path("outputs/jobs/20260913-012932-c024eece/artifacts/stages/generate/artifacts/song/checkpoints")
        padding = length - len(str(root / suffix)) - 1
        self.assertGreater(padding, 0)
        parent = root / ("p" * padding) / suffix
        self.assertEqual(len(str(parent)), length)
        return parent

    def test_reported_nested_checkpoint_fits_legacy_budget(self):
        reported = PureWindowsPath(
            r"C:\Users\chenm\Downloads\YuE2-T8-Local-v1.2.0-Windows-NVIDIA-20260911"
            r"\outputs\jobs\20260913-012932-c024eece\artifacts\stages\generate"
            r"\artifacts\song\checkpoints\semantic.tmp-6ff38922e3404f72add2623c06e3c6a4"
            r"\semantic.json.3424.0ef80dbd7ea74e87bf95db7380a98f35.tmp")
        self.assertEqual(len(str(reported)), 262)
        for parent_length in (160, 220):
            with self.subTest(parent_length=parent_length), tempfile.TemporaryDirectory(dir=ROOT) as directory:
                parent = self.checkpoint_parent(Path(directory), parent_length)
                parent.mkdir(parents=True)
                old = parent / ("semantic.tmp-" + "0" * 32) / ("semantic.json.3424." + "0" * 32 + ".tmp")
                seen = []
                with legacy_path_budget(seen):
                    # The former layout fails even though the final path fits.
                    with self.assertRaisesRegex(FileNotFoundError, "Legacy Windows path limit"):
                        io.open(old, "w")
                    seen.clear()
                    def save(path):
                        storage.write_json(path / "plan.json", {"lyrics": "测试歌词"})
                        np.save(path / "semantic.npy", np.array([5, 6, 7], dtype=np.int32))
                        atomic_json(path / "semantic.json", {"timing": {"seconds": 1}})
                        atomic_json(path / "semantic_manifest.json", {"complete": True})
                        self.assertFalse((parent / "semantic").exists())
                    atomic_stage(parent / "semantic", save)
                    self.assertEqual(json.loads((parent / "semantic/plan.json").read_text(encoding="utf-8"))["lyrics"], "测试歌词")
                    self.assertEqual(np.load(parent / "semantic/semantic.npy").tolist(), [5, 6, 7])
                    self.assertTrue(json.loads((parent / "semantic/semantic_manifest.json").read_text(encoding="utf-8"))["complete"])
                self.assertLess(max(map(len, seen)), 260)
                self.assertEqual([p.name for p in parent.iterdir()], ["semantic"])

    def test_json_serialization_failure_preserves_original_and_cleans_temp(self):
        for write in (atomic_json, storage.write_json):
            with self.subTest(writer=write.__name__), tempfile.TemporaryDirectory(dir=ROOT) as directory:
                path = Path(directory) / "semantic.json"
                write(path, {"complete": True})
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    write(path, {"invalid": float("nan")})
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])

    def test_failed_short_stage_does_not_replace_existing_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            destination = Path(directory) / "semantic"
            destination.mkdir()
            atomic_json(destination / "semantic.json", {"complete": True})
            def interrupted(path):
                atomic_json(path / "semantic.json", {"partial": True})
                raise InterruptedError("cancel")
            with self.assertRaises(InterruptedError):
                atomic_stage(destination, interrupted)
            self.assertTrue(json.loads((destination / "semantic.json").read_text(encoding="utf-8"))["complete"])
            self.assertEqual([p.name for p in destination.parent.iterdir()], ["semantic"])

    def test_short_json_names_remain_unique_for_concurrent_writers(self):
        for write in (atomic_json, storage.write_json):
            with self.subTest(writer=write.__name__), tempfile.TemporaryDirectory(dir=ROOT) as directory:
                path = Path(directory) / "semantic.json"
                errors = []
                def save(value):
                    try:
                        write(path, {"value": value})
                    except BaseException as exc:
                        errors.append(exc)
                threads = [threading.Thread(target=save, args=(value,)) for value in range(20)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertEqual(errors, [])
                self.assertIn(json.loads(path.read_text(encoding="utf-8"))["value"], range(20))
                self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])


if __name__ == "__main__":
    unittest.main()
