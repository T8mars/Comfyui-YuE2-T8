"""Functional end-to-end check of every capability this kit advertises.

Three jobs, all submitted through the service API so they run exactly as the WebUI
runs them:

  1. doctor          -> the kit's own GPU / runtime / model-hash self-check
  2. transcribe      -> SheetSage2 + MERT, with render_score=True so the
                        playwright/abcjs score renderer is exercised too
  3. voice_convert   -> Seed-VC (voice cloning) + Demucs (vocal separation)

The same short generated song is used as transcription source, cover source and
voice reference, which keeps each run fast while still exercising every stage.
That matters because a capability flag only proves an import succeeded -- these
three jobs prove real artifacts came out the other end.

Usage:
    python scripts/rocm/verify_capabilities.py
    python scripts/rocm/verify_capabilities.py --root . --source outputs/smoke01/audio.flac
    python scripts/rocm/verify_capabilities.py --service http://127.0.0.1:8189
"""
import argparse
import shutil
import sys
import time
from pathlib import Path


def newest_song(root: Path):
    """Find the most recent generated audio.flac under outputs/."""
    candidates = sorted(root.glob("outputs/**/audio.flac"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="kit checkout (default: two levels above this script)")
    parser.add_argument("--service", default="http://127.0.0.1:8189")
    parser.add_argument("--source", type=Path, default=None,
                        help="audio to feed the transcribe and voice jobs; defaults to the "
                             "newest outputs/**/audio.flac")
    parser.add_argument("--timeout-transcribe", type=int, default=1800)
    parser.add_argument("--timeout-voice", type=int, default=2400)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    import os
    os.environ["YUE2_HOME"] = str(root)
    os.environ["YUE2_KIT"] = str(root)
    os.environ["YUE2_SERVICE"] = args.service
    sys.path.insert(0, str(root))

    try:
        import client
    except ImportError:
        print("cannot import client.py from %s -- pass --root" % root)
        return 2

    source = args.source or newest_song(root)
    if source is None or not Path(source).is_file():
        print("no source audio; generate one first, or pass --source")
        return 2
    # The worker resolves source_path relative to the KIT ROOT, not uploads/, so
    # the upload has to sit inside the kit and be referenced with its prefix.
    name = "rocm_verify_source.flac"
    upload = root / "uploads" / name
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, upload)
    print("source       : %s (%d KB) -> uploads/%s" % (source, upload.stat().st_size // 1024, name))

    health = client.ensure_service()
    caps = health["ready"]["capabilities"]
    print("capabilities :", {key: caps[key] for key in
                             ("generation", "transcription", "score_renderer", "voice_conversion")})
    print(flush=True)

    def follow(job_id, timeout, label):
        started = time.time()
        last = None
        while time.time() - started < timeout:
            try:
                status = client.request("/api/jobs/%s" % job_id, timeout=30)
            except Exception as exc:
                print("    poll error: %s" % type(exc).__name__, flush=True)
                time.sleep(5)
                continue
            state = status.get("status")
            line = "status=%s stage=%s tokens=%s progress=%s" % (
                state, status.get("stage"), status.get("tokens"), status.get("progress"))
            if line != last:
                print("    %6.1fs  %s" % (time.time() - started, line), flush=True)
                last = line
            if state == "complete":
                return status
            if state in ("failed", "cancelled"):
                print("    %s: %s" % (state, status.get("error")), flush=True)
                log = status.get("log")
                if log and Path(log).is_file():
                    print("    --- log tail ---", flush=True)
                    for text in Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-25:]:
                        print("    " + text, flush=True)
                raise SystemExit("%s failed" % label)
            time.sleep(3)
        raise SystemExit("%s timed out after %ds" % (label, timeout))

    print("=== [1/3] doctor ===", flush=True)
    job = client.submit("doctor", {})
    print("  job", job["id"], flush=True)
    doctor = follow(job["id"], 600, "doctor")
    result = doctor.get("result", {})
    print("  torch/version:", result.get("versions", {}).get("torch"))
    print("  gpu          :", result.get("gpu"), "| cuda_available:", result.get("cuda_available"))
    print("  model sha    :", result.get("model", {}).get("sha256"))
    print(flush=True)

    print("=== [2/3] transcribe + score render ===", flush=True)
    job = client.submit("transcribe", {
        "source_path": "uploads/" + name,
        "melody_only": True,
        "dtype": "bf16",
        "preset": "default",
        "render_score": True,
        "render_audio": True,
    })
    print("  job", job["id"], flush=True)
    transcribe = follow(job["id"], args.timeout_transcribe, "transcribe").get("result", {})
    print("  keys:", sorted(transcribe), flush=True)
    for key in ("abc", "abc_error", "rendered", "render_error", "duration_seconds"):
        if key in transcribe:
            value = transcribe[key]
            shown = "(%d chars)" % len(str(value or "")) if key == "abc" else str(value)[:220]
            print("  %-16s %s" % (key, shown), flush=True)
    abc_text = transcribe.get("abc") or ""
    for text in abc_text.splitlines()[:10]:
        print("    " + text, flush=True)
    print(flush=True)

    print("=== [3/3] voice_convert (Seed-VC + Demucs) ===", flush=True)
    job = client.submit("voice_convert", {
        "source_path": "uploads/" + name,
        "reference_path": "uploads/" + name,
        "diffusion_steps": 8,
        "cfg_rate": 0.7,
        "semi_tone_shift": 0,
    })
    print("  job", job["id"], flush=True)
    voice = follow(job["id"], args.timeout_voice, "voice_convert").get("result", {})
    print("  keys:", sorted(voice), flush=True)
    for key in ("audio", "vocal", "accompaniment", "artifact_dir", "settings"):
        if key in voice:
            print("  %-16s %s" % (key, str(voice[key])[:200]), flush=True)
    print(flush=True)

    print("=== ALL CAPABILITIES EXERCISED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
