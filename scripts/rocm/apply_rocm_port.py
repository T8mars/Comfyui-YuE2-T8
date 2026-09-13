"""Apply the ROCm/HIP port to a YuE2-Music-T8 checkout, idempotently.

The functional changes here are the same ones proposed in the companion pull
requests:

  1. vendor/yue2/cuda_graph.py    decode-time attention backend probe
  2. vendor/seed-vc/inference.py  CAMPPlus / RMVPE precision
  3. app/yue2_app/voice_worker.py MIOpen JIT failures in the voice worker
  4. app/yue2_app/core_worker.py  VAE tile size from the device, not the budget

so on a checkout that already carries them every entry reports SKIP. The script
exists so that an AMD user can get a working kit today, without waiting for those
PRs to land, and so that the port stays reproducible.

Two further entries are kit hygiene rather than device support and are therefore
opt-in:

  --freeze-updater   make the in-app updater report "already up to date".
                     Re-extracting a release archive over the kit replaces the
                     ROCm PyTorch runtime and wipes every patch above.
  --device-wording   replace the hardcoded "NVIDIA" in the UI banner and the
                     three worker self-checks with device-neutral text. Pure
                     strings; the checks themselves already work through the HIP
                     alias, so the old wording only misleads.

Everything else is left alone: upstream files are modified only where a patch is
listed above, so the checkout stays easy to rebase.

Usage:
    python scripts/rocm/apply_rocm_port.py <kit> [<kit> ...] [--freeze-updater]
                                                                 [--device-wording]
Re-running is safe. An entry whose anchors are gone is reported as GONE rather
than as a failure -- upstream may have fixed it a different way, and that is not
an error for the user.
"""
import argparse
import sys
from pathlib import Path

# --------------------------------------------------------------------- 1. cuda_graph
CG_OLD = (
    '        if attention_backend == "auto":\n'
    '            attention_backend = "flash" if flash else "cudnn" if fused and torch.backends.cudnn.is_available() else "sdpa"\n'
)
CG_NEW = (
    '        if attention_backend == "auto":\n'
    '            # The probe above only inspects the ATen schema, which every backend\n'
    '            # shares. On ROCm/HIP torch.cuda is the HIP alias, so device.type is\n'
    '            # still "cuda" and _flash_attention_forward does advertise seqused_k,\n'
    '            # yet ROCm\'s mha_varlen_fwd rejects that argument:\n'
    '            #   RuntimeError: [ROCm] mha_varlen_fwd: seqused_k must be nullopt\n'
    '            # Picking "flash" there crashes on the first decode step. Passing\n'
    '            # seqused_k=None is not a substitute either: variable-length FA would\n'
    '            # then attend over the unused future cache slots. Masked SDPA is exact\n'
    '            # for this graph (0.0 max abs diff vs eager, captured and replayed on\n'
    '            # gfx1201), so prefer it on any HIP build.\n'
    '            if getattr(torch.version, "hip", None) is not None:\n'
    '                attention_backend = "sdpa"\n'
    '            else:\n'
    '                attention_backend = "flash" if flash else "cudnn" if fused and torch.backends.cudnn.is_available() else "sdpa"\n'
)

# --------------------------------------------------------------------- 2. Seed-VC
CP_A_OLD = (
    "    campplus_model.eval()\n"
    "    campplus_model.to(device)\n"
)
CP_A_NEW = (
    "    campplus_model.eval()\n"
    "    campplus_model.to(device)\n"
    "    # CAMPPlus is the only model in this function that would stay fp32 while every\n"
    "    # other one follows args.fp16. Beyond the inconsistency, its fp32 BatchNorm\n"
    "    # makes MIOpen JIT-compile MIOpenBatchNormFwdInferSpatial through HIPRTC on the\n"
    "    # first encoder call, which fails on ROCm wheels shipped without libc++ headers\n"
    "    # for comgr:\n"
    "    #   fatal error: 'type_traits' file not found -> miopenStatusUnknownError\n"
    "    # fp16 selects a precompiled batchnorm kernel instead, so follow args.fp16 like\n"
    "    # the rest of the ensemble.\n"
    "    if fp16:\n"
    "        campplus_model.half()\n"
)
CP_B_OLD = (
    "    feat2 = feat2 - feat2.mean(dim=0, keepdim=True)\n"
    "    style2 = campplus_model(feat2.unsqueeze(0))\n"
)
CP_B_NEW = (
    "    feat2 = feat2 - feat2.mean(dim=0, keepdim=True)\n"
    "    # kaldi.fbank returns fp32; match the encoder's dtype (see the CAMPPlus note\n"
    "    # in load_models -- with fp16 weights a fp32 input raises a dtype mismatch).\n"
    "    style2 = campplus_model(feat2.unsqueeze(0).half() if fp16 else feat2.unsqueeze(0))\n"
)
CP_C_OLD = "        f0_extractor = RMVPE(model_path, is_half=False, device=device)\n"
CP_C_NEW = (
    "        # RMVPE's fp32 BatchNorm triggers the same MIOpen HIPRTC JIT compile as\n"
    "        # CAMPPlus (see the note below). RMVPE supports half natively, so follow\n"
    "        # the caller's precision instead of pinning fp32.\n"
    "        f0_extractor = RMVPE(model_path, is_half=bool(fp16), device=device)\n"
)

