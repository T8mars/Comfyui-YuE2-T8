from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.yue2_app.asset_library import AssetLibrary
from app.yue2_app.training_cleanup import TrainingCleanup, cache_size
from app.yue2_app.library_cleanup import LibraryCleanup
from app.yue2_app.io import atomic_json
from app.yue2_app import service


class TrainingCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.library = AssetLibrary(self.root)
        self.cleanup = TrainingCleanup(self.library)
        self.outputs = self.root / 'outputs/jobs'
        self.outputs.mkdir(parents=True)
        self.store = service.JobStore.__new__(service.JobStore)
        self.store.lock = threading.Lock()
        self.store.storage_lock = threading.RLock()
        self.store.jobs = {}
        self.store.current_id = None
        self.store.assert_writable = lambda: None
        self.patches = [patch.object(service, 'ROOT', self.root), patch.object(service, 'OUTPUTS', self.outputs),
                        patch.object(service.assistant_data, 'all_drafts', return_value={})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def snapshot(self, manifest=None):
        ident = uuid.uuid4().hex
        with self.library.transaction() as db:
            db.execute('INSERT INTO dataset_snapshots VALUES(?,?,?,?,?,?)',
                       (ident, 'snapshot', 'yue2_style', json.dumps(manifest or {}), 'a' * 64, 1))
        return ident

    def make_run(self, state='complete', snapshot=None, config=None):
        run = self.library.create_training_run(title='training', training_kind='yue2_style',
                                              snapshot_id=snapshot or self.snapshot(), config=config or {'steps':200})
        self.library.update_training_run(run['id'], state=state, config=config or run['config'])
        directory = self.library.home / 'training' / run['id']
        checkpoint = directory / 'checkpoints/step-00000200'
        checkpoint.mkdir(parents=True)
        (checkpoint / 'optimizer.pt').write_bytes(b'training-cache')
        prepared = directory / 'prepared'
        prepared.mkdir()
        (prepared / 'tokens.npy').write_bytes(b'token-cache')
        return self.library.get_training_run(run['id']), directory

    def preview(self, *runs, **extra):
        return self.cleanup.preview({'run_ids':[run['id'] for run in runs], 'include_unused_snapshots':True, **extra})

    def delete(self, preview, **extra):
        return self.cleanup.purge({'run_ids':preview['run_ids'], 'snapshot_ids':preview['snapshot_ids'],
                                   'confirmed':True, **extra})

    def job(self, run, state='complete', kind='yue2_train'):
        ident = f'20990101-000000-{len(self.store.jobs)+1:08x}'
        directory = self.outputs / ident
        directory.mkdir()
        job = {'id':ident, 'kind':kind, 'request':{'run_id':run['id']}}
        status = {**job, 'status':state, 'resumable':True, 'result':{'audio':'saved-song.flac'}, 'created_at':1}
        atomic_json(directory / 'job.json', job)
        atomic_json(directory / 'status.json', status)
        self.store.jobs[ident] = status
        return ident

    def test_cache_snapshots_removed_sources_models_exports_preserved(self):
        source = self.library.create_text(kind='lyrics', title='source', text='original audio placeholder')
        snapshot = self.snapshot({'asset_id':source['id']})
        run, directory = self.make_run(snapshot=snapshot)
        model_path = self.root / 'model.safetensors'
        model_path.write_bytes(b'model')
        model = self.library.import_file(model_path, kind='model', title='trained')
        self.library.update_training_run(run['id'], model_asset_id=model['id'])
        exports = self.root / 'exports/song.flac'
        exports.parent.mkdir(); exports.write_bytes(b'export')
        trash = LibraryCleanup(self.library)
        trash.move([source['id']], 'trashed')
        self.assertFalse(trash.preview({'ids':[source['id']]})['deletable'])
        preview = self.preview(run)
        self.assertEqual(preview['bytes'], 25)
        self.assertEqual(preview['paths'][0]['checkpoints'], 1)
        result = self.delete(preview)
        self.assertEqual(result['deleted_runs'], [run['id']])
        self.assertEqual(result['deleted_snapshots'], [snapshot])
        self.assertFalse(directory.exists())
        self.assertEqual(self.library.revision_file(model['id'])[0].read_bytes(), b'model')
        self.assertTrue(self.library.revision_file(source['id'])[0].exists())
        self.assertEqual(trash.preview({'ids':[source['id']]})['deletable'], [source['id']])
        self.assertEqual(exports.read_bytes(), b'export')

    def test_shared_snapshot_survives_until_last_record_removed(self):
        snapshot = self.snapshot()
        first, _ = self.make_run(snapshot=snapshot)
        second, directory = self.make_run(snapshot=snapshot)
        preview = self.preview(first)
        self.assertFalse(preview['snapshot_ids'])
        self.assertTrue(preview['kept_snapshots'])
        self.delete(preview)
        self.assertEqual(self.library.count_snapshots(), 1)
        self.assertTrue(directory.exists())
        self.delete(self.preview(second))
        self.assertEqual(self.library.count_snapshots(), 0)

    def test_queued_preview_and_running_tasks_block_record_and_snapshot(self):
        run, directory = self.make_run()
        job_id = self.job(run, 'queued', 'yue2_preview')
        data = {'run_ids':[run['id']], 'snapshot_ids':[run['snapshot_id']], 'include_unused_snapshots':True}
        preview = self.store.training_cleanup(data)
        self.assertFalse(preview['run_ids'])
        self.assertFalse(preview['snapshot_ids'])
        self.assertTrue(directory.exists())
        self.store.current_id = job_id
        self.store.jobs[job_id]['status'] = 'complete'
        self.assertFalse(self.store.training_cleanup(data)['run_ids'])

    def test_paused_requires_explicit_discard_and_history_cannot_resume(self):
        run, directory = self.make_run(state='paused')
        job_id = self.job(run, 'paused')
        data = {'run_ids':[run['id']], 'include_unused_snapshots':True}
        self.assertFalse(self.store.training_cleanup(data)['run_ids'])
        data['discard_paused'] = True
        preview = self.store.training_cleanup(data)
        result = self.store.training_cleanup({**preview, 'discard_paused':True, 'confirmed':True}, execute=True)
        self.assertEqual(result['deleted_runs'], [run['id']])
        self.assertFalse(directory.exists())
        status = self.store.get(job_id)
        self.assertTrue(status['training_deleted'])
        self.assertFalse(status['resumable'])
        self.assertEqual(status['status'], 'cancelled')
        self.assertEqual(status['result']['audio'], 'saved-song.flac')
        with self.assertRaisesRegex(ValueError, '训练记录或缓存已清理'):
            self.store.resume(job_id)

    def test_stale_running_record_requires_verified_finished_current_job(self):
        run, directory = self.make_run(state='running')
        job_id = self.job(run, 'failed')
        data = {'run_ids':[run['id']]}
        self.assertFalse(self.store.training_cleanup(data)['run_ids'])
        self.library.update_training_run(run['id'], current_job_id=job_id)
        self.assertEqual(self.store.training_cleanup(data)['run_ids'], [run['id']])
        active_preview = self.job(run, 'queued', 'yue2_preview')
        self.assertFalse(self.store.training_cleanup(data)['run_ids'])
        self.store.jobs.pop(active_preview)
        self.store.current_id = job_id
        self.assertFalse(self.store.training_cleanup(data)['run_ids'])
        self.store.current_id = None
        result = self.store.training_cleanup({**data,'confirmed':True}, execute=True)
        self.assertEqual(result['deleted_runs'], [run['id']])
        self.assertFalse(directory.exists())

    def test_busy_file_keeps_retryable_record_and_snapshot_and_disallows_updates(self):
        run, directory = self.make_run()
        original = __import__('shutil').rmtree
        def busy(path, *args, **kwargs):
            if Path(path) == directory:
                (directory / 'prepared/tokens.npy').unlink()
                raise PermissionError('in use')
            return original(path, *args, **kwargs)
        with patch('app.yue2_app.training_cleanup.shutil.rmtree', busy):
            result = self.delete(self.preview(run))
        self.assertEqual(result['pending_runs'], [run['id']])
        self.assertEqual(self.library.count_snapshots(), 1)
        self.assertTrue(self.library.get_training_run(run['id'])['config']['cleanup_pending'])
        with self.assertRaisesRegex(ValueError, '部分清理'):
            self.library.update_training_run(run['id'], config={})
        self.assertTrue(self.cleanup.inventory()['items'][0]['cleanup_pending'])
        result = self.delete(self.preview(run))
        self.assertEqual(result['deleted_runs'], [run['id']])
        self.assertFalse(result['errors'])
        self.assertFalse(directory.exists())

    def test_approval_range_does_not_expand_after_protection_is_removed(self):
        snapshot = self.snapshot()
        first, _ = self.make_run(snapshot=snapshot)
        second, _ = self.make_run(snapshot=snapshot)
        preview = self.preview(first)
        self.delete(self.preview(second))
        result = self.delete(preview, include_unused_snapshots=True)
        self.assertFalse(result['deleted_snapshots'])
        self.assertEqual(self.library.count_snapshots(), 1)

    def test_draft_and_foreign_config_cache_references_fail_closed(self):
        run, directory = self.make_run()
        other, _ = self.make_run(config={'steps':200, 'foreign_cache':str(directory / 'prepared/tokens.npy')})
        self.assertFalse(self.preview(run)['run_ids'])
        self.delete(self.preview(other))
        self.assertFalse(self.cleanup.preview({'run_ids':[run['id']]}, protected=[run['id']])['run_ids'])
        self.assertFalse(self.cleanup.preview({'snapshot_ids':[run['snapshot_id']]}, protected=['*'])['snapshot_ids'])
        with patch.object(service.assistant_data, 'all_drafts', return_value={'global':{'create':{'error':'broken'}}}):
            self.assertFalse(self.store.training_cleanup({'run_ids':[run['id']]})['run_ids'])

    def test_pagination_and_orphan_snapshot_cleanup(self):
        snapshots = [self.snapshot() for _ in range(11)]
        first = self.cleanup.inventory(kind='snapshots')
        last = self.cleanup.inventory(kind='snapshots', offset=10)
        self.assertEqual((len(first['items']), len(last['items']), first['total']), (10, 1, 11))
        preview = self.cleanup.preview({'snapshot_ids':snapshots})
        result = self.delete(preview)
        self.assertEqual(len(result['deleted_snapshots']), 11)
        self.assertEqual(self.cleanup.inventory(kind='snapshots')['total'], 0)

    def test_validation_and_link_rejection_keep_external_data(self):
        run, directory = self.make_run()
        for data in ({'run_ids':['../escape'], 'confirmed':True}, {'run_ids':[run['id']]},
                     {'run_ids':[run['id']], 'discard_paused':'yes', 'confirmed':True}):
            with self.assertRaises(ValueError):
                self.cleanup.purge(data)
        outside = self.root / 'outside'; outside.mkdir(); (outside / 'keep').write_bytes(b'keep')
        link = directory / 'external'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('symlink creation unavailable')
        try:
            self.assertFalse(self.preview(run)['run_ids'])
            self.assertTrue((outside / 'keep').exists())
        finally:
            link.unlink()

    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive handle check')
    def test_windows_junction_cannot_delete_external_cache(self):
        import subprocess
        run, directory = self.make_run()
        outside = self.root / 'outside'; outside.mkdir(); (outside / 'keep').write_bytes(b'keep')
        link = directory / 'external'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True, check=True)
        try:
            self.assertFalse(self.preview(run)['run_ids'])
            self.assertTrue((outside / 'keep').exists())
        finally:
            link.rmdir()

    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive handle check')
    def test_real_windows_locked_checkpoint_can_retry(self):
        import ctypes
        from ctypes import wintypes
        run, directory = self.make_run()
        path = directory / 'checkpoints/step-00000200/optimizer.pt'
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            result = self.delete(self.preview(run))
            self.assertEqual(result['pending_runs'], [run['id']])
            self.assertTrue(path.exists())
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(self.delete(self.preview(run))['deleted_runs'], [run['id']])


if __name__ == '__main__':
    unittest.main()
