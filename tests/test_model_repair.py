import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.yue2_app.io import atomic_json
from app.yue2_app.model_repair import repair_vae
from app.yue2_app.model_verify import PINNED_MODELS
from app.yue2_app.service import acquire_instance_lock


class ModelRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.models = self.root/'shared models'
        atomic_json(self.root/'settings.json', {'schema': 1, 'model_directory': str(self.models)})
        self.payload = b'pinned decoder fixture'
        self.spec = copy.deepcopy(PINNED_MODELS['YuE2-Vae'])
        self.spec.update(sha256=hashlib.sha256(self.payload).hexdigest(), size=len(self.payload))
        patch = mock.patch.dict(PINNED_MODELS, {'YuE2-Vae': self.spec})
        patch.start()
        self.addCleanup(patch.stop)
        self.manifest = self.models/'MODEL_MANIFEST.json'
        atomic_json(self.manifest, {'bundle': 't8star/YuE2-Comfy', 'models': copy.deepcopy(PINNED_MODELS)})
        self.weight = self.models/self.spec['file']
        self.weight.parent.mkdir(parents=True)
        self.weight.write_bytes(self.payload)
        self.local_manifest = self.weight.parent/'weights_manifest.json'
        atomic_json(self.local_manifest, {'schema': 1, 'files': {'model.safetensors': {
            'sha256': self.spec['sha256'], 'bytes': self.spec['size']}}})

    def test_correct_weights_and_custom_model_directory_do_not_download(self):
        with mock.patch('urllib.request.urlopen') as download:
            result = repair_vae(self.root)
        download.assert_not_called()
        self.assertTrue(result['weight_valid'] and result['manifest_valid'])
        self.assertFalse(result['repaired'])
        self.assertFalse((self.root/'logs').exists())
        self.assertFalse((self.root/'models').exists())

    def test_corrupt_weight_is_verified_before_replacement_and_backed_up(self):
        previous = b'wrong decoder payload'
        self.weight.write_bytes(previous)
        with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(self.payload)) as download:
            result = repair_vae(self.root)
        self.assertIn('/94f6d64c1b53a1700e8f76c0c4faff8500be7780/YuE2-Vae/model.safetensors', download.call_args.args[0])
        self.assertEqual(self.weight.read_bytes(), self.payload)
        self.assertEqual((Path(result['backup'])/'model.safetensors').read_bytes(), previous)
        self.assertTrue(result['downloaded'] and result['repaired'])
        self.assertEqual(list(self.weight.parent.glob('*.tmp')), [])

    def test_bad_size_or_hash_download_preserves_original_files(self):
        for content in (b'x', b'x'*len(self.payload), self.payload+b'extra'):
            with self.subTest(length=len(content)):
                self.weight.write_bytes(b'previous decoder')
                before = [p.read_bytes() for p in (self.weight, self.manifest, self.local_manifest)]
                with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(content)), self.assertRaises(ValueError):
                    repair_vae(self.root)
                self.assertEqual(before, [p.read_bytes() for p in (self.weight, self.manifest, self.local_manifest)])
                self.assertEqual(list(self.weight.parent.glob('*.tmp')), [])

    def test_wrong_metadata_only_is_repaired_without_downloading_weight(self):
        manifest = json.loads(self.manifest.read_text('utf-8'))
        manifest['models']['YuE2-Vae']['sha256'] = '0'*64
        atomic_json(self.manifest, manifest)
        self.local_manifest.write_text('{}', encoding='utf-8')
        with mock.patch('urllib.request.urlopen') as download:
            result = repair_vae(self.root)
        download.assert_not_called()
        self.assertFalse(result['downloaded'])
        self.assertTrue(result['repaired'])
        self.assertEqual(json.loads(self.manifest.read_text('utf-8'))['models']['YuE2-Vae'], self.spec)

    def test_running_service_prevents_repair_and_download(self):
        self.weight.write_bytes(b'previous decoder')
        with acquire_instance_lock(self.root), mock.patch('urllib.request.urlopen') as download:
            with self.assertRaisesRegex(RuntimeError, '先关闭'):
                repair_vae(self.root)
        download.assert_not_called()
        self.assertEqual(self.weight.read_bytes(), b'previous decoder')

    def test_check_only_never_mutates_corrupt_installation(self):
        self.weight.write_bytes(b'previous decoder')
        with mock.patch('urllib.request.urlopen') as download:
            result = repair_vae(self.root, check_only=True)
        download.assert_not_called()
        self.assertFalse(result['weight_valid'])
        self.assertEqual(self.weight.read_bytes(), b'previous decoder')
        self.assertFalse((self.root/'logs').exists())


if __name__ == '__main__':
    unittest.main()
