# Adapted from T8mars at b09412575ef726b4b7637f7279f1848108435607; metadata-only reader.
from __future__ import annotations
import functools
import os
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO


_MAX_METADATA_STRING_BYTES = 16 * 1024 * 1024

_MAX_ARRAY_ITEMS = 4_000_000

class GGUFMetadataError(RuntimeError):
    pass

@dataclass(frozen=True)
class GGUFModelInfo:
    identifier: str
    path: str
    filename: str
    size: int
    architecture: str = ""
    model_type: str = ""
    name: str = ""
    context_length: int = 0
    projector_type: str = ""
    has_vision_encoder: bool = False
    has_chat_template: bool = False
    metadata_readable: bool = False
    metadata_error: str = ""

    @property
    def is_projector(self) -> bool:
        filename = self.filename.casefold()
        return (
            self.model_type.casefold() == "mmproj"
            or self.architecture.casefold() == "clip"
            or filename.startswith("mmproj")
            or "mmproj" in filename
        )

    @property
    def is_model(self) -> bool:
        return not self.is_projector

    def as_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("path", None)
        payload.update(
            is_projector=self.is_projector,
            text_capable=self.is_model,
        )
        return payload

def _read_exact(handle: BinaryIO, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise GGUFMetadataError("Unexpected end of GGUF metadata.")
    return data

def _unpack(handle: BinaryIO, layout: str) -> Any:
    size = struct.calcsize(layout)
    return struct.unpack(layout, _read_exact(handle, size))[0]

def _read_string(handle: BinaryIO, *, keep: bool) -> str:
    length = int(_unpack(handle, "<Q"))
    if length < 0 or length > _MAX_METADATA_STRING_BYTES:
        raise GGUFMetadataError("GGUF metadata string is unreasonably large.")
    data = _read_exact(handle, length)
    return data.decode("utf-8", errors="replace") if keep else ""

_SCALAR_LAYOUTS = {
    0: "<B",   # UINT8
    1: "<b",   # INT8
    2: "<H",   # UINT16
    3: "<h",   # INT16
    4: "<I",   # UINT32
    5: "<i",   # INT32
    6: "<f",   # FLOAT32
    7: "<?",   # BOOL
    10: "<Q",  # UINT64
    11: "<q",  # INT64
    12: "<d",  # FLOAT64
}

def _read_value(handle: BinaryIO, value_type: int, *, keep: bool) -> Any:
    if value_type in _SCALAR_LAYOUTS:
        value = _unpack(handle, _SCALAR_LAYOUTS[value_type])
        return value if keep else None
    if value_type == 8:  # STRING
        return _read_string(handle, keep=keep)
    if value_type == 9:  # ARRAY
        element_type = int(_unpack(handle, "<I"))
        if element_type == 9:
            raise GGUFMetadataError("Nested GGUF arrays are unsupported.")
        length = int(_unpack(handle, "<Q"))
        if length < 0 or length > _MAX_ARRAY_ITEMS:
            raise GGUFMetadataError("GGUF metadata array is unreasonably large.")
        if element_type in _SCALAR_LAYOUTS and not keep:
            handle.seek(struct.calcsize(_SCALAR_LAYOUTS[element_type]) * length, os.SEEK_CUR)
            return None
        if not keep:
            for _ in range(length):
                _read_value(handle, element_type, keep=False)
            return None
        values = [_read_value(handle, element_type, keep=True) for _ in range(length)]
        return values if keep else None
    raise GGUFMetadataError(f"Unsupported GGUF metadata value type: {value_type}.")

def _metadata_values(path: Path) -> dict[str, Any]:
    wanted = {
        "general.architecture",
        "general.type",
        "general.name",
        "tokenizer.chat_template",
        "clip.projector_type",
        "clip.has_vision_encoder",
    }
    values: dict[str, Any] = {}
    with path.open("rb") as handle:
        if _read_exact(handle, 4) != b"GGUF":
            raise GGUFMetadataError("File does not start with the GGUF magic header.")
        version = int(_unpack(handle, "<I"))
        if version not in (2, 3):
            raise GGUFMetadataError(f"Unsupported GGUF version: {version}.")
        _tensor_count = int(_unpack(handle, "<Q"))
        kv_count = int(_unpack(handle, "<Q"))
        if kv_count < 0 or kv_count > 1_000_000:
            raise GGUFMetadataError("GGUF metadata entry count is invalid.")
        for _index in range(kv_count):
            key = _read_string(handle, keep=True)
            value_type = int(_unpack(handle, "<I"))
            keep = key in wanted or key.endswith(".context_length")
            value = _read_value(handle, value_type, keep=keep)
            if keep:
                values[key] = value
    return values

@functools.lru_cache(maxsize=512)
def _cached_model_info(path_value: str, identifier: str, size: int, mtime_ns: int) -> GGUFModelInfo:
    del mtime_ns
    path = Path(path_value)
    try:
        values = _metadata_values(path)
        architecture = str(values.get("general.architecture") or "")
        context_keys = [key for key in values if key.endswith(".context_length")]
        context_length = int(values.get(f"{architecture}.context_length") or 0)
        if not context_length and context_keys:
            context_length = int(values.get(context_keys[0]) or 0)
        return GGUFModelInfo(
            identifier=identifier,
            path=str(path),
            filename=path.name,
            size=size,
            architecture=architecture,
            model_type=str(values.get("general.type") or ""),
            name=str(values.get("general.name") or ""),
            context_length=context_length,
            projector_type=str(values.get("clip.projector_type") or ""),
            has_vision_encoder=bool(values.get("clip.has_vision_encoder", False)),
            has_chat_template=bool(str(values.get("tokenizer.chat_template") or "").strip()),
            metadata_readable=True,
        )
    except (OSError, ValueError, TypeError, GGUFMetadataError) as error:
        return GGUFModelInfo(
            identifier=identifier,
            path=str(path),
            filename=path.name,
            size=size,
            metadata_error=str(error),
        )
