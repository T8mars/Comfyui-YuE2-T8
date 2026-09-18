import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor'))
from yue2.modeling_yue2 import YuE2Config, YuE2ForCausalLM
from yue2.nar import synthesize
from yue2.offloading import use_cpu_offload, enable_cpu_offload, disable_cpu_offload, execution_device
from yue2.sampling import generate_tokens
from yue2.protocol import Sampling


class CpuOffloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(2)

    def model(self):
        torch.manual_seed(47)
        return YuE2ForCausalLM(YuE2Config(hidden_size=16, num_hidden_layers=2, num_attention_heads=2,
                                        num_key_value_heads=1, head_dim=8, intermediate_size=32,
                                        max_position_embeddings=256, max_latent_frames=256)).eval()

    def test_policy_keeps_normal_gpu_and_uses_requested_and_physical_limits(self):
        for mode, budget, physical, expected in [('auto',24,24,False),('auto',8,24,True),
                                                ('auto',24,8,True),('gpu',8,8,False),('cpu-offload',24,24,True)]:
            with self.subTest(mode=mode,budget=budget,physical=physical):
                self.assertEqual(use_cpu_offload(mode,'cuda',budget,physical), expected)
        self.assertFalse(use_cpu_offload('auto','cpu',8,8))
        self.assertFalse(use_cpu_offload('auto','cuda',8,8,quantization='fp8'))
        for mode, backend, quantization in [('invalid','torch','none'),('cpu-offload','vllm','none'),('cpu-offload','torch','fp8')]:
            with self.assertRaises(ValueError):
                use_cpu_offload(mode,'cuda',8,8,backend,quantization)

    def test_ar_logits_and_sampling_are_preserved_and_exact_weights_restore(self):
        model = self.model()
        before = {name:value.detach().clone() for name,value in model.state_dict().items()}
        ids = torch.tensor([[1,2,3]])
        with torch.inference_mode():
            logits = model(ids).logits.clone()
        expected, _, _ = generate_tokens(model,[1,2,3],Sampling(min_tokens=2,max_tokens=3,temperature=0),47,'semantic',legacy_off=True,use_cuda_graph=False)
        enable_cpu_offload(model,'cpu')
        self.assertEqual(execution_device(model),torch.device('cpu'))
        self.assertTrue(any(parameter.device.type=='meta' for parameter in model.parameters()))
        with torch.inference_mode():
            torch.testing.assert_close(model(ids).logits,logits,atol=0,rtol=0)
        actual, _, _ = generate_tokens(model,[1,2,3],Sampling(min_tokens=2,max_tokens=3,temperature=0),47,'semantic',legacy_off=True,use_cuda_graph=True)
        self.assertEqual(actual,expected)
        disable_cpu_offload(model)
        for name,value in model.state_dict().items():
            torch.testing.assert_close(value,before[name],atol=0,rtol=0)
        self.assertFalse(any(hasattr(module,'_hf_hook') for module in model.modules()))
        enable_cpu_offload(model,'cpu'); disable_cpu_offload(model)

    def test_cached_nar_uses_execution_device_and_stage_offload_preserves_latents(self):
        model = self.model()
        baseline = synthesize(model,[1,2,3],[4,5,6],47,steps=2,context=128,offload_ar=True,query_chunk_size=4)
        enable_cpu_offload(model,'cpu')
        actual = synthesize(model,[1,2,3],[4,5,6],47,steps=2,context=128,offload_ar=True,query_chunk_size=4)
        torch.testing.assert_close(actual,baseline,atol=0,rtol=0)
        disable_cpu_offload(model)

    def test_cancelled_offloaded_sampling_can_restore_and_reuse_model(self):
        model = enable_cpu_offload(self.model(),'cpu')
        with self.assertRaises(InterruptedError):
            generate_tokens(model,[1],Sampling(min_tokens=0,max_tokens=3),47,'semantic',cancelled=lambda:True)
        disable_cpu_offload(model)
        self.assertTrue(all(parameter.device.type=='cpu' for parameter in model.parameters()))

    def test_forward_failure_restores_materialized_weights_and_hooks_for_reuse(self):
        model = self.model()
        ids = torch.tensor([[1,2,3]])
        with torch.inference_mode():
            expected = model(ids).logits.clone()
        enable_cpu_offload(model, 'cpu')
        projection = model.model.layers[0].self_attn.q_proj
        with patch.object(projection, '_old_forward', side_effect=RuntimeError('injected device failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected device failure'), torch.inference_mode():
                model(ids)
        disable_cpu_offload(model)
        self.assertFalse(any(hasattr(module, '_hf_hook') for module in model.modules()))
        with torch.inference_mode():
            torch.testing.assert_close(model(ids).logits, expected, atol=0, rtol=0)


if __name__ == '__main__':
    unittest.main()
