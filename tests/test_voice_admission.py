import json
from pathlib import Path
import tempfile
import queue
import threading
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from app.yue2_app import service


class VoiceAdmission(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'uploads').mkdir()
        (self.root/'outputs/jobs').mkdir(parents=True)
        self.audio = self.root/'uploads/source.wav'
        sf.write(self.audio,np.zeros(32000),16000)
        self.store = service.JobStore.__new__(service.JobStore)
        self.store.lock = threading.RLock()
        self.store.storage_lock = threading.RLock()
        self.store.jobs = {}
        self.store.updating = False
        self.store.pending = queue.Queue()
        self.capabilities = dict(rvc_inference=True,vocal_separation=True,voice_conversion=True,generation=True)
        for name, value in [('ROOT',self.root),('OUTPUTS',self.root/'outputs/jobs')]:
            patcher = patch.object(service,name,value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(service,'runtime_ready',side_effect=lambda:{'capabilities':self.capabilities})
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch('app.yue2_app.rvc_library.verify_voice',return_value={'indices':{'1':'index'}})
        patcher.start(); self.addCleanup(patcher.stop)

    def request(self):
        return {'backend':'compare','speaker_id':1,'voice_id':'test',
                'source_path':str(self.audio),'reference_path':str(self.audio)}

    def test_compare_validates_both_backends_before_queue(self):
        for capability in ('rvc_inference','vocal_separation','voice_conversion'):
            with self.subTest(capability=capability):
                self.capabilities[capability] = False
                with self.assertRaises(ValueError):
                    self.store.create('voice_convert',self.request())
                self.capabilities[capability] = True
        self.assertFalse(self.store.jobs)

    def test_bad_reference_rejected_before_generating_song(self):
        request = self.request()
        request['reference_path'] = str(self.root/'missing.wav')
        with self.assertRaisesRegex(ValueError,'参考音色'):
            self.store.create('reference_cover',{'generate':{},'voice':request})
        self.assertFalse(self.store.jobs)

    def test_valid_direct_compare_queues_without_lyrics_or_abc(self):
        job = self.store.create('voice_convert',self.request())
        saved = json.loads((self.root/'outputs/jobs'/job['id']/'job.json').read_text())
        self.assertEqual(saved['request']['backend'],'compare')
        self.assertNotIn('generate',saved['request'])
        self.assertEqual(job['result_panel'],'cover')

    def test_storage_migration_and_other_jobs_exclude_each_other(self):
        for kind, existing in [('rvc_storage_move','doctor'),('doctor','rvc_storage_move')]:
            for status in ('queued','running','cancelling'):
                self.store.jobs = {'active':{'kind':existing,'status':status}}
                with self.assertRaisesRegex(ValueError,'独占任务队列'):
                    self.store.create(kind,{})


if __name__ == '__main__':
    unittest.main()
