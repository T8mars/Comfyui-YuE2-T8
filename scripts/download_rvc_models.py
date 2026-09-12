"""Download or verify the pinned shared assets for RVC training/inference."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    from app.yue2_app.settings import model_directory
    assets = model_directory(root, strict=True) / 'RVC'
    manifest = json.loads((args.source.resolve() / 'app/yue2_app/rvc_assets.json').read_text(encoding='utf-8'))
    errors = []
    for relative, expected in manifest['files'].items():
        path = assets / relative
        def valid():
            if not path.is_file() or path.stat().st_size != expected['size']:
                return False
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest() == expected['sha256']
        if not valid() and not args.verify_only:
            from huggingface_hub import hf_hub_download
            hf_hub_download(repo_id=manifest['repository'], revision=manifest['revision'],
                            filename=relative, local_dir=assets, force_download=path.exists())
        if not valid():
            errors.append(relative)
        print(('INVALID ' if relative in errors else 'OK ') + relative, flush=True)
    if errors:
        raise RuntimeError('RVC assets missing or corrupt: ' + ', '.join(errors))


if __name__ == '__main__':
    main()
