from pathlib import Path
import tempfile
import unittest

import av
import numpy as np
import soundfile as sf

from app.yue2_app.midi_extract_worker import cached_events, decode_audio, readiness, validate_raw_events
from app.yue2_app.worker_common import JobContext, Cancelled


class MidiDecodeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.ctx=JobContext(self.root/'job')

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_wav_flac_mp3_m4a_decode_selected_range_and_rate(self):
        rate=44100
        y=(.15*np.sin(2*np.pi*440*np.arange(3*rate)/rate)).astype('float32')
        stereo=np.stack([y,y*.5],axis=1)
        sf.write(self.root/'input.wav',stereo,rate)
        sf.write(self.root/'input.flac',stereo,rate)
        for suffix,codec in (('mp3','mp3'),('m4a','aac')):
            with av.open(str(self.root/f'input.{suffix}'),'w') as container:
                stream=container.add_stream(codec,rate=rate)
                stream.layout='stereo'
                for start in range(0,len(y),4096):
                    frame=av.AudioFrame.from_ndarray(stereo[start:start+4096].T.copy(),format='fltp',layout='stereo')
                    frame.sample_rate=rate
                    for packet in stream.encode(frame):container.mux(packet)
                for packet in stream.encode(None):container.mux(packet)
        for suffix in ('wav','flac','mp3','m4a'):
            with self.subTest(format=suffix):
                output=self.root/f'{suffix}.wav'
                duration=decode_audio(self.root/f'input.{suffix}',output,.5,2.5,self.ctx)
                self.assertAlmostEqual(duration,2,places=2)
                samples,sr=sf.read(output)
                self.assertEqual(sr,22050)
                self.assertTrue(np.isfinite(samples).all())
                self.assertGreater(float(np.sqrt(np.mean(samples**2))),.03)

    def test_silence_is_valid_empty_audio_not_a_decode_failure(self):
        sf.write(self.root/'silence.wav',np.zeros(44100,dtype='float32'),44100)
        self.assertAlmostEqual(decode_audio(self.root/'silence.wav',self.root/'out.wav',0,None,self.ctx),1,places=2)

    def test_cancel_and_bad_files_do_not_report_completed_audio(self):
        sf.write(self.root/'source.wav',np.zeros(44100,dtype='float32'),44100)
        self.ctx.cancel_path.parent.mkdir(parents=True,exist_ok=True)
        self.ctx.cancel_path.touch()
        with self.assertRaises(Cancelled):
            decode_audio(self.root/'source.wav',self.root/'out.wav',0,None,self.ctx)
        self.ctx.cancel_path.unlink()
        (self.root/'bad.mp3').write_bytes(b'not audio')
        with self.assertRaises(Exception):
            decode_audio(self.root/'bad.mp3',self.root/'out.wav',0,None,self.ctx)

    def test_extract_readiness_does_not_require_generation_weights_or_codec(self):
        from app.yue2_app.mulacover_models import YOURMT3, CHORD_SIZES
        base=self.root/'models'/'SymbolicTranscriptor'
        files={YOURMT3['relative']:YOURMT3['bytes'],**{f'chord/{name}':size for name,size in CHORD_SIZES.items()}}
        for name,size in files.items():
            path=base/name
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('wb') as stream:
                stream.seek(size-1);stream.write(b'\0')
        source=self.root/'vendor'/'mulacover'/'src'/'mulacover'
        for relative in ('symbolic.py','_symbolic_transcription/melody.py','_symbolic_transcription/harmony.py','_symbolic_transcription/yourmt3/model/inference.py'):
            path=source/relative
            path.parent.mkdir(parents=True,exist_ok=True);path.touch()
        self.assertTrue(readiness(self.root)['ready'])
        self.assertFalse((self.root/'models'/'MuLaCover').exists())
        (base/YOURMT3['relative']).unlink()
        self.assertFalse(readiness(self.root)['ready'])

    def test_corrupt_cache_is_rebuilt_instead_of_poisoning_result(self):
        import json
        cache=self.root/'notes.json'
        for value in ('{broken', json.dumps({'not':'events'}), json.dumps([{'is_drum':False,'program':100,'pitch':60,'onset':float('nan'),'offset':1}])):
            cache.write_text(value,encoding='utf-8')
            result,warning=cached_events(cache,'notes',3)
            self.assertIsNone(result)
            self.assertIn('重新识别',warning)
        events=[{'is_drum':False,'program':100,'pitch':60,'velocity':1,'onset':0,'offset':1}]
        cache.write_text(json.dumps(events),encoding='utf-8')
        self.assertEqual(cached_events(cache,'notes',3),(events,''))
        with self.assertRaises(ValueError):
            validate_raw_events([['C:maj',1,0]],'chords',3)


if __name__=='__main__':unittest.main()
