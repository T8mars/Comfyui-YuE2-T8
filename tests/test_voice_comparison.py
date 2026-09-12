from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from app.yue2_app import voice_cache
from app.yue2_app.service import retained_audio_files
from app.yue2_app.voice_worker import compare_voices
from app.yue2_app.worker_common import JobContext, Cancelled


class VoiceComparison(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'outputs/jobs/test/artifacts'
        self.output.mkdir(parents=True)
        self.ctx = JobContext(self.output.parent)
        for name in voice_cache.FILES:
            (self.output / name).write_bytes(b'audio' * 10)
        self.models, self.source = voice_cache.identity({'components': {'Demucs': {'sha': '1'}, 'Seed': '2'}}, 'song')

    def test_reuse_across_backends_and_invalidate_content_or_model(self):
        self.assertTrue(voice_cache.save(self.root, self.output, self.models, self.source, self.ctx))
        destination = self.root / 'copy'
        destination.mkdir()
        other_models, other_source = voice_cache.identity({'components': {'Demucs': {'sha': '1'}, 'RVC': 'other'}}, 'song')
        self.assertTrue(voice_cache.restore(self.root, destination, other_models, other_source, self.ctx))
        self.assertEqual((destination / voice_cache.FILES[0]).read_bytes(), b'audio' * 10)
        for models, source in (voice_cache.identity({'components': {'Demucs': {'sha': '2'}}}, 'song'),
                               voice_cache.identity({'components': {'Demucs': {'sha': '1'}}}, 'changed')):
            self.assertFalse(voice_cache.restore(self.root, destination, models, source, self.ctx))

    def test_corruption_is_cache_miss_and_can_be_rebuilt(self):
        voice_cache.save(self.root, self.output, self.models, self.source, self.ctx)
        entry = self.root / 'cache/voice-separation' / voice_cache._key(self.models, self.source)
        (entry / voice_cache.FILES[0]).write_bytes(b'corrupt')
        self.assertFalse(voice_cache.restore(self.root, self.output, self.models, self.source, self.ctx))
        self.assertTrue(voice_cache.save(self.root, self.output, self.models, self.source, self.ctx))
        (entry / voice_cache.MANIFEST).write_text('[]')
        self.assertFalse(voice_cache.restore(self.root, self.output, self.models, self.source, self.ctx))

    def test_cancel_during_copy_leaves_no_committed_cache(self):
        with patch.object(self.ctx, 'check_cancelled', side_effect=[None, None, Cancelled('stop')]):
            with self.assertRaises(Cancelled):
                voice_cache.save(self.root, self.output, self.models, self.source, self.ctx)
        base = self.root / 'cache/voice-separation'
        self.assertEqual([p.name for p in base.iterdir() if p.name != '.lock'], [])

    def test_lru_is_bounded_and_ignores_unexpected_files(self):
        voice_cache.save(self.root, self.output, self.models, self.source, self.ctx)
        old = self.root / 'cache/voice-separation' / voice_cache._key(self.models, self.source)
        unknown = old.parent / ('a' * 64)
        unknown.mkdir()
        (unknown / 'user.txt').write_text('keep')
        changed = {**self.source, 'song_sha256': 'next'}
        old_size = sum(p.stat().st_size for p in old.iterdir())
        self.assertTrue(voice_cache.save(self.root, self.output, self.models, changed, self.ctx, max_bytes=old_size + 100))
        self.assertFalse(old.exists())
        self.assertEqual((unknown / 'user.txt').read_text(), 'keep')
        self.assertLessEqual(sum(p.stat().st_size for p in old.parent.glob('*/*')), old_size + 100)

    def test_a_b_order_resume_mapping_and_retained_first_on_failure(self):
        first = {'backend': 'seed-vc', 'audio': str(self.output / 'first.flac')}
        with patch('app.yue2_app.workflow_worker.run_stage', side_effect=[first, RuntimeError('RVC failed')]) as stage:
            with self.assertRaisesRegex(RuntimeError, 'RVC failed'):
                compare_voices(self.root, self.ctx, {'resume_from': str(self.root / 'outputs/jobs/old')})
        self.assertEqual([call.args[4]['backend'] for call in stage.call_args_list], ['seed-vc', 'rvc'])
        self.assertTrue(stage.call_args_list[1].args[4]['resume_from'].endswith('ab-rvc'))
        status = json.loads(self.ctx.status_path.read_text())
        self.assertEqual(status['result']['candidates'], [first])
        self.assertTrue(status['result']['partial'])
        self.assertEqual(retained_audio_files(status, self.ctx.job_dir), {self.output / 'first.flac'})

    def test_failed_seed_still_runs_rvc_but_cancellation_stops(self):
        second = {'backend': 'rvc', 'audio': str(self.output / 'second.flac')}
        with patch('app.yue2_app.workflow_worker.run_stage', side_effect=[RuntimeError('Seed failed'), second]) as stage:
            with self.assertRaisesRegex(RuntimeError, 'Seed failed'):
                compare_voices(self.root, self.ctx, {})
            self.assertEqual(stage.call_count, 2)
        self.assertEqual(json.loads(self.ctx.status_path.read_text())['result']['candidates'], [second])
        with patch('app.yue2_app.workflow_worker.run_stage', side_effect=Cancelled('stop')) as stage:
            with self.assertRaises(Cancelled):
                compare_voices(self.root, self.ctx, {})
            self.assertEqual(stage.call_count, 1)

    def test_completed_both_results_and_rejects_unfinished_or_external_files(self):
        candidates = [{'backend': backend, 'audio': str(self.output / f'{backend}.flac')}
                      for backend in ('seed-vc', 'rvc')]
        with patch('app.yue2_app.workflow_worker.run_stage', side_effect=candidates):
            result = compare_voices(self.root, self.ctx, {})
        self.assertFalse(result['partial'])
        self.assertEqual(result['completed_candidates'], 2)
        result['candidates'][0]['converted_vocal'] = str(self.root / 'outside.wav')
        result['candidates'][0]['manifest'] = str(self.output / 'secret.json')
        allowed = retained_audio_files({'result': result}, self.ctx.job_dir)
        self.assertEqual(allowed, {Path(c['audio']) for c in candidates})


if __name__ == '__main__':
    unittest.main()
