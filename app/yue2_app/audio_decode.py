"""Decode at native rate before Torch resampling for the paper preset.

TorchAudio 2.10 removed the old FFmpeg metadata/decoder API. PyAV supplies
FFmpeg decoding while preserving the preset's downmix-then-resample order.
"""
from pathlib import Path
import math


def load_paper_audio(path: Path, *, max_seconds=None):
    import av
    import numpy as np
    import torch
    from torchaudio.functional import resample

    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError('max_seconds must be finite and positive')
    chunks = []
    rate = None
    layout = None
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            if rate is None:
                rate, layout = frame.sample_rate, frame.layout.name
                decoder = av.AudioResampler(format='fltp', layout=layout, rate=rate)
            if frame.sample_rate != rate or frame.layout.name != layout:
                raise ValueError('Audio sample rate or channel layout changes within the file')
            for converted in decoder.resample(frame):
                chunks.append(converted.to_ndarray())
        if rate is not None:
            for converted in decoder.resample(None):
                chunks.append(converted.to_ndarray())
    if not chunks or not rate:
        raise ValueError('Audio contains no decodable samples')
    waveform = torch.from_numpy(np.concatenate(chunks, axis=1)).float().mean(dim=0)
    if rate != 24000:
        waveform = resample(waveform, rate, 24000)
    if max_seconds is not None:
        waveform = waveform[:round(max_seconds * 24000)]
    if waveform.numel() < 1025 or not torch.isfinite(waveform).all():
        raise ValueError('Audio must contain at least 1025 finite samples at 24 kHz')
    return waveform.contiguous()
