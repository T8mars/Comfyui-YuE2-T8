"""Shared RVC training-stage preparation and artifact validation.

The UI/queue owns the job. Every stage runs with the caller's interpreter;
this module never installs or selects another Python environment.
"""
from __future__ import annotations

import json
import codecs
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .io import atomic_json, sha256, within


def training_progress(text: str) -> dict:
    result = {}
    epochs = re.findall(r'(?:训练轮次|轮次)[：:]\s*(\d+)', text)
    if epochs:
        result['epoch'] = int(epochs[-1])
    losses = {}
    for name, number in re.findall(r'\bloss_(disc|gen|fm|mel|kl)=([+-]?[\d.eE+-]+)', text):
        try:
            value = float(number)
        except ValueError:
            continue
        if math.isfinite(value):
            losses[name] = value
    if losses:
        result['losses'] = losses
    return result


def training_options(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('训练设置必须是对象')
    def integer(name, default, low, high):
        value = raw.get(name, default)
        try:
            converted = int(value)
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f'{name} 必须为整数') from None
        if isinstance(value, bool) or str(value).strip() != str(converted):
            raise ValueError(f'{name} 必须为整数')
        value = converted
        if not low <= value <= high:
            raise ValueError(f'{name} 必须在 {low} 到 {high} 之间')
        return value
    version = str(raw.get('version', 'v2'))
    rate = str(raw.get('sample_rate', '48k'))
    method = str(raw.get('f0_method', 'rmvpe'))
    if version not in ('v1', 'v2') or rate not in ('32k', '40k', '48k'):
        raise ValueError('RVC 版本或采样率无效')
    if method not in ('rmvpe', 'pm'):
        raise ValueError('音高提取方式无效')
    return {'version': version, 'sample_rate': rate, 'f0_method': method,
            'epochs': integer('epochs', 100, 1, 2000),
            'save_every': integer('save_every', 5, 1, 100),
            'batch_size': integer('batch_size', 0, 0, 32),
            'gpu': integer('gpu', 0, 0, 31),
            'num_workers': integer('num_workers', 0, 0, 4)}


def choose_batch(options: dict) -> int:
    if options['batch_size']:
        return options['batch_size']
    import torch
    if not torch.cuda.is_available():
        return 1
    free, _ = torch.cuda.mem_get_info(options['gpu'])
    # Conservative starting points; OOM retries must be recorded by the job.
    gib = free / 2**30
    return 1 if gib < 8 else 2 if gib < 12 else 4 if gib < 20 else 6


