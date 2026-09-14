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
        from app.yue2_app.yue2_trainer import inspect_training_checkpoint, load_training_checkpoint, save_training_checkpoint
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
                                     best_validation=1.25, metadata={"training_identity": "fixed", "step": 7})
            checked = inspect_training_checkpoint(Path(temporary) / "step", identity="fixed", step=7)
            self.assertEqual(checked["adapter"]["rank"], 2)
            with self.assertRaisesRegex(ValueError, "训练记录"):
                inspect_training_checkpoint(Path(temporary) / "step", identity="other", step=7)
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
        config = training_config({"rank": 16})
        self.assertEqual(config["mode"], "cot_off_ar_lora")
        self.assertEqual(config["codec_window_tokens"], 768)
        with self.assertRaises(ValueError):
            training_config({"rank": 3})
        with self.assertRaises(ValueError):
            training_config({"codec_window_tokens": 128})

    def test_training_and_validation_windows_are_bounded_and_reproducible(self):
        import numpy as np
        from app.yue2_app.yue2_trainer import _training_codec_window, _validation_codec_windows
        codec = np.arange(2000)
        one = _training_codec_window(codec, 768, np.random.default_rng(17))
        two = _training_codec_window(codec, 768, np.random.default_rng(17))
        self.assertTrue(np.array_equal(one, two))
        self.assertEqual(len(one), 768)
        windows = _validation_codec_windows(codec, 768)
        self.assertEqual([int(item[0]) for item in windows], [0, 616, 1232])
        self.assertTrue(all(len(item) == 768 for item in windows))
        short = np.arange(200)
        self.assertIs(_training_codec_window(short, 768, np.random.default_rng(1)), short)
        self.assertIs(_validation_codec_windows(short, 768)[0], short)


if __name__ == "__main__":
    unittest.main()
