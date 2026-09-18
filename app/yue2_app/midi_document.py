"""Durable, project-scoped MIDI documents. No torch or generation imports."""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import tempfile
import uuid
import zipfile

from .asset_library import AssetLibrary
from .io import atomic_json, json_file_lock, within

ROLES = ('melody', 'chord', 'drums', 'other')
LABELS = {'melody': '旋律', 'chord': '和弦', 'drums': '鼓组', 'other': '其他'}
MAX_NOTES = 100_000
MAX_TICK = 100_000_000


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('MIDI 文档 ID 无效')
    return value


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name}必须是 {low}–{high} 之间的整数')
    return value


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name}必须在 {low}–{high} 之间')
    return float(value)


def blank_track(role, name=''):
    return {'id': uuid.uuid4().hex, 'name': name or LABELS[role], 'role': role,
            'channel': 9 if role == 'drums' else {'melody': 0, 'chord': 1, 'other': 2}[role],
            'program': 0, 'notes': [], 'events': [], 'muted': False, 'solo': False}


def blank_document(title='未命名 MIDI'):
    return {'schema': 1, 'title': title, 'ppq': 480, 'tempos': [{'tick': 0, 'tempo': 500_000}],
            'meters': [{'tick': 0, 'numerator': 4, 'denominator': 4}],
            'tracks': [blank_track(role) for role in ROLES[:3]], 'source': {},
            'mapping_confirmed': True, 'settings': {'snap': 16, 'zoom': 1, 'pitch_low': 48},
            'generation': {'lyrics': '', 'genre': '', 'instrument': '', 'mood': '', 'topic': '',
                           'duration_seconds': 30, 'seed': 831001, 'decode_seed': 831002}}


def example_document():
    data=blank_document('四小节练习 · C 大调')
    data['source']={'format': 'example', 'description': '内置练习乐谱，不是自动识别结果'}
    def note(tick,pitch,duration,velocity):
        return {'id': uuid.uuid4().hex, 'tick': tick, 'pitch': pitch, 'duration': duration, 'velocity': velocity}
    data['tracks'][0]['notes']=[note(index*960,pitch,840,90) for index,pitch in enumerate((60,64,67,69,67,64,62,60))]
    for index,chord in enumerate(((60,64,67),(59,62,67),(60,64,69),(60,65,69))):
        data['tracks'][1]['notes'].extend(note(index*1920,pitch,1920,75) for pitch in chord)
    data['tracks'][2]['notes']=[note(beat*480,36 if beat%2==0 else 38,60,65) for beat in range(16)]
    data['generation']['lyrics']='[Verse]\n风吹过窗边\n阳光落在琴键\n把今天的心愿\n唱给明天听见'
    data['generation'].update(genre='acoustic pop',instrument='piano, acoustic guitar, light drums',mood='warm, hopeful')
    return data


