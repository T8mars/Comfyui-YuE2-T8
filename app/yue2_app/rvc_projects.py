"""Training projects and material inspection for the local RVC workbench."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import time
import uuid

from .io import atomic_json, sha256, within
from .rvc_library import locations, material_location
from .rvc_training import training_options

AUDIO_SUFFIXES = {'.wav', '.flac', '.mp3', '.m4a', '.ogg', '.opus', '.aac', '.wma', '.mp4', '.webm'}


def project_path(root: Path, ident: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise ValueError('无效训练项目 ID')
    base = locations(root)['projects']
    return within(base, base / ident)


def get_project(root: Path, ident: str) -> dict:
    path = project_path(root, ident) / 'project.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('id') != ident:
        raise ValueError('训练项目记录无效')
    for item in value['materials']:
        for key in ('path', 'separated_path'):
            if item.get(key):
                item[key] = material_location(root, item[key])
    return value


def save_project(root: Path, project: dict):
    project['updated_at'] = time.time()
    atomic_json(project_path(root, project['id']) / 'project.json', project)


def list_projects(root: Path) -> list[dict]:
    values = []
    for path in locations(root)['projects'].glob('*/project.json'):
        try:
            value = get_project(root, path.parent.name)
            values.append(value)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(values, key=lambda value: value.get('updated_at', 0), reverse=True)


def create_project(root: Path, name: str) -> dict:
    name = str(name).strip()
    if not name or len(name) > 100:
        raise ValueError('训练项目名称需要 1–100 个字符')
    project = {'schema': 1, 'id': uuid.uuid4().hex, 'name': name,
               'created_at': time.time(), 'materials': [], 'speakers': [{'id': 0, 'name': name}],
               'options': training_options({}), 'state': 'draft'}
    save_project(root, project)
    return project


def inspect_audio(path: Path) -> dict:
    """Stream sample statistics; no neural model or CUDA allocation is needed."""
    import av
    import numpy as np
    frames = samples = clipped = quiet = 0
    peak = square = 0.0
    rate = channels = None
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            if rate is None:
                rate, channels = frame.sample_rate, len(frame.layout.channels)
                converter = av.AudioResampler(format='fltp', layout=frame.layout.name, rate=rate)
            if rate != frame.sample_rate or channels != len(frame.layout.channels):
                raise ValueError('文件中途改变了采样率或声道，请先导出为 WAV')
            for decoded in converter.resample(frame):
                audio = decoded.to_ndarray()
                if not np.isfinite(audio).all():
                    raise ValueError('音频含无效采样')
                frames += audio.shape[1]
                samples += audio.size
                peak = max(peak, float(np.abs(audio).max(initial=0)))
                square += float(np.square(audio.astype(np.float64)).sum())
                clipped += int((np.abs(audio) >= .999).sum())
                # Sample-level near-zero fraction is an indicator, not an
                # assertion of silent phrase duration or accompaniment.
                quiet += int((np.abs(audio) < .001).sum())
    if not rate or not frames:
        raise ValueError('没有可读取的音频')
    rms = math.sqrt(square / samples)
    warnings = []
    if peak < .0001:
        warnings.append('静音素材')
    elif rms < .01:
        warnings.append('音量偏低')
    if clipped / samples > .001:
        warnings.append('疑似削波失真')
    if quiet / samples > .5:
        warnings.append('近静音采样较多，请试听并裁去空白')
    if frames / rate < 1:
        warnings.append('素材不足 1 秒')
    return {'duration': round(frames / rate, 3), 'sample_rate': rate, 'channels': channels,
            'peak': peak, 'rms': rms, 'clipped_fraction': clipped / samples,
            'quiet_fraction': quiet / samples, 'warnings': warnings}


def add_material(root: Path, project: dict, source: Path, *, speaker_id: int = 0,
                 source_type: str = 'unknown', display_name: str | None = None) -> dict:
    source = source.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in AUDIO_SUFFIXES:
        raise ValueError('请选择支持的音频文件')
    if source.stat().st_size > 1024**3:
        raise ValueError('单个素材不能超过 1 GB')
    if speaker_id not in {int(s['id']) for s in project['speakers']}:
        raise ValueError('说话人 ID 不存在')
    if source_type not in ('dry', 'mix', 'unknown'):
        raise ValueError('素材类型无效')
    name = (str(display_name or source.name).replace('\\', '/').rsplit('/', 1)[-1])[:200]
    digest = sha256(source)
    duplicate = next((item['id'] for item in project['materials'] if item['sha256'] == digest), None)
    if duplicate:
        return {'duplicate_of': duplicate, 'name': name}
    stats = inspect_audio(source)
    ident = uuid.uuid4().hex
    directory = locations(root)['datasets'] / project['id']
    directory.mkdir(parents=True, exist_ok=True)
    destination = within(directory, directory / (ident + source.suffix.lower()))
    shutil.copy2(source, destination)
    if sha256(destination) != digest:
        destination.unlink(missing_ok=True)
        raise ValueError('素材复制校验失败，请重新导入')
    value = {**stats, 'id': ident, 'name': name, 'path': str(destination),
             'sha256': digest, 'speaker_id': speaker_id, 'source_type': source_type,
             'enabled': stats['peak'] >= .0001 and stats['duration'] >= 1,
             'reviewed': False, 'accompaniment': 'present' if source_type == 'mix' else 'unchecked'}
    project['materials'].append(value)
    save_project(root, project)
    return value


def selected_materials(project: dict) -> list[dict]:
    selected = [item for item in project['materials'] if item.get('enabled')]
    if not selected:
        raise ValueError('请至少选中一段训练素材')
    for item in selected:
        if not item.get('reviewed'):
            raise ValueError('请先试听并确认选中的训练素材')
        if item.get('accompaniment') == 'present' and not item.get('separated_path'):
            raise ValueError('含伴奏的素材请先分离人声，试听后再用于训练')
        path = Path(item.get('separated_path') or item['path'])
        expected = item.get('separated_sha256') if item.get('separated_path') else item['sha256']
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f'素材缺失或已改变：{item["name"]}')
    return selected
