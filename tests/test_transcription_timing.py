"""Clock invariants independent of model quality or a specific song."""
import unittest
from pathlib import Path
import tempfile

import numpy as np
import mido

from app.yue2_app.transcription_timing import fitted_bpm
from app.yue2_app.midi_document import blank_document, export_midi
from app.yue2_app.midi_extract_worker import tracks_from_raw


class TimingTest(unittest.TestCase):
    def test_fitted_tempo_removes_fft_bin_error_without_removing_preroll(self):
        # Real beat detections have frame rounding and a nonzero start time.
        seconds=np.round((1.25+np.arange(80)*.5)*22050/512)*512/22050
        self.assertAlmostEqual(fitted_bpm(seconds,117.453835),120,delta=.04)

    def test_variable_or_insufficient_beat_evidence_does_not_force_constant_fit(self):
        self.assertEqual(fitted_bpm([0,.5],99),99)
        self.assertEqual(fitted_bpm([0,.5,.5,1,1.5,2,2.5,3],99),99)
        self.assertEqual(fitted_bpm([0,1,2,3,4,5,7,9,11,13],99),99)
        self.assertEqual(fitted_bpm([float('nan')]*10,99),99)

    def test_midi_export_keeps_raw_seconds_and_silence_at_multiple_tempos(self):
        # No GPU/model import is required to serialize already recognized notes.
        # Inline the vendor chord utility's import path only in this test.
        import sys, types
        from unittest.mock import patch
        symbol=types.ModuleType('mulacover.symbolic');symbol._chord_pitches=lambda label: []
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules,{'mulacover.symbolic':symbol}):
            for bpm in (20,72.3,120,173.71,400):
                raw={'notes':[{'program':100,'is_drum':False,'pitch':60+i,'onset':t,'offset':t+.123,'velocity':100}
                              for i,t in enumerate((1.25,12.25,400.125,899.5))]}
                data=blank_document();data['tracks']=tracks_from_raw(raw,bpm);data['tempos'][0]['tempo']=round(60e6/bpm)
                path=export_midi(data,Path(folder)/f'{bpm}.mid')
                clock=0.;actual={}
                for message in mido.MidiFile(path):
                    clock+=message.time
                    if message.type=='note_on' and message.velocity:
                        actual[message.note]=[clock,None]
                    elif message.type=='note_off' or message.type=='note_on' and not message.velocity:
                        actual[message.note][1]=clock
                for expected in raw['notes']:
                    onset,offset=actual[expected['pitch']]
                    self.assertAlmostEqual(onset,expected['onset'],delta=.003)
                    self.assertAlmostEqual(offset,expected['offset'],delta=.003)


if __name__=='__main__':unittest.main()
