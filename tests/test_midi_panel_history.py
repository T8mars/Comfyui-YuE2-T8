"""Panel restoration must preserve songs despite new extraction/failure tasks."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.yue2_app import service
from app.yue2_app.io import atomic_json


class MidiPanelHistoryTest(unittest.TestCase):
    def test_latest_panel_keeps_previous_completed_song_and_excludes_transcription(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            store=service.JobStore.__new__(service.JobStore)
            store.lock=threading.RLock();store.storage_lock=threading.RLock();store.jobs={}
            project='a'*32
            with patch.object(service,'ROOT',root),patch.object(service,'OUTPUTS',root/'outputs/jobs'):
                entries=[('midi_extract','complete',6,project,{}),
                         ('mulacover_remix','failed',5,project,{}),
                         ('mulacover_remix','complete',4,'b'*32,{'audio':'other-project.flac'}),
                         ('mulacover_remix','complete',3,project,{'audio':'previous.flac'}),
                         ('mulacover_remix','complete',2,project,{'audio':'older.flac'})]
                for index,(kind,status,timestamp,owner,result) in enumerate(entries):
                    job_id=f'20260919-040000-{index:08x}'
                    value={'id':job_id,'kind':kind,'status':status,'created_at':timestamp,
                           'project_id':owner,'result_panel':'midi','result':result}
                    store.jobs[job_id]=value
                    atomic_json(service.job_directory(job_id)/'status.json',value)
                jobs,total=store.list_page(project_id=project,latest_by_panel=True,compact=True)
                self.assertEqual(total,2)
                self.assertEqual([job['status'] for job in jobs],['failed','complete'])
                self.assertEqual(jobs[1]['result']['audio'],'previous.flac')
                # A new completed song replaces the prior result, while extraction stays separate.
                latest=store.jobs['20260919-040000-00000001']
                latest.update(status='complete',result={'audio':'new.flac'})
                atomic_json(service.job_directory(latest['id'])/'status.json',latest)
                jobs,total=store.list_page(project_id=project,latest_by_panel=True)
                self.assertEqual(total,1)
                self.assertEqual(jobs[0]['result']['audio'],'new.flac')


if __name__=='__main__':unittest.main()
