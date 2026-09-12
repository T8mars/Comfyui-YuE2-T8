import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
import shutil

spec = importlib.util.spec_from_file_location('bundle_builder',Path(__file__).resolve().parents[1]/'scripts/build_unified_bundle.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
archive_relative,plain_files,runtime_files = builder.archive_relative,builder.plain_files,builder.runtime_files


class PortableBundleBoundary(unittest.TestCase):
    def test_archive_rejects_private_paths_roadmap_and_traversal(self):
        prefix = 'Release/'
        for name in ('roadmap.md','docs/ROADMAP.MD','userdata/voice.pth','cache/test','settings.json',
                     '../escape','C:escape','C:/escape','app\\escape'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                archive_relative(prefix+name,prefix)
        self.assertEqual(archive_relative(prefix+'app/web/index.html',prefix),Path('app/web/index.html'))

    def test_runtime_rejects_multiple_python_and_development_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crt = Path(__file__).resolve().parents[1]/'vendor/msvc-runtime/manifest.json'
            (root/'installed.json').write_text(json.dumps({'layout':'unified','python':'3.12.10',
                'msvc_runtime_manifest_sha256':builder.digest(crt)}))
            for entry in json.loads(crt.read_text(encoding='utf-8-sig'))['files']:
                shutil.copy2(crt.parent/entry['name'],root/entry['name'])
            (root/'python.exe').write_bytes(b'fixture, not executable')
            (root/'python312._pth').write_text('python312.zip\n.\nLib\\site-packages\n..\nimport site\n')
            files,_ = runtime_files(root)
            self.assertIn(root/'python.exe',files)
            (root/'voice').mkdir()
            (root/'voice/python.exe').write_bytes(b'old runtime')
            with self.assertRaisesRegex(ValueError,'one Python'):
                runtime_files(root)
            (root/'voice/python.exe').unlink()
            (root/'python312._pth').write_text('python312.zip\n.\nE:/development\nimport site\n')
            with self.assertRaisesRegex(ValueError,'nonportable'):
                runtime_files(root)

    def test_optional_gguf_and_cache_files_are_not_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('model.safetensors','personal.GGUF','compiled.pyc'):
                (root/name).write_bytes(b'fixture')
            (root/'.cache').mkdir()
            (root/'.cache/token').write_bytes(b'not published')
            self.assertEqual([p.name for p in plain_files(root)],['model.safetensors'])


if __name__=='__main__':
    unittest.main()
