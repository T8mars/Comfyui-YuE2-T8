import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from app.yue2_app.rvc_projects import add_material, create_project, inspect_audio, selected_materials
from app.yue2_app.rvc_training import prepare_training, training_options

ROOT = Path(__file__).resolve().parents[1]


class RvcMaterialsTest(unittest.TestCase):
    def setUp(self):
        (ROOT / 'cache').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='rvc-test-', dir=ROOT / 'cache')
        self.root = Path(self.temp.name).resolve()
        self.assertIn((ROOT / 'cache').resolve(), self.root.parents)

    def tearDown(self):
        self.temp.cleanup()

    def test_silence_duplicate_review_and_changed_source(self):
        path = self.root / 'sample.wav'
        sf.write(path, np.zeros(32000, dtype=np.float32), 16000)
        self.assertIn('静音素材', inspect_audio(path)['warnings'])
        wave = .2 * np.sin(np.arange(32000) * .07)
        sf.write(path, wave, 16000, subtype='FLOAT')
        project = create_project(self.root, '测试音色')
        item = add_material(self.root, project, path)
        self.assertEqual(add_material(self.root, project, path)['duplicate_of'], item['id'])
        with self.assertRaisesRegex(ValueError, '试听'):
            selected_materials(project)
        item['reviewed'] = True
        self.assertEqual(len(selected_materials(project)), 1)
        Path(item['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '已改变'):
            selected_materials(project)

    def test_clipped_audio(self):
        path = self.root / 'clipped.wav'
        sf.write(path, np.ones(32000), 16000, subtype='FLOAT')
        self.assertIn('疑似削波失真', inspect_audio(path)['warnings'])

    def test_resume_rejects_changed_training_features(self):
        workspace = self.root / 'training'
        experiment = workspace / 'logs' / 'example'
        for folder in ['0_gt_wavs', '3_feature768', '2a_f0', '2b-f0nsf']:
            (experiment / folder).mkdir(parents=True)
        sf.write(experiment / '0_gt_wavs/a.wav', np.ones(48000) * .1, 48000)
        feature = experiment / '3_feature768/a.npy'
        np.save(feature, np.zeros((50, 768), dtype=np.float32))
        for folder in ['2a_f0', '2b-f0nsf']:
            np.save(experiment / folder / 'a.wav.npy', np.ones(100))
        options = training_options({})
        speakers = [{'id': 0, 'name': 'voice'}]
        prepare_training(ROOT, workspace, 'example', options, speakers, batch_size=1)
        (experiment / 'G_saved.pth').touch()
        np.save(feature, np.ones((50, 768), dtype=np.float32))
        with self.assertRaisesRegex(ValueError, '已经改变'):
            prepare_training(ROOT, workspace, 'example', options, speakers, batch_size=1)

    def test_invalid_integer_options(self):
        for value in [True, None, 'bad', 1.5, float('nan')]:
            with self.assertRaises(ValueError):
                training_options({'epochs': value})


if __name__ == '__main__':
    unittest.main()
