"""HTTP routes for projects, durable assets and training dataset snapshots."""
from __future__ import annotations

import json
import mimetypes
import re
import urllib.parse
from pathlib import Path

from .asset_library import ASSET_KINDS, AssetLibrary
from .io import atomic_json, within


def parse_byte_range(value: str, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or "," in value:
        raise ValueError("不支持的音频读取范围")
    first, last = match.groups()
    if not first and not last:
        raise ValueError("音频读取范围为空")
    if first:
        start = int(first)
        end = min(size - 1, int(last)) if last else size - 1
        if start >= size or end < start:
            raise ValueError("音频读取范围超出文件")
    else:
        length = int(last)
        if length <= 0:
            raise ValueError("音频读取范围为空")
        start, end = max(0, size - length), size - 1
    return start, end


def stream_file(handler, path: Path, info: dict, *, head: bool = False) -> None:
    size = path.stat().st_size
    etag = f'"sha256-{info["blob_sha256"]}"'
    request_range = handler.headers.get("Range", "")
    if_range = handler.headers.get("If-Range", "")
    if if_range and if_range != etag:
        request_range = ""
    try:
        selected = parse_byte_range(request_range, size)
    except ValueError:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    start, end = selected if selected else (0, size - 1)
    handler.send_response(206 if selected else 200)
    handler.send_header("Content-Type", info.get("mime") or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    handler.send_header("Content-Length", str(end - start + 1))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("ETag", etag)
    handler.send_header("Cache-Control", "private, max-age=31536000, immutable")
    if selected:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    if head:
        return
    remaining = end - start + 1
    try:
        with path.open("rb") as stream:
            stream.seek(start)
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    break
                handler.wfile.write(block)
                remaining -= len(block)
    except (BrokenPipeError, ConnectionResetError):
        return


def waveform(library: AssetLibrary, asset_id: str, revision_id: str = "", bins: int = 720) -> dict:
    path, info = library.revision_file(asset_id, revision_id)
    bins = max(64, min(2048, int(bins)))
    cache = library.waveforms / f'{info["blob_sha256"]}-{bins}.json'
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("sha256") == info["blob_sha256"] and cached.get("bins") == bins:
            return cached
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    try:
        import numpy as np
        import soundfile as sf
        with sf.SoundFile(str(path)) as source:
            total, rate = len(source), int(source.samplerate)
            edges = np.linspace(0, total, bins + 1, dtype=np.int64)
            peaks = []
            for index in range(bins):
                length = int(edges[index + 1] - edges[index])
                source.seek(int(edges[index]))
                chunk = source.read(length, dtype="float32", always_2d=True)
                mono = chunk.mean(axis=1) if len(chunk) else np.zeros(0, dtype=np.float32)
                peaks.append([round(float(mono.min(initial=0)), 5),
                              round(float(mono.max(initial=0)), 5)])
    except Exception as first:
        try:
            import av
            import numpy as np
            frame_peaks, total, rate = [], 0, 0
            with av.open(str(path)) as container:
                for frame in container.decode(audio=0):
                    rate = frame.sample_rate
                    block = frame.to_ndarray().astype("float32")
                    mono = block.mean(axis=0) if block.ndim == 2 else block
                    total += int(mono.size)
                    frame_peaks.append((float(mono.min(initial=0)), float(mono.max(initial=0)), int(mono.size)))
            if not frame_peaks:
                raise ValueError("音频没有可读取的采样")
            ranges, cursor = [[0.0, 0.0] for _ in range(bins)], 0
            for minimum, maximum, length in frame_peaks:
                first = min(bins - 1, cursor * bins // total)
                last = min(bins - 1, max(cursor, cursor + length - 1) * bins // total)
                for index in range(first, last + 1):
                    ranges[index][0] = min(ranges[index][0], minimum)
                    ranges[index][1] = max(ranges[index][1], maximum)
                cursor += length
            peaks = [[round(low, 5), round(high, 5)] for low, high in ranges]
        except Exception as second:
            raise ValueError(f"暂时无法生成这个音频的波形：{second or first}") from second
    if not total or not rate:
        raise ValueError("音频没有可读取的采样")
    result = {"schema": 1, "sha256": info["blob_sha256"], "bins": bins,
              "duration": round(total / rate, 3), "peaks": peaks}
    atomic_json(cache, result)
    return result


def _query(parsed) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(parsed.query, keep_blank_values=True)


def get(handler, parsed, library: AssetLibrary, *, head: bool = False) -> bool:
    path, query = parsed.path, _query(parsed)
    if path == "/api/workbench/assets":
        handler._json(200, {"assets": library.list_assets(
            kind=query.get("kind", [""])[0], query=query.get("q", [""])[0],
            project_id=query.get("project_id", [""])[0], limit=int(query.get("limit", ["100"])[0]))})
        return True
    if path.startswith("/api/workbench/assets/"):
        pieces = path.split("/")
        if len(pieces) == 5 and pieces[4]:
            handler._json(200, library.get_asset(pieces[4]))
            return True
        if len(pieces) == 6 and pieces[5] in {"content", "waveform"}:
            revision_id = query.get("revision_id", [""])[0]
            if pieces[5] == "content":
                source, info = library.revision_file(pieces[4], revision_id)
                stream_file(handler, source, info, head=head)
            else:
                handler._json(200, waveform(library, pieces[4], revision_id,
                                            int(query.get("bins", ["720"])[0])))
            return True
    if path == "/api/workbench/projects":
        handler._json(200, {"projects": library.list_projects(int(query.get("limit", ["100"])[0]))})
        return True
    if path.startswith("/api/workbench/projects/"):
        pieces = path.split("/")
        if len(pieces) == 5 and pieces[4]:
            handler._json(200, library.get_project(pieces[4]))
            return True
    if path == "/api/workbench/snapshots":
        handler._json(200, {"snapshots": library.list_snapshots(query.get("training_kind", [""])[0])})
        return True
    if path == "/api/workbench/training-runs":
        handler._json(200, {"runs": library.list_training_runs(query.get("training_kind", [""])[0])})
        return True
    if path.startswith("/api/workbench/training-runs/"):
        pieces = path.split("/")
        if len(pieces) == 5 and pieces[4]:
            handler._json(200, library.get_training_run(pieces[4]))
            return True
    return False


def _allowed_source(root: Path, value: object) -> Path:
    source = Path(str(value or "")).expanduser().resolve()
    choices = (root / "uploads", root / "outputs" / "jobs", root / "exports", root / "userdata" / "rvc")
    for base in choices:
        try:
            return within(base, source)
        except ValueError:
            continue
    raise ValueError("只能将上传文件、任务结果、导出内容或已有 RVC 素材加入资产库")


def post(handler, parsed, library: AssetLibrary, root: Path) -> bool:
    path = parsed.path
    if path == "/api/workbench/assets/import":
        data = handler._body_json(128 * 1024)
        source = _allowed_source(root.resolve(), data.get("source_path"))
        handler._json(201, library.import_file(
            source, kind=str(data.get("kind", "other")), title=str(data.get("title", "")),
            tags=data.get("tags", []), rights=data.get("rights", {}),
            provenance={"source": str(source), **dict(data.get("provenance") or {})},
            metadata=data.get("metadata", {}),
        ))
        return True
    if path == "/api/workbench/assets/text":
        data = handler._body_json(4 * 1024 * 1024 + 128 * 1024)
        handler._json(201, library.create_text(
            kind=str(data.get("kind", "")), title=str(data.get("title", "")), text=str(data.get("text", "")),
            tags=data.get("tags", []), rights=data.get("rights", {}), provenance=data.get("provenance", {}),
            asset_id=data.get("asset_id") or None, parent_revision_id=data.get("parent_revision_id") or None,
        ))
        return True
    if path.startswith("/api/workbench/assets/") and path.endswith("/update"):
        asset_id = path.split("/")[4]
        data = handler._body_json(128 * 1024)
        handler._json(200, library.update_asset(asset_id, title=data.get("title"), tags=data.get("tags"),
                                                status=data.get("status")))
        return True
    if path == "/api/workbench/projects":
        data = handler._body_json(128 * 1024)
        handler._json(201, library.create_project(data.get("title", ""), data.get("metadata", {})))
        return True
    if path.startswith("/api/workbench/projects/") and path.endswith("/assets"):
        project_id = path.split("/")[4]
        data = handler._body_json(128 * 1024)
        handler._json(200, library.add_to_project(project_id, data.get("asset_id", ""),
                                                   revision_id=data.get("revision_id", ""),
                                                   role=data.get("role", "asset")))
        return True
    if path == "/api/workbench/snapshots":
        data = handler._body_json(2 * 1024 * 1024)
        handler._json(201, library.create_snapshot(title=data.get("title", ""),
                                                   training_kind=data.get("training_kind", ""),
                                                   items=data.get("items", []), options=data.get("options", {})))
        return True
    if path == "/api/workbench/training-runs":
        data = handler._body_json(256 * 1024)
        handler._json(201, library.create_training_run(
            title=data.get("title", ""), training_kind=data.get("training_kind", ""),
            snapshot_id=data.get("snapshot_id", ""), config=data.get("config", {})))
        return True
    if path.startswith("/api/workbench/training-runs/") and path.endswith("/update"):
        run_id = path.split("/")[4]
        data = handler._body_json(256 * 1024)
        handler._json(200, library.update_training_run(
            run_id, state=data.get("state"), config=data.get("config"),
            current_job_id=data.get("current_job_id"), model_asset_id=data.get("model_asset_id")))
        return True
    return False
