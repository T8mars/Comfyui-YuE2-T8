"""Run the vendored RVC compute core in a worker using the studio's Python.

Working files live in the user's project, never in vendor/rvc. Model files are
read from the configured model root. This process boundary also prevents RVC's
top-level modules from colliding with Seed-VC's modules in the same interpreter.
"""
from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

STAGES = {
    'preprocess': 'train/preprocess.py',
    'f0': 'train/dataset/extract_f0.py',
    'features': 'train/dataset/extract_hubert_feature.py',
    'train': 'train/train.py',
    'index': 'train/train_index.py',
}


def configure(root: Path, assets: Path, workspace: Path):
    source = root / 'vendor/rvc'
    if not (source / 'UPSTREAM.json').is_file():
        raise FileNotFoundError('RVC 计算组件未安装')
    workspace.mkdir(parents=True, exist_ok=True)
    for folder in ('assets/weights', 'assets/indices', 'logs'):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
    os.environ['RVC_ASSET_ROOT'] = str(assets)
    os.environ['rmvpe_root'] = str(assets)
    os.environ['weight_root'] = str(workspace / 'assets/weights')
    os.environ['index_root'] = str(workspace / 'logs')
    os.environ['outside_index_root'] = str(workspace / 'assets/indices')
    os.environ['RVC_CUDA_GRAPH'] = '0'  # bounded memory; enable only after baseline validation
    os.environ['RVC_AUDIO_FORCE_CPU'] = '1'  # FFmpeg decode, CUDA for neural computation
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['PATH'] = str(root / 'runtime/ffmpeg') + os.pathsep + os.environ.get('PATH', '')
    sys.path.insert(0, str(source))
    os.chdir(workspace)
    return source


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('stage', choices=[*STAGES, 'infer'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    source = configure(args.root.resolve(), args.assets.resolve(), args.workspace.resolve())
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    if args.stage == 'infer':
        # Importing upstream cli changes cwd; restore it before calling main.
        workspace = Path.cwd()
        from infer.cli import main as infer_main
        os.chdir(workspace)
        return infer_main(arguments)
    script = source / STAGES[args.stage]
    sys.argv = [str(script), *arguments]
    runpy.run_path(str(script), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
