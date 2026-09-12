import importlib.util
import json
import os
import py_compile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('studio_update_helper', ROOT / 'scripts/apply_update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class UpdateTransaction(unittest.TestCase):
    def test_equal_size_and_timestamp_cannot_reuse_old_bytecode(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source, target = base / 'source', base / 'target'
            source.mkdir(); target.mkdir()
            new, old = source / 'version.py', target / 'version.py'
            old.write_text('version = "1.2.2"\n')
            new.write_text('version = "9.8.7"\n')
            os.utime(new, (old.stat().st_atime, old.stat().st_mtime))
            py_compile.compile(str(old), doraise=True)
            backup, records = updater.apply_files(source, target, '9.8.7')
            def loaded_version():
                spec = importlib.util.spec_from_file_location('fixture_version', old)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.version
            self.assertEqual(loaded_version(), '9.8.7')
            updater.rollback(target, backup, records)
            self.assertEqual(loaded_version(), '1.2.2')

    def test_failed_post_copy_verification_restores_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source, target = base / 'source', base / 'target'
            source.mkdir(); target.mkdir()
            (source / 'module.py').write_text('new')
            (target / 'module.py').write_text('old')
            original_digest = updater.digest
            def invalid_destination(path):
                return 'bad-digest' if path == target / 'module.py' else original_digest(path)
            with patch.object(updater, 'digest', side_effect=invalid_destination):
                with self.assertRaises(RuntimeError):
                    updater.apply_files(source, target, '9.8.7')
            self.assertEqual((target / 'module.py').read_text(), 'old')

    def test_user_data_and_private_roadmap_are_never_update_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source, target = base / 'source', base / 'target'
            source.mkdir(); target.mkdir()
            for relative in ('roadmap.md', 'nested/ROADMAP.MD', 'userdata/voice.pth', 'settings.json', 'models/model.bin'):
                file = source / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('must not be copied')
            (source / 'app.py').write_text('app')
            backup, records = updater.apply_files(source, target, '9.8.7')
            self.assertEqual([record['file'] for record in records], ['app.py'])
            updater.rollback(target, backup, records)
            self.assertFalse((target / 'app.py').exists())

    def test_runtime_and_model_updates_are_detected_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source, target = base / 'source', base / 'target'
            source.mkdir(); (target / 'runtime').mkdir(parents=True)
            lock = source / 'requirements-unified.lock.txt'
            lock.write_text('torch==2.10.0+cu128')
            (target / 'runtime/python.exe').write_bytes(b'fixture')
            state = target / 'runtime/installed.json'
            state.write_text('broken json')
            self.assertEqual(updater.update_requirements(target, source), (True, False))
            state.write_text(json.dumps({'layout':'unified','runtime_lock_sha256':updater.digest(lock)}))
            (source / 'app/yue2_app').mkdir(parents=True)
            (source / 'app/yue2_app/rvc_assets.json').write_text('{}')
            self.assertEqual(updater.update_requirements(target, source), (False, True))
            crt = source / 'vendor/msvc-runtime/manifest.json'
            crt.parent.mkdir(parents=True)
            crt.write_text('{"version":"fixture"}')
            self.assertEqual(updater.update_requirements(target, source), (True, True))
            state.write_text(json.dumps({'layout':'unified','runtime_lock_sha256':updater.digest(lock),
                                        'msvc_runtime_manifest_sha256':updater.digest(crt)}))
            self.assertEqual(updater.update_requirements(target, source), (False, True))


if __name__ == '__main__':
    unittest.main()
