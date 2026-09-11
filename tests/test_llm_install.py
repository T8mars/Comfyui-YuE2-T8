import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.install_llm import download


class DownloadTests(unittest.TestCase):
    def test_resume_joins_existing_bytes_then_verifies_whole_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'wheel.whl'
            partial = path.with_suffix('.whl.partial')
            partial.write_bytes(b'first-')
            response = io.BytesIO(b'second')
            response.status, response.headers = 206, {'Content-Range': 'bytes 6-11/12'}
            with patch('urllib.request.urlopen', return_value=response) as open_url:
                download('https://example.invalid/wheel', path, hashlib.sha256(b'first-second').hexdigest())
            self.assertEqual(open_url.call_args.args[0].get_header('Range'), 'bytes=6-')
            self.assertEqual(path.read_bytes(), b'first-second')
            self.assertFalse(partial.exists())

    def test_mismatched_resume_keeps_partial_and_does_not_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'wheel.whl'
            partial = path.with_suffix('.whl.partial')
            partial.write_bytes(b'first-')
            response = io.BytesIO(b'wrong-data')
            response.status, response.headers = 206, {'Content-Range': 'bytes 3-11/12'}
            with patch('urllib.request.urlopen', return_value=response), self.assertRaisesRegex(RuntimeError, 'offset'):
                download('https://example.invalid/wheel', path, 'unused')
            self.assertEqual(partial.read_bytes(), b'first-')
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
