import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('unified_installer', ROOT/'scripts/install_unified_runtime.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class LocalCrt(unittest.TestCase):
    def test_missing_corrupt_and_loaded_identical_dlls(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'missing or changed'):
                installer.local_crt(ROOT, runtime)
            expected = installer.local_crt(ROOT, runtime, install=True)
            with patch.object(installer.shutil, 'copy2', side_effect=PermissionError('loaded DLL')):
                self.assertEqual(installer.local_crt(ROOT, runtime, install=True), expected)
            (runtime/'msvcp140.dll').write_bytes(b'corrupt')
            with self.assertRaisesRegex(RuntimeError, 'msvcp140.dll'):
                installer.local_crt(ROOT, runtime)


if __name__ == '__main__':
    unittest.main()
