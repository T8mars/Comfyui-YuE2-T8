from __future__ import annotations

import argparse
import json
from pathlib import Path

from .mulacover_core import run
from .worker_common import JobContext, configure_environment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args()
    root, job_dir = args.root.resolve(), args.job_dir.resolve()
    configure_environment(root)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8-sig"))
    ctx = JobContext(job_dir)
    try:
        result = run(root, job_dir, job["request"], ctx)
        ctx.finish(result=result)
        return 0
    except BaseException as exc:
        ctx.fail(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

