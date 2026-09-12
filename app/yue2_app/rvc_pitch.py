"""Describe training pitch coverage and resolve explicit RVC-only transposition."""
from __future__ import annotations
import math
from pathlib import Path


def voice_pitch_profile(voice: dict, speaker_id: int) -> dict | None:
    training = voice.get('training')
    profiles = training.get('pitch_profiles') if isinstance(training, dict) else None
    profile = profiles.get(str(speaker_id)) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict) or profile.get('basis') != 'training_continuous_f0':
        return None
    values = [profile.get(key) for key in ('p5_hz', 'median_hz', 'p95_hz')]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        return None
    if not 50 <= values[0] <= values[1] <= values[2] <= 1100:
        return None
    frames = profile.get('frames')
    if type(frames) is not int or frames < 100:
        return None
    return {key: profile[key] for key in ('basis', 'p5_hz', 'median_hz', 'p95_hz', 'frames')}


def training_pitch_profiles(directory: Path, entries: list[dict]) -> dict:
    """Use only the selected speaker's verified training curves, without claiming a hard vocal limit."""
    import numpy as np
    groups = {}
    for entry in entries:
        key = entry['output_key']
        if not isinstance(key, str) or not key or any(c in key for c in '/\\*?[]'):
            raise ValueError('训练片段标识无效')
        group = groups.setdefault(str(int(entry['speaker_id'])), [])
        for path in sorted((directory / '2b-f0nsf').glob(key + '_*.wav.npy')):
            data = np.load(path, allow_pickle=False)
            if data.ndim != 1 or not np.isfinite(data).all():
                raise ValueError('训练音高统计遇到损坏特征')
            group.append(data[(data >= 50) & (data <= 1100)])
    result = {}
    for speaker, arrays in groups.items():
        values = np.concatenate(arrays) if arrays else np.empty(0)
        if len(values) < 100:
            continue
        low, median, high = map(float, np.percentile(values, [5, 50, 95]))
        result[speaker] = {'basis': 'training_continuous_f0', 'p5_hz': round(low, 2),
                           'median_hz': round(median, 2), 'p95_hz': round(high, 2), 'frames': len(values)}
    return result


def rvc_pitch_shift(request: dict) -> int:
    value = request.get('rvc_pitch_shift', request.get('semi_tone_shift', 0))
    if isinstance(value, bool):
        raise ValueError('RVC 移调需要 -12 至 12 的整数半音')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError('RVC 移调需要 -12 至 12 的整数半音') from None
    if not math.isfinite(number) or not number.is_integer() or not -12 <= number <= 12:
        raise ValueError('RVC 移调需要 -12 至 12 的整数半音')
    return int(number)
