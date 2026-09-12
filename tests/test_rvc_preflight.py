import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.yue2_app.rvc_preflight import check_training


class TrainingPreflight(unittest.TestCase):
    def test_missing_base_models_and_low_disk_prevent_training(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': '-1'}):
            project = {'options': {'version': 'v1', 'sample_rate': '40k'},
                       'materials': [{'enabled': True, 'reviewed': True, 'duration': 600}]}
            with patch('app.yue2_app.rvc_preflight.shutil.disk_usage', return_value=SimpleNamespace(free=1024)):
                result = check_training(Path(temporary), project)
            self.assertFalse(result['ready'])
            self.assertTrue(any('空间不足' in error for error in result['errors']))
            self.assertTrue(any('pretrained/f0G40k.pth' in error for error in result['errors']))

    def test_short_confirmed_fixture_is_allowed_with_warning(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': '-1'}):
            root = Path(temporary)
            base = root / 'models/RVC/pretrained_v2'
            base.mkdir(parents=True)
            for name in ('f0G48k.pth', 'f0D48k.pth'):
                (base / name).write_bytes(b'fixture')
            project = {'options': {}, 'materials': [{'enabled': True, 'reviewed': True, 'duration': 12}]}
            with patch('app.yue2_app.rvc_preflight.shutil.disk_usage', return_value=SimpleNamespace(free=10*1024**3)):
                result = check_training(root, project)
            self.assertTrue(result['ready'])
            self.assertTrue(any('不足 5 分钟' in warning for warning in result['warnings']))
            project['materials'][0]['reviewed'] = False
            self.assertFalse(check_training(root, project)['ready'])


if __name__ == '__main__':
    unittest.main()
