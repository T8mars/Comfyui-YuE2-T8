"""Install and verify all studio components in the invoking Python 3.12 runtime.

The PowerShell controller owns directory promotion, so no running interpreter
renames or deletes its own DLLs. This helper never creates another environment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import importlib.metadata
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_crt(source, runtime, *, install=False):
    """Keep the Microsoft CRT beside python.exe, without changing Windows."""
    directory = source / 'vendor/msvc-runtime'
    manifest = directory / 'manifest.json'
    entries = json.loads(manifest.read_text(encoding='utf-8-sig'))['files']
    for entry in entries:
        name = entry['name']
        if Path(name).name != name or not name.endswith('.dll'):
            raise ValueError('Invalid CRT manifest path')
        original, destination = directory / name, runtime / name
        if digest(original) != entry['sha256']:
            raise RuntimeError('Bundled Microsoft CRT hash mismatch: ' + name)
        matches = destination.is_file() and digest(destination) == entry['sha256']
        if not matches and install:
            # Identical Python-bundled DLLs are skipped: they may already be loaded.
            # Different loaded DLLs fail normally; never replace them via reboot tricks.
            shutil.copy2(original, destination)
            matches = digest(destination) == entry['sha256']
        if not matches:
            raise RuntimeError('Local Microsoft CRT missing or changed: ' + name)
    return digest(manifest)


def run(command, *, root, environment, capture=False):
    print('Running:', ' '.join(map(str, command[:5])), flush=True)
    result = subprocess.run(list(map(str, command)), cwd=root, env=environment,
                            text=True, encoding='utf-8',
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        if capture:
            print((result.stdout or '')[-5000:], flush=True)
            print((result.stderr or '')[-8000:], file=sys.stderr, flush=True)
        result.check_returncode()
    return result


def verify(root, runtime, environment, renderer, source=None):
    source = source or root
    crt_sha = local_crt(source, runtime)
    python = runtime / 'python.exe'
    run([python, '-m', 'pip', 'check'], root=root, environment=environment)
    for line in (source / 'requirements-unified.lock.txt').read_text(encoding='utf-8').splitlines():
        pinned = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s]+)', line.strip())
        if pinned and importlib.metadata.version(pinned[1]) != pinned[2]:
            raise RuntimeError('Installed dependency does not match the release lock: ' + pinned[1])
    if importlib.metadata.version('llama-cpp-python').split('+')[0] != '0.3.49':
        raise RuntimeError('Installed GGUF backend does not match the release lock')
    snippets = {
        'core': "import torch, transformers, numpy, soundfile; assert torch.__version__ == '2.10.0+cu128'; assert transformers.__version__ == '4.57.6'; assert numpy.__version__ == '1.26.4'; import yue2; print(torch.__version__)",
        'transcription': 'import av, torchaudio, pretty_midi, mir_eval; assert torchaudio.__version__ == "2.10.0+cu128"; from transformers import AutoModel, AutoProcessor; print("ok")',
        'voice': 'import demucs, librosa, scipy; import inference; print("ok")',
        'rvc': 'import faiss, parselmouth; from infer.module.models import SynthesizerTrnMs768NSFsid; from infer.rmvpe import RMVPE; print("ok")',
        'llm': 'from app.yue2_app.llm_runtime import initialize_backends; import json; print(json.dumps(initialize_backends()))',
    }
    reports = {}
    for component, snippet in snippets.items():
        paths = [str(source), str(source / 'vendor')]
        if component == 'voice':
            paths.insert(0, str(source / 'vendor/seed-vc'))
        if component == 'rvc':
            paths.insert(0, str(source / 'vendor/rvc'))
        code = 'import sys; sys.path[:0] = ' + repr(paths) + '; ' + snippet
        if component == 'core':
            code += ('; import ctypes; from pathlib import Path; '
                     'crt = ctypes.CDLL("msvcp140.dll"); '
                     'buffer = ctypes.create_unicode_buffer(32768); '
                     'get_path = ctypes.windll.kernel32.GetModuleFileNameW; '
                     'get_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]; '
                     'assert get_path(crt._handle, buffer, len(buffer)); '
                     'assert Path(buffer.value).resolve() == Path(sys.executable).parent / "msvcp140.dll", buffer.value')
        result = run([python, '-X', 'utf8', '-c', code], root=root, environment=environment, capture=True)
        reports[component] = result.stdout.strip().splitlines()[-1]
    if renderer:
        code = ('from playwright.sync_api import sync_playwright; '
                'p=sync_playwright().start(); b=p.chromium.launch(headless=True,args=["--disable-gpu"]); '
                'page=b.new_page(); page.set_content("<p>renderer</p>"); assert page.inner_text("p")=="renderer"; '
                'b.close(); p.stop(); print("ok")')
        run([python, '-X', 'utf8', '-c', code], root=root, environment=environment)
    ffmpeg = runtime / 'ffmpeg/ffmpeg.exe'
    run([ffmpeg, '-version'], root=root, environment=environment, capture=True)
    probe = json.loads(reports.pop('llm'))
    probe['module'] = 'runtime/Lib/site-packages/llama_cpp/__init__.py'
    return {'schema': 2, 'layout': 'unified', 'python': '3.12.10', 'torch': '2.10.0+cu128',
            'runtime_lock_sha256': digest(source / 'requirements-unified.lock.txt'),
            'msvc_runtime_manifest_sha256': crt_sha,
            'installed_at': time.time(), 'checks': reports, 'renderer': renderer,
            'ffmpeg': 'runtime/ffmpeg/ffmpeg.exe', 'llm': {'probe': probe},
            'note': 'Component import and browser verification; inference regression is recorded separately.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--source', type=Path, help='Staged application source during an update')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--skip-renderer', action='store_true')
    args = parser.parse_args()
    root, runtime = args.root.resolve(), args.runtime.resolve()
    source = args.source.resolve() if args.source else root
    if Path(sys.executable).resolve() != runtime / 'python.exe' or sys.version_info[:3] != (3, 12, 10):
        raise RuntimeError('Run this installer with the target Python 3.12.10 interpreter')
    if root not in runtime.parents:
        raise ValueError('Runtime must be inside the selected studio directory')
    if any((runtime / name / 'python.exe').exists() for name in ('core', 'voice', 'transcribe', 'llm')):
        raise RuntimeError('Unified runtime contains an old secondary interpreter')
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'CUDA_HOME', 'CUDA_PATH'):
        environment.pop(name, None)
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', YUE2_HOME=str(root),
                       YUE2_KIT=str(root), PLAYWRIGHT_BROWSERS_PATH=str(runtime / 'playwright'))
    python = runtime / 'python.exe'
    if not args.verify_only:
        local_crt(source, runtime, install=True)
        run([python, '-m', 'pip', 'install', '--prefer-binary', '-r', source / 'requirements-unified.lock.txt'],
            root=root, environment=environment)
        result = run([python, '-c', 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())'],
                     root=root, environment=environment, capture=True)
        ffmpeg = runtime / 'ffmpeg'
        ffmpeg.mkdir(exist_ok=True)
        shutil.copy2(result.stdout.strip(), ffmpeg / 'ffmpeg.exe')
        if not args.skip_renderer:
            run([python, '-m', 'playwright', 'install', '--only-shell', 'chromium'], root=root, environment=environment)
    manifest = verify(root, runtime, environment, not args.skip_renderer, source)
    (runtime / 'installed.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