def validate_document(value):
    import mido
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('MIDI 文档格式无效')
    data = deepcopy(value)
    if not isinstance(data.get('title'), str) or not 1 <= len(data['title'].strip()) <= 200:
        raise ValueError('请填写不超过 200 字的 MIDI 名称')
    integer(data.get('ppq'), 'PPQ', 1, 32767)
    for key in ('tempos', 'meters'):
        events = data.get(key)
        if not isinstance(events, list) or not 1 <= len(events) <= 10000:
            raise ValueError('速度或拍号记录无效')
        seen = set()
        for event in events:
            if not isinstance(event, dict):
                raise ValueError('速度或拍号记录无效')
            tick = integer(event.get('tick'), '事件起点', 0, MAX_TICK)
            if tick in seen:
                raise ValueError('同一位置不能有重复速度或拍号')
            seen.add(tick)
            if key == 'tempos':
                integer(event.get('tempo'), '速度', 1, 0xffffff)
            else:
                integer(event.get('numerator'), '拍号分子', 1, 255)
                if event.get('denominator') not in (1, 2, 4, 8, 16, 32, 64, 128):
                    raise ValueError('拍号分母无效')
                for field, default in (('clocks_per_click', 24), ('notated_32nd_notes_per_beat', 8)):
                    if field in event:
                        integer(event[field], '拍号时钟', 0, 255)
        events.sort(key=lambda event: event['tick'])
        if events[0]['tick'] != 0:
            raise ValueError('速度和拍号必须包含起点记录')
    tracks = data.get('tracks')
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 128:
        raise ValueError('MIDI 轨道数量应为 1–128')
    ids, total, event_total = set(), 0, 0
    for track in tracks:
        ident(track.get('id'))
        if track['id'] in ids:
            raise ValueError('轨道 ID 重复')
        ids.add(track['id'])
        if track.get('role') not in ROLES:
            raise ValueError('请为轨道选择用途')
        if not isinstance(track.get('name'), str) or len(track['name']) > 200:
            raise ValueError('轨道名称无效')
        integer(track.get('channel'), '通道', 0, 15)
        integer(track.get('program'), '音色编号', 0, 127)
        if not isinstance(track.get('notes'), list) or not isinstance(track.get('events', []), list):
            raise ValueError('轨道音符或事件格式无效')
        total += len(track['notes'])
        event_total += len(track.get('events', []))
        if total > MAX_NOTES or event_total > 200_000:
            raise ValueError('音符或控制事件过多，请拆分 MIDI')
        for note in track['notes']:
            ident(note.get('id'))
            if note['id'] in ids:
                raise ValueError('音符 ID 重复')
            ids.add(note['id'])
            integer(note.get('tick'), '音符起点', 0, MAX_TICK)
            integer(note.get('duration'), '音符长度', 1, MAX_TICK - note['tick'])
            integer(note.get('pitch'), '音高', 0, 127)
            integer(note.get('velocity'), '力度', 1, 127)
        for event in track.get('events', []):
            integer(event.get('tick'), '控制事件起点', 0, MAX_TICK)
            message = event.get('message')
            if not isinstance(message, dict) or message.get('type') in ('note_on', 'note_off', 'end_of_track', 'set_tempo', 'time_signature'):
                raise ValueError('不支持的控制事件')
            try:
                mido.Message.from_dict(message) if not event.get('meta') else mido.MetaMessage.from_dict(message)
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError('无效的 MIDI 控制事件') from exc
    if not isinstance(data.get('settings', {}), dict) or not isinstance(data.get('generation', {}), dict):
        raise ValueError('编辑设置格式无效')
    if len(json.dumps(data, ensure_ascii=False).encode()) > 16 * 1024 * 1024:
        raise ValueError('MIDI 文档超过 16 MiB，请拆分')
    # Identity, version and project always come from the server row.
    for key in ('id', 'version', 'project_id', 'created_at', 'updated_at', 'asset_id', 'archived_at'):
        data.pop(key, None)
    return data


