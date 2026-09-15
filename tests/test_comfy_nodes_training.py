import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_nodes(relative="nodes.py"):
    root = Path(__file__).resolve().parents[1]
    package_name = "yue2_node_feature_test_" + relative.replace("/", "_").replace(".", "_")
    package = types.ModuleType(package_name)
    package.__path__ = [str((root / relative).parent)]
    with patch.dict(sys.modules, {package_name: package}):
        spec = importlib.util.spec_from_file_location(package_name + ".nodes", root / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
    return module


class ComfyTrainingNodes(unittest.TestCase):
    def setUp(self):
        self.nodes = load_nodes()
        self.model = {"backend": "torch-eager", "memory_budget_gib": 12,
                      "offload_ar": True, "nar_attention": "sdpa", "nar_query_chunk_size": 256}
        self.ident = "a" * 32

    def test_root_and_installable_node_sources_match(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "nodes.py").read_bytes(), (root / "comfyui_nodes/nodes.py").read_bytes())

    def test_lora_handle_and_strength_reach_generation(self):
        complete = {"id": "job", "result": {"audio": "song.wav", "artifact_dir": "artifacts"}}
        with patch.object(self.nodes.client, "run", return_value=complete) as run, \
             patch.object(self.nodes, "audio_value", return_value="audio"):
            result = self.nodes.YuE2GenerateSong().generate(
                self.model, "style", "lyrics", "off", 1, 1, 1,
                style_model_scale=0.4, trained_style_model={"asset_id": self.ident})
        self.assertEqual(result[0], "audio")
        self.assertEqual(run.call_args.args[1]["style_model_asset_id"], self.ident)
        self.assertEqual(run.call_args.args[1]["style_model_scale"], 0.4)
        with self.assertRaisesRegex(ValueError, "只支持直接生成"):
            self.nodes.YuE2GenerateSong().generate(
                self.model, "style", "lyrics", "full", 1, 1, 1,
                trained_style_model={"asset_id": self.ident})
        with self.assertRaisesRegex(ValueError, "尚未产生歌曲风格模型"):
            self.nodes.YuE2GenerateSong().generate(
                self.model, "style", "lyrics", "off", 1, 1, 1,
                trained_style_model={"asset_id": ""})

    def test_yue2_training_output_connects_to_generation(self):
        health = {"ready": {"capabilities": {"yue2_training": True}}}
        prepared = {"id": "prepare", "result": {"run_id": self.ident}}
        trained = {"id": "train", "result": {"model_asset": {"id": self.ident, "title": "风格"}}}
        with patch.object(self.nodes.client, "ensure_service", return_value=health), \
             patch.object(self.nodes.client, "request", return_value={"config": {}}), \
             patch.object(self.nodes.client, "run", side_effect=[prepared, trained]) as run:
            handle, _ = self.nodes.YuE2TrainStyle().train(
                f"训练 [{self.ident}]", "预处理并训练", True)
        self.assertEqual(handle["asset_id"], self.ident)
        self.assertEqual([call.args[0] for call in run.call_args_list], ["yue2_prepare", "yue2_train"])

    def test_rvc_train_and_cover_use_registered_voice(self):
        health = {"ready": {"capabilities": {
            "rvc_training": True, "rvc_inference": True, "vocal_separation": True}}}
        voice = {"id": self.ident, "name": "测试音色", "speakers": [{"id": 7, "name": "歌手"}]}
        trained = {"id": "train", "result": {"voice": voice}}
        covered = {"id": "cover", "result": {"audio": "cover.wav"}}
        with patch.object(self.nodes.client, "ensure_service", return_value=health), \
             patch.object(self.nodes.client, "request", return_value={"ready": True, "errors": []}), \
             patch.object(self.nodes.client, "run", return_value=trained):
            handle, _ = self.nodes.YuE2RVCTrain().train(f"项目 [{self.ident}]", True)
        self.assertEqual(handle["speaker_id"], 7)
        with patch.object(self.nodes.client, "ensure_service", return_value=health), \
             patch.object(self.nodes, "save_comfy_audio", return_value=Path("source.wav")), \
             patch.object(self.nodes, "audio_value", return_value="audio"), \
             patch.object(self.nodes.client, "run", return_value=covered) as run:
            audio, _, _ = self.nodes.YuE2RVCCover().convert(handle, {}, -12, 0.75, 0.33, 0, 0)
        self.assertEqual(audio, "audio")
        payload = run.call_args.args[1]
        self.assertEqual(payload["voice_id"], self.ident)
        self.assertEqual(payload["speaker_id"], 7)
        self.assertEqual(payload["rvc_pitch_shift"], -12)

    def test_all_new_nodes_are_exported(self):
        expected = {"YuE2RVCVoiceLoader", "YuE2RVCCover", "YuE2RVCTrain", "YuE2TrainStyle"}
        self.assertTrue(expected <= self.nodes.NODE_CLASS_MAPPINGS.keys())


if __name__ == "__main__":
    unittest.main()
