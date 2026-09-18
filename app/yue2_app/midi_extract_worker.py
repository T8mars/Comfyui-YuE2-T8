"""Independent audio-to-MIDI task using the kit's one Python and GPU queue."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import gc
import hashlib
import json
import math
from pathlib import Path

from .asset_library import AssetLibrary
from .io import atomic_json, sha256
from .midi_document import MidiStore, VersionConflict, blank_document, blank_track, number, ident, integer
from .mulacover_models import CHORD_NAMES, CHORD_SIZES, YOURMT3, UPSTREAM_COMMIT
from .settings import model_directory
from .worker_common import JobContext, Cancelled, configure_environment


def readiness(root):
    base = model_directory(root)/'SymbolicTranscriptor'
    required = {base/YOURMT3['relative']: YOURMT3['bytes']}
    required.update({base/'chord'/name: CHORD_SIZES[name] for name in CHORD_NAMES})
    missing = [str(path) for path, size in required.items() if not path.is_file() or path.stat().st_size != size]
    source = root/'vendor'/'mulacover'/'src'/'mulacover'
    for relative in ('symbolic.py', '_symbolic_transcription/melody.py', '_symbolic_transcription/harmony.py', '_symbolic_transcription/yourmt3/model/inference.py'):
        if not (source/relative).is_file():
            missing.append(str(source/relative))
    return {'ready': not missing, 'missing': missing}


def normalize_request(root, value):
    from .mulacover_core import _input_path, AUDIO_SUFFIXES
    source = _input_path(root, value.get('source_path'), '待提取音乐', AUDIO_SUFFIXES)
    if source.stat().st_size > 1024*1024*1024:
        raise ValueError('音频不能超过 1 GiB，请先裁剪')
    start = number(value.get('clip_start', 0), '提取起点', 0, 7200)
    end = value.get('clip_end')
    if end not in (None, ''):
        end = number(end, '提取终点', start+.05, start+900)
    else:
        end = None
    bpm = value.get('bpm')
    if bpm not in (None, ''):
        bpm = number(bpm, 'BPM', 20, 400)
    else:
        bpm = None
    project = str(value.get('project_id') or '')
    if project:
        AssetLibrary(root).get_project(project)
    editor = {}
    if value.get('editor_document_id'):
        document_id=ident(value['editor_document_id'])
        document=MidiStore(AssetLibrary(root)).get(document_id)
        if document['project_id'] != project:
            raise ValueError('编辑器文档与提取目标项目不一致')
        editor={'editor_document_id': document_id,
                'editor_document_version': integer(value.get('editor_document_version'), '编辑文档版本', 1, 1_000_000_000)}
    return {'source_path': str(source), 'clip_start': start, 'clip_end': end, 'bpm': bpm,
            'project_id': project, 'title': str(value.get('title') or source.name)[:170],
            '_local_references': value.get('_local_references', []), **editor}


def decode_audio(source, target, start, end, ctx):
    """Stream bounded, finite float audio; selection duration is unrelated to generation."""
    import av
    import numpy as np
    import soundfile as sf
    rate, cursor, written = 22050, 0, 0
    first = round(start*rate)
    last = round(end*rate) if end is not None else None
    target.parent.mkdir(parents=True, exist_ok=True)
    resampler = av.AudioResampler(format='fltp', layout='mono', rate=rate)
    with av.open(str(source)) as container, sf.SoundFile(str(target), 'w', samplerate=rate, channels=1, subtype='FLOAT') as output:
        if not container.streams.audio:
            raise ValueError('文件没有音频流')
        done = False
        for frame in container.decode(audio=0):
            ctx.check_cancelled()
            for converted in resampler.resample(frame):
                block = converted.to_ndarray().reshape(-1)
                if not np.isfinite(block).all():
                    raise ValueError('音频包含无效采样')
                low = max(0, first-cursor)
                high = min(len(block), last-cursor) if last is not None else len(block)
                if high > low:
                    written += high-low
                    if written > 900*rate:
                        raise ValueError('整首超过 15 分钟，请选择需要提取的片段；不会自动截断')
                    output.write(block[low:high])
                cursor += len(block)
                if last is not None and cursor >= last:
                    done = True
                    break
            if done:
                break
        if not done:
            for converted in resampler.resample(None):
                block = converted.to_ndarray().reshape(-1)
                if not np.isfinite(block).all():
                    raise ValueError('音频包含无效采样')
                low = max(0, first-cursor)
                high = min(len(block), last-cursor) if last is not None else len(block)
                if high > low:
                    written += high-low
                    if written > 900*rate:
                        raise ValueError('整首超过 15 分钟，请选择片段')
                    output.write(block[low:high])
                cursor += len(block)
    if written < rate//20:
        raise ValueError('选择范围没有足够的可读取音频')
    return written/rate


def tracks_from_raw(raw, bpm):
    import uuid
    from mulacover.symbolic import _chord_pitches
    groups = {}
    for item in raw.get('notes', []):
        key = 'drums' if item['is_drum'] else int(item['program'])
        if key not in groups:
            role = 'drums' if key == 'drums' else 'melody' if key == 100 else 'other'
            groups[key] = blank_track(role, '鼓组' if key == 'drums' else '主唱旋律' if key == 100 else f'候选乐器 · GM {key+1}')
            groups[key]['program'] = 0 if key == 'drums' else key
        start = max(0, round(item['onset']*bpm/60*480))
        finish = max(start+1, round(item['offset']*bpm/60*480))
        velocity = item.get('velocity', 100)
        # YourMT3 events may use velocity=1 as a binary gate rather than a MIDI level.
        velocity = 100 if velocity <= 1 else max(1, min(127, int(velocity)))
        groups[key]['notes'].append({'id': uuid.uuid4().hex, 'tick': start,
            'duration': finish-start, 'pitch': int(item['pitch']), 'velocity': velocity})
    tracks = [groups.get(100, blank_track('melody')), blank_track('chord'), groups.get('drums', blank_track('drums'))]
    tracks += [track for key, track in groups.items() if key not in (100, 'drums')]
    for label, onset, offset in raw.get('chords', []):
        start = max(0, round(onset*bpm/60*480))
        finish = max(start+1, round(offset*bpm/60*480))
        for pitch in _chord_pitches(label):
            tracks[1]['notes'].append({'id': uuid.uuid4().hex, 'tick': start, 'duration': finish-start,
                                     'pitch': 60+pitch, 'velocity': 85})
    return tracks


def validate_raw_events(value, kind, duration):
    """Reject corrupt cached events before they can replace a usable document."""
    if not isinstance(value, list) or len(value) > 100_000:
        raise ValueError('识别缓存事件数量或格式无效')
    for event in value:
        if kind == 'notes':
            if not isinstance(event, dict) or type(event.get('is_drum')) is not bool:
                raise ValueError('识别缓存音符格式无效')
            integer(event.get('pitch'), '识别音高', 0, 127)
            integer(event.get('program'), '识别乐器', 0, 128 if event['is_drum'] else 127)
            number(event.get('velocity', 100), '识别力度', 0, 127)
            start, end = event.get('onset'), event.get('offset')
        else:
            if not isinstance(event, (list, tuple)) or len(event) != 3 or not isinstance(event[0], str) or len(event[0]) > 200:
                raise ValueError('识别缓存和弦格式无效')
            _, start, end = event
        number(start, '识别起点', 0, duration + 5)
        number(end, '识别终点', 0, duration + 5)
        if end <= start:
            raise ValueError('识别缓存事件没有有效长度')
    return value


def cached_events(path, kind, duration):
    if not path.is_file():
        return None, ''
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('识别缓存过大')
        return validate_raw_events(json.loads(path.read_text(encoding='utf-8')), kind, duration), ''
    except (OSError, ValueError, TypeError, KeyError):
        return None, f'{"音符" if kind == "notes" else "和弦"}识别缓存损坏，已重新识别；原素材与已有编辑保留。'


def run(root, job_dir, request, ctx):
    import numpy as np
    import soundfile as sf
    from .mulacover_core import prepare_imports
    prepared = normalize_request(root, request)
    state = readiness(root)
    if not state['ready']:
        raise ValueError('转谱模型或源码不完整：'+'、'.join(state['missing']))
    prepare_imports(root)
    library, store = AssetLibrary(root), MidiStore(AssetLibrary(root))
    original = Path(prepared['source_path'])
    source_hash = sha256(original)
    ctx.update('midi_decode', message='解码选中的音乐范围')
    decoded = job_dir/'artifacts'/'midi'/'source.wav'
    duration = decode_audio(original, decoded, prepared['clip_start'], prepared['clip_end'], ctx)
    samples, rate = sf.read(decoded, dtype='float32')
    ctx.update('midi_tempo', message='估计速度；可在结果中修正，不重复识别')
    import librosa
    tempo, _ = librosa.beat.beat_track(y=samples, sr=rate)
    estimated = float(np.asarray(tempo).reshape(-1)[0])
    estimated = estimated if math.isfinite(estimated) and estimated > 0 else 120.0
    bpm = prepared['bpm'] or estimated
    ctx.check_cancelled()
    refs = prepared.get('_local_references', [])
    source_ref = next((item for item in refs if item.get('asset_id')), None)
    if source_ref:
        asset_id, revision_id = source_ref['asset_id'], source_ref.get('revision_id', '')
    else:
        original_asset = library.import_file(original, kind='song', title=prepared['title'], provenance={'source': str(original)})
        asset_id, revision_id = original_asset['id'], original_asset['current_revision_id']
    data = blank_document(prepared['title']+' · 提取 MIDI')
    data['tempos'][0]['tempo'] = round(60_000_000/bpm)
    data['source'] = {'format': 'audio', 'asset_id': asset_id, 'revision_id': revision_id,
        'sha256': source_hash, 'clip_start': prepared['clip_start'], 'clip_end': prepared['clip_end'],
        'duration_seconds': duration, 'estimated_bpm': estimated, 'recognition_bpm': bpm}
    data['extraction'] = {'tracks': {role: {'status': 'pending'} for role in ('melody', 'chord', 'drums')}}
    document = store.create(data, prepared['project_id'], current=False)
    result = {'midi_document_id': document['id'], 'project_id': prepared['project_id'], 'duration_seconds': duration, 'bpm': bpm}
    result.update({key: prepared[key] for key in ('editor_document_id','editor_document_version') if key in prepared})
    ctx.update('midi_notes', result=result, message='识别旋律、候选乐器与鼓组')
    cache_key = hashlib.sha256(json.dumps({'source': source_hash, 'start': prepared['clip_start'], 'end': prepared['clip_end'],
        'upstream': UPSTREAM_COMMIT, 'yourmt3': YOURMT3['revision'],
        'models': {str(path.relative_to(model_directory(root))): sha256(path) for path in
            [model_directory(root)/'SymbolicTranscriptor'/YOURMT3['relative'],
             *(model_directory(root)/'SymbolicTranscriptor'/'chord'/name for name in CHORD_NAMES)]},
        'frontend': 'raw_seconds-v1-float32'}, sort_keys=True).encode()).hexdigest()
    cache = store.home/'cache'/cache_key
    cache.mkdir(parents=True, exist_ok=True)
    if not np.any(samples):
        # Exact digital silence has no notes; do not let a recognizer invent a melody.
        atomic_json(cache/'notes.json', [])
        atomic_json(cache/'chords.json', [])
    raw, errors = {'notes': [], 'chords': []}, []
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('本地音乐转 MIDI 需要 NVIDIA CUDA；编辑与试听不需要')
    device, backend = torch.device('cuda:0'), None
    ctx.memory('midi_start')

    def commit():
        nonlocal document
        data['raw_transcription'] = raw
        data['tracks'] = tracks_from_raw(raw, bpm)
        for role in ('melody', 'chord', 'drums'):
            status = data['extraction']['tracks'][role]
            status['note_count'] = sum(len(t['notes']) for t in data['tracks'] if t['role'] == role)
            if status['status'] == 'complete' and not status['note_count']:
                status['status'] = 'empty'
        try:
            document = store.save(document['id'], data, document['version'], prepared['project_id'])
        except VersionConflict:
            # A user can open the committed stage while recognition is still running.
            # Keep their edits and finish in a sibling document instead of overwriting them.
            data['source']['parent_document_id']=document['id']
            document=store.create(data, prepared['project_id'], current=False)
            result['warnings']=['提取期间原文档已被编辑；完整识别结果另存，原编辑已保留。']
            result['midi_document_id']=document['id']
        result['document_version'] = document['version']
        result['tracks'] = data['extraction']['tracks']
        if any(state['status'] in ('complete', 'empty') for state in result['tracks'].values()):
            # Commit to the library before starting another cancellable model stage.
            try:
                asset = store.publish(document['id'], document['version'], group_update=True)
            except VersionConflict:
                data['source']['parent_document_id']=document['id']
                document=store.create(data, prepared['project_id'], current=False)
                result.update(midi_document_id=document['id'],document_version=document['version'],
                              warnings=['提取期间原文档已被编辑；完整识别结果另存，原编辑已保留。'])
                asset=store.publish(document['id'],document['version'],group_update=True)
            result['asset_ids'] = [asset['id']]
        ctx.update(ctx.last_stage or 'midi_export', result=result)

    try:
        try:
            notes, warning = cached_events(cache/'notes.json', 'notes', duration)
            if warning:
                result.setdefault('warnings', []).append(warning)
            if notes is None:
                from mulacover._symbolic_transcription.melody import MelodyTranscriber
                backend = MelodyTranscriber(model_directory(root)/'SymbolicTranscriptor'/YOURMT3['relative'], device)
                notes = backend.transcribe(decoded, check_cancelled=ctx.check_cancelled,
                    on_progress=lambda a,b: ctx.progress('midi_notes', a, b))
                notes = validate_raw_events([asdict(note) for note in notes], 'notes', duration)
                atomic_json(cache/'notes.json', notes)
            raw['notes'] = notes
            for role in ('melody', 'drums'):
                data['extraction']['tracks'][role] = {'status': 'complete'}
        except Cancelled:
            raise
        except Exception as exc:
            errors.append(str(exc))
            for role in ('melody', 'drums'):
                data['extraction']['tracks'][role] = {'status': 'failed', 'error': str(exc)}
        finally:
            backend = None
            gc.collect()
            torch.cuda.empty_cache()
            commit()
        ctx.check_cancelled()
        ctx.update('midi_chords', message='识别和弦音级（五个模型）', result=result)
        try:
            chords, warning = cached_events(cache/'chords.json', 'chords', duration)
            if warning:
                result.setdefault('warnings', []).append(warning)
            if chords is None:
                from mulacover._symbolic_transcription.harmony import ChordTranscriber
                backend = ChordTranscriber(model_directory(root)/'SymbolicTranscriptor'/'chord', device)
                chords = backend.transcribe(decoded, bpm, raw_seconds=True, check_cancelled=ctx.check_cancelled,
                    on_progress=lambda a,b: ctx.progress('midi_chords', a, b))
                chords = validate_raw_events(chords, 'chords', duration)
                atomic_json(cache/'chords.json', chords)
            raw['chords'] = chords
            data['extraction']['tracks']['chord'] = {'status': 'complete'}
        except Cancelled:
            raise
        except Exception as exc:
            errors.append(str(exc))
            data['extraction']['tracks']['chord'] = {'status': 'failed', 'error': str(exc)}
        finally:
            backend = None
            gc.collect()
            torch.cuda.empty_cache()
            commit()
        ctx.check_cancelled()
        ctx.update('midi_export', result=result, message='保存三轨 MIDI 与来源记录')
        if all(data['extraction']['tracks'][role]['status'] == 'failed' for role in ('melody', 'chord', 'drums')):
            raise RuntimeError('三轨识别均失败：'+'；'.join(errors))
        result.update(partial=bool(errors), failures=errors)
        return result
    finally:
        if not result.get('asset_ids') and any(state['status'] in ('complete', 'empty') for state in data['extraction']['tracks'].values()):
            # Successful stages survive later cancellation/failure in the asset library too.
            asset = store.publish(document['id'], document['version'], group_update=True)
            result['asset_ids'] = [asset['id']]
            ctx.update(ctx.last_stage or 'midi_export', result=result)
        backend = None
        gc.collect()
        torch.cuda.empty_cache()
        ctx.memory('midi_finished')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--job-dir', type=Path, required=True)
    args = parser.parse_args()
    configure_environment(args.root)
    job = json.loads((args.job_dir/'job.json').read_text(encoding='utf-8-sig'))
    ctx = JobContext(args.job_dir)
    try:
        ctx.finish(result=run(args.root, args.job_dir, job['request'], ctx))
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
