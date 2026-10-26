import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.yue2_app.artifacts import generation_provenance
from app.yue2_app.core_worker import add_upstream, generate_one, run_semantic
from app.yue2_app.io import atomic_json
from app.yue2_app.model_verify import PINNED_MODELS
from app.yue2_app.worker_common import JobContext


class GenerationProvenanceTests(unittest.TestCase):
    def setUp(self):
        add_upstream(Path(__file__).resolve().parents[1])
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        atomic_json(self.root/'models/MODEL_MANIFEST.json', {
            'bundle': 't8star/YuE2-Comfy', 'models': copy.deepcopy(PINNED_MODELS)})
        self.weights = {key: {'files': {'model.safetensors': {
            'sha256': PINNED_MODELS[name]['sha256'], 'bytes': PINNED_MODELS[name]['size']}}}
            for key, name in (('mot', 'YuE2-3B'), ('vae', 'YuE2-Vae'))}

    def test_wrong_vae_is_rejected_before_planning_or_semantic_generation(self):
        from yue2 import SymbolicPlan
        from yue2.protocol import SongRequest
        from yue2.pipeline import SemanticResult
        request = SongRequest(style='piano', lyrics='Morning light', seed=12)
        plan = SymbolicPlan(request, None, [], [151643])
        weights = copy.deepcopy(self.weights)
        weights['vae']['files']['model.safetensors']['sha256'] = '0'*64
        pipe = SimpleNamespace(weights=weights, device='cpu',
            effective_config=lambda *_: {'generation': {}},
            plan=mock.Mock(return_value=plan),
            generate_semantic=mock.Mock(return_value=SemanticResult(plan, [1], {}, False)))
        job = self.root/'outputs/jobs/render'
        job.mkdir(parents=True)
        ctx = JobContext(job)
        ctx.root = self.root
        with self.assertRaisesRegex(ValueError, 'YuE2-Vae'):
            generate_one(pipe, ctx, request.to_dict(), job/'artifacts/song', 12, initial_plan=plan)
        pipe.plan.assert_not_called()
        pipe.generate_semantic.assert_not_called()
        self.assertFalse((job/'artifacts/song/checkpoints/identity.json').exists())

    def test_valid_vae_identity_and_real_mismatch_remain_distinct(self):
        self.assertEqual(generation_provenance(self.root, self.weights)['runtime_identity'], self.weights)
        self.weights['vae']['files']['model.safetensors']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, '实际 SHA-256.*00000000'):
            generation_provenance(self.root, self.weights)
        del self.weights['vae']['files']['model.safetensors']['sha256']
        with self.assertRaisesRegex(ValueError, '缺少.*哈希'):
            generation_provenance(self.root, self.weights)

    def test_standalone_semantic_stage_checks_before_model_inference(self):
        from yue2 import SymbolicPlan
        from yue2.protocol import SongRequest
        plan = SymbolicPlan(SongRequest(style='piano', lyrics='Morning light'), None, [], [151643])
        directory = self.root/'outputs/jobs/plan/artifacts/plan'
        plan.save(directory)
        weights = copy.deepcopy(self.weights)
        weights['vae']['files']['model.safetensors']['sha256'] = '0'*64
        pipe = SimpleNamespace(weights=weights, close=mock.Mock(), generate_semantic=mock.Mock())
        job = self.root/'outputs/jobs/semantic'
        job.mkdir(parents=True)
        with mock.patch('app.yue2_app.core_worker.create_pipe', return_value=pipe), \
                self.assertRaisesRegex(ValueError, 'YuE2-Vae'):
            run_semantic(self.root, JobContext(job), {'plan_dir': str(directory)})
        pipe.generate_semantic.assert_not_called()
        pipe.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
