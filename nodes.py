from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from . import client


CATEGORY = "YuE2 音乐"


def base_request(model: dict) -> dict:
    return {"backend": model["backend"], "memory_budget_gib": model["memory_budget_gib"],
            "offload_ar": model.get("offload_ar", False)}


def audio_value(path: str):
    import numpy as np
    import soundfile as sf
    import torch
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    if not np.isfinite(data).all():
        raise ValueError("YuE2 音频包含非有限值")
    return {"waveform": torch.from_numpy(data.T.copy()).unsqueeze(0), "sample_rate": int(rate)}


def first_audio(status: dict):
    result = status["result"]
    if result.get("audio"):
        return result["audio"]
    return result["candidates"][0]["audio"]


class YuE2ModelLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "backend": (["torch-eager", "torch"], {"default": "torch-eager"}),
            "memory_budget_gib": ("FLOAT", {"default": 23.5, "min": 12.0, "max": 24.0, "step": 0.5}),
            "offload_ar": ("BOOLEAN", {"default": False}),
        }}
    RETURN_TYPES = ("YUE2_MODEL", "STRING")
    RETURN_NAMES = ("model", "status")
    FUNCTION = "load"
    CATEGORY = CATEGORY

    def load(self, backend, memory_budget_gib, offload_ar):
        health = client.ensure_service()
        missing = [name for name, ready in health["ready"]["models"].items() if not ready]
        if not health["ready"]["core_python"] or missing:
            raise RuntimeError("YuE2 未就绪：" + ("缺少模型 " + ", ".join(missing) if missing else "核心运行时未安装"))
        handle = {"backend": backend, "memory_budget_gib": float(memory_budget_gib),
                  "offload_ar": bool(offload_ar), "service": client.SERVICE}
        return (handle, json.dumps(health, ensure_ascii=False))


class YuE2GenerateSong:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("YUE2_MODEL",),
            "style": ("STRING", {"multiline": True, "default": "Mandarin pop, warm female vocal, piano, strings"}),
            "lyrics": ("STRING", {"multiline": True, "default": "[Verse]\n晚风穿过城市的灯\n[Chorus]\n让这首歌飞过长空"}),
            "cot": (["full", "melody", "off"], {"default": "full"}),
            "seed": ("INT", {"default": 831001, "min": 0, "max": 0x7fffffffffffffff}),
            "cfg_scale": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 20.0, "step": 0.01}),
            "candidates": ("INT", {"default": 1, "min": 1, "max": 8}),
        }, "optional": {"abc": ("STRING", {"multiline": True, "default": ""})}}
    RETURN_TYPES = ("AUDIO", "YUE2_RESULT", "STRING", "STRING")
    RETURN_NAMES = ("audio", "result", "metadata", "output_directory")
    FUNCTION = "generate"
    CATEGORY = CATEGORY

    def generate(self, model, style, lyrics, cot, seed, cfg_scale, candidates, abc=""):
        if cot == "off" and abc.strip():
            raise ValueError("off 模式不能输入 ABC")
        payload = {**base_request(model), "style": style, "lyrics": lyrics, "cot": cot,
                   "seed": int(seed), "cfg_scale": float(cfg_scale), "candidates": int(candidates)}
        if abc.strip(): payload["abc"] = abc
        status = client.run("generate", payload)
        result = {"job_id": status["id"], **status["result"]}
        return (audio_value(first_audio(status)), result, json.dumps(status, ensure_ascii=False),
                status["result"].get("artifact_dir", str(Path(first_audio(status)).parent)))


