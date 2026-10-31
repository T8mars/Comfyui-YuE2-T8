import json
import queue
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock

from app.yue2_app import service


class CoverSeedPersistenceTests(unittest.TestCase):
    def test_actual_seed_survives_queue_and_retry_for_both_cover_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = root/'outputs/jobs'; outputs.mkdir(parents=True)
            voice = root/'uploads/reference.wav'; voice.parent.mkdir()
            with wave.open(str(voice),'wb') as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
                stream.writeframes(b'\0\0'*8000)
            store = object.__new__(service.JobStore)
            store.updating = False
            store.lock = threading.RLock(); store.storage_lock = threading.RLock()
            store.jobs = {}; store.pending = queue.Queue()
            store.current_id = None; store.current_process = None
            with mock.patch.object(service,'ROOT',root), mock.patch.object(service,'OUTPUTS',outputs), \
                    mock.patch.object(service,'runtime_ready',return_value={
                        'capabilities':{'generation':True,'voice_conversion':True}}):
                for kind in ('generate','reference_cover'):
                    generation = {'style':'piano','lyrics':'Morning light','cot':'melody',
                                  'seed':9007199254740991,'abc':'L:1/8\nM:4/4\nK:C\nC8|'}
                    request = generation if kind == 'generate' else {
                        'generate':generation, 'voice':{'reference_path':str(voice)}}
                    first = store.create(kind,request,source='webui',client_request_id=kind)
                    saved = json.loads((outputs/first['id']/'job.json').read_text('utf-8'))
                    actual = saved['request'].get('generate',saved['request'])
                    self.assertEqual(actual['seed'],9007199254740991)
                    retry = store.create(kind,request,source='webui',client_request_id=kind)
                    self.assertEqual(first['id'],retry['id'])
                    self.assertTrue(retry['deduplicated'])
                fixed_zero = store.create('generate',{'style':'piano','lyrics':'Morning light','seed':0})
                saved = json.loads((outputs/fixed_zero['id']/'job.json').read_text('utf-8'))
                self.assertEqual(saved['request']['seed'],0)  # API/ComfyUI zero remains a fixed seed.


if __name__ == '__main__':
    unittest.main()
