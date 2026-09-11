import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))
from yue2.nar import attention, song_chunks


class AttentionMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(2)

    def data(self, qlength, klength):
        generator = torch.Generator().manual_seed(47)
        return (torch.randn(qlength, 16, 8, generator=generator),
                torch.randn(klength, 8, 8, generator=generator),
                torch.randn(klength, 8, 8, generator=generator))

    def test_tiling_preserves_all_keys_and_absolute_causality(self):
        for causal, qlength, klength in [(False, 37, 71), (True, 37, 37), (False, 1, 71)]:
            q, k, v = self.data(qlength, klength)
            reference = attention(q, k, v, backend="math", causal=causal, query_chunk_size=qlength)
            stats = {}
            result = attention(q, k, v, backend="math", causal=causal, query_chunk_size=8, stats=stats)
            torch.testing.assert_close(result, reference, atol=2e-6, rtol=2e-5)
            self.assertLessEqual(stats["max_query_rows"], 8)

    def test_oom_reduces_block_and_preserves_result(self):
        q, k, v = self.data(37, 71)
        reference = attention(q, k, v, backend="math", query_chunk_size=37)
        original = torch.nn.functional.scaled_dot_product_attention
        calls = []
        def limited(query, *args, **kwargs):
            calls.append(query.shape[-2])
            if query.shape[-2] > 8:
                raise torch.OutOfMemoryError("simulated allocation pressure")
            return original(query, *args, **kwargs)
        stats = {}
        with patch("yue2.nar.F.scaled_dot_product_attention", side_effect=limited):
            result = attention(q, k, v, backend="math", query_chunk_size=32, stats=stats)
        self.assertEqual(stats["oom_retries"], 2)
        self.assertEqual(calls[:3], [32, 16, 8])
        torch.testing.assert_close(result, reference, atol=2e-6, rtol=2e-5)

    def test_oom_retries_are_bounded(self):
        q, k, v = self.data(37, 71)
        with patch("yue2.nar.F.scaled_dot_product_attention", side_effect=torch.OutOfMemoryError("full")) as call:
            with self.assertRaises(torch.OutOfMemoryError):
                attention(q, k, v, backend="math", query_chunk_size=32)
            self.assertEqual(call.call_count, 4)

    def test_cancel_between_query_blocks(self):
        q, k, v = self.data(37, 71)
        with patch("yue2.nar.F.scaled_dot_product_attention") as call:
            with self.assertRaises(InterruptedError):
                attention(q, k, v, cancelled=lambda: True)
            call.assert_not_called()

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA BF16 block-size regression")
    def test_cuda_bf16_query_sizes_128_256_512_match(self):
        torch.manual_seed(53)
        q = torch.randn(513, 16, 128, device="cuda", dtype=torch.bfloat16)
        k = torch.randn(1025, 8, 128, device="cuda", dtype=torch.bfloat16)
        v = torch.randn_like(k)
        reference = attention(q, k, v, backend="math", query_chunk_size=513)
        for rows in (128, 256, 512):
            stats = {}
            actual = attention(q, k, v, backend="math", query_chunk_size=rows, stats=stats)
            torch.testing.assert_close(actual, reference, atol=.002, rtol=.02)
            self.assertEqual(stats["max_query_rows"], rows)
            self.assertEqual(stats["min_query_rows"], 1)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA backend fallback probe")
    def test_unavailable_fused_kernel_uses_bounded_math(self):
        q = torch.randn(17, 16, 128, device="cuda", dtype=torch.bfloat16)
        k = torch.randn(31, 8, 128, device="cuda", dtype=torch.bfloat16)
        v = torch.randn_like(k)
        original = torch.nn.functional.scaled_dot_product_attention
        attempts = []
        def unavailable_once(query, *args, **kwargs):
            attempts.append(query.shape[-2])
            if len(attempts) == 1:
                raise RuntimeError("No available kernel. Aborting execution.")
            return original(query, *args, **kwargs)
        stats = {}
        with patch("torch.backends.cuda.can_use_flash_attention", return_value=False), \
                patch("torch.backends.cuda.can_use_cudnn_attention", return_value=True), \
                patch("yue2.nar.F.scaled_dot_product_attention", side_effect=unavailable_once):
            output = attention(q, k, v, query_chunk_size=8, stats=stats)
        self.assertTrue(torch.isfinite(output).all())
        self.assertEqual(stats["math"], 3)
        self.assertEqual(attempts, [8, 8, 8, 1])

    def test_original_song_boundaries_and_noise_are_unchanged(self):
        chunks = song_chunks([1] * 1802, [5] * 7506, 831001)
        self.assertEqual(len(chunks), 1)
        generator = torch.Generator().manual_seed(831001)
        torch.testing.assert_close(chunks[0].noise, torch.randn(7506, 64, generator=generator), rtol=0, atol=0)
        chunks = song_chunks([1] * 10, [4] * 19, 47, context=32)
        self.assertEqual([len(chunk.noise) for chunk in chunks], [9, 9, 1])
        generator = torch.Generator().manual_seed(47)
        torch.testing.assert_close(torch.cat([chunk.noise for chunk in chunks]),
                                   torch.randn(19, 64, generator=generator), rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
