"""Commit generator/discriminator checkpoints as one recoverable training step."""
import json
from pathlib import Path
import uuid

from .io import atomic_json, sha256, within


def has_checkpoint(directory):
    directory = Path(directory)
    marker = directory / 'studio-checkpoints.json'
    if marker.is_file():
        return bool(json.loads(marker.read_text(encoding='utf-8')).get('pairs'))
    return any(directory.glob('[GD]_*.pth'))


def checkpoint_path(directory, kind):
    directory = Path(directory).resolve()
    marker = directory / 'studio-checkpoints.json'
    if marker.is_file():
        pairs = json.loads(marker.read_text(encoding='utf-8'))['pairs']
        for index, pair in enumerate(reversed(pairs)):
            files = {key: within(directory, directory / pair[key]['file']) for key in ('G', 'D')}
            if all(file.is_file() and sha256(file) == pair[key]['sha256'] for key, file in files.items()):
                if index:
                    print('最近检查点不完整，已恢复上一组完整检查点', flush=True)
                return str(files[kind])
        raise FileNotFoundError('没有完整的 G/D 检查点组')
    # Import older studio checkpoints only when both training epochs agree.
    import torch
    pairs = []
    for generator in directory.glob('G_*.pth'):
        discriminator = generator.with_name('D_' + generator.name[2:])
        if not discriminator.is_file():
            continue
        try:
            g = torch.load(generator, map_location='cpu', weights_only=True)
            epoch = int(g['iteration'])
            del g
            d = torch.load(discriminator, map_location='cpu', weights_only=True)
            if int(d['iteration']) == epoch:
                pairs.append((epoch, generator, discriminator))
            del d
        except (OSError, ValueError, KeyError, RuntimeError):
            continue
    if not pairs:
        raise FileNotFoundError('没有可恢复的完整检查点')
    _, generator, discriminator = max(pairs, key=lambda item: item[0])
    return str(generator if kind == 'G' else discriminator)


def save_pair(directory, generator, discriminator, optim_g, optim_d, learning_rate, epoch):
    import torch
    directory = Path(directory).resolve()
    marker = directory / 'studio-checkpoints.json'
    history = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {'schema': 1, 'pairs': []}
    if not marker.exists():
        if has_checkpoint(directory):
            legacy = {kind: Path(checkpoint_path(directory, kind)) for kind in ('G', 'D')}
            saved = torch.load(legacy['G'], map_location='cpu', weights_only=True)
            old_epoch = int(saved['iteration'])
            del saved
            history['pairs'] = [{'epoch': old_epoch, **{kind: {'file': file.name, 'sha256': sha256(file)}
                                 for kind, file in legacy.items()}}]
        atomic_json(marker, history)
    token = uuid.uuid4().hex
    pair = {'epoch': int(epoch)}
    staged = []
    try:
        for kind, model, optimizer in [('G', generator, optim_g), ('D', discriminator, optim_d)]:
            temporary = within(directory, directory / f'.{kind}-{token}.tmp')
            filename = f'{kind}_{int(epoch)}.pth'
            staged.append((temporary, within(directory, directory / filename)))
            weights = model.module.state_dict() if hasattr(model, 'module') else model.state_dict()
            torch.save({'model': weights, 'iteration': int(epoch), 'optimizer': optimizer.state_dict(),
                        'learning_rate': learning_rate}, temporary)
            pair[kind] = {'file': filename, 'sha256': sha256(temporary)}
        for temporary, target in staged:
            temporary.replace(target)
        # Publishing this marker is the commit point; older complete pairs survive interruption.
        previous = [value for value in history['pairs'] if value['epoch'] != int(epoch)]
        valid_previous = [value for value in previous if all(
            (directory / value[kind]['file']).is_file() and
            sha256(within(directory, directory / value[kind]['file'])) == value[kind]['sha256']
            for kind in ('G', 'D'))]
        history['pairs'] = [*valid_previous, pair][-2:]
        atomic_json(marker, history)
        retained = {value[kind]['file'] for value in history['pairs'] for kind in ('G', 'D')}
        for value in previous:
            for kind in ('G', 'D'):
                if value[kind]['file'] not in retained:
                    within(directory, directory / value[kind]['file']).unlink(missing_ok=True)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
