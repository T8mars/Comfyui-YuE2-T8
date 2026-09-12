import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.apply_update import apply_files


class UpdateInstallTests(unittest.TestCase):
    def test_staged_updater_preserves_large_user_directories_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "install"
            source = target / "cache/updates/1.2.2-test"
            for name, content in {
                "app/yue2_app/service.py": b"new service",
                "scripts/apply_update.py": b"new helper",
                "models/model.bin": b"must stay",
            }.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            old = target / "app/yue2_app/service.py"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(b"old service")
            model = target / "models/model.bin"
            model.parent.mkdir(parents=True, exist_ok=True)
            model.write_bytes(b"user model")
            backup, records = apply_files(source, target, "1.2.2")
            self.assertEqual(old.read_bytes(), b"new service")
            self.assertEqual(model.read_bytes(), b"user model")
            self.assertEqual((backup / "app/yue2_app/service.py").read_bytes(), b"old service")
            self.assertTrue(any(item["file"] == "scripts/apply_update.py" for item in records))

    def test_retired_installer_leaves_legacy_installation_and_data_unchanged(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            originals = {
                "app/yue2_app/service.py": b"old service",
                "nodes.py": b"old root nodes",
                "comfyui_nodes/nodes.py": b"old bundled nodes",
                "comfyui_nodes/yue2_home.txt": b"user configured home",
                "models/user-model.bin": b"user model",
                "outputs/jobs/user-record.json": b"user result",
                "uploads/user.wav": b"user audio",
                "settings.json": b'{"model_directory":"custom"}',
                "userdata/assistant/config.json": b'{"provider":"local","credential_id":"reference-only"}',
                "userdata/assistant/drafts/create.json": b'{"revision":7,"draft":{"lyrics":"my work"}}',
                "runtime/llm/user-runtime-file": b"user installed runtime",
                "models/LLM/custom.gguf": b"user weights",
            }
            for name, content in originals.items():
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            result = subprocess.run([sys.executable, "-X", "utf8", str(source / "scripts/install_memory_fix.py"), str(target)],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 2)
            self.assertIn("未修改任何文件", result.stderr)
            for name, content in originals.items():
                self.assertEqual((target / name).read_bytes(), content)
            self.assertFalse((target / "logs/backups").exists())



if __name__ == "__main__":
    unittest.main()