def parse_midi(path: Path, title=''):
    import mido
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('MIDI 文件不能超过 16 MiB')
    try:
        try:
            midi = mido.MidiFile(path, clip=False, charset='utf-8')
        except UnicodeDecodeError:
            midi = mido.MidiFile(path, clip=False, charset='latin1')
    except (OSError, EOFError, ValueError, KeyError) as exc:
        raise ValueError(f'MIDI 文件无法读取：{exc}') from exc
    if midi.type not in (0, 1):
        raise ValueError('不支持各轨独立时间线的 MIDI Type 2，请先导出为 Type 0/1')
    if midi.ticks_per_beat <= 0:
        raise ValueError('不支持 SMPTE 时间码，请导出为正 PPQ 的 MIDI')
    data = blank_document(title or path.name)
    data.update(ppq=midi.ticks_per_beat, tracks=[], mapping_confirmed=False)
    tempos, meters = {0: 500000}, {0: {'tick': 0, 'numerator': 4, 'denominator': 4}}
    count = 0
    for original in midi.tracks:
        name = next((msg.name for msg in original if msg.type == 'track_name'), '')
        channels = {}
        tick, active = 0, defaultdict(deque)
        for msg in original:
            count += 1
            if count > 500_000:
                raise ValueError('MIDI 事件过多，请拆分')
            tick += msg.time
            integer(tick, 'MIDI 时间', 0, MAX_TICK)
            if msg.type == 'set_tempo':
                tempos[tick] = msg.tempo
                continue
            if msg.type == 'time_signature':
                meters[tick] = {'tick': tick, 'numerator': msg.numerator, 'denominator': msg.denominator}
                for field, default in (('clocks_per_click', 24), ('notated_32nd_notes_per_beat', 8)):
                    if getattr(msg, field) != default:
                        meters[tick][field] = getattr(msg, field)
                continue
            if msg.type in ('end_of_track', 'track_name'):
                continue
            channel = getattr(msg, 'channel', -1)
            if channel not in channels:
                role = 'drums' if channel == 9 else 'other'
                if channel != 9:
                    if re.search(r'chord|和弦', name, re.I):
                        role = 'chord'
                    elif re.search(r'melody|vocal|旋律|主唱', name, re.I):
                        role = 'melody'
                track = blank_track(role, name or f'轨道 {len(data["tracks"])+1}')
                track['channel'] = max(0, channel)
                channels[channel] = track
                data['tracks'].append(track)
            track = channels[channel]
            if msg.type in ('note_on', 'note_off'):
                key = (channel, msg.note)
                if msg.type == 'note_on' and msg.velocity:
                    active[key].append((tick, msg.velocity))
                else:
                    if not active[key]:
                        raise ValueError('MIDI 含没有起始音符的 note-off，请先修复源文件')
                    start, velocity = active[key].popleft()
                    track['notes'].append({'id': uuid.uuid4().hex, 'tick': start,
                        'duration': max(1, tick-start), 'pitch': msg.note, 'velocity': velocity})
            else:
                if msg.type == 'program_change' and tick == 0:
                    track['program'] = msg.program
                raw = msg.dict()
                raw['time'] = 0
                track['events'].append({'tick': tick, 'meta': msg.is_meta, 'message': raw})
        if any(active.values()):
            raise ValueError('MIDI 有未结束的音符，请先修复源文件')
    data['tempos'] = [{'tick': t, 'tempo': value} for t, value in sorted(tempos.items())]
    data['meters'] = [value for _, value in sorted(meters.items())]
    if not data['tracks']:
        data['tracks'] = [blank_track('melody')]
    return validate_document(data)


