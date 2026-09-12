import argparse
import os
import sys
import warnings
from io import BytesIO
from pathlib import Path


warnings.filterwarnings(
    "ignore",
    message=r"`torch\.nn\.utils\.weight_norm` is deprecated.*",
    category=FutureWarning,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("weight_root", str(PROJECT_ROOT / "assets" / "weights"))
os.environ.setdefault("index_root", str(PROJECT_ROOT / "logs"))
os.environ.setdefault("outside_index_root", str(PROJECT_ROOT / "assets" / "indices"))
os.environ.setdefault("rmvpe_root", str(PROJECT_ROOT / "assets" / "rmvpe"))

AUDIO_EXTENSIONS = {
    ".wav",
    ".flac",
    ".mp3",
    ".m4a",
    ".ogg",
    ".opus",
    ".aac",
    ".wma",
    ".mp4",
    ".mkv",
    ".webm",
}
OUTPUT_FORMATS = {"wav", "flac", "mp3", "m4a"}


def build_parser():
    parser = argparse.ArgumentParser(
        description="Offline RVC inference for single-speaker and multi-speaker models."
    )
    parser.add_argument("--model", required=True, help="Model filename or .pth path.")
    parser.add_argument("--input", help="Input audio file or directory.")
    parser.add_argument("--output", help="Output audio file or directory.")
    parser.add_argument(
        "--speaker-id",
        type=int,
        help="Speaker ID. Multi-speaker models default to their smallest declared ID.",
    )
    parser.add_argument(
        "--list-speakers",
        action="store_true",
        help="List the model's speaker metadata and exit.",
    )
    parser.add_argument("--pitch", type=int, default=0, help="Pitch shift in semitones.")
    parser.add_argument(
        "--f0-method", choices=["pm", "rmvpe"], default="rmvpe"
    )
    parser.add_argument(
        "--index",
        help="Explicit added index path. The matching speaker index is selected automatically when omitted.",
    )
    parser.add_argument("--index-rate", type=float, default=0.75)
    parser.add_argument("--resample-sr", type=int, default=0)
    parser.add_argument("--rms-mix-rate", type=float, default=1.0)
    parser.add_argument("--protect", type=float, default=0.33)
    parser.add_argument(
        "--format", dest="output_format", choices=sorted(OUTPUT_FORMATS)
    )
    parser.add_argument(
        "--recursive", action="store_true", help="Scan input subdirectories."
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def resolve_model(value):
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    if not candidate.is_file():
        candidate = (Path(os.environ["weight_root"]) / value).resolve()
    if not candidate.is_file():
        raise FileNotFoundError("Model not found: %s" % value)
    if candidate.suffix.lower() != ".pth":
        raise ValueError("Model must be a .pth file: %s" % candidate)
    return candidate


def load_model_metadata(model_path):
    import torch

    checkpoint = torch.load(str(model_path), map_location="cpu")
    weight = checkpoint.get("weight", {}) if isinstance(checkpoint, dict) else {}
    embedding = weight.get("emb_g.weight")
    if embedding is None:
        raise ValueError("Model does not contain weight/emb_g.weight")
    speaker_count = int(embedding.shape[0])
    speakers = []
    seen = set()
    for item in checkpoint.get("speaker_info", []):
        try:
            speaker_id = int(item["id"])
            speaker_name = str(item["name"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            0 <= speaker_id < speaker_count
            and speaker_name
            and speaker_id not in seen
        ):
            speakers.append({"id": speaker_id, "name": speaker_name})
            seen.add(speaker_id)
    speakers.sort(key=lambda item: item["id"])
    return speaker_count, speakers


def select_speaker(speaker_count, speakers, requested_id):
    if speakers:
        valid_ids = {item["id"] for item in speakers}
        speaker_id = speakers[0]["id"] if requested_id is None else requested_id
        if speaker_id not in valid_ids:
            raise ValueError(
                "Speaker ID %s is not declared by this multi-speaker model; available IDs: %s"
                % (speaker_id, ", ".join(str(value) for value in sorted(valid_ids)))
            )
        return speaker_id
    speaker_id = 0 if requested_id is None else requested_id
    if speaker_id < 0 or speaker_id >= speaker_count:
        raise ValueError(
            "Speaker ID must be between 0 and %s for this model" % (speaker_count - 1)
        )
    return speaker_id


def resolve_index(value, model_name, speaker_id, index_rate):
    from infer.vc.utils import get_index_path_from_model

    if index_rate == 0:
        return ""
    if value:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        if "trained" in candidate.name:
            candidate = candidate.with_name(candidate.name.replace("trained", "added"))
        index_path = str(candidate)
    else:
        index_path = get_index_path_from_model(model_name, speaker_id)
    if not index_path or not Path(index_path).is_file():
        raise FileNotFoundError(
            "No usable added index for speaker ID %s. Supply --index or use --index-rate 0."
            % speaker_id
        )
    return str(Path(index_path).resolve())


def collect_jobs(input_value, output_value, output_format, recursive):
    input_path = Path(input_value).expanduser().resolve()
    output_path = Path(output_value).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError("Input not found: %s" % input_path)

    if input_path.is_file():
        suffix_format = output_path.suffix.lower().lstrip(".")
        if output_path.suffix and suffix_format not in OUTPUT_FORMATS:
            raise ValueError("Unsupported output suffix: %s" % output_path.suffix)
        selected_format = output_format or suffix_format or "wav"
        if output_path.suffix:
            output_file = output_path.with_suffix(".%s" % selected_format)
        else:
            output_path.mkdir(parents=True, exist_ok=True)
            output_file = output_path / (input_path.stem + "." + selected_format)
        return [(input_path, output_file, selected_format)]

    selected_format = output_format or "wav"
    output_path.mkdir(parents=True, exist_ok=True)
    iterator = input_path.rglob("*") if recursive else input_path.iterdir()
    jobs = []
    for path in sorted(iterator):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        relative_parent = path.relative_to(input_path).parent if recursive else Path()
        output_file = output_path / relative_parent / (path.stem + "." + selected_format)
        jobs.append((path, output_file, selected_format))
    if not jobs:
        raise ValueError("No supported audio files found in: %s" % input_path)
    return jobs


def write_audio(path, audio, sample_rate, output_format):
    import soundfile as sf
    from infer.audio import wav2

    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format in ("wav", "flac"):
        sf.write(str(path), audio, sample_rate)
        return
    with BytesIO() as wav_file:
        sf.write(wav_file, audio, sample_rate, format="wav")
        wav_file.seek(0)
        with open(path, "wb") as output_file:
            wav2(wav_file, output_file, output_format)


def create_config():
    from configs.config import Config

    original_argv = sys.argv[:]
    sys.argv = [sys.argv[0]]
    try:
        return Config()
    finally:
        sys.argv = original_argv


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.list_speakers and (not args.input or not args.output):
        parser.error("--input and --output are required unless --list-speakers is used")
    if not 0 <= args.index_rate <= 1:
        parser.error("--index-rate must be between 0 and 1")
    if not 0 <= args.rms_mix_rate <= 1:
        parser.error("--rms-mix-rate must be between 0 and 1")
    if not 0 <= args.protect <= 0.5:
        parser.error("--protect must be between 0 and 0.5")
    if args.resample_sr and args.resample_sr < 16000:
        parser.error("--resample-sr must be 0 or at least 16000")

    model_path = resolve_model(args.model)
    os.environ["weight_root"] = str(model_path.parent)
    model_name = model_path.name
    speaker_count, speakers = load_model_metadata(model_path)
    if args.list_speakers:
        if speakers:
            for item in speakers:
                print("%s\t%s" % (item["id"], item["name"]))
        else:
            print("0-%s" % (speaker_count - 1))
        return 0

    speaker_id = select_speaker(speaker_count, speakers, args.speaker_id)
    index_path = resolve_index(
        args.index, model_name, speaker_id, args.index_rate
    )
    jobs = collect_jobs(
        args.input, args.output, args.output_format, args.recursive
    )
    existing = [str(output) for _, output, _ in jobs if output.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(
            "Output already exists; use --overwrite: %s" % existing[0]
        )

    from i18n.i18n import I18nAuto
    from infer.vc.modules import VC

    i18n = I18nAuto()
    config = create_config()
    print(i18n("当前设备：%s | 推理精度：%s") % (config.device, config.dtype))
    print("%s: %s" % (i18n("选择模型"), model_name))
    print("%s: %s" % (i18n("说话人ID（0~109）"), speaker_id))
    print("%s: %s" % (i18n("选择索引"), index_path or i18n("未使用")))

    vc = VC(config)
    vc.get_vc(model_name)
    failed = 0
    for input_path, output_path, output_format in jobs:
        status, result = vc.vc_single(
            speaker_id,
            str(input_path),
            args.pitch,
            args.f0_method,
            index_path,
            args.index_rate,
            args.resample_sr,
            args.rms_mix_rate,
            args.protect,
        )
        print(status)
        if not result or result[0] is None or result[1] is None:
            failed += 1
            continue
        write_audio(output_path, result[1], result[0], output_format)
        print(str(output_path))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print("rvc-cli: error: %s" % error, file=sys.stderr)
        raise SystemExit(1)
