from pathlib import Path
import tempfile
import unittest
import numpy as np
from app.yue2_app.rvc_pitch import rvc_pitch_shift, training_pitch_profiles, voice_pitch_profile


class PitchCoverage(unittest.TestCase):
    def test_profiles_separate_speakers_and_exclude_unselected_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);folder = root / '2b-f0nsf';folder.mkdir()
            for name, hz in [('a_0.wav.npy', 150), ('ab_0.wav.npy', 500), ('b_0.wav.npy', 300), ('unused_0.wav.npy', 900)]:
                np.save(folder / name, np.full(200, hz, dtype=np.float32))
            profiles = training_pitch_profiles(root, [{'output_key':'a','speaker_id':0}, {'output_key':'b','speaker_id':1}])
            self.assertEqual(profiles['0']['median_hz'], 150)
            self.assertEqual(profiles['1']['median_hz'], 300)
            self.assertEqual(profiles['0']['frames'], 200)
            np.save(folder / 'a_0.wav.npy', np.array([float('nan')]))
            with self.assertRaises(ValueError):
                training_pitch_profiles(root, [{'output_key':'a','speaker_id':0}])

    def test_imported_bad_profile_is_unavailable_not_an_instruction_to_shift(self):
        profile = {'basis':'training_continuous_f0','p5_hz':120,'median_hz':200,'p95_hz':350,'frames':1000}
        voice = {'training':{'pitch_profiles':{'0':profile}}}
        self.assertEqual(voice_pitch_profile(voice, 0), profile)
        for value in (float('nan'), 1200, 40, True):
            profile['p95_hz'] = value
            self.assertIsNone(voice_pitch_profile(voice, 0))
        self.assertIsNone(voice_pitch_profile({'training':[]}, 0))
        self.assertIsNone(voice_pitch_profile({}, 0))

    def test_explicit_rvc_pitch_preserves_legacy_and_does_not_change_seed_settings(self):
        self.assertEqual(rvc_pitch_shift({}), 0)
        self.assertEqual(rvc_pitch_shift({'semi_tone_shift':3}), 3)
        request = {'semi_tone_shift':0, 'rvc_pitch_shift':-12}
        self.assertEqual(rvc_pitch_shift(request), -12)
        self.assertEqual(request['semi_tone_shift'], 0)
        for value in (True, None, 1.5, -13, 13, float('nan'), 'bad'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rvc_pitch_shift({'rvc_pitch_shift':value})

if __name__ == '__main__':unittest.main()
