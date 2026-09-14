"""YuE2 AR LoRA training with immutable inputs and exact step-boundary resume."""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import time
import uuid
from pathlib import Path

from .io import atomic_json, sha256, within
from .yue2_adapter import attach_ar_lora, inspect_adapter, load_adapter_into_wrappers, save_adapter

MAX_SEQUENCE = 12288
LOSS_BLOCK = 256
CHECKPOINT_SCHEMA = 1


def training_config(value: dict | None) -> dict:
    value = dict(value or {})
    result = {
        "rank": int(value.get("rank", 16)),
        "steps": int(value.get("steps", 800)),
        "gradient_accumulation": int(value.get("gradient_accumulation", 2)),
        "learning_rate": float(value.get("learning_rate", 1e-4)),
        "user_fraction": float(value.get("user_fraction", .7)),
        "warmup_steps": int(value.get("warmup_steps", 50)),
        "schedule_steps": int(value.get("schedule_steps", value.get("steps", 800))),
        "validate_every": int(value.get("validate_every", 100)),
        "save_every": int(value.get("save_every", 100)),
        "seed": int(value.get("seed", 831001)),
        "max_sequence": MAX_SEQUENCE,
        "loss_block": LOSS_BLOCK,
        "mode": "cot_off_ar_lora",
    }
    if result["rank"] not in {4, 8, 16, 32, 64}:
        raise ValueError("LoRA rank 只能是 4、8、16、32 或 64")
    if not 1 <= result["steps"] <= 100000:
        raise ValueError("训练步数必须是 1–100000")
    if not 1 <= result["gradient_accumulation"] <= 32:
        raise ValueError("梯度累积必须是 1–32")
    if not math.isfinite(result["learning_rate"]) or not 1e-7 <= result["learning_rate"] <= 1e-2:
        raise ValueError("学习率必须是 1e-7–1e-2 的有限数值")
    if not math.isfinite(result["user_fraction"]) or not 0 <= result["user_fraction"] <= 1:
        raise ValueError("用户素材采样比例必须是 0–1")
    for name in ("warmup_steps", "schedule_steps", "validate_every", "save_every"):
        if result[name] < 1:
            raise ValueError("训练调度参数必须为正整数")
    return result


def _identity(value: object) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _nested_tuple(value):
    return tuple(_nested_tuple(item) for item in value) if isinstance(value, list) else value


def _optimizer_to_device(optimizer) -> None:
    import torch
    for state in optimizer.state.values():
        for name, value in state.items():
            if torch.is_tensor(value):
                state[name] = value.to(optimizer.param_groups[0]["params"][0].device)


def _checkpoint_files(directory: Path) -> dict:
    return {name: {"sha256": sha256(directory / name), "bytes": (directory / name).stat().st_size}
            for name in ("adapter.safetensors", "state.pt", "sampler.json")}


