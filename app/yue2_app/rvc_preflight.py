"""Resource estimates for training; no neural model is loaded for this check."""
import os
from pathlib import Path
import shutil
import subprocess

from .rvc_library import locations
from .rvc_training import training_options
from .settings import model_directory


def check_training(root: Path, project: dict) -> dict:
    options = training_options(project['options'])
    selected = [item for item in project['materials'] if item.get('enabled')]
    seconds = sum(float(item['duration']) for item in selected)
    target = locations(root)['projects']
    existing = target
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    # Conservative additional-space budget: two recoverable G/D pairs, audio,
    # HuBERT/F0 features and FAISS index. Not an exact prediction of final size.
    required = 2 * 1024**3 + int(seconds * 800_000)
    errors, warnings = [], []
    if not selected:
        errors.append('请先导入并选择训练素材')
    if any(not item.get('reviewed') for item in selected):
        errors.append('请先逐段试听并确认选中的素材')
    if free < required:
        errors.append(f'训练目录空间不足：预计还需约 {required / 1024**3:.1f} GB，可用 {free / 1024**3:.1f} GB')
    if seconds < 300:
        warnings.append('素材不足 5 分钟，可用于流程试跑；音色质量需要更多干净素材和试听评估')
    assets = model_directory(root, strict=True) / 'RVC'
    folder = 'pretrained_v2' if options['version'] == 'v2' else 'pretrained'
    missing = [f'{folder}/f0{network}{options["sample_rate"]}.pth' for network in ('G', 'D')
               if not (assets / folder / f'f0{network}{options["sample_rate"]}.pth').is_file()]
    if missing:
        errors.append('缺少所选版本的训练底模：' + '、'.join(missing))
    import psutil
    ram = psutil.virtual_memory().available
    if ram < 2 * 1024**3:
        warnings.append('可用系统内存不足 2 GB，建议关闭暂时不用的应用后再训练')
    gpu = None
    if os.environ.get('CUDA_VISIBLE_DEVICES') != '-1':
        try:
            output = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.free,memory.total',
                '--format=csv,noheader,nounits'], text=True, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), stderr=subprocess.DEVNULL)
            for line in output.splitlines():
                ident, available, total = map(int, line.split(','))
                if ident == options['gpu']:
                    gpu = {'id': ident, 'free_mib': available, 'total_mib': total}
                    if available < 4096:
                        warnings.append('当前可用显存不足 4 GB，建议等待其他 GPU 任务结束；自动批次会尽量降低占用')
        except (OSError, ValueError, subprocess.SubprocessError):
            warnings.append('暂时无法读取显存；训练启动时仍会检查可用设备')
    return {'ready': not errors, 'errors': errors, 'warnings': warnings, 'duration_seconds': seconds,
            'training_directory': str(target), 'required_free_bytes_estimate': required,
            'disk_free_bytes': free, 'ram_available_bytes': ram, 'gpu': gpu}
