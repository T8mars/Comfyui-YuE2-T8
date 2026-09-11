"""Install only the isolated text runtime. No music runtime/model modification."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
PYTHON_SHA = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
WHEEL = "llama_cpp_python-0.3.49+cu128-cp312-cp312-win_amd64.whl"
WHEEL_URL = "https://github.com/JamePeng/llama-cpp-python/releases/download/v0.3.49-cu128-win-20260831/" + WHEEL
WHEEL_SHA = "8f8f41e7d735754a294dd9ec35412d60e596d497c95f8f13ae09935bd98b8205"
DEPENDENCIES = ("pip==25.3", "numpy==2.2.6", "diskcache==5.6.3", "jinja2==3.1.6",
                "typing-extensions==4.15.0", "requests==2.32.5", "Pillow==12.3.0", "MarkupSafe==3.0.3",
                "charset-normalizer==3.5.1", "idna==3.19", "urllib3==2.7.0", "certifi==2026.7.22")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(url, path, expected):
    if path.is_file() and sha(path) == expected:
        print("Verified cached", path.name, flush=True)
        return
    partial = path.with_suffix(path.suffix + ".partial")
    print("Downloading", path.name, flush=True)
    offset = partial.stat().st_size if partial.is_file() else 0
    request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
    with urllib.request.urlopen(request, timeout=120) as response:
        resume = offset and response.status == 206
        if resume and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise RuntimeError("Server returned a mismatched resume offset; partial download preserved")
        if response.status not in (200, 206):
            raise RuntimeError("Unexpected download status")
        if offset and not resume:
            print("Server did not accept byte ranges; downloading a fresh copy", flush=True)
        count, announced = (offset if resume else 0), 0
        with partial.open("ab" if resume else "wb") as stream:
            while block := response.read(1024 * 1024):
                stream.write(block); count += len(block)
                if count - announced > 32 * 1024 * 1024:
                    print(round(count / 2**20), "MiB", flush=True); announced = count
    if sha(partial) != expected:
        raise RuntimeError("Download SHA256 mismatch: " + path.name)
    partial.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    if os.name != "nt":
        raise RuntimeError("This distribution targets Windows x64")
    target = root / "runtime/llm"
    downloads = root / "downloads/llm"
    downloads.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    pyarchive = root / "downloads/python-3.12.10-embed-amd64.zip"
    download(PYTHON_URL, pyarchive, PYTHON_SHA)
    wheel = downloads / WHEEL
    download(WHEEL_URL, wheel, WHEEL_SHA)
    python = target / "python.exe"
    if not python.is_file():
        with zipfile.ZipFile(pyarchive) as archive:
            archive.extractall(target)
    (target / "python312._pth").write_text("python312.zip\n.\nLib\\site-packages\n..\\..\nimport site\n", encoding="ascii")
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    subprocess.run([sys.executable, "-m", "pip", "--python", str(python), "install", "--only-binary=:all:",
                    *DEPENDENCIES, str(wheel)], cwd=root, env=environment, check=True)
    subprocess.run([str(python), "-m", "pip", "check"], cwd=root, env=environment, check=True)
    # Reuse the bundle's CUDA 12 libraries as files, never import Torch or alter core.
    cuda = target / "cuda"
    cuda.mkdir(exist_ok=True)
    libraries = {}
    for name in ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"):
        source = root / "runtime/core/Lib/site-packages/torch/lib" / name
        if not source.is_file():
            raise RuntimeError("The core CUDA 12 runtime is incomplete: " + name)
        digest = sha(source)
        destination = cuda / name
        if not destination.is_file() or sha(destination) != digest:
            shutil.copy2(source, destination)
        if sha(destination) != digest:
            raise RuntimeError("CUDA library copy verification failed: " + name)
        libraries[name] = {"sha256": digest, "bytes": destination.stat().st_size}
    for name in ("CUDA_PATH", "CUDA_HOME"):
        environment.pop(name, None)
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(map(str, (target, target / "Scripts", windows / "System32", windows)))
    probe = subprocess.run([str(python), "-X", "utf8", "-m", "app.yue2_app.llm_runtime"],
        cwd=root, env=environment, capture_output=True, text=True)
    if probe.returncode:
        raise RuntimeError("Isolated runtime probe failed:\n" + probe.stderr[-5000:])
    manifest = {"schema": 1, "python_sha256": PYTHON_SHA, "wheel": WHEEL, "wheel_url": WHEEL_URL,
                "wheel_sha256": WHEEL_SHA, "probe": json.loads(probe.stdout.strip().splitlines()[-1]),
                "cuda_libraries": libraries,
                "dependencies": list(DEPENDENCIES),
                "note": "Import probe only; test the selected GGUF before claiming inference compatibility."}
    (target / "installed.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
