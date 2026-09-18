"""MIDI document routes; storage and downloads never call a model."""
from pathlib import Path
from urllib.parse import parse_qs

from .midi_document import MidiStore, VersionConflict, blank_document, generation_check, parse_midi, integer
from .io import sha256, within


def input_reference(library, root, value):
    """Resolve the same durable descriptors used by local jobs, without a worker."""
    from .workbench_api import _allowed_source
    if isinstance(value, dict) and '$asset' in value:
        source, info = library.revision_file(str(value['$asset']), str(value.get('revision_id') or ''))
        return source, {'asset_id': str(value['$asset']), 'revision_id': info['id']}
    if isinstance(value, dict) and '$job_file' in value:
        descriptor = value['$job_file']
        import re, json
        job_id = str(descriptor.get('job_id') or '')
        if not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{8}', job_id):
            raise ValueError('任务文件引用无效')
        folder = root/'outputs'/'jobs'/job_id
        status = json.loads((folder/'status.json').read_text(encoding='utf-8'))
        if status.get('status') != 'complete':
            raise ValueError('只能打开已完成任务的 MIDI')
        source = within(folder, folder/str(descriptor.get('relative') or ''))
        return source, {'job_id': job_id, 'relative': str(descriptor['relative'])}
    return _allowed_source(root.resolve(), value), {}

PREFIX = '/api/workbench/midi'


def get(handler, parsed, library, *, head=False):
    if not parsed.path.startswith(PREFIX):
        return False
    from .workbench_api import stream_file
    store, query = MidiStore(library), parse_qs(parsed.query)
    project = query.get('project_id', [''])[0]
    path = parsed.path[len(PREFIX):].strip('/').split('/')
    if path == ['current']:
        handler._json(200, {'document': store.current(project)})
    elif path == ['documents']:
        handler._json(200, store.page(project,int(query.get('limit',['20'])[0]),int(query.get('offset',['0'])[0]),archived=query.get('archived',['0'])[0]=='1'))
    elif path == ['cache']:
        handler._json(200, store.cache_info())
    elif len(path) == 2 and path[0] == 'documents':
        version = int(query['version'][0]) if query.get('version') else None
        handler._json(200, store.get(path[1], version))
    elif len(path) == 3 and path[0] == 'documents' and path[2] == 'download':
        version = int(query.get('version', ['0'])[0])
        target = store.download(path[1], version, query.get('role', ['combined'])[0])
        stream_file(handler, target, {'blob_sha256': sha256(target), 'mime': 'application/zip' if target.suffix == '.zip' else 'audio/midi'}, head=head)
    else:
        handler._json(404, {'error': 'MIDI 接口不存在'})
    return True


