"""Local HTTP routes for the RVC workbench; computation stays in workers."""
import json
import mimetypes
from pathlib import Path
import re

from .io import atomic_json, within
from .rvc_library import get_voice, list_voices, locations, rename_voice, trash_voice
from .rvc_projects import create_project, get_project, list_projects, save_project
from .rvc_training import training_options


def send_audio(handler, path: Path):
    try:
        _send_audio(handler, path)
    except ConnectionError:
        # Browsers cancel an old range when seeking, replacing or leaving audio.
        return


def _send_audio(handler, path: Path):
    """Serve previews in bounded chunks and support the browser's range reads."""
    if not path.is_file():
        raise ValueError('试听文件不存在')
    size = path.stat().st_size
    start, end, partial = 0, size - 1, False
    requested = handler.headers.get('Range')
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
        if not match or not any(match.groups()):
            handler._error(416, '不支持的音频读取范围')
            return
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(size - 1, int(last)) if last else size - 1
        else:
            start = max(0, size - int(last))
        if start > end or start >= size:
            handler.send_response(416)
            handler.send_header('Content-Range', f'bytes */{size}')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return
        partial = True
    handler.send_response(206 if partial else 200)
    handler.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    handler.send_header('Content-Length', str(end - start + 1))
    handler.send_header('Accept-Ranges', 'bytes')
    handler.send_header('Cache-Control', 'no-cache')
    if partial:
        handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
    handler.end_headers()
    with path.open('rb') as stream:
        stream.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def get(handler, parsed, store, root: Path) -> bool:
    path = parsed.path
    match = re.fullmatch(r'/api/rvc/download/([a-f0-9]{32}-[a-f0-9]{8}\.zip)', path)
    if match:
        send_audio(handler, within(root / 'exports/rvc', root / 'exports/rvc' / match[1]))
        return True
    if path == '/api/rvc':
        handler._json(200, {'projects': list_projects(root), 'voices': list_voices(root),
                           'locations': {k: str(v) for k, v in locations(root).items()},
                           'defaults': training_options({})})
        return True
    match = re.fullmatch(r'/api/rvc/projects/([a-f0-9]{32})/preflight', path)
    if match:
        from .rvc_preflight import check_training
        handler._json(200, check_training(root, get_project(root, match[1])))
        return True
    match = re.fullmatch(r'/api/rvc/projects/([a-f0-9]{32})', path)
    if match:
        handler._json(200, get_project(root, match[1]))
        return True
    match = re.fullmatch(r'/api/rvc/projects/([a-f0-9]{32})/audio/([a-f0-9]{32})/(original|vocal)', path)
    if match:
        project = get_project(root, match[1])
        material = next((item for item in project['materials'] if item['id'] == match[2]), None)
        if material is None:
            raise ValueError('素材不存在')
        key = 'path' if match[3] == 'original' else 'separated_path'
        if not material.get(key):
            raise ValueError('音频尚未生成')
        file = within(locations(root)['datasets'], Path(material[key]))
        send_audio(handler, file)
        return True
    match = re.fullmatch(r'/api/rvc/voices/([a-f0-9]{32})/preview', path)
    if match:
        voice = get_voice(root, match[1])
        if not voice.get('preview'):
            raise ValueError('该音色还没有试听音频')
        directory = Path(voice['directory'])
        send_audio(handler, within(directory, directory / voice['preview']))
        return True
    return False


def post(handler, parsed, store, root: Path) -> bool:
    path = parsed.path
    if not path.startswith('/api/rvc/'):
        return False
    # Project mutations cannot race with an importing/training worker snapshot.
    with store.lock:
        candidates = [ident for ident, job in store.jobs.items()
                      if (job.get('kind', '').startswith('rvc_') or job.get('kind') in {'reference_cover', 'voice_convert'})
                      and job['status'] not in {'complete', 'failed', 'cancelled'}]
    active = [ident for ident in candidates if store.get(ident)['status'] not in {'complete', 'failed', 'cancelled'}]
    if active:
        raise ValueError('音色任务正在运行或排队，请结束后再修改项目')
    data = handler._body_json(1024 * 1024)
    if not isinstance(data, dict):
        raise ValueError('参数必须是对象')
    if path == '/api/rvc/storage/preview':
        from .rvc_storage import preview
        handler._json(200, preview(root, data.get('directories', {})))
        return True
    match = re.fullmatch(r'/api/rvc/voices/([a-f0-9]{32})/(rename|remove|open)', path)
    if match:
        if match[2] == 'rename':
            handler._json(200, rename_voice(root, match[1], str(data.get('name', ''))))
        elif match[2] == 'remove':
            handler._json(200, {'recovery_directory': str(trash_voice(root, match[1]))})
        else:
            import os
            voice = get_voice(root, match[1])
            os.startfile(voice['directory'])
            handler._json(200, {'opened': voice['directory']})
        return True
    if path == '/api/rvc/projects':
        handler._json(201, create_project(root, data.get('name', '我的音色')))
        return True
    match = re.fullmatch(r'/api/rvc/projects/([a-f0-9]{32})', path)
    if match:
        project = get_project(root, match[1])
        if 'options' in data:
            project['options'] = training_options(data['options'])
        if 'speakers' in data:
            speakers, seen = [], set()
            for item in data['speakers']:
                sid, name = int(item['id']), str(item['name']).strip()
                if sid in seen or not 0 <= sid <= 109 or not name or len(name) > 100 or any(c in name for c in '|\n\r'):
                    raise ValueError('说话人 ID 或名称无效')
                speakers.append({'id': sid, 'name': name})
                seen.add(sid)
            if not speakers:
                raise ValueError('至少需要一个说话人')
            project['speakers'] = speakers
        updates = {item['id']: item for item in data.get('materials', [])}
        ids = {item['id'] for item in project['materials']}
        if set(updates) - ids:
            raise ValueError('更新包含不存在的素材')
        for item in project['materials']:
            update = updates.get(item['id'], {})
            for field in ('enabled', 'reviewed'):
                if field in update:
                    if type(update[field]) is not bool:
                        raise ValueError('素材选择与试听确认必须是布尔值')
                    item[field] = update[field]
            if 'speaker_id' in update:
                sid = int(update['speaker_id'])
                if sid not in {speaker['id'] for speaker in project['speakers']}:
                    raise ValueError('素材对应的说话人不存在')
                item['speaker_id'] = sid
        remove = set(data.get('remove_material_ids', []))
        if remove - ids:
            raise ValueError('要移除的素材不存在')
        # Originals remain in the project dataset directory for recovery.
        project['materials'] = [item for item in project['materials'] if item['id'] not in remove]
        if any(item['speaker_id'] not in {speaker['id'] for speaker in project['speakers']} for item in project['materials']):
            raise ValueError('请先移除或重新分配该说话人的素材，再移除说话人')
        save_project(root, project)
        handler._json(200, project)
        return True
    if path == '/api/rvc/open':
        import os
        allowed = locations(root)
        key = str(data.get('kind', 'projects'))
        if key not in allowed:
            raise ValueError('不支持的目录')
        allowed[key].mkdir(parents=True, exist_ok=True)
        os.startfile(str(allowed[key]))
        handler._json(200, {'opened': str(allowed[key])})
        return True
    return False
