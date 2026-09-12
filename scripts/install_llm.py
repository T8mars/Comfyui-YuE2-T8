"""Install the optional GGUF backend into the shared studio runtime."""
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
    target = root / "runtime"
    python = target / "python.exe"
    if os.name != "nt" or not python.is_file():
        raise RuntimeError("请先运行安装运行环境，将整合包升级为统一 Python 环境")
    if Path(sys.executable).resolve() != python.resolve():
        raise RuntimeError("必须使用整合包 runtime/python.exe 安装本地 LLM")
    if not (target / "Lib/site-packages/torch/lib/cublas64_12.dll").is_file():
        raise RuntimeError("统一 CUDA 运行环境不完整，请重新运行安装运行环境")
    downloads = root / "downloads/llm"
    downloads.mkdir(parents=True, exist_ok=True)
    wheel = downloads / WHEEL
    download(WHEEL_URL, wheel, WHEEL_SHA)
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], cwd=root, check=True)
    subprocess.run([str(python), "-m", "pip", "check"], cwd=root, check=True)
    environment = os.environ.copy()
    environment.update(YUE2_HOME=str(root), YUE2_KIT=str(root))
    probe = subprocess.run([str(python), "-X", "utf8", "-m", "app.yue2_app.llm_runtime"],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8")
    if probe.returncode:
        raise RuntimeError("本地 LLM 组件验证失败：\n" + probe.stderr[-5000:])
    state = target / "installed.json"
    manifest = json.loads(state.read_text(encoding="utf-8-sig")) if state.exists() else {"schema": 2, "layout": "unified"}
    details = json.loads(probe.stdout.strip().splitlines()[-1])
    details["module"] = "runtime/Lib/site-packages/llama_cpp/__init__.py"
    manifest["llm"] = {"wheel": WHEEL, "wheel_sha256": WHEEL_SHA, "probe": details}
    state.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("本地 LLM 与音乐功能共用 runtime/python.exe。请选择 GGUF 模型并测试连接。", flush=True)


if __name__ == "__main__":
    main()