def prepare_training(root: Path, workspace: Path, experiment: str, options: dict,
                     speakers: list[dict], *, batch_size: int) -> Path:
    import numpy as np
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', experiment):
        raise ValueError('无效训练项目 ID')
    directory = within(workspace / 'logs', workspace / 'logs' / experiment)
    template_version = 'v1' if options['sample_rate'] == '40k' else options['version']
    config = json.loads((root / 'vendor/rvc/configs' / template_version /
                        (options['sample_rate'] + '.json')).read_text(encoding='utf-8'))
    config['train'].update(num_workers=options['num_workers'], batch_size=batch_size)
    ids = {int(s['id']) for s in speakers}
    if not ids or min(ids) < 0 or max(ids) > 109 or len(ids) != len(speakers):
        raise ValueError('说话人 ID 必须唯一，并位于 0–109')
    config['model']['spk_embed_dim'] = max(ids) + 1
    config['speaker_info'] = speakers
    manifest_path = directory / 'multispeaker_manifest.json'
    assignments = {}
    if manifest_path.exists():
        assignments = {entry['output_key']: int(entry['speaker_id']) for entry in
                       json.loads(manifest_path.read_text(encoding='utf-8'))['entries']}
    feature_dir = '3_feature768' if options['version'] == 'v2' else '3_feature256'
    rows, fingerprint = [], []
    for wav in sorted((directory / '0_gt_wavs').glob('*.wav')):
        feature = directory / feature_dir / (wav.stem + '.npy')
        f0 = directory / '2a_f0' / (wav.name + '.npy')
        f0f = directory / '2b-f0nsf' / (wav.name + '.npy')
        for path in (feature, f0, f0f):
            if not path.is_file():
                raise ValueError(f'训练特征缺失：{path.name}；请重新执行特征提取')
            array = np.load(path, allow_pickle=False)
            if not array.size or not np.isfinite(array).all():
                raise ValueError(f'训练特征损坏：{path.name}')
        shape = np.load(feature, mmap_mode='r').shape
        if len(shape) != 2 or shape[1] != (768 if options['version'] == 'v2' else 256):
            raise ValueError('HuBERT 特征与模型版本不匹配')
        key = wav.stem if wav.stem in assignments else wav.stem.rsplit('_', 1)[0]
        speaker = assignments.get(key) if assignments else next(iter(ids))
        if speaker not in ids or (not assignments and len(ids) > 1):
            raise ValueError('素材缺少对应的说话人 ID')
        paths = (wav, feature, f0, f0f)
        rows.append('|'.join([*(p.as_posix() for p in paths), str(speaker)]))
        fingerprint.append({'files': [sha256(p) for p in paths], 'speaker': speaker})
    if not rows:
        raise ValueError('没有可训练素材，请先完成素材预处理和特征提取')
    identity = {'version': options['version'], 'sample_rate': options['sample_rate'],
                'f0_method': options['f0_method'], 'speakers': speakers, 'files': fingerprint}
    identity_path = directory / 'studio-training-identity.json'
    if any(directory.glob('[GD]_*.pth')):
        if not identity_path.exists() or json.loads(identity_path.read_text(encoding='utf-8')) != identity:
            raise ValueError('素材或训练结构已经改变；请创建新训练项目，原检查点已保留')
    atomic_json(identity_path, identity)
    atomic_json(directory / 'config.json', config)
    (directory / 'filelist.txt').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return directory


def run_stage(root: Path, assets: Path, workspace: Path, stage: str, arguments: list,
              ctx, *, extra_env: dict | None = None) -> None:
    ctx.check_cancelled()
    log_path = ctx.job_dir / ('rvc-' + stage + '.log')
    offset = log_path.stat().st_size if log_path.exists() else 0
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    recent = ''
    def forward_log():
        nonlocal offset, recent
        with log_path.open('rb') as source:
            source.seek(offset)
            chunk = source.read(256 * 1024)
            offset = source.tell()
        text = decoder.decode(chunk)
        if text:
            print(text, end='', flush=True)
        recent = (recent + text)[-8192:]
        return training_progress(recent) if stage == 'train' else {}
    environment = os.environ.copy()
    environment.update(PYTHONIOENCODING='utf-8', OMP_NUM_THREADS='2')
    environment.update(extra_env or {})
    command = [sys.executable, '-X', 'utf8', '-m', 'app.yue2_app.rvc_runner',
               '--root', str(root), '--assets', str(assets), '--workspace', str(workspace),
               stage, *map(str, arguments)]
    with log_path.open('ab', buffering=0) as log:
        child = subprocess.Popen(command, cwd=root, env=environment, stdout=log,
                                 stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            last = 0
            while child.poll() is None:
                ctx.check_cancelled()
                if time.monotonic() - last > 1:
                    ctx.update('rvc_' + stage, child_pid=child.pid, stage_log=str(log_path), **forward_log())
                    last = time.monotonic()
                time.sleep(.2)
        except BaseException:
            if child.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    child.terminate()
                child.wait(timeout=20)
            raise
    ctx.check_cancelled()
    ctx.update('rvc_' + stage, **forward_log())
    if child.returncode:
        tail = log_path.read_text(encoding='utf-8', errors='replace')[-4000:]
        raise RuntimeError(f'RVC {stage} 失败（{child.returncode}）：\n{tail}')
    # A zero exit code alone is insufficient: the caller must verify each
    # expected artifact before persisting stage completion or publishing a voice.
