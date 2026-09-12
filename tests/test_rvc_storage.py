from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.yue2_app.io import atomic_json
from app.yue2_app.rvc_library import locations, storage_settings
from app.yue2_app.rvc_projects import get_project, save_project
from app.yue2_app.rvc_storage import migrate, preview
from app.yue2_app.worker_common import JobContext, Cancelled


class StorageMigration(unittest.TestCase):
    def fixture(self, root):
        ident = 'a'*32
        path = root/'userdata/rvc/datasets'/ident/'voice.wav'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original audio')
        atomic_json(root/'userdata/rvc/projects'/ident/'project.json',
                    {'id':ident,'materials':[{'path':str(path)}]})
        voice = root/'userdata/rvc/voices'/('b'*32)
        voice.mkdir(parents=True)
        (voice/'model.pth').write_bytes(b'user model')
        return ident, path

    def context(self, root, name):
        path = root/'outputs/jobs'/name
        path.mkdir(parents=True)
        return JobContext(path)

    def destinations(self, root):
        return {kind:str(root/'relocated'/kind) for kind in ('projects','datasets','voices')}

    def test_all_data_verified_and_material_paths_follow_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ident, original = self.fixture(root)
            result = migrate(root, {'directories':self.destinations(root)}, self.context(root,'first'))
            self.assertTrue(result['migration_committed'])
            self.assertEqual(len(result['backups']),3)
            self.assertTrue(original.exists(), 'Original backup was deleted')
            moved = Path(get_project(root, ident)['materials'][0]['path'])
            self.assertEqual(moved.read_bytes(), original.read_bytes())
            self.assertIn(locations(root)['datasets'], moved.parents)

    def test_cancel_then_resume_keeps_original_settings_until_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.fixture(root)
            first = self.context(root,'first')
            original_update = first.update
            def interrupt(stage, **extra):
                original_update(stage, **extra)
                if stage == 'rvc_storage_copy':
                    first.cancel_path.touch()
            first.update = interrupt
            with self.assertRaises(Cancelled):
                migrate(root, {'directories':self.destinations(root)}, first)
            self.assertEqual(storage_settings(root),{})
            second = self.context(root,'second')
            result = migrate(root, {'resume_from':str(first.job_dir)}, second)
            self.assertTrue(result['migration_committed'])

    def test_failed_settings_commit_resumes_promoted_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.fixture(root)
            first = self.context(root,'first')
            def fail_settings(path,value):
                if path == root/'userdata/rvc/settings.json':
                    raise OSError('injected settings write failure')
                atomic_json(path,value)
            with patch('app.yue2_app.rvc_storage.atomic_json', side_effect=fail_settings):
                with self.assertRaises(OSError):
                    migrate(root, {'directories':self.destinations(root)}, first)
            self.assertEqual(storage_settings(root),{})
            result = migrate(root, {'resume_from':str(first.job_dir)}, self.context(root,'second'))
            self.assertTrue(result['migration_committed'])

    def test_refuses_overlap_and_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.fixture(root)
            for target in (root, root/'runtime/new', root/'userdata/rvc/datasets/nested'):
                with self.assertRaises(ValueError):
                    preview(root, {'datasets':str(target)})
            occupied = root/'occupied'
            occupied.mkdir()
            (occupied/'keep.txt').write_text('keep')
            with self.assertRaises(ValueError):
                preview(root, {'datasets':str(occupied)})
            self.assertEqual((occupied/'keep.txt').read_text(),'keep')

    def test_original_material_records_survive_multiple_moves(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ident, original = self.fixture(root)
            migrate(root, {'directories':self.destinations(root)}, self.context(root,'first'))
            nested = root/'userdata/rvc/datasets/new-location'
            migrate(root, {'directories':{'datasets':str(nested)}}, self.context(root,'second'))
            moved = Path(get_project(root, ident)['materials'][0]['path'])
            self.assertEqual(moved, nested/ident/'voice.wav')
            self.assertEqual(moved.read_bytes(), original.read_bytes())
            save_project(root, get_project(root, ident))
            third = root/'third-dataset'
            migrate(root, {'directories':{'datasets':str(third)}}, self.context(root,'third'))
            self.assertEqual(Path(get_project(root, ident)['materials'][0]['path']), third/ident/'voice.wav')

    def test_changed_original_cannot_resume_a_stale_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, original = self.fixture(root)
            first = self.context(root,'first')
            with patch('app.yue2_app.rvc_storage.sha256', side_effect=Cancelled('cancel')):
                with self.assertRaises(Cancelled):
                    migrate(root, {'directories':self.destinations(root)}, first)
            original.write_bytes(b'new audio after cancellation')
            with self.assertRaisesRegex(ValueError, '原目录发生改变'):
                migrate(root, {'resume_from':str(first.job_dir)}, self.context(root,'second'))
            self.assertEqual(storage_settings(root),{})


if __name__ == '__main__':
    unittest.main()
