import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
import shutil

spec = importlib.util.spec_from_file_location('bundle_builder',Path(__file__).resolve().parents[1]/'scripts/build_unified_bundle.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
archive_relative,plain_files,runtime_files,scrub_paths = (builder.archive_relative,builder.plain_files,
                                                          builder.runtime_files,builder.scrub_paths)
release_spec = importlib.util.spec_from_file_location('release_builder',Path(__file__).resolve().parents[1]/'scripts/build_release.py')
release_builder = importlib.util.module_from_spec(release_spec)
release_spec.loader.exec_module(release_builder)


class PortableBundleBoundary(unittest.TestCase):
    def test_full_bundle_requires_all_integrated_music_models(self):
        for name in ('MuLaCover','HeartCodec-oss','Qwen3-Embedding-0.6B','SymbolicTranscriptor'):
            self.assertIn(name,builder.MODEL_DIRS)
        self.assertIn('MULACOVER_MODEL_MANIFEST.json',builder.MODEL_MANIFESTS)

    def test_archive_rejects_private_paths_roadmap_and_traversal(self):
        prefix = 'Release/'
        for name in ('roadmap.md','docs/ROADMAP.MD','userdata/voice.pth','cache/test','settings.json',
                     '../escape','/escape','C:escape','C:/escape','app\\escape'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                archive_relative(prefix+name,prefix)
        self.assertEqual(archive_relative(prefix+'app/web/index.html',prefix),Path('app/web/index.html'))

    def test_release_builder_rejects_windows_and_posix_traversal_on_every_os(self):
        for name in ('../escape', '/escape', 'C:escape', 'C:/escape', r'app\escape'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release_builder.safe_archive_path(name)
        self.assertEqual(release_builder.safe_archive_path('app/web/index.html'), Path('app/web/index.html'))

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
            browser = root/'playwright/browser'
            browser.mkdir(parents=True)
            (browser/'debug.log').write_text('mutable browser diagnostics')
            links = root/'playwright/.links'
            links.mkdir()
            (links/'installation').write_text('development install path')
            scripts = root/'Scripts'
            scripts.mkdir()
            (scripts/'pip.exe').write_bytes(b'absolute launcher')
            direct = root/'package.dist-info'
            direct.mkdir()
            (direct/'direct_url.json').write_text('{"url":"file:///private/build"}')
            files,_ = runtime_files(root)
            self.assertIn(root/'python.exe',files)
            self.assertNotIn(browser/'debug.log',files)
            self.assertNotIn(links/'installation',files)
            self.assertNotIn(scripts/'pip.exe',files)
            self.assertNotIn(direct/'direct_url.json',files)
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

    def test_public_reports_remove_plain_slash_and_json_escaped_build_paths(self):
        path = Path('E:/private/build').absolute()
        escaped = json.dumps(str(path))[1:-1]
        value = f'{path}\n{str(path).replace(chr(92), "/")}\n{escaped}'
        cleaned = scrub_paths(value,path)
        self.assertEqual(cleaned.count('%BUILD_PATH%'),3)
        self.assertNotIn('private',cleaned)


if __name__=='__main__':
    unittest.main()
