"""Repair the pinned decoder after stopping this installation; keep prior files."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from .io import atomic_json
from .model_verify import PINNED_MODELS, pinned_entries, sha256
from .settings import model_directory

MODEL_REVISION = '94f6d64c1b53a1700e8f76c0c4faff8500be7780'
MODEL_NAME = 'YuE2-Vae'


def repair_vae(root: Path, *, check_only: bool = False) -> dict:
    root = root.resolve()
    directory = model_directory(root, strict=True)
    spec = PINNED_MODELS[MODEL_NAME]
    weight = directory/spec['file']
    manifest_path = directory/'MODEL_MANIFEST.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    repaired_manifest = copy.deepcopy(manifest)
    if not isinstance(repaired_manifest.get('models'), dict):
        raise ValueError('模型来源清单缺少 models；请先恢复完整 MODEL_MANIFEST.json')
    repaired_manifest['models'][MODEL_NAME] = copy.deepcopy(spec)
    pinned_entries(repaired_manifest)
    actual = sha256(weight) if weight.is_file() else None
    valid = actual == spec['sha256'] and weight.stat().st_size == spec['size']
    local_manifest = weight.parent/'weights_manifest.json'
    expected_local = {'schema': 1, 'files': {'model.safetensors': {
        'sha256': spec['sha256'], 'bytes': spec['size']}}}
    try:
        metadata = json.loads(local_manifest.read_text(encoding='utf-8-sig'))
        local_ok = metadata.get('files') == expected_local['files']
    except (OSError, ValueError, AttributeError):
        local_ok = False
    state = {'model': MODEL_NAME, 'file': str(weight), 'expected_sha256': spec['sha256'],
             'actual_sha256': actual, 'weight_valid': valid,
             'manifest_valid': manifest == repaired_manifest and local_ok,
             'downloaded': False, 'repaired': False}
    if check_only or (valid and state['manifest_valid']):
        return state
    # Use the same lock as the service, so this tool cannot replace a live decoder.
    from .service import acquire_instance_lock
    try:
        service_lock = acquire_instance_lock(root)
    except RuntimeError as exc:
        raise RuntimeError('请先关闭此整合包工作台，再运行 VAE 修复；不停止或中断运行任务') from exc
    with service_lock:
        weight.parent.mkdir(parents=True, exist_ok=True)
        # Fixed short staging names also support Windows installs with long paths.
        descriptor, staging_name = tempfile.mkstemp(prefix='v-', suffix='.tmp', dir=weight.parent)
        staging = Path(staging_name)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                if not valid:
                    url = f'https://huggingface.co/t8star/YuE2-Comfy/resolve/{MODEL_REVISION}/{spec["file"]}'
                    checksum, received = hashlib.sha256(), 0
                    with urllib.request.urlopen(url, timeout=60) as response:
                        while chunk := response.read(8*1024*1024):
                            received += len(chunk)
                            if received > spec['size']:
                                raise ValueError('VAE 下载大小超出清单；原文件未替换')
                            checksum.update(chunk)
                            target.write(chunk)
                            print(f'下载 VAE：{received}/{spec["size"]} 字节', flush=True)
                    if received != spec['size'] or checksum.hexdigest() != spec['sha256']:
                        raise ValueError('VAE 下载完整性校验失败；原文件未替换，请检查网络后重试')
            backup = Path(tempfile.mkdtemp(prefix=f'vae-{time.strftime("%Y%m%d-%H%M%S")}-',
                                          dir=_backup_directory(root)))
            for source, name in ((manifest_path, 'MODEL_MANIFEST.json'),
                                 (local_manifest, 'weights_manifest.json')):
                if source.is_file():
                    shutil.copy2(source, backup/name)
            if not valid:
                if weight.is_file():
                    shutil.copy2(weight, backup/'model.safetensors')
                os.replace(staging, weight)
                state['downloaded'] = True
            atomic_json(local_manifest, expected_local)
            atomic_json(manifest_path, repaired_manifest)
            state.update(actual_sha256=sha256(weight), backup=str(backup), repaired=True,
                         manifest_valid=True, weight_valid=True)
            if state['actual_sha256'] != spec['sha256']:
                raise ValueError('VAE 安装后校验失败，请检查磁盘；原文件已备份')
            return state
        finally:
            staging.unlink(missing_ok=True)


def _backup_directory(root: Path) -> Path:
    directory = root/'logs/backups'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description='检查或修复整合包固定版本 VAE 权重，保留原文件备份')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    try:
        result = repair_vae(args.root, check_only=args.check_only)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if result['weight_valid'] and result['manifest_valid'] else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'VAE 检查修复失败：{exc}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
