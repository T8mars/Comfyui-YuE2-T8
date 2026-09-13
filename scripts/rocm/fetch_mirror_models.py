"""Download the t8 model bundle over plain resumable HTTP, optionally via a mirror.

Upstream's installer uses `huggingface_hub`:

    python -m huggingface_hub.cli.hf download t8star/YuE2-Comfy \
        --revision <rev> --local-dir <models>

That is the right default, and this script does the same job for networks where
it does not work. Measured on the box this port was developed on:

    huggingface.co, 6.76 GB weight        RemoteDisconnected (small API calls
                                          succeed, bulk transfers are reset)
    hf-mirror.com, same weight            HTTP 206, ~5 MiB/s single stream,
                                          ~60 MiB/s with 6 parallel ranges
    huggingface_hub client                incompatible with the mirror
                                          (LocalEntryNotFoundError)
    huggingface_hub cache on Windows      PermissionError creating symlinks
                                          without Developer Mode

So this downloads ranged chunks in parallel with resume, verifies every file
against the repository's declared size, and then tells you to run the kit's own
`scripts/verify_models.py` / `verify_voice_models.py`, which check the sha256s
from the manifests. Nothing here weakens the kit's integrity gate -- it only
replaces the transport.

Usage:
    python scripts/rocm/fetch_mirror_models.py
    python scripts/rocm/fetch_mirror_models.py --models-dir D:\\YuE2\\models
    python scripts/rocm/fetch_mirror_models.py --only Seed-VC/ --only Demucs/
    python scripts/rocm/fetch_mirror_models.py --check      # report, download nothing
"""
import argparse
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.request
from pathlib import Path

DEFAULT_REPO = "t8star/YuE2-Comfy"
DEFAULT_REVISION = "a083f106499daead99259dd0c443a5494254cfc5"
HOSTS = ["https://hf-mirror.com", "https://huggingface.co"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) t8-rocm-mirror-fetch"
LOCK = threading.Lock()

socket.setdefaulttimeout(90)
# The mirror's certificate chain is not always complete on older Windows trust
# stores; this is a bulk read from a public model repository, not a trust boundary.
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(message):
    with LOCK:
        print(message, flush=True)


def file_table(host, repo):
    request = urllib.request.Request("%s/api/models/%s?blobs=true" % (host, repo),
                                     headers={"User-Agent": UA})
    payload = json.loads(urllib.request.urlopen(request, timeout=60, context=CTX).read())
    return {item["rfilename"]: (item.get("size") or 0) for item in payload.get("siblings", [])}


def open_range(url, start):
    headers = {"User-Agent": UA}
    if start:
        headers["Range"] = "bytes=%d-" % start
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90, context=CTX)


def fetch_range(url, destination, low, high, stalls_limit=8):
    """Append to `destination` until it covers [low, high]; resume after a drop."""
    want = high - low + 1
    stalls = 0
    while stalls < stalls_limit:
        have = os.path.getsize(destination) if os.path.exists(destination) else 0
        if have >= want:
            return True
        try:
            response = open_range(url, low + have)
            if have and response.status != 206:
                # The server ignored our Range header; restart this chunk cleanly
                # rather than splicing a whole-file response into the middle.
                response.close()
                os.remove(destination)
                continue
            with open(destination, "ab") as handle:
                got = have
                while got < want:
                    block = response.read(min(1 << 21, want - got))
                    if not block:
                        break
                    handle.write(block)
                    got += len(block)
            response.close()
            if os.path.getsize(destination) >= want:
                return True
            stalls += 1
        except Exception:
            stalls += 1
            time.sleep(2)
    return os.path.exists(destination) and os.path.getsize(destination) >= want


