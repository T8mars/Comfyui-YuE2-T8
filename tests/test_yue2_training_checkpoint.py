import random
import tempfile
import unittest
from pathlib import Path


class TinyBlock:
    pass


def tiny_model():
    from torch import nn
    block = TinyBlock(); block.self_attn = TinyBlock(); block.mlp = TinyBlock()
    for name in ("q_proj", "k_proj", "v_proj", "o_proj"):
        setattr(block.self_attn, name, nn.Linear(4, 4, bias=False))
    block.mlp.gate_proj = nn.Linear(4, 6, bias=False)
    block.mlp.up_proj = nn.Linear(4, 6, bias=False)
    block.mlp.down_proj = nn.Linear(6, 4, bias=False)
    model = TinyBlock(); model.model = TinyBlock(); model.model.layers = [block]
    return model


class CheckpointTests(unittest.TestCase):
    def test_restores_parameters_optimizer_and_samplers(self):
        import numpy as np
        import torch
        from app.yue2_app.yue2_adapter import attach_ar_lora
        from app.yue2_app.yue2_trainer import load_training_checkpoint, save_training_checkpoint
        with tempfile.TemporaryDirectory() as temporary:
            model = tiny_model(); attached = attach_ar_lora(model, 2)
            params = [p for module in attached.values() for p in (module.A, module.B)]
            optimizer = torch.optim.AdamW(params, lr=1e-4)
            loss = sum(parameter.sum() for parameter in params); loss.backward(); optimizer.step()
            expected = [parameter.detach().clone() for parameter in params]
            sampler = random.Random(12); numpy_generator = np.random.default_rng(34)
            sampler.random(); numpy_generator.random()
            save_training_checkpoint(Path(temporary) / "step", attached=attached, optimizer=optimizer,
                                     step=7, rank=2, identity="fixed", sampler=sampler,
                                     numpy_generator=numpy_generator, history=[{"step": 7}],
                                     best_validation=1.25, metadata={})
            expected_python = sampler.random(); expected_numpy = numpy_generator.random()
            with torch.no_grad():
                for parameter in params: parameter.zero_()
            sampler.random(); numpy_generator.random()
            state = load_training_checkpoint(Path(temporary) / "step", attached=attached,
                                             optimizer=optimizer, identity="fixed", sampler=sampler,
                                             numpy_generator=numpy_generator)
            self.assertEqual(state["step"], 7)
            self.assertTrue(all(torch.equal(a, b) for a, b in zip(expected, params)))
            self.assertEqual(sampler.random(), expected_python)
            self.assertEqual(numpy_generator.random(), expected_numpy)

    def test_rejects_identity_change(self):
        from app.yue2_app.yue2_trainer import training_config
        self.assertEqual(training_config({"rank": 16})["mode"], "cot_off_ar_lora")
        with self.assertRaises(ValueError):
            training_config({"rank": 3})


if __name__ == "__main__":
    unittest.main()
