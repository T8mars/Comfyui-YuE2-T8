"""Vendor the audited RVC compute core only; no upstream WebUI or runtime."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = '81eed5e8f68b6bed1789f682fe78cdd324495afc'


def main():
    source = ROOT / 'research/rvc-upstream'
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != REVISION:
        raise RuntimeError(f'Unexpected RVC source revision: {actual}')
    dest = ROOT / 'vendor/rvc'
    selected = []
    for directory in ('infer', 'train', 'configs', 'i18n'):
        selected.extend(p for p in (source / directory).rglob('*')
                        if p.is_file() and p.suffix in ('.py', '.json') and '__pycache__' not in p.parts)
    selected.extend(source / f'tools/{name}.py' for name in
                    ('cuda_graph', 'file_io', 'multispeaker', 'process_utils', 'progress'))
    selected.append(source / 'LICENSE')
    hashes = {}
    for file in selected:
        rel = file.relative_to(source)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        hashes[rel.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    (dest / 'UPSTREAM.json').write_text(json.dumps({
        'repository': 'https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI',
        'revision': actual, 'license': 'MIT', 'files': hashes,
        'scope': 'Training and offline inference core; no upstream WebUI or Python runtime',
    }, indent=2) + '\n', encoding='utf-8')
    # Keep local adaptations reproducible and separate from upstream provenance.
    adaptations = {
        'i18n/i18n.py': [
            ('import os\n', 'import os\nfrom pathlib import Path\n'),
            ('    return json.loads(read_text(f"./i18n/locale/{language}.json"))',
             '    return json.loads(read_text(Path(__file__).resolve().parent / "locale" / f"{language}.json"))'),
            ('        if not os.path.exists(f"./i18n/locale/{language}.json"):',
             '        if not (Path(__file__).resolve().parent / "locale" / f"{language}.json").is_file():'),
        ],
        'infer/hubert.py': [
            ('import logging\n', 'import logging\nimport os\n'),
            ('HUBERT_MODEL_PATH = (PROJECT_ROOT / "assets" / "hubert_base").resolve()',
             'HUBERT_MODEL_PATH = (Path(os.environ.get("RVC_ASSET_ROOT", PROJECT_ROOT / "assets")) / "hubert_base").resolve()'),
        ],
        'train/train.py': [
            ('os.environ["CUDA_VISIBLE_DEVICES"] = hps.gpus.replace("-", ",")',
             'os.environ["CUDA_VISIBLE_DEVICES"] = "-1" if os.environ.get("RVC_TRAIN_DEVICE") == "cpu" else hps.gpus.replace("-", ",")'),
            ('from train.process_ckpt import savee',
             'from train.process_ckpt import savee\nfrom app.yue2_app.rvc_checkpoints import checkpoint_path, has_checkpoint, save_pair'),
            ('utils.latest_checkpoint_path(hps.model_dir, "D_*.pth")', 'checkpoint_path(hps.model_dir, "D")'),
            ('            utils.latest_checkpoint_path(hps.model_dir, "G_*.pth"), net_g, optim_g',
             '            checkpoint_path(hps.model_dir, "G"), net_g, optim_g'),
            ('        global_step = (epoch_str - 1) * len(train_loader)',
             '        epoch_str += 1  # Resume after the fully committed epoch.\n        global_step = (epoch_str - 1) * len(train_loader)'),
            ('    single_cuda = torch.cuda.is_available() and n_gpus == 1',
             '    single_cuda = n_gpus <= 1  # Single CPU/GPU runs do not need distributed sockets.'),
            ('        children[i].join()\n',
             '        children[i].join()\n        if children[i].exitcode != 0:\n            raise RuntimeError(f"RVC training worker {i} failed: {children[i].exitcode}")\n'),
            ('        # traceback.print_exc()\n        epoch_str = 1',
             '        if has_checkpoint(hps.model_dir):\n            raise RuntimeError("RVC checkpoint cannot be resumed; preserve it and repair or start a new project")\n        epoch_str = 1'),
            ('    train_loader = DataLoader(\n',
             '    worker_count = max(0, min(4, int(getattr(hps.train, "num_workers", 0))))\n    train_loader = DataLoader(\n'),
            ('        num_workers=4,', '        num_workers=worker_count,'),
            ('        persistent_workers=True,\n        prefetch_factor=8,',
             '        persistent_workers=worker_count > 0,\n        prefetch_factor=2 if worker_count else None,'),
        ],
        'train/dataset/extract_f0.py': [
            ('"assets/rmvpe/rmvpe.pt", is_half=is_half, device=device',
             'os.path.join(os.environ.get("rmvpe_root", "assets/rmvpe"), "rmvpe.pt"), is_half=is_half, device=device'),
        ],
        'train/preprocess.py': [
            ('\n            self.norm_write(tmp_audio, output_key, idx1)\n',
             '\n                self.norm_write(tmp_audio, output_key, idx1)\n'),
        ],
    }
    patched = {}
    for relative, replacements in adaptations.items():
        path = dest / relative
        content = path.read_text(encoding='utf-8')
        for original, replacement in replacements:
            if content.count(original) != 1:
                raise RuntimeError(f'Patch context changed in {relative}: {original}')
            content = content.replace(original, replacement)
        if relative == 'train/train.py':
            start = content.index('    if epoch % hps.save_every_epoch == 0 and rank == 0:')
            end = content.index('        if rank == 0 and hps.save_every_weights == "1":', start)
            content = (content[:start] +
                '    if (epoch % hps.save_every_epoch == 0 or epoch >= hps.total_epoch) and rank == 0:\n'
                '        save_pair(hps.model_dir, net_g, net_d, optim_g, optim_d, hps.train.learning_rate, epoch)\n'
                + content[end:])
        path.write_text(content, encoding='utf-8')
        patched[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    (dest / 'PATCHES.json').write_text(json.dumps({
        'description': 'Configured shared assets and bounded Windows data-loader memory',
        'files': patched,
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Vendored {len(hashes)} RVC files at {actual}')


if __name__ == '__main__':
    main()
