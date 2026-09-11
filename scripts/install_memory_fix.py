"""Install the tested code update with file-level backups; preserve user data."""
import argparse
import hashlib
import json
import shutil
import time
import tomllib
import urllib.request
import urllib.error
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    version = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    target = args.target.resolve(strict=True)
    if target == source or not (target / "app/yue2_app/service.py").is_file():
        raise ValueError("Target must be a different, existing YuE2 installation")
    state_file = target / "server.json"
    if state_file.is_file():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{int(state['port'])}/api/health", timeout=2) as response:
                health = json.load(response)
            if Path(health.get("root", "")).resolve() == target:
                raise RuntimeError("请先停止目标整合包的服务，再安装更新")
        except (urllib.error.URLError, TimeoutError):
            pass
    backup = target / "logs/backups" / ("before-" + version + "-" + time.strftime("%Y%m%d-%H%M%S"))
    files = [path for path in (source / "app").rglob("*")
             if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"]
    files += list((source / "vendor/yue2").glob("*.py"))
    # The bundled ComfyUI installer copies this directory, not the root nodes.py.
    files += [path for path in (source / "comfyui_nodes").rglob("*")
              if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
              and path.name != "yue2_home.txt"]
    files += [source / name for name in (
        "vendor/seed-vc/inference.py", "nodes.py", "client.py", "pyproject.toml",
        "CHANGELOG.md", "USER_GUIDE.md", "VALIDATION.md")]
    files += [source / "scripts/install_llm.py", source / "安装本地LLM.bat"]
    copies = [(path, path.relative_to(source)) for path in files]
    if not (source / "comfyui_nodes").is_dir():
        # GitHub code archives keep the node files at the repository root.
        copies += [(source / name, Path("comfyui_nodes") / name) for name in ("__init__.py", "client.py", "nodes.py")]
    records = []
    for path, relative in copies:
        destination = (target / relative).resolve()
        if target not in destination.parents:
            raise ValueError("Update destination leaves the chosen installation")
        existed = destination.is_file()
        if existed:
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Copy verification failed: {relative}")
        records.append({"file": relative.as_posix(), "sha256": digest, "existed": existed})
    backup.mkdir(parents=True, exist_ok=True)
    manifest = {"version": version, "source": str(source), "target": str(target), "files": records}
    (backup / "update_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"updated_files": len(records), "backup": str(backup), "target": str(target)}))


if __name__ == "__main__":
    main()