def post(handler, parsed, library, root):
    if not parsed.path.startswith(PREFIX):
        return False
    from .workbench_api import _allowed_source
    store, data = MidiStore(library), handler._body_json(16*1024*1024)
    path = parsed.path[len(PREFIX):].strip('/').split('/')
    try:
        if path == ['documents']:
            result = store.create(data.get('document') or blank_document(), data.get('project_id', ''))
        elif path == ['examples', 'simple']:
            from .midi_document import example_document
            result = store.create(example_document(), data.get('project_id', ''))
        elif path == ['cache','clear']:
            if data.get('confirmed') is not True:
                raise ValueError('请先确认识别缓存清理说明')
            result = store.clear_cache()
        elif path == ['source']:
            source, ref = input_reference(library, root, data.get('source_path'))
            from .asset_library import AUDIO_SUFFIXES
            if source.suffix.lower() not in AUDIO_SUFFIXES:
                raise ValueError('请选择音频素材')
            project = store.scope(data.get('project_id', ''))
            if not ref.get('asset_id'):
                asset = library.import_file(source, kind='song', title=str(data.get('title') or source.name), provenance=ref)
                ref.update(asset_id=asset['id'], revision_id=asset['current_revision_id'])
            if project:
                library.add_to_project(project, ref['asset_id'], revision_id=ref['revision_id'], role='source')
            result = {'source_path': {'$asset': ref['asset_id'], 'revision_id': ref['revision_id']},
                      'title': str(data.get('title') or source.name),
                      'url': f'/api/workbench/assets/{ref["asset_id"]}/content?revision_id={ref["revision_id"]}'}
        elif path == ['import']:
            if data.get('asset_id'):
                source, info = library.revision_file(data['asset_id'], data.get('revision_id', ''))
                asset = library.get_asset(data['asset_id'])
                document = parse_midi(source, asset['title'])
            else:
                source = _allowed_source(root.resolve(), data.get('source_path'))
                if source.suffix.lower() not in ('.mid', '.midi'):
                    raise ValueError('请选择 .mid 或 .midi 文件')
                # Parse first; malformed input stays in uploads and is never silently replaced.
                document = parse_midi(source, data.get('title') or source.name)
                asset = library.import_file(source, kind='midi', title=document['title'])
                info = {'id': asset['current_revision_id']}
            document['source'] = {'asset_id': asset['id'], 'revision_id': info['id'], 'sha256': sha256(source), 'format': 'midi'}
            result = store.create(document, data.get('project_id', ''))
        elif path == ['import-group']:
            import math
            groups, refs = [], []
            for role in ('melody', 'chord', 'drums'):
                value = (data.get('sources') or {}).get(role)
                if not value:
                    continue
                source, ref = input_reference(library, root, value)
                parsed_midi = parse_midi(source, data.get('title') or '旋律与和弦 MIDI')
                if not ref.get('asset_id'):
                    original = library.import_file(source, kind='midi', title=f'{data.get("title") or "MIDI 条件"} · {role}')
                    ref.update(asset_id=original['id'], revision_id=original['current_revision_id'])
                groups.append((role, parsed_midi))
                refs.append({**ref, 'sha256': sha256(source), 'role': role})
            if not groups:
                raise ValueError('还没有 MIDI 条件，请先提取或上传')
            ppq = math.lcm(*(group['ppq'] for _, group in groups))
            if ppq > 32767:
                raise ValueError('这些 MIDI 的 PPQ 无法直接合并，请先统一 PPQ')
            document = blank_document(data.get('title') or '重新编曲 MIDI')
            document['ppq'], document['tracks'] = ppq, []
            base = groups[0][1]
            for key in ('tempos', 'meters'):
                document[key] = [{**e, 'tick': e['tick']*ppq//base['ppq']} for e in base[key]]
            for role, group in groups:
                for key in ('tempos', 'meters'):
                    converted = [{**e, 'tick': e['tick']*ppq//group['ppq']} for e in group[key]]
                    if converted != document[key]:
                        raise ValueError('三个 MIDI 的速度或拍号不同，请先统一时间线再合并')
                for track in group['tracks']:
                    track['role'] = role
                    for note in track['notes']:
                        note['tick'] = note['tick']*ppq//group['ppq']
                        note['duration'] = note['duration']*ppq//group['ppq']
                    for event in track['events']:
                        event['tick'] = event['tick']*ppq//group['ppq']
                    document['tracks'].append(track)
            for role in ('melody', 'chord', 'drums'):
                if not any(track['role'] == role for track in document['tracks']):
                    from .midi_document import blank_track
                    document['tracks'].append(blank_track(role))
            document['source'] = {'format': 'midi_group', 'references': refs}
            result = store.create(document, data.get('project_id', ''))
        elif len(path) == 3 and path[0] == 'documents':
            document_id, action = path[1:]
            if action == 'save':
                result = store.save(document_id, data.get('document'), data.get('version'), data.get('project_id', ''))
            elif action == 'select':
                result = store.select(document_id, data.get('project_id', ''))
            elif action in ('archive','restore'):
                result = store.archive(document_id,data.get('project_id',''),archived=action=='archive')
            elif action == 'check':
                result = generation_check(store.get(document_id), data.get('allow_empty_chords') is True)
            elif action == 'correct-bpm':
                from .mulacover_core import prepare_imports
                from .midi_extract_worker import tracks_from_raw
                from .midi_document import number
                document = store.get(document_id)
                if not document.get('raw_transcription'):
                    raise ValueError('这份 MIDI 没有原始音频识别时间')
                bpm = number(data.get('bpm'), 'BPM', 20, 400)
                prepare_imports(root)
                document['tracks'] = tracks_from_raw(document['raw_transcription'], bpm)
                document['tempos'] = [{'tick': 0, 'tempo': round(60_000_000/bpm)}]
                document['source']['recognition_bpm'] = bpm
                result = store.save(document_id, document, data.get('version'), data.get('project_id', ''))
            elif action == 'publish':
                result = store.publish(document_id, integer(data.get('version'), '文档版本', 1, 1_000_000_000))
            elif action == 'snapshot':
                result = store.snapshot(document_id, data.get('version'),
                    allow_empty_chords=data.get('allow_empty_chords') is True,
                    acknowledge_projection=data.get('acknowledge_projection') is True)
            else:
                raise ValueError('MIDI 操作不存在')
        else:
            raise ValueError('MIDI 操作不存在')
    except VersionConflict as exc:
        handler._json(409, {'error': str(exc), 'code': 'midi_version_conflict'})
        return True
    handler._json(200, result)
    return True
