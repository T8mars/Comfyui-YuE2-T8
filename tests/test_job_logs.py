from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.yue2_app import service


class StageLogs(unittest.TestCase):
    def test_nested_voice_logs_are_readable_even_if_parent_log_is_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ident = '20260912-000000-aabbccdd'
            logs = root/'logs'
            jobs = root/'outputs/jobs'
            logs.mkdir()
            (logs/(ident+'.log')).write_text('Parent')
            child = jobs/ident/'artifacts/stages/voice/artifacts/stages/ab-seed-vc/worker.log'
            child.parent.mkdir(parents=True)
            child.write_text('x'*100000+'\nActual voice failure')
            with patch.object(service,'LOGS',logs),patch.object(service,'OUTPUTS',jobs):
                text = service.job_log_text(ident)
                self.assertIn('Actual voice failure',text)
                self.assertIn('ab-seed-vc/worker.log',text)
                self.assertIn('Parent',text)
                self.assertLess(len(text),51000)
                (logs/(ident+'.log')).unlink()
                self.assertIn('Actual voice failure',service.job_log_text(ident))


if __name__=='__main__':
    unittest.main()