# --------------------------------------------------------------------- 3. voice worker
VW_OLD = (
    "        import torch\n"
    '        if not torch.cuda.is_available():\n'
    '            raise RuntimeError("参考音色运行时未检测到 NVIDIA CUDA")\n'
)
VW_NEW = (
    "        import torch\n"
    "        # In ROCm PyTorch torch.backends.cudnn *is* the MIOpen backend, and MIOpen\n"
    "        # JIT-compiles several fp32 kernels (spatial batchnorm, the GRU inside\n"
    "        # RMVPE) through HIPRTC on first use. Wheels built without libc++ headers for\n"
    "        # comgr fail that compile with\n"
    "        #   fatal error: 'type_traits' file not found -> miopenStatusUnknownError\n"
    "        # Disabling the backend makes conv/BN/RNN use PyTorch's native kernels,\n"
    "        # which need no JIT. CUDA builds keep cuDNN enabled, and generation\n"
    "        # (core_worker) is untouched -- it keeps using MIOpen for its GEMMs.\n"
    '        if getattr(torch.version, "hip", None) is not None:\n'
    "            torch.backends.cudnn.enabled = False\n"
    '        if not torch.cuda.is_available():\n'
    '            raise RuntimeError("参考音色运行时未检测到 NVIDIA CUDA")\n'
)

# --------------------------------------------------------------------- 4. VAE tiles
CW_OLD = (
    "def create_pipe(root: Path, request: dict):\n"
    "    from yue2 import YuE2Pipeline\n"
    "\n"
    "    paths = model_paths(root)\n"
    '    backend = request.get("backend", "torch-eager")\n'
    '    budget = float(request.get("memory_budget_gib", 23.5))\n'
    "    return YuE2Pipeline.from_pretrained(\n"
    '        str(paths["model"]), vae=str(paths["vae"]), device="cuda",\n'
    "        memory_budget_gib=budget, backend=backend, quantization=\"none\",\n"
    '        offload_ar=bool(request.get("offload_ar", True)), local_files_only=True,\n'
    '        nar_attention=request.get("nar_attention", "sdpa"),\n'
    '        nar_query_chunk_size=int(request.get("nar_query_chunk_size", 256)),\n'
    '        verify_hashes=bool(request.get("verify_hashes", False)), progress=False,\n'
    "    )\n"
)
CW_NEW = (
    "def vae_core_frames_for(request: dict) -> int:\n"
    '    """Pick a VAE tile size from the actual device, not from the memory budget.\n'
    "\n"
    "    The pipeline derives vae_core_frames from memory_budget_gib alone (512 frames\n"
    "    at or below 12 GiB, otherwise 1024), which bakes in the 24 GiB card this\n"
    "    project was validated on. A 16 GiB card gets 1024-frame tiles whose shape\n"
    "    selects a much slower convolution solver: for the same 175 s song on an\n"
    "    RX 9070 XT (gfx1201) VAE decode took 313 s with 1024 frames versus 107 s with\n"
    "    512, and the whole request dropped from 613.7 s to 393.4 s. Note this is not\n"
    "    a pure VRAM/bandwidth effect -- decode time is not monotonic in tile size\n"
    "    (256 frames measures slower than both 128 and 512), because the solver is\n"
    "    chosen per tensor shape; 512 is simply the good bucket on this part.\n"
    "\n"
    "    Default to the smaller tile below 20 GiB and still honour an explicit\n"
    '    "vae_core_frames" request field. A 24 GiB card keeps the previous 1024.\n'
    '    """\n'
    '    explicit = request.get("vae_core_frames")\n'
    "    if explicit:\n"
    "        return int(explicit)\n"
    "    try:\n"
    "        import torch\n"
    "        total_gib = torch.cuda.get_device_properties(0).total_memory / 2 ** 30\n"
    "    except Exception:\n"
    "        return 512\n"
    "    return 1024 if total_gib >= 20 else 512\n"
    "\n"
    "\n"
    "def create_pipe(root: Path, request: dict):\n"
    "    from yue2 import YuE2Pipeline\n"
    "\n"
    "    paths = model_paths(root)\n"
    '    backend = request.get("backend", "torch-eager")\n'
    '    budget = float(request.get("memory_budget_gib", 23.5))\n'
    "    return YuE2Pipeline.from_pretrained(\n"
    '        str(paths["model"]), vae=str(paths["vae"]), device="cuda",\n'
    "        memory_budget_gib=budget, backend=backend, quantization=\"none\",\n"
    '        offload_ar=bool(request.get("offload_ar", True)), local_files_only=True,\n'
    '        nar_attention=request.get("nar_attention", "sdpa"),\n'
    '        nar_query_chunk_size=int(request.get("nar_query_chunk_size", 256)),\n'
    '        verify_hashes=bool(request.get("verify_hashes", False)), progress=False,\n'
    "        vae_core_frames=vae_core_frames_for(request),\n"
    "    )\n"
)

