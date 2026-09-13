"""Make descript-audiotools importable under torch >= 2.9.

torch 2.9 turned `torch.distributed` into a lazy module: it no longer exposes
`ReduceOp` until a process group has been initialised. descript-audiotools (which
reaches the voice path through demucs -> dac) evaluates `dist.ReduceOp` in the
*class body* of `Tracker`, so the import itself raises:

    AttributeError: module 'torch.distributed' has no attribute 'ReduceOp'
      audiotools/ml/decorators.py:288  op: dist.ReduceOp = dist.ReduceOp.AVG

and Seed-VC never starts. This is a descript-audiotools / modern-torch
incompatibility rather than an AMD problem -- it reproduces on NVIDIA with the
same torch version. Seed-VC never initialises distributed training, so a sentinel
is enough to keep the annotation importable.

This edits an installed site-packages file, so unlike the source patches it
cannot travel in the repository. Run it after the runtime exists:

    python scripts/rocm/patch_audiotools.py runtime/Lib/site-packages
    python scripts/rocm/patch_audiotools.py --check runtime/Lib/site-packages

The fix upstream may prefer instead is pinning a compatible
descript-audiotools, or running this as a post-install step from the installer.
"""
import argparse
import sys
from pathlib import Path

ANCHOR = "import torch.distributed as dist\n"
MARKER = "_ReduceOpStub"
PATCH = (
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


def target(site_packages: Path) -> Path:
    return site_packages / "audiotools" / "ml" / "decorators.py"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("site_packages", type=Path,
                        help="e.g. runtime/Lib/site-packages")
    parser.add_argument("--check", action="store_true",
                        help="report whether the patch is needed, change nothing")
    args = parser.parse_args(argv)

    path = target(args.site_packages.resolve())
    if not path.is_file():
        print("no audiotools at %s -- is the runtime installed?" % path)
        return 0
    # newline="" keeps the file's existing line endings intact; text-mode
    # round-tripping would rewrite every LF as CRLF on Windows.
    with open(path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    if MARKER in text:
        print("already patched: %s" % path)
        return 0
    if text.count(ANCHOR) != 1:
        print("anchor matched %d times in %s, expected 1 -- not touching it"
              % (text.count(ANCHOR), path))
        return 1
    if args.check:
        print("needs patching: %s" % path)
        return 1
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace(ANCHOR, PATCH))
    print("patched: %s" % path)
    print("Verify with:  python -c \"import audiotools\"   (using that runtime)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
