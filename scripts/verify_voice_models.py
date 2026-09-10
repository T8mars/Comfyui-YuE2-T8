from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    models = args.root.resolve() / "models"
    manifest = json.loads((models / "VOICE_MODEL_MANIFEST.json").read_text(encoding="utf-8-sig"))
    checked = 0
    for component in manifest["components"].values():
        for record in component["files"]:
            relative = Path(record["path"])
            path = (models / relative).resolve()
            if models.resolve() not in path.parents or path.is_symlink() or not path.is_file():
                raise FileNotFoundError(relative.as_posix())
            if path.stat().st_size != int(record["size"]):
                raise ValueError(f"文件大小不符：{relative.as_posix()}")
            if digest(path) != str(record["sha256"]).lower():
                raise ValueError(f"SHA256 不符：{relative.as_posix()}")
            checked += 1
    print(f"Reference voice models verified: {checked} files")


if __name__ == "__main__":
    main()
