"""Keep the audio clock separate from a recognizer's notated beat grid."""
from __future__ import annotations

from io import BytesIO
import math
from pathlib import Path
import re

from .io import atomic_json


def atomic_text(path, value):
    from .midi_document import atomic_payload
    atomic_payload(path, lambda stream: stream.write(value.encode('utf-8')))


def fitted_bpm(beat_seconds, fallback):
    """Fit the measured span, rather than the quantized autocorrelation bin.

    This changes the displayed beat grid, never the recognized note seconds.
    Leave expressive/variable tempo material alone instead of inventing a fit.
    """
    import numpy as np
    beats = np.asarray(beat_seconds, dtype=float)
    if len(beats) < 8 or not np.isfinite(beats).all():
        return float(fallback)
    delta = np.diff(beats)
    if np.any(delta <= 0):
        return float(fallback)
    period = float(np.median(delta))
    if np.quantile(abs(delta-period), .9) > period*.12:
        return float(fallback)
    slope = float(np.polyfit(np.arange(len(beats)), beats, 1)[0])
    bpm = 60/slope if slope > 0 else 0
    return bpm if 20 <= bpm <= 400 else float(fallback)


def _rows(path):
    if not path.is_file():
        return []
    return [line.split('\t') for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def align_abc_to_audio(output: Path, result: dict, notation_builder):
    """Build the YuE2-compatible constant-tempo ABC on the original audio clock.

    SheetSage's notation adds pickup padding and uses a global mean tempo.
    Rebuilding from *raw seconds*, with zero at the beginning of the audio,
    avoids both padding displacement and accumulated tempo-map drift. Keep
    the original beat-organized score as a separate, unmodified artifact.
    """
    import pretty_midi
    from .assistant_rules.yue2_abc import parse_abc

    original = result.get('abc') or (output/'score.abc').read_text(encoding='utf-8')
    parsed = parse_abc(original)
    bpm = parsed.bpm
    raw = (output/'melody.mid').read_bytes()
    midi = pretty_midi.PrettyMIDI(BytesIO(raw))
    notes = [n for t in midi.instruments for n in t.notes]
    duration = max(float(result.get('duration_seconds') or 0), max((n.end for n in notes), default=0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('转谱时间范围无效')
    match = re.fullmatch(r'M:(\d+)/(\d+)', original.splitlines()[2])
    numerator, denominator = map(int, match.groups())
    # Double the beat subdivision to obtain at least a 1/32-note notation
    # grid without mutating the downloaded model's global serializer settings.
    dense_denominator = denominator*2 if denominator <= 64 else denominator
    dense_numerator = numerator*(dense_denominator//denominator)
    period = 60/bpm*4/dense_denominator
    count = math.ceil(duration/period)
    if count > 200_000:
        raise ValueError('转谱乐谱过长，请分段处理')
    beats = [[i*period, i % dense_numerator+1, dense_numerator, dense_denominator] for i in range(count+1)]
    keys = [[float(a), float(b), key] for a,b,key in _rows(output/'key.lab')]
    if not keys:
        raise ValueError('原始转谱缺少调性，保留原乐谱')
    chords = [[float(a),float(b),label] for a,b,label in _rows(output/'chord.lab')]
    structures = [[float(a),float(b),label] for a,b,label in _rows(output/'structure.lab')]
    aligned, _ = notation_builder(raw, beats, chords, keys, structures,
                                  melody_only=bool(result.get('melody_only', True)))
    aligned = aligned.replace(f'M:{dense_numerator}/{dense_denominator}\n', f'M:{numerator}/{denominator}\n')
    verified = parse_abc(aligned)
    warnings = [
        'ABC 已按原音频时间对齐，保留开头空白；为兼容 YuE2 使用固定速度和细分音符网格。'
        '原始 MIDI 保留秒级识别时间，节拍整理版保存在 score.notation.abc。'
    ]
    if len({(int(r[2]), int(r[3])) for r in _rows(output/'beat.lab') if len(r)>=4}) > 1:
        warnings.append('原曲含拍号变化；音频对齐版使用首个拍号，原拍号变化保留在节拍整理版。')
    def starts(score):
        return sorted(float(n[0])*60/score.bpm for v in score.voices.values() for n in v.notes)
    first_raw = min((n.start for n in notes), default=None)
    after, before = starts(verified), starts(parsed)
    info = {'mode':'audio-clock', 'bpm':bpm, 'grid_seconds':period/4,
            'source_first_note_seconds':first_raw,
            'original_abc_first_note_seconds':before[0] if before else None,
            'aligned_abc_first_note_seconds':after[0] if after else None,
            'original_score':'score.notation.abc', 'audio_aligned_score':'score.abc',
            'raw_midi':'transcription.mid', 'warnings':warnings}
    # All validation completes before changing the successful original score.
    atomic_text(output/'score.notation.abc', original)
    atomic_text(output/'score.abc', aligned)
    atomic_json(output/'timing.json', info)
    result['abc'] = aligned
    result['timing'] = info
    result['warnings'] = list(result.get('warnings') or []) + warnings
    return info