def atomic_payload(destination, writer):
    """Publish complete bytes with short sibling names, including on Windows."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with json_file_lock(destination):
        descriptor, name = tempfile.mkstemp(prefix='m-', suffix='.tmp', dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                writer(stream)
            for attempt in range(20):
                try:
                    os.replace(temporary, destination)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(.01)
        finally:
            temporary.unlink(missing_ok=True)


def note_voices(notes):
    """Split nested same-pitch notes into tracks so FIFO MIDI readers keep durations."""
    voices, endings = [[]], [{}]
    for note in sorted(notes, key=lambda n: (n['tick'], n['tick']+n['duration'])):
        end = note['tick']+note['duration']
        for index, previous in enumerate(endings):
            if previous.get(note['pitch'], -1) <= end:
                break
        else:
            if len(voices) >= 128:
                raise ValueError('同音高嵌套音符超过 128 个声部，请拆分素材')
            index = len(voices)
            voices.append([])
            endings.append({})
        voices[index].append(note)
        endings[index][note['pitch']] = end
    return voices


def export_midi(data, destination: Path, role='', *, model=False):
    import mido
    data = validate_document(data)
    output_ppq=480 if model else data['ppq']
    midi = mido.MidiFile(type=1, ticks_per_beat=output_ppq, charset='utf-8')
    conductor = mido.MidiTrack()
    midi.tracks.append(conductor)
    events = [(v['tick'], mido.MetaMessage('set_tempo', tempo=v['tempo'])) for v in data['tempos']]
    events += [(v['tick'], mido.MetaMessage('time_signature', **{k: v[k] for k in ('numerator', 'denominator', 'clocks_per_click', 'notated_32nd_notes_per_beat') if k in v})) for v in data['meters']]
    last = 0
    for tick, message in sorted(events, key=lambda item: item[0]):
        if model:
            tick=round(tick*output_ppq/data['ppq'])
        conductor.append(message.copy(time=tick-last))
        last = tick
    conductor.append(mido.MetaMessage('end_of_track'))
    expanded = []
    for track in data['tracks']:
        if role and track['role'] != role:
            continue
        for index, notes in enumerate(note_voices(track['notes'])):
            item = {**track, 'notes': notes}
            if index:
                item['name'] = f'{track["name"]} · 声部 {index+1}'
                item['events'] = [e for e in track.get('events', []) if not e.get('meta')]
            expanded.append(item)
    if len(expanded) > 128:
        raise ValueError('导出所需声部超过 128 条轨道，请拆分素材')
    for track in expanded:
        if role and track['role'] != role:
            continue
        output = mido.MidiTrack()
        midi.tracks.append(output)
        channel = (9 if role == 'drums' else 0) if model else track['channel']
        output.append(mido.MetaMessage('track_name', name=track['name']))
        output.append(mido.Message('program_change', channel=channel, program=track['program']))
        events = []
        if not model:
            for event in track.get('events', []):
                msg = mido.MetaMessage.from_dict(event['message']) if event.get('meta') else mido.Message.from_dict(event['message'])
                events.append((event['tick'], 1, msg))
        for note in track['notes']:
            start, end = note['tick'], note['tick']+note['duration']
            if model:
                start=start*output_ppq/data['ppq']
                end=end*output_ppq/data['ppq']
                unit = output_ppq/4
                start = math.floor(start/unit+.5)*unit
                end = max(start+unit, math.floor(end/unit+.5)*unit)
                start, end = round(start), round(end)
            events.extend([(start, 2, mido.Message('note_on', channel=channel, note=note['pitch'], velocity=note['velocity'])),
                           (end, 0, mido.Message('note_off', channel=channel, note=note['pitch'], velocity=0))])
        last = 0
        for tick, _, message in sorted(events, key=lambda item: (item[0], item[1])):
            output.append(message.copy(time=tick-last))
            last = tick
        output.append(mido.MetaMessage('end_of_track'))
    atomic_payload(destination, lambda stream: midi.save(file=stream))
    return destination


def generation_check(data, allow_empty_chords=False):
    data = validate_document(data)
    counts = {role: sum(len(t['notes']) for t in data['tracks'] if t['role'] == role) for role in ROLES}
    errors, warnings, changed = [], [], 0
    if not data.get('mapping_confirmed'):
        errors.append('先确认每条轨道用于旋律、和弦、鼓组或仅保留')
    if not counts['melody']:
        errors.append('旋律至少需要一个音符；可指定真实乐器轨道或手动添加')
    if not counts['chord'] and not allow_empty_chords:
        errors.append('请添加和弦，或明确选择“使用空和弦条件”')
    unit = data['ppq']/4
    for track in data['tracks']:
        if track['role'] == 'other':
            continue
        for note in track['notes']:
            start = math.floor(note['tick']/unit+.5)
            end = max(start+1, math.floor((note['tick']+note['duration'])/unit+.5))
            changed += int(start*unit != note['tick'] or end*unit != note['tick']+note['duration'])
            if end > 5000:
                errors.append('生成条件超过 5000 个十六分音符，请先裁剪生成副本；原 MIDI 导出不受此限制')
                break
    if changed:
        warnings.append(f'{changed} 个音符在生成副本中会对齐十六分网格，原 MIDI 保留原时序')
    if len(data['tempos']) > 1:
        warnings.append('MIDI 含速度变化；MuLaCover 按拍位置读取条件，不保证生成音乐跟随速度变化')
    return {'ready': not errors, 'errors': list(dict.fromkeys(errors)), 'warnings': warnings,
            'changed_notes': changed, 'counts': counts}


def duration_seconds(data):
    end = max((n['tick'] + n['duration'] for t in data['tracks'] for n in t['notes']), default=0)
    seconds, previous, tempo = 0.0, 0, data['tempos'][0]['tempo']
    for event in data['tempos']:
        if event['tick'] > end:
            break
        seconds += (event['tick'] - previous) / data['ppq'] * tempo / 1_000_000
        previous, tempo = event['tick'], event['tempo']
    return seconds + (end - previous) / data['ppq'] * tempo / 1_000_000


class VersionConflict(ValueError):
    pass


def document_locked(method):
    @wraps(method)
    def serialized(self, document_id, *args, **kwargs):
        with json_file_lock(self.home/ident(document_id)/'edit'):
            return method(self, document_id, *args, **kwargs)
    return serialized


class MidiStore:
    def __init__(self, library: AssetLibrary):
        self.library = library
        self.home = library.home / 'midi'
        self.home.mkdir(exist_ok=True)
        with library.transaction() as db:
            migrate_versions = db.execute("SELECT 1 FROM sqlite_master WHERE name='midi_versions'").fetchone() is None
            db.execute('CREATE TABLE IF NOT EXISTS midi_documents(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version INTEGER NOT NULL, data_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, asset_id TEXT NOT NULL DEFAULT "")')
            db.execute('CREATE TABLE IF NOT EXISTS midi_current(scope TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES midi_documents(id))')
            db.execute('CREATE TABLE IF NOT EXISTS midi_snapshots(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES midi_documents(id), document_version INTEGER NOT NULL, data_json TEXT NOT NULL, created_at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS midi_versions(document_id TEXT NOT NULL REFERENCES midi_documents(id), version INTEGER NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(document_id,version))')
            if 'archived_at' not in {row[1] for row in db.execute('PRAGMA table_info(midi_documents)')}:
                db.execute('ALTER TABLE midi_documents ADD COLUMN archived_at REAL NOT NULL DEFAULT 0')
            if migrate_versions:
                for path in self.home.glob('*/*/document.json'):
                    if not re.fullmatch(r'[a-f0-9]{32}', path.parent.parent.name) or not path.parent.name.isdigit():
                        continue
                    frozen = json.loads(path.read_text(encoding='utf-8'))
                    if db.execute('SELECT 1 FROM midi_documents WHERE id=?', (path.parent.parent.name,)).fetchone():
                        db.execute('INSERT OR IGNORE INTO midi_versions VALUES(?,?,?)',
                            (path.parent.parent.name, int(path.parent.name), json.dumps(frozen, ensure_ascii=False)))

    def scope(self, value):
        project_id = str(value or '')
        if project_id:
            self.library.get_project(project_id)
        return project_id

    def get(self, document_id, version=None):
        with self.library.reading() as db:
            row = db.execute('SELECT * FROM midi_documents WHERE id=?', (ident(document_id),)).fetchone()
        if row is None:
            raise ValueError('MIDI 文档不存在')
        data = json.loads(row['data_json'])
        result = {**data, **{key: row[key] for key in ('id', 'project_id', 'version', 'created_at', 'updated_at', 'asset_id', 'archived_at')}}
        if version is not None and version != row['version']:
            integer(version, '文档版本', 1, 1_000_000_000)
            path = self.home / result['id'] / str(version) / 'document.json'
            with self.library.reading() as db:
                frozen = db.execute('SELECT data_json FROM midi_versions WHERE document_id=? AND version=?', (result['id'], version)).fetchone()
            if frozen:
                result = json.loads(frozen[0])
            elif path.is_file():
                result = json.loads(path.read_text(encoding='utf-8'))
            else:
                with self.library.reading() as db:
                    snapshot = db.execute('SELECT data_json FROM midi_snapshots WHERE document_id=? AND document_version=? ORDER BY created_at DESC LIMIT 1', (result['id'], version)).fetchone()
                if snapshot is None:
                    raise ValueError('这份 MIDI 的固定版本不存在')
                result = json.loads(snapshot[0])
        return result

    def current(self, project_id):
        scope = self.scope(project_id)
        with self.library.reading() as db:
            row = db.execute('SELECT document_id FROM midi_current WHERE scope=?', (scope,)).fetchone()
        return self.get(row[0]) if row else None

    def page(self, project_id, limit=20, offset=0, *, archived=False):
        scope = self.scope(project_id)
        integer(limit, '每页文档数量', 1, 100)
        integer(offset, '文档位置', 0, 1_000_000)
        condition='archived_at>0' if archived else 'archived_at=0'
        with self.library.reading() as db:
            total=db.execute(f'SELECT COUNT(*) FROM midi_documents WHERE project_id=? AND {condition}', (scope,)).fetchone()[0]
            rows = db.execute(f'SELECT id,version,updated_at,data_json FROM midi_documents WHERE project_id=? AND {condition} ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', (scope,limit,offset)).fetchall()
        return {'documents': [{'id': row['id'], 'version': row['version'], 'updated_at': row['updated_at'],
                 'title': json.loads(row['data_json'])['title']} for row in rows], 'total':total, 'limit':limit, 'offset':offset}

    def list(self, project_id):
        return self.page(project_id,100)['documents']

    def select(self, document_id, project_id):
        data = self.get(document_id)
        scope = self.scope(project_id)
        if data['project_id'] != scope:
            raise ValueError('这份 MIDI 属于其他项目，请导入副本')
        if data['archived_at']:
            raise ValueError('这份 MIDI 已归档，请先恢复文档或导入副本')
        with self.library.transaction() as db:
            db.execute('INSERT INTO midi_current VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET document_id=excluded.document_id', (scope, data['id']))
        return data

    def create(self, data, project_id='', *, current=True):
        data = validate_document(data)
        scope, document_id, now = self.scope(project_id), uuid.uuid4().hex, time.time()
        with self.library.transaction() as db:
            db.execute('INSERT INTO midi_documents(id,project_id,version,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                       (document_id, scope, 1, json.dumps(data, ensure_ascii=False), now, now))
            if current:
                db.execute('INSERT INTO midi_current VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET document_id=excluded.document_id', (scope, document_id))
        return self.get(document_id)

    @document_locked
    def save(self, document_id, data, version, project_id):
        ident(document_id)
        integer(version, '文档版本', 1, 1_000_000_000)
        scope, data = self.scope(project_id), validate_document(data)
        with self.library.transaction() as db:
            row = db.execute('SELECT project_id,version,archived_at FROM midi_documents WHERE id=?', (document_id,)).fetchone()
            if row is None or row['project_id'] != scope:
                raise ValueError('MIDI 文档与当前项目不一致')
            if row['version'] != version:
                raise VersionConflict('其他窗口已保存新版；本机编辑已保留，请重新载入或另存副本')
            if row['archived_at']:
                raise VersionConflict('文档已归档；本机编辑保留，可恢复文档或另存副本')
            db.execute('UPDATE midi_documents SET version=version+1,data_json=?,updated_at=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), time.time(), document_id))
            saved_row=db.execute('SELECT * FROM midi_documents WHERE id=?', (document_id,)).fetchone()
        return {**data, **{key: saved_row[key] for key in ('id','project_id','version','created_at','updated_at','asset_id','archived_at')}}

    @document_locked
    def archive(self, document_id, project_id, *, archived=True):
        scope=self.scope(project_id)
        data=self.get(document_id)
        if data['project_id']!=scope:
            raise ValueError('文档与当前项目不一致')
        with self.library.transaction() as db:
            db.execute('UPDATE midi_documents SET archived_at=?,updated_at=? WHERE id=?',
                       (time.time() if archived else 0,time.time(),document_id))
            if archived:
                db.execute('DELETE FROM midi_current WHERE document_id=?',(document_id,))
            else:
                db.execute('INSERT INTO midi_current VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET document_id=excluded.document_id',(scope,document_id))
        return {'document':self.get(document_id),'archived':archived}

    def cache_files(self):
        cache=self.home/'cache'
        if not cache.is_dir() or cache.is_symlink():
            return []
        files=[]
        for folder in cache.iterdir():
            if not re.fullmatch(r'[a-f0-9]{64}',folder.name) or not folder.is_dir() or folder.is_symlink():
                continue
            for name in ('notes.json','chords.json'):
                path=folder/name
                if path.is_file() and not path.is_symlink():
                    files.append(within(self.home,path))
        return files

    def cache_info(self):
        files=self.cache_files()
        return {'files':len(files),'bytes':sum(path.stat().st_size for path in files if path.exists()),
                'description':'仅原始识别缓存；编辑文档、已保存 MIDI、生成快照、音频和模型均保留。下次相同音频提取可能需要重新识别。'}

    def clear_cache(self):
        from filelock import Timeout
        changed,skipped=[],[]
        for path in self.cache_files():
            try:
                with json_file_lock(path,timeout=0):
                    path.unlink(missing_ok=True)
                changed.append(path.name)
            except (OSError,Timeout):
                skipped.append(path.name)
        return {'removed':len(changed),'skipped':len(skipped),**self.cache_info()}

    @document_locked
    def publish(self, document_id, version, *, group_update=False):
        data = self.get(document_id)
        if data['version'] != version:
            raise VersionConflict('文档已改变，请保存后重试')
        path = self.home / data['id'] / str(version) / 'combined.mid'
        export_midi(data, path)
        atomic_json(path.parent/'document.json', data)
        with self.library.transaction() as db:
            db.execute('INSERT OR IGNORE INTO midi_versions VALUES(?,?,?)', (data['id'], version, json.dumps(data, ensure_ascii=False)))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.library.reading() as db:
            row = db.execute('SELECT asset_id FROM midi_documents WHERE id=?', (data['id'],)).fetchone()
        previous, previous_id = None, None
        if row and row[0]:
            try:
                previous = self.library.get_asset(row[0])
                if previous['metadata'].get('document_version') == version and previous['status'] == 'active':
                    return previous
                if group_update and previous['status'] == 'active' and previous['metadata'].get('midi_document_id') == document_id:
                    previous_id = previous['id']
            except ValueError:
                pass
        # One grouped multi-track asset per explicit saved version, never per download.
        asset = self.library.import_file(path, kind='midi', title=f'{data["title"]} · v{version}', asset_id=previous_id,
            metadata={'midi_document_id': data['id'], 'document_version': version,
                      'track_group_id': f'midi:{data["id"]}', 'roles': list(ROLES[:3]), 'sha256': digest,
                      'bpm': 60_000_000/data['tempos'][0]['tempo'], 'ppq': data['ppq'],
                      'duration': duration_seconds(data),
                      'note_counts': generation_check(data, True)['counts'],
                      'track_states': data.get('extraction', {}).get('tracks', {})},
            provenance={'source': data.get('source', {}), 'document_id': data['id']})
        if data['project_id']:
            if previous_id:
                self.library.remove_from_project(data['project_id'], previous_id)
            self.library.add_to_project(data['project_id'], asset['id'], role='midi')
        with self.library.transaction() as db:
            db.execute('UPDATE midi_documents SET asset_id=? WHERE id=?', (asset['id'], data['id']))
        return asset

    def download(self, document_id, version, role):
        data = self.get(document_id)
        if data['version'] != version:
            raise VersionConflict('文档已改变，请刷新下载链接')
        if role not in ('combined', 'zip', *ROLES[:3]):
            raise ValueError('MIDI 下载轨道无效')
        folder = self.home / data['id'] / str(version)
        states = data.get('extraction', {}).get('tracks', {})
        if role in states and states[role].get('status') in ('failed', 'pending'):
            raise ValueError('该轨识别失败或尚未完成，没有可下载的 MIDI；其他成功轨可保留')
        if role == 'zip':
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / 'tracks.zip'
            def write_archive(stream):
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for key in ROLES[:3]:
                        if states.get(key, {}).get('status') in ('failed', 'pending'):
                            continue
                        export_midi(data, folder/f'{key}.mid', key)
                        archive.write(folder/f'{key}.mid', f'{key}.mid')
                    archive.writestr('manifest.json', json.dumps({'document_id': data['id'], 'version': version,
                        'source': data.get('source', {}), 'tracks': states,
                        'note_counts': generation_check(data, True)['counts'],
                        'description': '识别和弦为中心八度和弦音级；不是原始分轨或精确演奏还原'}, ensure_ascii=False, indent=2))
            atomic_payload(target, write_archive)
            return target
        return export_midi(data, folder/f'{role}.mid', '' if role == 'combined' else role)

    @document_locked
    def snapshot(self, document_id, version, *, allow_empty_chords=False, acknowledge_projection=False):
        integer(version, '文档版本', 1, 1_000_000_000)
        data = self.get(document_id)
        if data['version'] != version:
            raise VersionConflict('文档已改变，请先保存')
        check = generation_check(data, allow_empty_chords)
        if not check['ready']:
            raise ValueError('；'.join(check['errors']))
        if check['warnings'] and not acknowledge_projection:
            raise ValueError('请确认生成副本的量化和速度说明')
        snapshot_id = uuid.uuid4().hex
        folder = self.home / 's' / snapshot_id
        source = {}
        for role in ROLES[:3]:
            source[{'melody': 'melody_midi', 'chord': 'chord_midi', 'drums': 'drum_midi'}[role]] = str(export_midi(data, folder/f'{role}.mid', role, model=True))
        atomic_json(folder/'document.json', data)
        with self.library.transaction() as db:
            db.execute('INSERT INTO midi_snapshots VALUES(?,?,?,?,?)', (snapshot_id, data['id'], version, json.dumps(data, ensure_ascii=False), time.time()))
        return {'snapshot_id': snapshot_id, 'document_id': data['id'], 'document_version': version,
                'project_id': data['project_id'], 'source': source,
                'hashes': {key: hashlib.sha256(Path(path).read_bytes()).hexdigest() for key, path in source.items()},
                'check': check}
