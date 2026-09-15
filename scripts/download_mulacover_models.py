from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path


def progress(blocks: int, block_size: int, total: int) -> None:
    done = min(total, blocks * block_size)
    if total:
        print(f"DOWNLOAD {done}/{total} ({done / total:.1%})", flush=True)


def fetch(url: str, destination: Path, expected: int | None = None) -> None:
    if destination.is_file() and (expected is None or destination.stat().st_size == expected):
        print(f"REUSE {destination}", flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as target:
        total = int(source.headers.get("Content-Length") or 0)
        copied = 0
        while True:
            chunk = source.read(8 * 1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
            copied += len(chunk)
            print(f"DOWNLOAD {destination.name} {copied}/{total or expected or 0}", flush=True)
    if expected is not None and temporary.stat().st_size != expected:
        raise RuntimeError(f"{destination.name} 大小不匹配：{temporary.stat().st_size} != {expected}")
    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    sys.path.insert(0, str(root))
    from app.yue2_app.mulacover_models import (
        CHORD_COMMIT, CHORD_NAMES, CHORD_SIZES, MODEL_REPOSITORIES, YOURMT3,
        paths, readiness, write_manifest,
    )
    from huggingface_hub import snapshot_download

    locations = paths(root)
    for name, spec in MODEL_REPOSITORIES.items():
        print(f"MODEL {name} {spec['repo']}@{spec['revision']}", flush=True)
        snapshot_download(repo_id=spec["repo"], revision=spec["revision"],
                          local_dir=locations[name], max_workers=2)
    transcriptor = locations["SymbolicTranscriptor"]
    fetch(YOURMT3["url"], transcriptor / YOURMT3["relative"], YOURMT3["bytes"])
    for filename in CHORD_NAMES:
        fetch(
            "https://raw.githubusercontent.com/music-x-lab/"
            f"ISMIR2019-Large-Vocabulary-Chord-Recognition/{CHORD_COMMIT}/cache_data/{filename}",
            transcriptor / "chord" / filename,
            CHORD_SIZES[filename],
        )
    state = readiness(root)
    manifest = write_manifest(root)
    print(f"MANIFEST {manifest}", flush=True)
    if not state["ready"]:
        missing = [name for name, item in state["components"].items() if not item["ready"]]
        raise RuntimeError("模型下载后仍不完整：" + "、".join(missing))
    print("READY", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
