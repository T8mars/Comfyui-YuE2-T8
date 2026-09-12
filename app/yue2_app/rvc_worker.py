"""RVC workbench jobs executed by the studio's serial GPU queue."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .io import atomic_json, sha256, within
from .rvc_library import inspect_checkpoint, locations, register_voice, import_voice, export_voice
from .rvc_projects import AUDIO_SUFFIXES, add_material, get_project, project_path, save_project, selected_materials
from .rvc_training import choose_batch, prepare_training, run_stage, training_options
from .settings import model_directory
from .worker_common import JobContext, configure_environment


def import_materials(root, project, request, ctx):
    files = [within(root / 'uploads', Path(path)) for path in request.get('paths', [])]
    if request.get('folder'):
        folder = Path(request['folder']).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError('素材文件夹不存在')
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES and folder in path.resolve().parents:
                files.append(path)
                if len(files) > 1000:
                    raise ValueError('单次最多导入 1000 个音频，请拆分文件夹后重试；本次尚未导入')
    if len(files) > 1000:
        raise ValueError('单次最多导入 1000 个音频')
    if not files:
        raise ValueError('没有找到支持的音频素材')
    report = {'imported': [], 'duplicates': [], 'errors': []}
    for i, path in enumerate(files):
        ctx.progress('rvc_import', i, len(files))
        try:
            item = add_material(root, project, path, speaker_id=int(request.get('speaker_id', 0)),
                                source_type=request.get('source_type', 'unknown'),
                                display_name=request.get('names', {}).get(str(path)))
            report['duplicates' if 'duplicate_of' in item else 'imported'].append(item)
        except (ValueError, OSError) as exc:
            report['errors'].append({'name': path.name, 'error': str(exc)})
    ctx.progress('rvc_import', len(files), len(files))
    if not report['imported'] and not report['duplicates']:
        raise ValueError('素材全部导入失败：' + json.dumps(report['errors'], ensure_ascii=False))
    return report


def separate_materials(root, project, request, ctx):
    from .voice_worker import _separate_vocals
    import numpy as np
    import soundfile as sf
    selected = set(request.get('material_ids', []))
    items = [item for item in project['materials'] if item['id'] in selected]
    if not items:
        raise ValueError('请先选中需要分离或检测伴奏的素材')
    directory = locations(root)['datasets'] / project['id'] / 'separated'
    directory.mkdir(parents=True, exist_ok=True)
    for i, item in enumerate(items):
        ctx.progress('rvc_separate', i, len(items))
        vocal, backing = directory / (item['id'] + '.wav'), directory / (item['id'] + '-backing.wav')
        _separate_vocals(root, Path(item['path']), ctx, vocal, backing)
        va, _ = sf.read(vocal, dtype='float32')
        bg, _ = sf.read(backing, dtype='float32')
        ratio = float(np.mean(bg**2) / max(float(np.mean(va**2) + np.mean(bg**2)), 1e-12))
        item.update(separated_path=str(vocal), separated_sha256=sha256(vocal),
                    accompaniment='present' if ratio > .2 else 'low_estimate',
                    accompaniment_energy_ratio=ratio, reviewed=False)
        save_project(root, project)
    return {'separated': len(items), 'review_required': True}


def train_voice(root, project, request, ctx):
    from .rvc_preflight import check_training
    preflight = check_training(root, project)
    ctx.update('rvc_preflight', preflight=preflight)
    if not preflight['ready']:
        raise ValueError('；'.join(preflight['errors']))
    import numpy as np
    import torch
    options = training_options(project['options'])
    cuda = torch.cuda.is_available()
    materials = selected_materials(project)
    workspace = project_path(root, project['id']) / 'training'
    experiment = project['id']
    directory = workspace / 'logs' / experiment
    directory.mkdir(parents=True, exist_ok=True)
    assets = model_directory(root, strict=True) / 'RVC'
    pretrained = assets / ('pretrained_v2' if options['version'] == 'v2' else 'pretrained')
    generator, discriminator = [pretrained / ('f0' + prefix + options['sample_rate'] + '.pth') for prefix in ('G', 'D')]
    if not generator.is_file() or not discriminator.is_file():
        raise ValueError('所选 RVC 版本的训练底模尚未下载，请安装对应底模后重试')
    names = {int(s['id']): s['name'] for s in project['speakers']}
    if {int(item['speaker_id']) for item in materials} != set(names):
        raise ValueError('每个说话人都需要至少一段已确认的素材；请补充素材或移除空说话人')
    entries = [{'path': item.get('separated_path') or item['path'], 'speaker_id': int(item['speaker_id']),
                'speaker_name': names[int(item['speaker_id'])], 'repeat': 1,
                'output_key': 'ms_' + item['id']} for item in materials]
    identity = {'materials': [{k: item.get(k) for k in ('id', 'sha256', 'separated_sha256', 'speaker_id')}
                              for item in materials],
                'version': options['version'], 'sample_rate': options['sample_rate'], 'f0_method': options['f0_method']}
    state_path = directory / 'studio-stages.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'identity': identity, 'stages': {}}
    if state['identity'] != identity:
        raise ValueError('素材、说话人或训练结构发生变化，请新建训练项目；原有检查点已保留')
    atomic_json(directory / 'multispeaker_manifest.json', {'version': 1, 'entries': entries})
    def complete(stage, files):
        files = list(files)
        if not files:
            raise RuntimeError(f'RVC {stage} 没有产生有效文件')
        state['stages'][stage] = {p.relative_to(workspace).as_posix(): sha256(p) for p in files}
        atomic_json(state_path, state)
        ctx.update('rvc_' + stage, resumable=True)
    def done(stage):
        files = state['stages'].get(stage, {})
        return bool(files) and all((workspace / p).is_file() and sha256(workspace / p) == digest for p, digest in files.items())
    def run(stage, args):
        run_stage(root, assets, workspace, stage, args, ctx,
                  extra_env={'RVC_TRAIN_DEVICE': 'cuda' if cuda else 'cpu',
                             **({} if cuda else {'CUDA_VISIBLE_DEVICES': '-1'})})
    manifest = directory / 'multispeaker_manifest.json'
    if not done('preprocess'):
        if any(directory.glob('[GD]_*.pth')):
            raise ValueError('已训练项目的预处理文件损坏，请修复备份或新建项目')
        run('preprocess', [directory, int(options['sample_rate'][:-1]) * 1000, 1, directory, 'True', 3.7, manifest])
        for entry in entries:
            if not list((directory / '0_gt_wavs').glob(entry['output_key'] + '_*.wav')):
                raise ValueError(f'素材未产生有效训练片段：{Path(entry["path"]).name}')
        complete('preprocess', [*(directory / '0_gt_wavs').glob('*.wav'), *(directory / '1_16k_wavs').glob('*.wav')])
    wavs = sorted((directory / '1_16k_wavs').glob('*.wav'))
    if not done('f0'):
        if options['f0_method'] == 'rmvpe' and cuda:
            run('f0', ['cuda', 1, 0, options['gpu'], directory, 'False'])
        else:
            run('f0', ['cpu', directory, 1, options['f0_method']])
        files = [directory / folder / (wav.name + '.npy') for wav in wavs for folder in ('2a_f0', '2b-f0nsf')]
        for file in files:
            value = np.load(file, allow_pickle=False)
            if not value.size or not np.isfinite(value).all():
                raise ValueError('F0 特征缺失或无效')
        complete('f0', files)
    feature_dir = directory / ('3_feature768' if options['version'] == 'v2' else '3_feature256')
    if not done('features'):
        run('features', ['cuda', 1, 0, options['gpu'], directory, options['version'], 'False'] if cuda
            else ['cpu', 1, 0, directory, options['version'], 'False'])
        complete('features', [feature_dir / (wav.stem + '.npy') for wav in wavs])
    batch = choose_batch(options)
    prepare_training(root, workspace, experiment, options, project['speakers'], batch_size=batch)
    ctx.update('rvc_train', batch_size=batch, epochs=options['epochs'])
    model = workspace / 'assets/weights' / (experiment + '.pth')
    if not done('train') or state.get('trained_epochs', 0) < options['epochs']:
        retries = 0
        while True:
            try:
                run('train', ['-e', experiment, '-sr', options['sample_rate'], '-v', options['version'], '-f0', 1,
                          '-bs', batch, '-g', options['gpu'], '-te', options['epochs'], '-se', options['save_every'],
                          '-sw', 1, '-l', 0, '-c', 0, '-pg', generator, '-pd', discriminator])
                break
            except RuntimeError as exc:
                oom = any(text in str(exc).lower() for text in ('cuda out of memory', 'cuda error: out of memory', 'torch.outofmemoryerror'))
                if options['batch_size'] or batch <= 1 or not oom:
                    raise
                batch = max(1, batch // 2)
                retries += 1
                ctx.update('rvc_train', batch_size=batch, oom_retries=retries,
                           message=f'显存不足，已释放训练进程并将批大小降至 {batch}，从完整检查点继续')
                print(f'CUDA OOM: retry {retries}, batch size {batch}', flush=True)
                prepare_training(root, workspace, experiment, options, project['speakers'], batch_size=batch)
    metadata = inspect_checkpoint(model)
    if (metadata['trained_epochs'] or 0) < options['epochs']:
        raise RuntimeError('训练进程未输出达到目标轮次的模型，检查点已保留')
    previous_model_hash = state['stages'].get('train', {}).get(model.relative_to(workspace).as_posix())
    if previous_model_hash != sha256(model):
        for stage in ('index', 'preview'):
            state['stages'].pop(stage, None)
    state['trained_epochs'] = metadata['trained_epochs']
    complete('train', [model])
    if not done('index'):
        state['stages'].pop('preview', None)
        run('index', [experiment, options['version'], workspace / 'assets/indices', 2, 'auto'])
    indices = {}
    for sid in names:
        matches = list(directory.glob(f'added_*_spkid{sid}.index'))
        if len(matches) != 1:
            raise ValueError(f'说话人 {sid} 缺少唯一匹配的 index')
        indices[sid] = matches[0]
    complete('index', indices.values())
    ctx.update('rvc_export')
    first = entries[0]
    preview_source = next((directory / '0_gt_wavs').glob(first['output_key'] + '_*.wav'))
    preview = workspace / 'preview.wav'
    if not done('preview'):
        run('infer', ['--model', model, '--input', preview_source, '--output', preview,
                      '--speaker-id', first['speaker_id'], '--index', indices[first['speaker_id']],
                      '--f0-method', options['f0_method'], '--overwrite'])
    from .rvc_projects import inspect_audio
    if inspect_audio(preview)['peak'] < 1e-6:
        raise RuntimeError('训练后的试听音频无声，请检查素材和训练日志')
    complete('preview', [preview])
    voice = register_voice(root, model, indices, name=project['name'], project_id=project['id'], preview=preview,
                           reuse_project=True,
                           training={'options': options, 'duration': sum(item['duration'] for item in materials),
                                     'materials': len(materials), 'segments': len(wavs), 'batch_size': batch})
    project.update(state='trained', voice_id=voice['id'])
    save_project(root, project)
    return {'voice': voice, 'project_id': project['id']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--job-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    ctx = JobContext(within(root / 'outputs/jobs', args.job_dir))
    configure_environment(root)
    try:
        job = json.loads((ctx.job_dir / 'job.json').read_text(encoding='utf-8'))
        request = job['request']
        ctx.update('starting', pid=os.getpid())
        if job['kind'] == 'rvc_storage_move':
            from .rvc_storage import migrate
            result = migrate(root, request, ctx)
        elif job['kind'] == 'rvc_model_import':
            ctx.update('rvc_model_import')
            result = {'voice': import_voice(root, request)}
        elif job['kind'] == 'rvc_model_export':
            ctx.update('rvc_model_export')
            archive = export_voice(root, request['voice_id'])
            result = {'download_url': '/api/rvc/download/' + archive.name}
        else:
            project = get_project(root, request['project_id'])
            action = {'rvc_import': import_materials, 'rvc_separate': separate_materials, 'rvc_train': train_voice}[job['kind']]
            result = action(root, project, request, ctx)
        ctx.finish(result=result, committed=bool(result.get('migration_committed')))
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 130 if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else 1


if __name__ == '__main__':
    raise SystemExit(main())
