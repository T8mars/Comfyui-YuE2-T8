"""Persistent user voice library, separate from downloadable RVC base models."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import time
import uuid
import zipfile
import tempfile

from .io import atomic_json, sha256, within


def storage_settings(root: Path) -> dict:
    saved = root / 'userdata/rvc/settings.json'
    config = json.loads(saved.read_text(encoding='utf-8')) if saved.exists() else {}
    if not isinstance(config, dict):
        raise ValueError('音色目录设置损坏')
    return config


def locations(root: Path) -> dict[str, Path]:
    home = root / 'userdata/rvc'
    config = storage_settings(root)
    result = {'home': home.resolve()}
    for kind, default in [('projects', home / 'projects'), ('voices', home / 'voices'),
                          ('datasets', home / 'datasets')]:
        path = Path(str(config.get(kind + '_directory') or default)).expanduser()
        result[kind] = (path if path.is_absolute() else root / path).resolve()
    return result


def material_location(root: Path, value: str) -> str:
    path = Path(value).resolve()
    current = locations(root)['datasets']
    if current in path.parents:
        return str(path)
    history = storage_settings(root).get('previous_dataset_directories', [])
    for previous in sorted(history, key=lambda value: len(Path(value).parts), reverse=True):
        previous = Path(previous).resolve()
        if previous in path.parents:
            return str(current / path.relative_to(previous))
    return str(path)


def entry_path(root: Path, voice_id: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', voice_id):
        raise ValueError('无效音色 ID')
    base = locations(root)['voices']
    return within(base, base / voice_id)


def get_voice(root: Path, voice_id: str) -> dict:
    directory = entry_path(root, voice_id)
    value = json.loads((directory / 'voice.json').read_text(encoding='utf-8'))
    if value.get('id') != voice_id:
        raise ValueError('音色记录损坏')
    value['directory'] = str(directory)
    value['model_path'] = str(directory / 'model.pth')
    if isinstance(value.get('training'), dict):
        from .rvc_pitch import voice_pitch_profile
        profiles = {str(s['id']): profile for s in value.get('speakers', [])
                    if (profile := voice_pitch_profile(value, int(s['id']))) is not None}
        value['training'] = {**value['training'], 'pitch_profiles': profiles}
    return value


def list_voices(root: Path) -> list[dict]:
    voices = []
    for path in locations(root)['voices'].glob('*/voice.json'):
        if not path.parent.name.startswith('.'):
            try:
                voices.append(get_voice(root, path.parent.name))
            except (OSError, ValueError, TypeError):
                continue
    return sorted(voices, key=lambda value: value.get('created_at', 0), reverse=True)


def inspect_checkpoint(path: Path) -> dict:
    """Call in a worker, never in the HTTP service process."""
    import torch
    if not path.is_file() or not 1024 <= path.stat().st_size <= 1024**3:
        raise ValueError('音色模型文件不存在或大小无效')
    checkpoint = torch.load(path, map_location='cpu', weights_only=True)
    if not isinstance(checkpoint, dict) or 'weight' not in checkpoint or 'config' not in checkpoint:
        raise ValueError('请选择可推理的 RVC 音色模型；训练中的 G/D 检查点不能直接导入')
    weights, config = checkpoint['weight'], checkpoint['config']
    version = checkpoint.get('version', 'v1')
    if version not in ('v1', 'v2') or not isinstance(config, (list, tuple)) or len(config) != 18:
        raise ValueError('RVC 模型结构或版本无效')
    embedding = weights.get('emb_g.weight') if isinstance(weights, dict) else None
    if not isinstance(embedding, torch.Tensor) or embedding.ndim != 2 or not 1 <= embedding.shape[0] <= 110:
        raise ValueError('RVC 模型缺少有效的说话人权重')
    for tensor in weights.values():
        if not isinstance(tensor, torch.Tensor) or not torch.isfinite(tensor).all():
            raise ValueError('RVC 模型权重含无效数据')
    sr = int(config[-1])
    if sr not in (32000, 40000, 48000):
        raise ValueError('RVC 模型采样率无效')
    # Many single-speaker legacy checkpoints retain all 109 pretrained rows.
    # Undeclared rows are not evidence of 109 trained voices.
    speakers = checkpoint.get('speaker_info') or [{'id': 0, 'name': '默认音色'}]
    normalized, seen = [], set()
    for speaker in speakers:
        sid = int(speaker['id'])
        if sid in seen or not 0 <= sid < embedding.shape[0]:
            raise ValueError('模型说话人信息与权重不一致')
        normalized.append({'id': sid, 'name': str(speaker['name'])[:100]})
        seen.add(sid)
    epoch_match = re.fullmatch(r'(\d+)epoch', str(checkpoint.get('info', '')))
    return {'version': version, 'sample_rate': sr, 'f0': bool(checkpoint.get('f0', 1)),
            'speakers': normalized, 'speaker_count': int(embedding.shape[0]),
            'trained_epochs': int(epoch_match[1]) if epoch_match else None,
            'feature_dimensions': 768 if version == 'v2' else 256}


def register_voice(root: Path, model: Path, indices: dict[int, Path], *, name: str,
                   project_id: str | None = None, preview: Path | None = None, training: dict | None = None,
                   reuse_project: bool = False) -> dict:
    import faiss
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError('音色名称需要 1–100 个字符')
    metadata = inspect_checkpoint(model)
    speakers = {s['id'] for s in metadata['speakers']}
    if set(indices) != speakers:
        raise ValueError('每个说话人必须提供对应的检索 index')
    for index in indices.values():
        if not index.is_file() or not 1 <= index.stat().st_size <= 2 * 1024**3:
            raise ValueError('检索 index 文件无效')
        value = faiss.read_index(str(index))
        if value.d != metadata['feature_dimensions'] or not value.is_trained or value.ntotal < 1:
            raise ValueError('index 维度、训练状态或内容与模型不匹配')
    files = {'model.pth': model}
    index_names = {str(sid): f'speaker-{sid}.index' for sid in sorted(indices)}
    files.update({index_names[str(sid)]: path for sid, path in indices.items()})
    if preview is not None:
        files['preview.wav'] = preview
    expected = {filename: sha256(path) for filename, path in files.items()}
    if reuse_project and project_id:
        for existing in list_voices(root):
            if existing.get('project_id') == project_id and existing.get('files') == expected:
                try:
                    reused = verify_voice(root, existing['id'])
                    # Re-running an already completed project can enrich metadata without retraining or duplicating its voice.
                    if training and training.get('pitch_profiles'):
                        saved = json.loads((Path(reused['directory']) / 'voice.json').read_text(encoding='utf-8'))
                        saved['training'] = training
                        atomic_json(Path(reused['directory']) / 'voice.json', saved)
                        reused = get_voice(root, existing['id'])
                    return reused
                except (ValueError, OSError):
                    continue
    voice_id = uuid.uuid4().hex
    target = entry_path(root, voice_id)
    temporary = target.with_name('.' + voice_id)
    temporary.mkdir(parents=True)
    try:
        hashes = {}
        for filename, source in files.items():
            shutil.copy2(source, temporary / filename)
            hashes[filename] = sha256(temporary / filename)
        if hashes != expected:
            raise ValueError('登记期间音色文件发生变化，请重试')
        atomic_json(temporary / 'voice.json', {
            **metadata, 'schema': 1, 'id': voice_id, 'name': name,
            'project_id': project_id, 'created_at': time.time(), 'indices': index_names,
            'preview': 'preview.wav' if preview is not None else None, 'files': hashes,
            'training': training,
        })
        temporary.replace(target)
    except BaseException:
        # Only this call's freshly created, owned temporary directory is removed.
        shutil.rmtree(within(locations(root)['voices'], temporary), ignore_errors=True)
        raise
    return get_voice(root, voice_id)


def verify_voice(root: Path, voice_id: str) -> dict:
    value = get_voice(root, voice_id)
    directory = entry_path(root, voice_id)
    for filename, digest in value['files'].items():
        path = within(directory, directory / filename)
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f'音色文件缺失或损坏：{filename}')
    return value


def export_voice(root: Path, voice_id: str) -> Path:
    value = verify_voice(root, voice_id)
    directory = entry_path(root, voice_id)
    output = root / 'exports/rvc' / f'{voice_id}-{uuid.uuid4().hex[:8]}.zip'
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in ['voice.json', *value['files']]:
            archive.write(within(directory, directory / filename), filename)
    return output


def import_voice(root: Path, request: dict) -> dict:
    """Validate untrusted checkpoints only inside the queue worker."""
    archive_path = request.get('archive')
    if not archive_path:
        model = within(root / 'uploads', Path(request['model']))
        raw_indices = request.get('indices', {})
        if not isinstance(raw_indices, dict):
            raise ValueError('index 映射必须是说话人 ID 到文件的对象')
        indices = {int(sid): within(root / 'uploads', Path(path)) for sid, path in raw_indices.items()}
        return register_voice(root, model, indices, name=str(request.get('name', '导入的音色')))
    archive_path = within(root / 'uploads', Path(archive_path))
    cache = root / 'cache/rvc-import'
    cache.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(dir=cache) as temporary:
        directory = within(cache, Path(temporary))
        entries = archive.infolist()
        if not entries or len(entries) > 224 or sum(item.file_size for item in entries) > 4 * 1024**3:
            raise ValueError('音色压缩包为空或超出大小限制')
        seen = set()
        for item in entries:
            # Portable voice archives have a flat, explicit layout. Never extract links/paths.
            name = item.filename
            if not re.fullmatch(r'voice\.json|model\.pth|speaker-\d+\.index|preview\.wav', name) or name.casefold() in seen:
                raise ValueError('请选择本软件导出的音色 ZIP；其他模型请用 PTH + INDEX 导入')
            seen.add(name.casefold())
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('音色压缩包不能包含符号链接')
            if name == 'voice.json' and item.file_size > 1024 * 1024:
                raise ValueError('音色说明过大')
        if 'voice.json' not in seen or 'model.pth' not in seen:
            raise ValueError('音色压缩包缺少模型或说明')
        metadata = json.loads(archive.read('voice.json'))
        hashes = metadata.get('files')
        if not isinstance(hashes, dict) or set(hashes) != seen - {'voice.json'}:
            raise ValueError('压缩包文件清单不完整')
        for item in entries:
            target = within(directory, directory / item.filename)
            with archive.open(item) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if item.filename != 'voice.json' and sha256(target) != hashes[item.filename]:
                raise ValueError('音色压缩包校验失败：' + item.filename)
        indices = {int(sid): within(directory, directory / filename)
                   for sid, filename in metadata['indices'].items()}
        preview = directory / 'preview.wav' if 'preview.wav' in seen else None
        if preview:
            from .rvc_projects import inspect_audio
            inspect_audio(preview)
        return register_voice(root, directory / 'model.pth', indices,
                              name=str(request.get('name') or metadata.get('name') or '导入的音色'), preview=preview,
                              training=metadata.get('training') if isinstance(metadata.get('training'), dict) else None)


def rename_voice(root: Path, voice_id: str, name: str) -> dict:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError('音色名称需要 1–100 个字符')
    directory = entry_path(root, voice_id)
    value = json.loads((directory / 'voice.json').read_text(encoding='utf-8'))
    value.update(name=name, updated_at=time.time())
    atomic_json(directory / 'voice.json', value)
    return get_voice(root, voice_id)


def trash_voice(root: Path, voice_id: str) -> Path:
    source = entry_path(root, voice_id)
    get_voice(root, voice_id)
    # Keep a recoverable copy beside the library, on the same volume.
    trash = within(locations(root)['voices'], locations(root)['voices'] / '.trash')
    trash.mkdir(parents=True, exist_ok=True)
    target = within(trash, trash / (voice_id + '-' + uuid.uuid4().hex[:8]))
    source.rename(target)
    return target