def grab_large(url, destination, size, label, parts):
    chunk = size // parts
    bounds = [(i * chunk, size - 1 if i == parts - 1 else (i + 1) * chunk - 1)
              for i in range(parts)]
    done = [False] * parts
    started = time.time()

    def worker(index, low, high):
        done[index] = fetch_range(url, "%s.p%02d" % (destination, index), low, high)

    threads = [threading.Thread(target=worker, args=(index, low, high), daemon=True)
               for index, (low, high) in enumerate(bounds)]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        time.sleep(12)
        got = sum(os.path.getsize("%s.p%02d" % (destination, i)) for i in range(parts)
                  if os.path.exists("%s.p%02d" % (destination, i)))
        elapsed = time.time() - started
        log("   %-46s %7.1f/%7.1f MiB  %5.2f MiB/s"
            % (label, got / 2 ** 20, size / 2 ** 20, got / 2 ** 20 / elapsed if elapsed else 0))
    for thread in threads:
        thread.join()
    if not all(done):
        log("   %s MISSING PARTS %s" % (label, [i for i, ok in enumerate(done) if not ok]))
        return False
    with open(destination, "wb") as out:
        for index in range(parts):
            piece = "%s.p%02d" % (destination, index)
            with open(piece, "rb") as source:
                while True:
                    block = source.read(1 << 22)
                    if not block:
                        break
                    out.write(block)
            os.remove(piece)
    return os.path.getsize(destination) == size


def grab_small(url, destination, size, attempts=6):
    for _ in range(attempts):
        try:
            data = open_range(url, 0).read()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            if size and len(data) != size:
                time.sleep(2)
                continue
            return True
        except Exception:
            time.sleep(3)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--host", action="append", default=[],
                        help="mirror host, repeatable; default tries hf-mirror then huggingface.co")
    parser.add_argument("--models-dir", type=Path, default=None,
                        help="destination; defaults to <kit>/models")
    parser.add_argument("--only", action="append", default=[],
                        help="download only paths with this prefix, repeatable")
    parser.add_argument("--parts", type=int, default=6, help="parallel ranges per large file")
    parser.add_argument("--large", type=int, default=40 * 1024 * 1024,
                        help="size at or above which a file is fetched in parallel ranges")
    parser.add_argument("--skip-manifests", action="store_true",
                        help="do not fetch MODEL_MANIFEST.json / VOICE_MODEL_MANIFEST.json")
    parser.add_argument("--check", action="store_true", help="report only, download nothing")
    args = parser.parse_args(argv)

    base = Path(__file__).resolve().parents[2]
    models = args.models_dir or (base / "models")
    models.mkdir(parents=True, exist_ok=True)
    hosts = args.host or HOSTS

    table = None
    for host in hosts:
        try:
            table = file_table(host, args.repo)
            log("file table from %s: %d files" % (host.split("//")[1], len(table)))
            break
        except Exception as exc:
            log("  %s failed: %s" % (host, type(exc).__name__))
    if table is None:
        log("cannot list %s on any host; pass --host or use huggingface_hub instead" % args.repo)
        return 1

    wanted = sorted(table)
    if args.only:
        wanted = [name for name in wanted if name.startswith(tuple(args.only))]
    if args.skip_manifests:
        wanted = [name for name in wanted if not name.endswith("_MANIFEST.json")]
    total = sum(table[name] for name in wanted)
    log("payload: %d files, %.2f GB -> %s" % (len(wanted), total / 1024 ** 3, models))

    missing, failed = [], []
    for name in wanted:
        size = table[name]
        destination = models / name.replace("/", os.sep)
        if destination.is_file() and size and destination.stat().st_size == size:
            continue
        if destination.is_file() and not size:
            continue
        if args.check:
            missing.append((name, size))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = "%s/%s/resolve/%s/%s" % (hosts[0], args.repo, args.revision, name)
        log("  fetching %-50s %8.1f MiB" % (name, size / 2 ** 20))
        ok = (grab_large(url, str(destination), size, name, args.parts)
              if size >= args.large else grab_small(url, destination, size))
        if not ok:
            log("  FAILED %s" % name)
            failed.append(name)

    if args.check:
        if not missing:
            log("all %d files present" % len(wanted))
            return 0
        log("missing %d of %d files (%.2f GB):" % (len(missing), len(wanted),
                                                   sum(size for _, size in missing) / 1024 ** 3))
        for name, size in missing[:40]:
            log("  %-50s %8.1f MiB" % (name, size / 2 ** 20))
        if len(missing) > 40:
            log("  ... and %d more" % (len(missing) - 40))
        return 1

    if failed:
        log("FAILED FILES: %s" % failed)
        return 1

    log("")
    log("Now verify against the kit's own pinned sha256 manifests:")
    log("  runtime\\python.exe scripts\\verify_models.py --root .")
    log("  runtime\\python.exe scripts\\verify_voice_models.py --root .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
