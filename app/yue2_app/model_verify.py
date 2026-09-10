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

PINNED_MODELS = {
    "YuE2-3B": {
        "source": "mrfakename/YuE2-3B",
        "revision": "9c7af7677010933b77b159d9dc1a2848c58e26a1",
        "file": "YuE2-3B/model.safetensors",
        "size": 7261441640,
        "sha256": "1d55c42c1a9875c34f5d736e15078449992b044e807ce2a138e6cf289a1e59e9",
    },
    "YuE2-Vae": {
        "source": "m-a-p/YuE2-Vae",
        "revision": "95535e72a97bc0f09b8ada125d26b4009428c0e8",
        "file": "YuE2-Vae/model.safetensors",
        "size": 530512720,
        "sha256": "807ce9d5149fa27c5ad3e6582058469852e908f6c5acc8c8aa338e7ab7751346",
    },
    "SheetSage2": {
        "source": "m-a-p/SheetSage2",
        "revision": "eab522a8168e8b8b8c4856bf8609cd86198f01fe",
        "file": "SheetSage2/model.safetensors",
        "size": 228738564,
        "sha256": "b235f68091a5f5b644000f2b5acb57d1e70432aca2b34ab1b9cf27236e1f4274",
    },
    "MERT-v2-FullSong": {
        "source": "m-a-p/MERT-v2-FullSong",
        "revision": "d8ba1c745e733b3908ce6ad16ebeb17ac7600a42",
        "file": "MERT-v2-FullSong/model.safetensors",
        "size": 2529812848,
        "sha256": "e6dd2ab187d6dd62b6521cd7d8f932e237acf0c5757745a7232082e28391350d",
    },
}


def pinned_entries(manifest: dict, names=None) -> dict:
    if manifest.get("bundle") != "t8star/YuE2-Comfy":
        raise ValueError("Unexpected model bundle identity")
    entries = manifest.get("models")
    selected_names = tuple(names or PINNED_MODELS)
    if not isinstance(entries, dict) or any(name not in entries for name in selected_names):
        raise ValueError("Model manifest does not contain the required models")
    selected = {}
    for name in selected_names:
        entry = entries[name]
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid model manifest entry: {name}")
        expected = PINNED_MODELS[name]
        for key, value in expected.items():
            actual = entry.get(key)
            if key == "sha256":
                actual, value = str(actual).lower(), str(value).lower()
            if actual != value:
                raise ValueError(f"Pinned model identity mismatch: {name}.{key}")
        selected[name] = {key: entry[key] for key in expected}
    return selected


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
    entries = manifest.get("models")
    if not isinstance(entries, dict) or set(entries) != set(REQUIRED_FILES):
        raise ValueError("Model manifest does not contain the four required models")
    pinned_entries(manifest)

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
