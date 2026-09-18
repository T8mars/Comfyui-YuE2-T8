"""Exact BF16 CPU weight offload using the bundled Accelerate runtime."""
import torch


def use_cpu_offload(mode, device, budget, physical_memory_gib=None, backend="torch-eager", quantization="none"):
    if mode not in {"auto", "gpu", "cpu-offload"}:
        raise ValueError("model_loading must be auto, gpu, or cpu-offload")
    if mode == "cpu-offload" and (backend == "vllm" or quantization != "none"):
        raise ValueError("CPU offload requires unquantized PyTorch inference")
    if mode == "gpu" or backend == "vllm" or quantization != "none":
        return False
    if torch.device(device).type != "cuda":
        return False
    physical = budget if physical_memory_gib is None else physical_memory_gib
    return mode == "cpu-offload" or min(float(budget), float(physical)) <= 12


def execution_device(model):
    if getattr(model, "_yue2_cpu_offloaded", False):
        return model._yue2_execution_device
    return next(model.parameters()).device


def enable_cpu_offload(model, device):
    from accelerate import cpu_offload
    if not getattr(model, "_yue2_cpu_offloaded", False):
        cpu_offload(model, execution_device=torch.device(device), offload_buffers=True)
        model._yue2_execution_device = torch.device(device)
        model._yue2_cpu_offloaded = True
    return model


def disable_cpu_offload(model):
    if getattr(model, "_yue2_cpu_offloaded", False):
        from accelerate.hooks import remove_hook_from_submodules
        remove_hook_from_submodules(model)
        model._yue2_cpu_offloaded = False
    return model
