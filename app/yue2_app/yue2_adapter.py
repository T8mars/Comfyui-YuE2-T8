"""Named YuE2 AR adapters and the pinned v4 NAR companion contract."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

AR_PROJECTIONS = (
    ("self_attn", ("q_proj", "k_proj", "v_proj", "o_proj")),
    ("mlp", ("gate_proj", "up_proj", "down_proj")),
)
NAR_PROJECTIONS = (
    ("nar_self_attn", ("q_proj", "k_proj", "v_proj", "o_proj")),
    ("nar_mlp", ("gate_proj", "up_proj", "down_proj")),
)
SCALING_CONVENTION = "weight_plus_scale_times_B_matmul_A"


def file_sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def adapter_content_sha256(path: Path) -> str:
    """Hash canonical metadata and tensor bytes, independent of container map order."""
    import torch
    from safetensors import safe_open
    path = Path(path)
    digest = hashlib.sha256(b"yue2-ar-lora-content-v1\0")
    with safe_open(path, framework="pt", device="cpu") as archive:
        metadata = archive.metadata() or {}
        digest.update(json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8"))
        for name in sorted(archive.keys()):
            tensor = archive.get_tensor(name).detach().cpu().contiguous()
            descriptor = {"name": name, "dtype": str(tensor.dtype), "shape": list(tensor.shape)}
            digest.update(json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("ascii"))
            digest.update(tensor.view(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def target_modules(model, *, nar: bool = False):
    groups = NAR_PROJECTIONS if nar else AR_PROJECTIONS
    for layer_index, layer in enumerate(model.model.layers):
        for module_name, names in groups:
            module = getattr(layer, module_name)
            for name in names:
                yield f"layers.{layer_index}.{module_name}.{name}", module, name


def _validate_rank(rank: object) -> int:
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= 256:
        raise ValueError("LoRA rank 必须是 1–256 的整数")
    return rank


def attach_ar_lora(model, rank: int):
    import torch
    from torch import nn
    rank = _validate_rank(rank)

    class LoRALinear(nn.Module):
        def __init__(self, base, ident):
            super().__init__()
            self.base, self.ident = base, ident
            self.A = nn.Parameter(torch.randn(rank, base.in_features, device=base.weight.device,
                                              dtype=torch.float32) / math.sqrt(base.in_features))
            self.B = nn.Parameter(torch.zeros(base.out_features, rank, device=base.weight.device,
                                              dtype=torch.float32))

        def forward(self, value):
            delta = (value.float() @ self.A.T) @ self.B.T
            return self.base(value) + delta.to(value.dtype)

    attached = {}
    for ident, module, name in target_modules(model):
        base = getattr(module, name)
        wrapper = LoRALinear(base, ident)
        setattr(module, name, wrapper)
        attached[ident] = wrapper
    return attached


def adapter_tensors(attached: dict) -> dict:
    return {f"{name}.{part}": getattr(module, part).detach().cpu().contiguous()
            for name, module in attached.items() for part in ("A", "B")}


def save_adapter(path: Path, attached: dict, *, rank: int, metadata: dict) -> dict:
    from safetensors.torch import save_file
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    information = {
        "schema": "yue2-ar-lora-v1", "rank": str(_validate_rank(rank)),
        "scaling_convention": SCALING_CONVENTION,
        "metadata": json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }
    try:
        save_file(adapter_tensors(attached), temporary, metadata=information)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(path), "sha256": file_sha256(path),
            "content_sha256": adapter_content_sha256(path), "bytes": path.stat().st_size,
            "rank": rank, "scaling_convention": SCALING_CONVENTION}


def inspect_adapter(path: Path, *, scale: float = 1.0) -> dict:
    from safetensors import safe_open
    if not math.isfinite(scale) or not 0 <= scale <= 2:
        raise ValueError("歌曲风格强度必须在 0–2 之间")
    path = Path(path)
    with safe_open(path, framework="pt", device="cpu") as archive:
        metadata = archive.metadata() or {}
        if metadata.get("schema") != "yue2-ar-lora-v1" or metadata.get("scaling_convention") != SCALING_CONVENTION:
            raise ValueError("歌曲风格模型格式或缩放约定无效")
        rank = _validate_rank(int(metadata.get("rank", "0")))
        names = set(archive.keys())
        if not names or any(not name.endswith((".A", ".B")) for name in names):
            raise ValueError("歌曲风格模型张量集合无效")
    try:
        details = json.loads(metadata.get("metadata", "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("歌曲风格模型元数据无效") from exc
    return {"sha256": file_sha256(path), "content_sha256": adapter_content_sha256(path),
            "bytes": path.stat().st_size, "rank": rank,
            "scale": scale, "scaling_convention": SCALING_CONVENTION, "metadata": details}


def load_adapter_into_wrappers(path: Path, attached: dict) -> dict:
    from safetensors import safe_open
    path = Path(path)
    with safe_open(path, framework="pt", device="cpu") as archive:
        metadata = archive.metadata() or {}
        if metadata.get("schema") != "yue2-ar-lora-v1" or metadata.get("scaling_convention") != SCALING_CONVENTION:
            raise ValueError("YuE2 AR LoRA schema 或缩放约定不匹配")
        rank = _validate_rank(int(metadata.get("rank", "0")))
        expected = {f"{name}.{part}" for name in attached for part in ("A", "B")}
        if set(archive.keys()) != expected:
            raise ValueError("YuE2 AR LoRA 张量集合与当前模型不匹配")
        import torch
        with torch.no_grad():
            for name, module in attached.items():
                for part in ("A", "B"):
                    value = archive.get_tensor(f"{name}.{part}")
                    target = getattr(module, part)
                    if value.shape != target.shape or target.shape[0 if part == "A" else 1] != rank:
                        raise ValueError("YuE2 AR LoRA 张量形状与 rank 不匹配")
                    if not torch.isfinite(value).all():
                        raise ValueError("YuE2 AR LoRA 含无效数值")
                    target.copy_(value.to(target.device, target.dtype))
    return {"sha256": file_sha256(path), "bytes": path.stat().st_size, "rank": rank,
            "metadata": json.loads(metadata.get("metadata", "{}"))}


def merge_ar_adapter(model, path: Path, *, scale: float = 1.0) -> dict:
    from safetensors import safe_open
    import torch
    if not math.isfinite(scale) or not 0 <= scale <= 2:
        raise ValueError("歌曲风格强度必须在 0–2 之间")
    path = Path(path)
    modules = {name: getattr(module, attribute) for name, module, attribute in target_modules(model)}
    with safe_open(path, framework="pt", device="cpu") as archive:
        metadata = archive.metadata() or {}
        if metadata.get("schema") != "yue2-ar-lora-v1" or metadata.get("scaling_convention") != SCALING_CONVENTION:
            raise ValueError("歌曲风格模型格式或缩放约定无效")
        rank = _validate_rank(int(metadata.get("rank", "0")))
        expected = {f"{name}.{part}" for name in modules for part in ("A", "B")}
        if set(archive.keys()) != expected:
            raise ValueError("歌曲风格模型与 YuE2 基模层结构不一致")
        with torch.no_grad():
            for name, linear in modules.items():
                A, B = archive.get_tensor(name + ".A"), archive.get_tensor(name + ".B")
                if A.shape != (rank, linear.in_features) or B.shape != (linear.out_features, rank):
                    raise ValueError("歌曲风格模型张量形状无效")
                if not torch.isfinite(A).all() or not torch.isfinite(B).all():
                    raise ValueError("歌曲风格模型含无效数值")
                linear.weight.add_((scale * (B.float().to(linear.weight.device) @
                                             A.float().to(linear.weight.device))).to(linear.weight.dtype))
    return inspect_adapter(path, scale=scale)


def merge_nar_companion(model, path: Path) -> dict:
    """Apply the fixed NAR LoRA and replace its complete IO projections."""
    import torch
    payload = torch.load(Path(path), map_location="cpu", weights_only=True)
    if not isinstance(payload, dict) or set(payload) != {"lora", "io", "rank"}:
        raise ValueError("v4 NAR companion schema 无效")
    rank = _validate_rank(int(payload["rank"]))
    values = iter(payload["lora"])
    with torch.no_grad():
        count = 0
        for _, module, attribute in target_modules(model, nar=True):
            linear = getattr(module, attribute)
            try:
                A, B = next(values), next(values)
            except StopIteration as exc:
                raise ValueError("v4 NAR companion 张量数量不足") from exc
            if A.shape != (rank, linear.in_features) or B.shape != (linear.out_features, rank):
                raise ValueError("v4 NAR companion 张量形状无效")
            linear.weight.add_((B.float().to(linear.weight.device) @
                                A.float().to(linear.weight.device)).to(linear.weight.dtype))
            count += 1
        try:
            next(values)
            raise ValueError("v4 NAR companion 张量数量过多")
        except StopIteration:
            pass
        if set(payload["io"]) != {"vae2llm", "llm2vae"}:
            raise ValueError("v4 NAR companion 缺少完整 IO 参数")
        model.vae2llm.load_state_dict(payload["io"]["vae2llm"], strict=True)
        model.llm2vae.load_state_dict(payload["io"]["llm2vae"], strict=True)
    return {"sha256": file_sha256(path), "bytes": Path(path).stat().st_size,
            "rank": rank, "merged_linears": count, "io_replaced": True,
            "scaling_convention": SCALING_CONVENTION}
