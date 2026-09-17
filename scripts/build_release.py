"""Build the stable updater assets from an immutable, clean Git HEAD."""
import argparse
import ast
import hashlib
import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


def safe_archive_path(name: str) -> Path:
    if name == "":
        return Path(".")
    path = Path(name)
    posix_path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)
    if (path.is_absolute() or posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive
            or ".." in path.parts or ".." in posix_path.parts or ".." in windows_path.parts or "\\" in name):
        raise ValueError(f"Unsafe release archive path: {name}")
    return path


def build(output):
    root = Path(__file__).resolve().parents[1]
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(root), *arguments])
    if git("status", "--porcelain", "--untracked-files=normal").strip():
        raise RuntimeError("Commit the release source before building assets")
    commit = git("rev-parse", "HEAD").decode().strip()
    project = git("show", "HEAD:pyproject.toml").decode()
    version = re.search(r'^version = "(\d+\.\d+\.\d+)"$', project, re.M).group(1)
    for filename in ("client.py", "comfyui_nodes/client.py", "app/yue2_app/__init__.py"):
        assert f'__version__ = "{version}"' in git("show", f"HEAD:{filename}").decode(), filename
    tag = "v" + version
    prefix = f"Comfyui-YuE2-T8-{tag}/"
    output.mkdir(parents=True, exist_ok=True)
    asset = output / f"Comfyui-YuE2-T8-{tag}-code.zip"
    git("archive", "--format=zip", f"--prefix={prefix}", f"--output={asset}", commit,
        "--", ".", ":(exclude).github", ":(exclude)tests")
    preserve = ["models/", "runtime/", "downloads/", "outputs/", "uploads/", "exports/", "logs/", "cache/", "userdata/",
                "settings.json", "retention.json", "server.json", "service.lock", "yue2_home.txt", "roadmap.md"]
    with zipfile.ZipFile(asset) as archive:
        assert archive.testzip() is None
        names = [name.removeprefix(prefix) for name in archive.namelist()]
        source_tree = ast.parse(git("show", "HEAD:app/yue2_app/mulacover_models.py").decode())
        source_files = next(ast.literal_eval(node.value) for node in source_tree.body
                            if isinstance(node, ast.Assign) and any(
                                isinstance(target, ast.Name) and target.id == "SOURCE_FILES"
                                for target in node.targets))
        for relative in source_files:
            assert f"vendor/mulacover/{relative}" in names, f"Missing MuLaCover source: {relative}"
        assert not any(name.startswith((".github/", "tests/")) for name in names)
        for name in names:
            path = safe_archive_path(name)
            lower_name = name.lower()
            assert not any(part.lower() == "roadmap.md" for part in path.parts), name
            assert not any(lower_name == protected or (protected.endswith("/") and lower_name.startswith(protected)) for protected in preserve), name
            assert not lower_name.endswith((".pyc", ".safetensors", ".pth", ".pt", ".bin", ".gguf", ".onnx", ".ckpt")), name
        for required in ("nodes.py", "client.py", "app/yue2_app/workflow_worker.py", "vendor/yue2/nar.py",
                         "vendor/yue2/pipeline.py", "vendor/seed-vc/inference.py", "app/web/app.js",
                         "requirements-unified.lock.txt", "scripts/setup_unified.ps1",
                         "scripts/install_unified_runtime.py", "scripts/apply_unified_update.ps1",
                         "scripts/runtime_paths.ps1", "scripts/install_staged_models.py",
                         "scripts/download_rvc_models.py", "vendor/rvc/LICENSE",
                         "vendor/msvc-runtime/manifest.json", "vendor/msvc-runtime/msvcp140.dll",
                         "app/yue2_app/rvc_worker.py", "app/yue2_app/rvc_assets.json", "app/web/rvc.js",
                         "app/yue2_app/asset_library.py", "app/yue2_app/training_resources.py",
                         "app/yue2_app/training_worker.py", "app/yue2_app/yue2_adapter.py",
                         "app/yue2_app/yue2_trainer.py", "app/yue2_app/yue2_training_data.py",
                         "app/yue2_app/yue2_training_assets.json", "app/yue2_app/workbench_api.py",
                         "app/web/workbench.js", "app/web/workbench.css",
                         "app/web/vendor/bootstrap-icons.css",
                         "app/web/vendor/fonts/bootstrap-icons.woff2",
                         "app/web/vendor/BOOTSTRAP-ICONS-LICENSE.txt"):
            assert required in names, required
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    asset.with_suffix(asset.suffix + ".sha256").write_text(f"{digest}  {asset.name}\n", encoding="utf-8")
    manifest = {"schema": 1, "channel": "stable", "version": version, "tag": tag,
                "published_at": datetime.now(timezone.utc).date().isoformat(), "asset": asset.name,
                "download_url": f"https://github.com/T8mars/Comfyui-YuE2-T8/releases/download/{tag}/{asset.name}",
                "sha256": digest, "models_included": False,
                "model_repository": "https://huggingface.co/t8star/YuE2-Comfy", "preserve": preserve,
                "source_commit": commit, "archive_root": prefix.rstrip("/")}
    (output / "update-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"asset": str(asset), "bytes": asset.stat().st_size, **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output.resolve())