class YuE2PlanSong:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("YUE2_MODEL",), "style": ("STRING", {"multiline": True}),
            "lyrics": ("STRING", {"multiline": True}),
            "cot": (["full", "melody"], {"default": "full"}),
            "seed": ("INT", {"default": 831001, "min": 0, "max": 0x7fffffffffffffff}),
        }, "optional": {"abc": ("STRING", {"multiline": True, "default": ""})}}
    RETURN_TYPES = ("YUE2_PLAN", "STRING", "STRING")
    RETURN_NAMES = ("plan", "abc", "metadata")
    FUNCTION = "plan"
    CATEGORY = CATEGORY

    def plan(self, model, style, lyrics, cot, seed, abc=""):
        payload = {**base_request(model), "style": style, "lyrics": lyrics, "cot": cot, "seed": int(seed)}
        if abc.strip(): payload["abc"] = abc
        status = client.run("plan", payload)
        handle = {"job_id": status["id"], "model": model, **status["result"]}
        return (handle, status["result"].get("abc") or "", json.dumps(status, ensure_ascii=False))


class YuE2RenderPlan:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("YUE2_MODEL",), "plan": ("YUE2_PLAN",),
            "exact_original_plan": ("BOOLEAN", {"default": True}),
            "edited_abc": ("STRING", {"multiline": True, "default": ""}),
        }}
    RETURN_TYPES = ("AUDIO", "YUE2_RESULT", "STRING")
    RETURN_NAMES = ("audio", "result", "metadata")
    FUNCTION = "render"
    CATEGORY = CATEGORY

    def render(self, model, plan, exact_original_plan, edited_abc):
        payload = {**base_request(model), "plan_dir": plan["plan_dir"], "exact": bool(exact_original_plan)}
        if not exact_original_plan:
            request = dict(plan["request"])
            request["abc"] = edited_abc or plan.get("abc")
            payload.update(request)
        status = client.run("render_plan", payload)
        result = {"job_id": status["id"], **status["result"]}
        return (audio_value(first_audio(status)), result, json.dumps(status, ensure_ascii=False))


class YuE2Transcribe:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("YUE2_MODEL",), "audio": ("AUDIO",),
                "melody_only": ("BOOLEAN", {"default": True}),
                "render_score": (["none", "pdf", "png", "svg"], {"default": "none"})}}
    RETURN_TYPES = ("YUE2_TRANSCRIPTION", "STRING", "STRING")
    RETURN_NAMES = ("transcription", "abc", "metadata")
    FUNCTION = "transcribe"
    CATEGORY = CATEGORY

    def transcribe(self, model, audio, melody_only, render_score):
        import soundfile as sf
        root = client.find_root(); uploads = root / "uploads"; uploads.mkdir(parents=True, exist_ok=True)
        path = uploads / f"comfy-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.wav"
        waveform = audio["waveform"][0].detach().float().cpu().numpy().T
        sf.write(path, waveform, int(audio["sample_rate"]), subtype="FLOAT")
        payload = {"source_path": str(path), "melody_only": bool(melody_only), "dtype": "bf16",
                   "preset": "default", "render_score": False if render_score == "none" else render_score}
        status = client.run("transcribe", payload)
        handle = {"job_id": status["id"], "model": model, **status["result"]}
        return (handle, status["result"].get("abc") or "", json.dumps(status, ensure_ascii=False))


class YuE2GenerateCover:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("YUE2_MODEL",), "transcription": ("YUE2_TRANSCRIPTION",),
                "style": ("STRING", {"multiline": True}), "lyrics": ("STRING", {"multiline": True}),
                "seed": ("INT", {"default": 831001, "min": 0, "max": 0x7fffffffffffffff})},
                "optional": {"reviewed_abc": ("STRING", {"multiline": True, "default": ""})}}
    RETURN_TYPES = ("AUDIO", "YUE2_RESULT", "STRING")
    RETURN_NAMES = ("audio", "result", "metadata")
    FUNCTION = "cover"
    CATEGORY = CATEGORY

    def cover(self, model, transcription, style, lyrics, seed, reviewed_abc=""):
        payload = {**base_request(model), "style": style, "lyrics": lyrics,
                   "abc": reviewed_abc or transcription["abc"], "cot": "melody", "seed": int(seed),
                   "cfg_scale": 1.0, "candidates": 1}
        status = client.run("generate", payload); result={"job_id":status["id"],**status["result"]}
        return (audio_value(first_audio(status)), result, json.dumps(status, ensure_ascii=False))


