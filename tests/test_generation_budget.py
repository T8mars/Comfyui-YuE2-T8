import importlib.util, json, queue, sys, tempfile, threading, types, unittest
from pathlib import Path
from unittest.mock import patch, Mock
from app.yue2_app import service, core_worker

class GenerationBudget(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); (self.root/'outputs/jobs').mkdir(parents=True)
        self.store = service.JobStore.__new__(service.JobStore)
        self.store.lock = threading.RLock(); self.store.storage_lock = threading.RLock()
        self.store.jobs = {}; self.store.updating = False; self.store.pending = queue.Queue()
        for name, value in [('ROOT', self.root), ('OUTPUTS', self.root/'outputs/jobs')]:
            p = patch.object(service, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(service, 'runtime_ready', return_value={'capabilities': {'generation':True,'rvc_inference':True,'vocal_separation':True}})
        p.start(); self.addCleanup(p.stop)
        p = patch('app.yue2_app.rvc_library.verify_voice', return_value={'indices':{'0':'index'}})
        p.start(); self.addCleanup(p.stop)

    def request(self, kind, value):
        generation = {'memory_budget_gib':value}
        return {'generate':generation,'voice':{'backend':'rvc','voice_id':'test'}} if kind=='reference_cover' else generation

    def test_custom_budget_survives_queue_and_pipeline_constructor(self):
        pipeline = Mock()
        with patch.dict('sys.modules', {'yue2':types.SimpleNamespace(YuE2Pipeline=pipeline)}), patch.object(core_worker,'model_paths',return_value={'model':'model','vae':'vae'}):
            for kind in ('generate','plan','render_plan','reference_cover'):
                for value in (8, 16.25, 32):
                    with self.subTest(kind=kind, value=value):
                        job=self.store.create(kind,self.request(kind,value))
                        saved=json.loads((self.root/'outputs/jobs'/job['id']/'job.json').read_text())['request']
                        generation=saved['generate'] if kind=='reference_cover' else saved
                        self.assertEqual(generation['memory_budget_gib'],value)
                        core_worker.create_pipe(self.root,generation)
                        self.assertEqual(pipeline.from_pretrained.call_args.kwargs['memory_budget_gib'],value)

    def test_invalid_budget_rejected_before_queue(self):
        for kind in ('generate','plan','render_plan','reference_cover'):
            for value in (None, True, False, '', 'bad', [], {}, 0, 2, -3, float('inf'), float('nan')):
                with self.subTest(kind=kind, value=value), self.assertRaisesRegex(ValueError,'显存预算'):
                    self.store.create(kind,self.request(kind,value))
        self.assertFalse(self.store.jobs)
        self.assertTrue(self.store.pending.empty())

    def test_comfyui_budget_widget_matches_service_range(self):
        package_name = 'yue2_comfy_test_package'
        package = types.ModuleType(package_name)
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        with patch.dict(sys.modules, {package_name: package}):
            spec = importlib.util.spec_from_file_location(
                package_name + '.nodes', Path(__file__).resolve().parents[1] / 'nodes.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
                config = module.YuE2ModelLoader.INPUT_TYPES()['required']['memory_budget_gib'][1]
            finally:
                sys.modules.pop(spec.name, None)
        self.assertLessEqual(config['min'], 2.5)
        self.assertGreaterEqual(config['max'], 32)

    def test_vae_tile_uses_smaller_of_device_and_budget(self):
        cases = [
            ({'memory_budget_gib': 8}, 24, 512),
            ({'memory_budget_gib': 23.5}, 24, 1024),
            ({'memory_budget_gib': 32}, 16, 512),
            ({'memory_budget_gib': 32, 'vae_core_frames': 256}, 24, 256),
        ]
        for request, physical, expected in cases:
            with self.subTest(request=request, physical=physical):
                self.assertEqual(core_worker.vae_core_frames_for(request, physical), expected)

    def test_invalid_explicit_vae_tile_is_rejected(self):
        for value in (True, False, 0, -1, 12.5, '512', 2048):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'vae_core_frames'):
                core_worker.vae_core_frames_for({'memory_budget_gib': 24, 'vae_core_frames': value}, 24)

if __name__ == '__main__': unittest.main()