# ------------------------------------------------------------------ 5. audiotools
AT_REL = "Lib/site-packages/audiotools/ml/decorators.py"
AT_OLD = "import torch.distributed as dist\n"
AT_NEW = (
    "import torch.distributed as dist\n"
    "\n"
    "# ROCm-port patch: torch >= 2.9 makes torch.distributed lazy and hides\n"
    "# ReduceOp until a process group is initialised, but descript-audiotools\n"
    "# evaluates `dist.ReduceOp` in the Tracker class body at import time, which\n"
    "# crashes every Seed-VC launch with:\n"
    "#   AttributeError: module 'torch.distributed' has no attribute 'ReduceOp'\n"
    "# Seed-VC never initialises distributed training, so a minimal sentinel is\n"
    "# enough to keep the annotation importable.\n"
    "if not hasattr(dist, 'ReduceOp'):\n"
    "    class _ReduceOpStub:\n"
    "        AVG = 'avg'\n"
    "        SUM = 'sum'\n"
    "    dist.ReduceOp = _ReduceOpStub\n"
)

# -------------------------------------------------------------- 6/7. kit hygiene
UP_REL = "app/yue2_app/updater.py"
UP_OLD = (
    'def public_update(manifest: dict, current_version: str) -> dict:\n'
    '    available = version_tuple(manifest["version"]) > version_tuple(current_version)\n'
)
UP_NEW = (
    'def public_update(manifest: dict, current_version: str) -> dict:\n'
    "    # ROCm-port patch: in-app self-update re-extracts a release archive over the\n"
    "    # kit, replacing the ROCm PyTorch runtime and wiping every port patch. Report\n"
    "    # the kit as up to date; to upgrade, install the new release and re-run\n"
    "    # scripts/rocm/apply_rocm_port.py.\n"
    "    available = False\n"
)
IDX_REL = "app/web/index.html"
IDX_OLD = "所有推理都在你的 NVIDIA GPU 上完成。"
IDX_NEW = "所有推理都在你的本地 GPU（CUDA/HIP）上完成。"
VID_OLD = "未检测到 NVIDIA CUDA"
VID_NEW = "未检测到可用的 GPU（CUDA/HIP）"
VIDTR_OLD = "转谱运行时未检测到 NVIDIA CUDA"
VIDTR_NEW = "转谱运行时未检测到可用的 GPU（CUDA/HIP）"

