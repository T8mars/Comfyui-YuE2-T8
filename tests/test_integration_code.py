import json
import tempfile
import unittest
from pathlib import Path

from app.yue2_app.config import ROOT, model_paths
from app.yue2_app.io import atomic_json, public_job, within
from app.yue2_app.core_worker import generation_kwargs


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

    def test_path_boundary(self):
        base = ROOT / "outputs"
        self.assertEqual(within(base, base / "jobs"), (base / "jobs").resolve())
        with self.assertRaises(ValueError):
            within(base, ROOT / "models")

    def test_public_status_hides_command(self):
        self.assertNotIn("command", public_job({"id": "x", "command": ["secret"]}))

    def test_generation_request_modes(self):
        value = generation_kwargs({"style": "爵士", "lyrics": "词", "cot": "melody", "seed": 42})
        self.assertEqual(value["seed"], 42)
        self.assertEqual(value["cot"], "melody")

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