class YuE2GenerateSemantic:
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"model": ("YUE2_MODEL",), "plan": ("YUE2_PLAN",)}}
    RETURN_TYPES=("YUE2_SEMANTIC","STRING"); RETURN_NAMES=("semantic","metadata"); FUNCTION="run"; CATEGORY=CATEGORY+"/高级"
    def run(self, model, plan):
        status=client.run("semantic",{**base_request(model),"plan_dir":plan["plan_dir"]})
        return ({"job_id":status["id"],"model":model,**status["result"]},json.dumps(status,ensure_ascii=False))


class YuE2Synthesize:
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"model": ("YUE2_MODEL",), "semantic": ("YUE2_SEMANTIC",)}}
    RETURN_TYPES=("YUE2_LATENTS","STRING"); RETURN_NAMES=("latents","metadata"); FUNCTION="run"; CATEGORY=CATEGORY+"/高级"
    def run(self, model, semantic):
        status=client.run("synthesize",{**base_request(model),"semantic_dir":semantic["semantic_dir"]})
        return ({"job_id":status["id"],"model":model,**status["result"]},json.dumps(status,ensure_ascii=False))


class YuE2Decode:
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"model": ("YUE2_MODEL",), "latents": ("YUE2_LATENTS",)}}
    RETURN_TYPES=("AUDIO","YUE2_RESULT","STRING"); RETURN_NAMES=("audio","result","metadata"); FUNCTION="run"; CATEGORY=CATEGORY+"/高级"
    def run(self, model, latents):
        status=client.run("decode",{**base_request(model),"latent_dir":latents["latent_dir"]})
        result={"job_id":status["id"],**status["result"]}
        return (audio_value(status["result"]["audio"]),result,json.dumps(status,ensure_ascii=False))


class YuE2SaveArtifacts:
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"result": ("YUE2_RESULT",),
            "destination": ("STRING", {"default": ""})}}
    RETURN_TYPES=("STRING",); RETURN_NAMES=("export_directory",); FUNCTION="save"; CATEGORY=CATEGORY
    OUTPUT_NODE = True
    def save(self, result, destination):
        response=client.request("/api/export",method="POST",data={"job_id":result["job_id"],"destination":destination})
        return (response["destination"],)


class YuE2Unload:
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"model": ("YUE2_MODEL",), "cancel_current": ("BOOLEAN", {"default": False})}}
    RETURN_TYPES=("STRING",); RETURN_NAMES=("status",); FUNCTION="unload"; CATEGORY=CATEGORY
    OUTPUT_NODE = True
    def unload(self, model, cancel_current):
        response=client.request("/api/unload",method="POST",data={"cancel_current":bool(cancel_current),"force":False})
        return (json.dumps(response,ensure_ascii=False),)


NODE_CLASS_MAPPINGS = {
    "YuE2ModelLoader": YuE2ModelLoader, "YuE2GenerateSong": YuE2GenerateSong,
    "YuE2PlanSong": YuE2PlanSong, "YuE2RenderPlan": YuE2RenderPlan,
    "YuE2Transcribe": YuE2Transcribe, "YuE2GenerateCover": YuE2GenerateCover,
    "YuE2GenerateSemantic": YuE2GenerateSemantic, "YuE2Synthesize": YuE2Synthesize,
    "YuE2Decode": YuE2Decode, "YuE2SaveArtifacts": YuE2SaveArtifacts, "YuE2Unload": YuE2Unload,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "YuE2ModelLoader": "YuE2 模型服务", "YuE2GenerateSong": "YuE2 生成歌曲",
    "YuE2PlanSong": "YuE2 生成乐谱计划", "YuE2RenderPlan": "YuE2 渲染乐谱计划",
    "YuE2Transcribe": "YuE2 音频转谱", "YuE2GenerateCover": "YuE2 生成翻唱",
    "YuE2GenerateSemantic": "YuE2 生成语义 Tokens", "YuE2Synthesize": "YuE2 声学合成",
    "YuE2Decode": "YuE2 VAE 解码", "YuE2SaveArtifacts": "YuE2 导出工件", "YuE2Unload": "YuE2 卸载/取消",
}
