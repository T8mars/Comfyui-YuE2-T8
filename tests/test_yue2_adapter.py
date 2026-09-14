import tempfile
import unittest
from pathlib import Path


class TinyBlock:
    pass


class AdapterTests(unittest.TestCase):
    def make_model(self):
        import torch
        from torch import nn
        block = TinyBlock()
        block.self_attn = TinyBlock()
        block.mlp = TinyBlock()
        for name in ("q_proj", "k_proj", "v_proj", "o_proj"):
            setattr(block.self_attn, name, nn.Linear(5, 5, bias=False))
        block.mlp.gate_proj = nn.Linear(5, 7, bias=False)
        block.mlp.up_proj = nn.Linear(5, 7, bias=False)
        block.mlp.down_proj = nn.Linear(7, 5, bias=False)
        model = TinyBlock()
        model.model = TinyBlock()
        model.model.layers = [block]
        return model

    def test_roundtrip_and_exact_merge_convention(self):
        import torch
        from app.yue2_app.yue2_adapter import (adapter_content_sha256, attach_ar_lora,
                                                load_adapter_into_wrappers, save_adapter)
        with tempfile.TemporaryDirectory() as temporary:
            model = self.make_model()
            attached = attach_ar_lora(model, 2)
            first = next(iter(attached.values()))
            with torch.no_grad():
                first.A.fill_(0.25)
                first.B.fill_(0.5)
            path = Path(temporary) / "adapter.safetensors"
            receipt = save_adapter(path, attached, rank=2, metadata={"snapshot": "abc"})
            with torch.no_grad():
                first.A.zero_(); first.B.zero_()
            loaded = load_adapter_into_wrappers(path, attached)
            self.assertEqual(loaded["rank"], 2)
            self.assertEqual(loaded["metadata"]["snapshot"], "abc")
            self.assertEqual(receipt["scaling_convention"], "weight_plus_scale_times_B_matmul_A")
            self.assertEqual(receipt["content_sha256"], adapter_content_sha256(path))
            self.assertTrue(torch.all(first.A == .25))
            self.assertTrue(torch.all(first.B == .5))

    def test_merge_changes_only_expected_weight(self):
        import torch
        from app.yue2_app.yue2_adapter import attach_ar_lora, save_adapter, merge_ar_adapter
        with tempfile.TemporaryDirectory() as temporary:
            training_model = self.make_model()
            attached = attach_ar_lora(training_model, 2)
            with torch.no_grad():
                for layer in attached.values():
                    layer.A.fill_(.2); layer.B.fill_(.3)
            path = Path(temporary) / "adapter.safetensors"
            save_adapter(path, attached, rank=2, metadata={})
            inference_model = self.make_model()
            before = inference_model.model.layers[0].self_attn.q_proj.weight.detach().clone()
            merge_ar_adapter(inference_model, path, scale=.5)
            delta = inference_model.model.layers[0].self_attn.q_proj.weight.detach() - before
            self.assertTrue(torch.allclose(delta, torch.full_like(delta, .06), atol=1e-6))

    def test_rejects_invalid_scale(self):
        from app.yue2_app.yue2_adapter import merge_ar_adapter
        with self.assertRaises(ValueError):
            merge_ar_adapter(self.make_model(), Path("missing"), scale=2.1)


if __name__ == "__main__":
    unittest.main()
