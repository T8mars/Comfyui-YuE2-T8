import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))
from yue2.quantization import prepare_fp8_ar


class QuantizationGuardTests(unittest.TestCase):
    def test_fp8_is_rejected_on_hip_before_capability_probe(self):
        with patch("torch.version.hip", "6.4"), patch("torch.cuda.is_available", return_value=True), \
                patch("torch.cuda.get_device_capability") as capability:
            with self.assertRaisesRegex(RuntimeError, "HIP/ROCm"):
                prepare_fp8_ar(object(), "cuda")
            capability.assert_not_called()


if __name__ == "__main__":
    unittest.main()
