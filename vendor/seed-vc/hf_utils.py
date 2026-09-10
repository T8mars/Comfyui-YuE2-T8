import os
from pathlib import Path
from huggingface_hub import hf_hub_download


def load_custom_model_from_hf(repo_id, model_filename="pytorch_model.bin", config_filename=None):
    local_root = os.environ.get("SEED_VC_MODEL_ROOT")
    if local_root:
        model_path = Path(local_root).resolve() / model_filename
        if not model_path.is_file():
            raise FileNotFoundError(f"Seed-VC local model is missing: {model_path}")
        if config_filename is None:
            return str(model_path)
        config_path = Path(local_root).resolve() / config_filename
        if not config_path.is_file():
            raise FileNotFoundError(f"Seed-VC local config is missing: {config_path}")
        return str(model_path), str(config_path)
    os.makedirs("./checkpoints", exist_ok=True)
    model_path = hf_hub_download(repo_id=repo_id, filename=model_filename, cache_dir="./checkpoints")
    if config_filename is None:
        return model_path
    config_path = hf_hub_download(repo_id=repo_id, filename=config_filename, cache_dir="./checkpoints")

    return model_path, config_path
