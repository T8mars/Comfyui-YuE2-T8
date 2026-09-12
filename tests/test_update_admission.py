"""Update downloads and GPU job admission must reserve the same idle installation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.yue2_app import service


class UpdateAdmission(unittest.TestCase):
    def make_store(self):
        store = service.JobStore.__new__(service.JobStore)
        store.lock = threading.RLock()
        store.storage_lock = threading.RLock()
        store.updating = False
        store.jobs = {}
        def create(*args, **kwargs):
            store.jobs['job'] = {'status': 'queued'}
            return store.jobs['job']
        store._create = create
        return store

    def test_concurrent_update_and_job_cannot_both_enter(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(service, 'ROOT', Path(temporary)):
            for _ in range(20):
                (Path(temporary) / 'logs/update-status.json').unlink(missing_ok=True)
                store = self.make_store()
                ready = threading.Barrier(2)
                def attempt(update):
                    ready.wait()
                    try:
                        store.begin_update() if update else store.create('doctor', {})
                        return True
                    except ValueError:
                        return False
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(attempt, (True, False)))
                self.assertEqual(sum(results), 1)
                self.assertFalse(store.updating and store.jobs)

    def test_duplicate_update_rejected_and_download_failure_unlocks(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(service, 'ROOT', Path(temporary)):
            store = self.make_store()
            store.begin_update()
            with self.assertRaises(ValueError):
                store.begin_update()
            with self.assertRaises(ValueError):
                store.create('doctor', {})
            store.abort_update(ValueError('download failed'))
            self.assertEqual(store.create('doctor', {})['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