def save_training_checkpoint(directory: Path, *, attached: dict, optimizer, step: int,
                             rank: int, identity: str, sampler: random.Random,
                             numpy_generator, history: list[dict], best_validation: float | None,
                             metadata: dict) -> dict:
    """Atomically publish a complete resume point after an optimizer step."""
    import torch
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.with_name("." + directory.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.mkdir()
    try:
        adapter = save_adapter(temporary / "adapter.safetensors", attached, rank=rank, metadata=metadata)
        state = {
            "schema": CHECKPOINT_SCHEMA, "step": int(step), "identity": identity,
            "optimizer": optimizer.state_dict(), "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            "history": list(history), "best_validation": best_validation,
        }
        torch.save(state, temporary / "state.pt")
        atomic_json(temporary / "sampler.json", {
            "python": sampler.getstate(), "numpy": numpy_generator.bit_generator.state,
        })
        manifest = {"schema": CHECKPOINT_SCHEMA, "identity": identity, "step": int(step),
                    "adapter": adapter, "files": _checkpoint_files(temporary)}
        atomic_json(temporary / "manifest.json", manifest)
        if directory.exists():
            shutil.rmtree(directory)
        os.replace(temporary, directory)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def load_training_checkpoint(directory: Path, *, attached: dict, optimizer,
                             identity: str, sampler: random.Random, numpy_generator) -> dict:
    import torch
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != CHECKPOINT_SCHEMA or manifest.get("identity") != identity:
        raise ValueError("检查点与当前素材、基模或训练参数不一致")
    expected = manifest.get("files", {})
    if set(expected) != {"adapter.safetensors", "state.pt", "sampler.json"}:
        raise ValueError("检查点文件清单不完整")
    for name, record in expected.items():
        path = within(directory, directory / name)
        if (not path.is_file() or path.is_symlink() or path.stat().st_size != int(record.get("bytes", -1))
                or sha256(path) != record.get("sha256")):
            raise ValueError("检查点已损坏或未完整写入")
    load_adapter_into_wrappers(directory / "adapter.safetensors", attached)
    state = torch.load(directory / "state.pt", map_location="cpu", weights_only=True)
    if state.get("schema") != CHECKPOINT_SCHEMA or state.get("identity") != identity:
        raise ValueError("检查点恢复状态无效")
    optimizer.load_state_dict(state["optimizer"])
    _optimizer_to_device(optimizer)
    torch.set_rng_state(state["torch_rng"])
    if torch.cuda.is_available() and state.get("cuda_rng"):
        torch.cuda.set_rng_state_all(state["cuda_rng"])
    sample_state = json.loads((directory / "sampler.json").read_text(encoding="utf-8"))
    sampler.setstate(_nested_tuple(sample_state["python"]))
    numpy_generator.bit_generator.state = sample_state["numpy"]
    return state


def _lr(config: dict, step: int) -> float:
    warmup = min(1.0, step / config["warmup_steps"])
    phase = min(step, config["schedule_steps"]) / config["schedule_steps"]
    decay = .2 + .8 * .5 * (1 + math.cos(math.pi * phase))
    return config["learning_rate"] * warmup * decay


def _sequence(prefix, codec, device):
    import torch
    from yue2.protocol import CODEC_OFFSET, MUSIC_END
    values = [int(value) for value in prefix] + [int(value) + CODEC_OFFSET for value in codec] + [MUSIC_END]
    if len(values) > MAX_SEQUENCE:
        raise ValueError("训练序列超过 12288 token；系统拒绝静默截断")
    return torch.tensor([values], device=device, dtype=torch.long), len(prefix)


def _loss(model, ids, prefix_length: int, *, gradient: bool):
    import torch
    import torch.nn.functional as F
    from torch.utils.checkpoint import checkpoint
    backbone = model.model
    hidden = backbone.embed_tokens(ids)
    position = torch.arange(ids.shape[1], device=ids.device)[None]
    cos, sin = backbone.rotary_emb(position)
    for layer in backbone.layers:
        def layer_forward(value, *, current=layer):
            return current(value, cos, sin, past_key_value=None, attention_mask=None,
                           ar_mask=None, cache_position=None)
        hidden = checkpoint(layer_forward, hidden, use_reentrant=False) if gradient else layer_forward(hidden)
    hidden = backbone.norm(hidden[0])
    predicted = hidden[prefix_length - 1:-1]
    target = ids[0, prefix_length:]
    if predicted.shape[0] != target.shape[0] or not len(target):
        raise ValueError("训练序列没有可预测的音乐 token")
    total = predicted.new_zeros((), dtype=torch.float32)
    for start in range(0, len(target), LOSS_BLOCK):
        feature, label = predicted[start:start + LOSS_BLOCK], target[start:start + LOSS_BLOCK]
        def block_loss(value, wanted):
            return F.cross_entropy(model.lm_head(value).float(), wanted, reduction="sum")
        total = total + (checkpoint(block_loss, feature, label, use_reentrant=False)
                         if gradient else block_loss(feature, label))
    return total / len(target)


def _load_dataset(directory: Path) -> tuple[dict, list[dict], list[dict]]:
    import numpy as np
    directory = Path(directory).resolve()
    dataset = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
    check = dict(dataset); claimed = check.pop("identity", "")
    if dataset.get("schema") != 1 or claimed != _identity(check):
        raise ValueError("训练数据清单身份无效")
    for name, digest in dataset.get("files", {}).items():
        path = within(directory, directory / name)
        if not path.is_file() or path.is_symlink() or sha256(path) != digest:
            raise ValueError("训练数据缓存不完整或内容已改变")
    records = []
    for record in dataset.get("records", []):
        prefix = np.load(within(directory, directory / record["prefix"]), allow_pickle=False)
        codec = np.load(within(directory, directory / record["codec"]), allow_pickle=False)
        if prefix.ndim != 1 or codec.ndim != 1 or prefix.dtype.kind not in "iu" or codec.dtype.kind not in "iu":
            raise ValueError("训练 token 数组无效")
        records.append({**record, "prefix_array": prefix, "codec_array": codec})
    train = [item for item in records if item["split"] == "train"]
    validation = [item for item in records if item["split"] == "validation"]
    if not train or not validation:
        raise ValueError("YuE2 训练至少需要不同歌曲组的训练集和验证集")
    return dataset, train, validation


def train(root: Path, run: dict, prepared: Path, ctx) -> dict:
    import numpy as np
    import torch
    from .asset_library import AssetLibrary
    from .artifacts import declared_model_provenance
    from .config import model_paths, upstream_path
    from .training_resources import manifest as resource_manifest, resource_directory
    from .yue2_training_data import load_regularizer

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("YuE2 训练需要支持 BF16 的 NVIDIA CUDA GPU")
    config = training_config(run.get("config"))
    prompt_defaults = {key: str(run.get("config", {}).get(key, ""))
                       for key in ("default_style", "default_lyrics")}
    dataset, user_train, validation = _load_dataset(prepared)
    resources = resource_manifest()
    provenance = declared_model_provenance(root, ("YuE2-3B",))
    identity_data = {"dataset": dataset["identity"], "config": config, "base": provenance,
                     "head": resources["files"]["tokenizer_head_joint_v4.pt"]["sha256"],
                     "nar": resources["files"]["nar_lora_joint_v4.pt"]["sha256"],
                     "source_revision": resources["revision"]}
    training_identity = _identity(identity_data)
    import sys
    vendor = str(upstream_path(root))
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from yue2.modeling_yue2 import YuE2ForCausalLM
    from yue2.protocol import SongRequest, token_prefixes
    from yue2.tokenization_yue2 import YuE2TextTokenizer
    paths = model_paths(root, strict=True)
    ctx.update("yue2_loading", message="正在加载 YuE2 基模", resumable=True)
    torch.manual_seed(config["seed"])
    torch.cuda.manual_seed_all(config["seed"])
    model = YuE2ForCausalLM.from_pretrained(
        paths["model"], local_files_only=True, torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True).to("cuda").eval()
    model.requires_grad_(False)
    attached = attach_ar_lora(model, config["rank"])
    trainable = [parameter for module in attached.values() for parameter in (module.A, module.B)]
    optimizer = torch.optim.AdamW(trainable, lr=config["learning_rate"], betas=(.9, .95), weight_decay=0)
    sampler = random.Random(config["seed"])
    numpy_generator = np.random.default_rng(config["seed"])
    tokenizer = YuE2TextTokenizer(paths["model"] / "qwen.tiktoken")
    regularizer = load_regularizer(root)
    regularizer_train = [item for item in regularizer if item["src"] == "minted"]
    if not regularizer_train:
        raise ValueError("固定 regularizer 没有训练记录")
    prefix_cache = {}
    def arrays(item):
        if "prefix_array" in item:
            return item["prefix_array"], item["codec_array"]
        name = item["name"]
        if name not in prefix_cache:
            prefix_cache[name] = np.asarray(token_prefixes(
                SongRequest(style=item["style"][:1500], lyrics=item["lyrics"], cot="off", seed=1), tokenizer),
                dtype=np.int64)
        return prefix_cache[name], item["codec"]

    home = AssetLibrary(root).home / "training" / run["id"]
    checkpoints = home / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    last_pointer = checkpoints / "last.json"
    start_step, history, best = 0, [], None
    if last_pointer.is_file():
        pointer = json.loads(last_pointer.read_text(encoding="utf-8"))
        resume = within(checkpoints, checkpoints / pointer["directory"])
        state = load_training_checkpoint(resume, attached=attached, optimizer=optimizer,
                                         identity=training_identity, sampler=sampler,
                                         numpy_generator=numpy_generator)
        start_step = int(state["step"])
        history, best = list(state.get("history", [])), state.get("best_validation")
        ctx.update("yue2_loading", message=f"已从第 {start_step} 步恢复", resumed_step=start_step)

    @torch.no_grad()
    def evaluate():
        losses = []
        for item in validation:
            prefix, codec = arrays(item)
            ids, prefix_length = _sequence(prefix, codec, "cuda")
            losses.append(float(_loss(model, ids, prefix_length, gradient=False)))
        return sum(losses) / len(losses)

    def checkpoint(step: int, validation_loss: float | None):
        nonlocal best
        if validation_loss is not None and (best is None or validation_loss < best):
            best = validation_loss
        destination = checkpoints / f"step-{step:08d}"
        manifest = save_training_checkpoint(
            destination, attached=attached, optimizer=optimizer, step=step,
            rank=config["rank"], identity=training_identity, sampler=sampler,
            numpy_generator=numpy_generator, history=history, best_validation=best,
            metadata={**identity_data, "training_identity": training_identity, "step": step,
                      "nar_companion_sha256": resources["files"]["nar_lora_joint_v4.pt"]["sha256"]},
        )
        atomic_json(last_pointer, {"directory": destination.name, "step": step,
                                   "manifest_sha256": sha256(destination / "manifest.json")})
        run_config = {**config, **prompt_defaults, "prepared": str(prepared), "last_checkpoint": str(destination),
                      "training_identity": training_identity, "history": history, "best_validation": best}
        AssetLibrary(root).update_training_run(run["id"], state="paused" if ctx.pause_requested() else "running",
                                               config=run_config)
        return manifest

    optimizer.zero_grad(set_to_none=True)
    ctx.memory("yue2_training_loaded", trainable_parameters=sum(p.numel() for p in trainable))
    for step in range(start_step + 1, config["steps"] + 1):
        started = time.perf_counter()
        step_loss = 0.0
        for _ in range(config["gradient_accumulation"]):
            use_user = sampler.random() < config["user_fraction"]
            pool = user_train if use_user else regularizer_train
            item = pool[sampler.randrange(len(pool))]
            prefix, codec = arrays(item)
            ids, prefix_length = _sequence(prefix, codec, "cuda")
            loss = _loss(model, ids, prefix_length, gradient=True)
            (loss / config["gradient_accumulation"]).backward()
            step_loss += float(loss.detach()) / config["gradient_accumulation"]
        torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        rate = _lr(config, step)
        for group in optimizer.param_groups:
            group["lr"] = rate
        optimizer.step(); optimizer.zero_grad(set_to_none=True)
        validation_loss = None
        if step % config["validate_every"] == 0 or step == config["steps"]:
            validation_loss = evaluate()
        history.append({"step": step, "train_loss": step_loss, "validation_loss": validation_loss,
                        "learning_rate": rate, "seconds": time.perf_counter() - started})
        history[:] = history[-2000:]
        ctx.update("yue2_training", completed=step, total=config["steps"],
                   progress=step / config["steps"], train_loss=step_loss,
                   validation_loss=validation_loss, history=history[-200:], resumable=True)
        should_save = (step % config["save_every"] == 0 or step == config["steps"]
                       or validation_loss is not None or ctx.pause_requested())
        manifest = checkpoint(step, validation_loss) if should_save else None
        if ctx.pause_requested():
            return {"paused": True, "step": step, "checkpoint": str(checkpoints / f"step-{step:08d}"),
                    "manifest": manifest, "history": history, "best_validation": best}
        ctx.check_cancelled()
    final = checkpoints / f"step-{config['steps']:08d}" / "adapter.safetensors"
    final_adapter = inspect_adapter(final)
    library = AssetLibrary(root)
    model_asset = library.import_file(
        final, kind="model", title=run["title"], tags=["YuE2", "AR LoRA", "歌曲风格"],
        provenance={"training_run_id": run["id"], "snapshot_id": run["snapshot_id"],
                    "source_revision": resources["revision"]},
        metadata={"model_type": "yue2_ar_lora", "rank": config["rank"],
                  "training_identity": training_identity,
                  "adapter_content_sha256": final_adapter["content_sha256"],
                  "supported_cot": ["off"],
                  "nar_companion_sha256": resources["files"]["nar_lora_joint_v4.pt"]["sha256"],
                  "training_source_revision": resources["revision"]},
    )
    library.update_training_run(run["id"], state="complete", model_asset_id=model_asset["id"],
                                config={**config, **prompt_defaults, "prepared": str(prepared), "history": history,
                                        "best_validation": best, "training_identity": training_identity})
    return {"paused": False, "step": config["steps"], "model_asset": model_asset,
            "adapter": str(final), "history": history, "best_validation": best,
            "nar_companion": str(resource_directory(root) / "nar_lora_joint_v4.pt")}