# (relative path, idempotence marker, [(old, new), ...], opt-in flag)
PATCHES = [
    ("vendor/yue2/cuda_graph.py", 'torch.version, "hip"', [(CG_OLD, CG_NEW)], None),
    ("vendor/seed-vc/inference.py", "is_half=bool(fp16)",
     [(CP_A_OLD, CP_A_NEW), (CP_B_OLD, CP_B_NEW), (CP_C_OLD, CP_C_NEW)], None),
    ("app/yue2_app/voice_worker.py", 'torch.version, "hip"', [(VW_OLD, VW_NEW)], None),
    ("app/yue2_app/core_worker.py", "vae_core_frames_for", [(CW_OLD, CW_NEW)], None),
    (AT_REL, "ROCm-port patch", [(AT_OLD, AT_NEW)], None),
    (UP_REL, "ROCm-port patch: in-app self-update", [(UP_OLD, UP_NEW)], "freeze_updater"),
    (IDX_REL, "本地 GPU（CUDA/HIP）上完成", [(IDX_OLD, IDX_NEW)], "device_wording"),
    ("app/yue2_app/core_worker.py", VID_NEW,
     [("自检失败：" + VID_OLD, "自检失败：" + VID_NEW)], "device_wording"),
    # The voice worker's own message is rewritten by the entry above, so it needs a
    # separate anchor and marker here (the wording pass runs after the source pass).
    ("app/yue2_app/voice_worker.py", "参考音色运行时未检测到可用的 GPU",
     [('raise RuntimeError("参考音色运行时未检测到 NVIDIA CUDA")',
       'raise RuntimeError("参考音色运行时未检测到可用的 GPU（CUDA/HIP）")')], "device_wording"),
    ("app/yue2_app/transcribe_worker.py", VIDTR_NEW, [(VIDTR_OLD, VIDTR_NEW)], "device_wording"),
]


def patch_file(root, rel, marker, pairs):
    """Return 'PATCH'/'SKIP'/'GONE'/'FAIL'/'MISS' for one entry."""
    path = root / rel
    if not path.is_file():
        # The audiotools entry lives in the installed runtime, which is created by
        # setup_rocm_runtime.ps1; applying the port before installing is a normal
        # order of events, so this is a hint rather than a failure.
        if rel.startswith("Lib/") or rel.startswith("runtime/"):
            return "SKIP", "runtime not installed yet"
        return "MISS", "not found in this checkout"
    # Read and write with newline="" so the file's existing line endings survive
    # untouched. Text-mode round-tripping rewrites every LF as CRLF on Windows and
    # turns a two-line fix into a whole-file diff.
    with open(path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    if marker in text:
        return "SKIP", "already patched"
    hits = [(old, new, text.count(old)) for old, new in pairs]
    if all(count == 0 for _, _, count in hits):
        return "GONE", "upstream no longer matches these anchors"
    if any(count > 1 for _, _, count in hits):
        bad = [count for _, _, count in hits if count > 1]
        return "FAIL", "anchor matched %s times, expected 1" % bad
    # Some anchors apply and others are already gone: apply what still matches.
    # Without this, a patch that upstream fixed halfway would abort the whole run.
    for old, new, count in hits:
        if count == 1:
            text = text.replace(old, new)
    applied = sum(1 for _, _, count in hits if count == 1)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return "PATCH", "applied %d of %d hunk%s" % (applied, len(pairs), "s" if len(pairs) != 1 else "")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kits", nargs="*", help="kit checkout(s) to patch")
    parser.add_argument("--freeze-updater", action="store_true",
                        help="make the in-app updater report 'already up to date'")
    parser.add_argument("--device-wording", action="store_true",
                        help="replace the hardcoded 'NVIDIA' UI/self-check text")
    args = parser.parse_args(argv)
    if not args.kits:
        parser.print_help()
        return 2
    enabled = {"freeze_updater": args.freeze_updater, "device_wording": args.device_wording}
    failures = 0
    for kit in args.kits:
        root = Path(kit).resolve()
        print("=== %s ===" % root)
        for rel, marker, pairs, flag in PATCHES:
            if flag and not enabled[flag]:
                print("  --    %-40s (opt-in: --%s)" % (rel, flag.replace("_", "-")))
                continue
            status, detail = patch_file(root, rel, marker, pairs)
            print("  %-5s %-40s (%s)" % (status, rel, detail))
            if status in {"FAIL", "MISS"}:
                failures += 1
        print()
    if failures:
        print("%d entr%s need attention" % (failures, "y" if failures == 1 else "ies"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
