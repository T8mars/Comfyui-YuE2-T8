from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REQUIRED_FILES = {
    "YuE2-3B": ("config.json", "qwen.tiktoken", "yue2_generation_config.json"),
    "YuE2-Vae": ("config.json", "modeling_vae.py"),
    "SheetSage2": ("config.json", "modeling_sheetsage2.py", "processor_config.json"),
    "MERT-v2-FullSong": ("config.json", "modeling_mert2.py", "preprocessor_config.json"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_bundle(root: Path, progress: bool = True) -> dict:
    models = root.resolve() / "models"
    manifest_path = models / "MODEL_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("bundle") != "t8star/YuE2-Comfy":
        raise ValueError("Unexpected model bundle identity")
    entries = manifest.get("models")
    if not isinstance(entries, dict) or set(entries) != set(REQUIRED_FILES):
        raise ValueError("Model manifest does not contain the four required models")

    checked = {}
    for name, required in REQUIRED_FILES.items():
        entry = entries[name]
        relative = Path(str(entry["file"]))
        weight = (models / relative).resolve()
        if models != weight and models not in weight.parents:
            raise ValueError(f"Model path escapes bundle: {relative}")
        if weight.is_symlink() or not weight.is_file():
            raise FileNotFoundError(f"Missing model weight: {relative}")
        directory = weight.parent
        missing = [filename for filename in required if not (directory / filename).is_file()]
        if missing:
            raise FileNotFoundError(f"{name} is missing required files: {', '.join(missing)}")
        expected_size = int(entry["size"])
        if weight.stat().st_size != expected_size:
            raise ValueError(f"Model size mismatch: {relative}")
        if progress:
            print(f"Verifying {relative} ({expected_size / 2**30:.2f} GiB)", flush=True)
        digest = sha256(weight)
        if digest.lower() != str(entry["sha256"]).lower():
            raise ValueError(f"Model SHA-256 mismatch: {relative}")
        checked[name] = {"file": str(relative), "bytes": expected_size, "sha256": digest}
    return checked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the pinned YuE2 model bundle")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    checked = verify_bundle(args.root)
    print(f"Verified {len(checked)} model weights", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
