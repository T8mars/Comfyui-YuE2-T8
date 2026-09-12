"""Build a portable full bundle from a signed-off code ZIP and one clean runtime.

The target must be new. Failed builds remain inspectable and are never published
by this script. No local settings, user voices, recordings or GGUF are copied.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

MODEL_DIRS = {'Demucs','MERT-v2-FullSong','RVC','Seed-VC','SheetSage2','YuE2-3B','YuE2-Vae'}
MODEL_MANIFESTS = {'MODEL_MANIFEST.json','VOICE_MODEL_MANIFEST.json'}
EXCLUDED = {'__pycache__','.git','.cache','.pytest_cache','__MACOSX'}
PRIVATE_ROOTS = {'models','runtime','downloads','outputs','uploads','exports','logs','cache','userdata','research'}
PRIVATE_FILES = {'settings.json','server.json','retention.json','service.lock','yue2_home.txt','roadmap.md'}
FLASH_WHEELS = {
    'prebuilt/flash_attn-2.8.3+cu128torch2.10-cp312-cp312-win_amd64.whl':
        '1dcb69d150658ad65ad5299a2789bc49302099db05fd64848b86e6f6a412a52d',
    'wheels/flash_attn-2.8.3+cu128torch2.10.sm120-cp312-cp312-win_amd64.whl':
        '82092704caa35442a21c34cde557f674c0cb6c251486134985457825a01fe959',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def plain_files(directory):
    if directory.is_symlink() or directory.is_junction():
        raise ValueError(f'Bundle source must contain actual files, not links: {directory}')
    for parent,dirs,files in os.walk(directory,followlinks=False):
        dirs[:] = [d for d in dirs if d not in EXCLUDED]
        for name in [*dirs,*files]:
            path = Path(parent)/name
            if path.is_symlink() or path.is_junction():
                raise ValueError(f'Bundle source contains a link: {path}')
        for name in files:
            path = Path(parent)/name
            if path.suffix.lower() not in {'.pyc','.pyo','.gguf'}:
                yield path


def archive_relative(filename,prefix):
    if not filename.startswith(prefix):
        raise ValueError('Code ZIP contains an unexpected root')
    path = Path(filename[len(prefix):])
    if path.is_absolute() or path.drive or '..' in path.parts or '\\' in filename:
        raise ValueError('Code ZIP contains an unsafe path')
    if any(part.lower()=='roadmap.md' for part in path.parts):
        raise ValueError('Local roadmap must never enter a release or bundle')
    if path.parts and (path.parts[0].lower() in PRIVATE_ROOTS or path.name.lower() in PRIVATE_FILES):
        raise ValueError('Code ZIP contains local data/settings')
    return path


def runtime_files(runtime):
    installed = json.loads((runtime/'installed.json').read_text(encoding='utf-8-sig'))
    if installed.get('layout')!='unified' or installed.get('python')!='3.12.10':
        raise ValueError('Full bundle requires the validated unified CPython3.12.10 runtime')
    files = list(plain_files(runtime))
    interpreters = [p.relative_to(runtime).as_posix() for p in files if p.name.lower()=='python.exe']
    if interpreters!=['python.exe']:
        raise ValueError(f'Expected exactly one Python executable: {interpreters}')
    lines = {line.strip() for line in (runtime/'python312._pth').read_text().splitlines() if line.strip()}
    if lines != {'python312.zip','.','Lib\\site-packages','..','import site'}:
        raise ValueError('Runtime has development or nonportable Python search paths')
    return files,installed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--update-manifest',required=True,type=Path)
    parser.add_argument('--runtime',required=True,type=Path)
    parser.add_argument('--models',required=True,type=Path)
    parser.add_argument('--launcher',required=True,type=Path)
    parser.add_argument('--flash-build',required=True,type=Path)
    parser.add_argument('--target',required=True,type=Path)
    args = parser.parse_args()
    target = args.target.absolute()
    if target.exists() or target.is_symlink() or target.is_junction() or target==Path(target.anchor):
        raise ValueError('Select a new, dedicated bundle directory')
    manifest = json.loads(args.update_manifest.read_text(encoding='utf-8'))
    archive = args.update_manifest.parent/manifest['asset']
    if archive.parent.resolve()!=args.update_manifest.parent.resolve() or digest(archive)!=manifest['sha256']:
        raise ValueError('Code ZIP hash/path does not match its release manifest')
    runtime = args.runtime.absolute()
    files,installed = runtime_files(runtime)
    copies = [(p,Path('runtime')/p.relative_to(runtime)) for p in files]
    for name in sorted(MODEL_DIRS):
        directory = args.models/name
        if not directory.is_dir():
            raise ValueError(f'Required model component missing: {name}')
        copies.extend((p,Path('models')/name/p.relative_to(directory)) for p in plain_files(directory))
    copies.extend((args.models/name,Path('models')/name) for name in MODEL_MANIFESTS)
    for relative,expected in FLASH_WHEELS.items():
        wheel = args.flash_build/relative
        if digest(wheel)!=expected:
            raise ValueError('Flash Attention wheel hash mismatch')
        copies.append((wheel,Path('extras/flash-attention')/wheel.name))
    for name in ('README.md','final-status.json','local-verification.json','prebuilt-verification.json'):
        copies.append((args.flash_build/name,Path('extras/flash-attention/provenance')/name))
    copies.append((args.flash_build/'src/LICENSE',Path('extras/flash-attention/LICENSE')))
    if not args.launcher.is_file():
        raise ValueError('Native launcher missing')
    copies.append((args.launcher,Path('YuE2-T8.exe')))
    with zipfile.ZipFile(archive) as code:
        entries = []
        for item in code.infolist():
            relative = archive_relative(item.filename,manifest['archive_root']+'/')
            if item.is_dir() or not relative.parts:
                continue
            if (item.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Code ZIP contains a symbolic link')
            if relative.parts[0] in {'.github','tests'} or relative.name in {'.gitignore','.gitattributes','.comfyignore'}:
                continue
            entries.append((item,relative))
        if installed['runtime_lock_sha256']!=hashlib.sha256(code.read(manifest['archive_root']+'/requirements-unified.lock.txt')).hexdigest():
            raise ValueError('Runtime lock differs from release source')
        total = sum(p.stat().st_size for p,_ in copies)+sum(item.file_size for item,_ in entries)
        parent = target.parent
        while not parent.exists():
            parent = parent.parent
        if shutil.disk_usage(parent).free < total+2*2**30:
            raise ValueError('Insufficient disk space for actual portable files and verification margin')
        target.mkdir(parents=True)
        state = target/'FULL_BUNDLE_BUILD.json'
        def record(value):
            state.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        report = {'state':'building','version':manifest['version'],'source_commit':manifest['source_commit'],
                  'target':str(target),'bytes':total,'published':False}
        record(report)
        for item,relative in entries:
            path = target/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(code.read(item))
    def copy(item):
        src,relative = item
        dst = target/relative
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)
        if digest(src)!=digest(dst):
            raise ValueError(f'Copy hash mismatch: {relative}')
        return src.stat().st_size
    done,last = 0,time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(copy,item) for item in copies]):
            done += future.result()
            if time.monotonic()-last>15:
                print(json.dumps({'copy_bytes':done,'total_bytes':total}),flush=True)
                last=time.monotonic()
    for name in ('outputs','uploads','exports','logs','cache','userdata','downloads'):
        (target/name).mkdir(exist_ok=True)
    (target/'settings.json').write_text('{"schema":1,"model_directory":"models"}\n',encoding='utf-8')
    extras = target/'extras/flash-attention'
    (extras/'README.txt').write_text('Flash Attention 可选编译产物\n\nWindows x64 / Python3.12 / Torch2.10 / CUDA12.8。\n带 sm120 的轮子仅用于 SM120；另一份社区轮子包含多架构内核。\n二者不能同时安装。当前 YuE2 推理未接入独立 flash_attn，安装轮子不会自动加速歌曲生成；正常使用无需安装。\n原生 Torch SDPA 与独立 flash_attn 是不同后端。\n本机源码编译和GPU验证记录已保留，禁止据此宣称整曲加速。\n',encoding='utf-8-sig')
    (extras/'SHA256.json').write_text(json.dumps({Path(k).name:v for k,v in FLASH_WHEELS.items()},indent=2),encoding='utf-8')
    (target/'本地LLM模型下载（可选）.txt').write_text('本地 LLM 模型（可选）\nhttps://pan.quark.cn/s/55eab3bb2d9b\n\nAPI 创作无需下载本地 LLM。所有功能共用 runtime/python.exe；GGUF 权重按需下载。\n在 AI 创作助手中选择本地模式并设置 GGUF 目录。路径非必填，默认使用 models/LLM。\n模型目录不需要 mmproj 文件。生成歌词、曲风和 ABC 后可发送到对应页面。\n',encoding='utf-8-sig')
    python = target/'runtime/python.exe'
    commands = [
        [python,'-X','utf8',target/'scripts/install_unified_runtime.py','--root',target,'--runtime',target/'runtime','--verify-only'],
        [python,'-X','utf8',target/'scripts/verify_models.py','--root',target],
        [python,'-X','utf8',target/'scripts/verify_voice_models.py','--root',target],
        [python,'-X','utf8',target/'scripts/download_rvc_models.py','--root',target,'--source',target,'--verify-only'],
    ]
    environment = {**os.environ,'YUE2_HOME':str(target),'YUE2_KIT':str(target)}
    with (target/'logs/bundle-verification.log').open('wb') as log:
        for command in commands:
            subprocess.run(list(map(str,command)),cwd=target,env=environment,stdout=log,stderr=log,check=True,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    runtime_files(target/'runtime')
    report.update(state='verified',runtime='runtime/python.exe',models_included=True,llm_weights_included=False,
                  flash_attention_wheels_included=True,flash_attention_enabled=False)
    record(report)
    (target/'FULL_BUNDLE_MANIFEST.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
