from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import client

CATEGORY = "YuE2 音乐"


def base_request(model: dict) -> dict:
    return {"backend": model["backend"], "memory_budget_gib": model["memory_budget_gib"],
            "offload_ar": model.get("offload_ar", True),
            "nar_attention": model.get("nar_attention", "sdpa"),
            "nar_query_chunk_size": model.get("nar_query_chunk_size", 256)}


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


def save_comfy_audio(audio: dict, prefix: str) -> Path:
    import numpy as np
    import soundfile as sf
    root = client.find_root()
    uploads = root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    batch = audio["waveform"]
    if int(batch.shape[0]) != 1:
        raise ValueError("YuE2 一次只接受一条 AUDIO；请先拆分批次")
    waveform = batch[0].detach().float().cpu().numpy().T
    if not np.isfinite(waveform).all() or waveform.size == 0:
        raise ValueError("输入 AUDIO 为空或包含无效采样")
    path = uploads / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.wav"
    sf.write(path, waveform, int(audio["sample_rate"]), subtype="FLOAT")
    return path


class YuE2ModelLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "backend": (["torch-eager", "torch"], {"default": "torch-eager"}),
            "memory_budget_gib": ("FLOAT", {"default": 23.5, "min": 12.0, "max": 24.0, "step": 0.5}),
            "offload_ar": ("BOOLEAN", {"default": True}),
        }, "optional": {
            "nar_attention": (["sdpa", "math", "cudnn"], {"default": "sdpa"}),
            "nar_query_chunk_size": ("INT", {"default": 256, "min": 1, "max": 1024}),
        }}
    RETURN_TYPES = ("YUE2_MODEL", "STRING")
    RETURN_NAMES = ("model", "status")
    FUNCTION = "load"
    CATEGORY = CATEGORY

    def load(self, backend, memory_budget_gib, offload_ar, nar_attention="sdpa", nar_query_chunk_size=256):
        health = client.ensure_service()
        ready = health["ready"]
        missing = [name for name in ("model", "vae") if not ready["models"].get(name)]
        if not ready.get("capabilities", {}).get("generation"):
            details = []
            if not ready.get("core_python"):
                details.append("核心运行时")
            if not ready.get("upstream_source"):
                details.append("推理源码")
            if missing:
                details.append("模型 " + ", ".join(missing))
            raise RuntimeError("YuE2 生成环境未就绪：缺少 " + "、".join(details))
        handle = {"backend": backend, "memory_budget_gib": float(memory_budget_gib),
                  "offload_ar": bool(offload_ar), "service": client.SERVICE,
                  "nar_attention": nar_attention, "nar_query_chunk_size": int(nar_query_chunk_size)}
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
        if abc.strip():
            payload["abc"] = abc
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
        if abc.strip():
            payload["abc"] = abc
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
        ready = client.ensure_service()["ready"]
        if not ready.get("capabilities", {}).get("transcription"):
            raise RuntimeError("YuE2 转谱环境未就绪：请安装转谱运行时、模型和 FFmpeg")
        if render_score != "none" and not ready.get("capabilities", {}).get("score_renderer"):
            raise RuntimeError("YuE2 乐谱渲染器未安装；请重新运行安装脚本且不要使用 -SkipRenderer")
        root = client.find_root()
        uploads = root / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        path = uploads / f"comfy-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.wav"
        batch = audio["waveform"]
        if int(batch.shape[0]) != 1:
            raise ValueError("YuE2 音频转谱一次只接受一条 AUDIO；请先拆分批次")
        waveform = batch[0].detach().float().cpu().numpy().T
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
        abc = str(reviewed_abc or transcription.get("abc") or "").strip()
        if not abc:
            detail = transcription.get("abc_error") or "转谱没有产生可用的 ABC"
            raise ValueError(f"无法生成翻唱：{detail}；请提供核对后的 ABC")
        payload = {**base_request(model), "style": style, "lyrics": lyrics,
                   "abc": abc, "cot": "melody", "seed": int(seed),
                   "cfg_scale": 1.0, "candidates": 1}
        status = client.run("generate", payload)
        result = {"job_id": status["id"], **status["result"]}
        return (audio_value(first_audio(status)), result, json.dumps(status, ensure_ascii=False))


class YuE2ReferenceVoiceCover:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("YUE2_MODEL",),
            "song_audio": ("AUDIO",),
            "reference_voice": ("AUDIO",),
            "diffusion_steps": ("INT", {"default": 30, "min": 4, "max": 50, "step": 1}),
            "timbre_strength": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 1.5, "step": 0.05}),
            "auto_match_pitch": ("BOOLEAN", {"default": False}),
            "semitone_shift": ("INT", {"default": 0, "min": -12, "max": 12, "step": 1}),
            "vocal_gain_db": ("FLOAT", {"default": 0.0, "min": -18.0, "max": 12.0, "step": 0.5}),
            "accompaniment_gain_db": ("FLOAT", {"default": 0.0, "min": -18.0, "max": 12.0, "step": 0.5}),
        }}
    RETURN_TYPES = ("AUDIO", "YUE2_RESULT", "STRING")
    RETURN_NAMES = ("audio", "result", "metadata")
    FUNCTION = "convert"
    CATEGORY = CATEGORY

    def convert(self, model, song_audio, reference_voice, diffusion_steps, timbre_strength,
                auto_match_pitch, semitone_shift, vocal_gain_db, accompaniment_gain_db):
        ready = client.ensure_service()["ready"]
        if not ready.get("capabilities", {}).get("voice_conversion"):
            raise RuntimeError("参考音色环境未就绪：请安装 Seed-VC、Demucs 模型和 voice 运行时")
        source = save_comfy_audio(song_audio, "comfy-song")
        reference = save_comfy_audio(reference_voice, "comfy-reference")
        payload = {
            "source_path": str(source), "reference_path": str(reference),
            "diffusion_steps": int(diffusion_steps), "cfg_rate": float(timbre_strength),
            "auto_f0_adjust": bool(auto_match_pitch), "semi_tone_shift": int(semitone_shift),
            "vocal_gain_db": float(vocal_gain_db),
            "accompaniment_gain_db": float(accompaniment_gain_db),
        }
        status = client.run("voice_convert", payload)
        result = {"job_id": status["id"], **status["result"]}
        return (audio_value(status["result"]["audio"]), result,
                json.dumps(status, ensure_ascii=False))


class YuE2GenerateSemantic:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("YUE2_MODEL",), "plan": ("YUE2_PLAN",)}}

    RETURN_TYPES = ("YUE2_SEMANTIC", "STRING")
    RETURN_NAMES = ("semantic", "metadata")
    FUNCTION = "run"
    CATEGORY = CATEGORY + "/高级"

    def run(self, model, plan):
        status = client.run("semantic", {**base_request(model), "plan_dir": plan["plan_dir"]})
        return ({"job_id": status["id"], "model": model, **status["result"]},
                json.dumps(status, ensure_ascii=False))


class YuE2Synthesize:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("YUE2_MODEL",), "semantic": ("YUE2_SEMANTIC",)}}

    RETURN_TYPES = ("YUE2_LATENTS", "STRING")
    RETURN_NAMES = ("latents", "metadata")
    FUNCTION = "run"
    CATEGORY = CATEGORY + "/高级"

    def run(self, model, semantic):
        status = client.run("synthesize", {**base_request(model), "semantic_dir": semantic["semantic_dir"]})
        return ({"job_id": status["id"], "model": model, **status["result"]},
                json.dumps(status, ensure_ascii=False))


class YuE2Decode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("YUE2_MODEL",), "latents": ("YUE2_LATENTS",)}}

    RETURN_TYPES = ("AUDIO", "YUE2_RESULT", "STRING")
    RETURN_NAMES = ("audio", "result", "metadata")
    FUNCTION = "run"
    CATEGORY = CATEGORY + "/高级"

    def run(self, model, latents):
        status = client.run("decode", {**base_request(model), "latent_dir": latents["latent_dir"]})
        result = {"job_id": status["id"], **status["result"]}
        return (audio_value(status["result"]["audio"]), result,
                json.dumps(status, ensure_ascii=False))


class YuE2SaveArtifacts:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "result": ("YUE2_RESULT",),
            "destination": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("export_directory",)
    FUNCTION = "save"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **_kwargs):
        return float("nan")

    def save(self, result, destination):
        response = client.request(
            "/api/export", method="POST",
            data={"job_id": result["job_id"], "destination": destination},
        )
        return (response["destination"],)


class YuE2Unload:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("YUE2_MODEL",),
                "cancel_current": ("BOOLEAN", {"default": False}),
            },
            "optional": {"force_cancel": ("BOOLEAN", {"default": False})},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "unload"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **_kwargs):
        return float("nan")

    def unload(self, model, cancel_current, force_cancel=False):
        response = client.request(
            "/api/unload", method="POST",
            data={"cancel_current": bool(cancel_current), "force": bool(force_cancel)},
        )
        return (json.dumps(response, ensure_ascii=False),)


NODE_CLASS_MAPPINGS = {
    "YuE2ModelLoader": YuE2ModelLoader, "YuE2GenerateSong": YuE2GenerateSong,
    "YuE2PlanSong": YuE2PlanSong, "YuE2RenderPlan": YuE2RenderPlan,
    "YuE2Transcribe": YuE2Transcribe, "YuE2GenerateCover": YuE2GenerateCover,
    "YuE2ReferenceVoiceCover": YuE2ReferenceVoiceCover,
    "YuE2GenerateSemantic": YuE2GenerateSemantic, "YuE2Synthesize": YuE2Synthesize,
    "YuE2Decode": YuE2Decode, "YuE2SaveArtifacts": YuE2SaveArtifacts, "YuE2Unload": YuE2Unload,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "YuE2ModelLoader": "YuE2 模型服务", "YuE2GenerateSong": "YuE2 生成歌曲",
    "YuE2PlanSong": "YuE2 生成乐谱计划", "YuE2RenderPlan": "YuE2 渲染乐谱计划",
    "YuE2Transcribe": "YuE2 音频转谱", "YuE2GenerateCover": "YuE2 旋律重制",
    "YuE2ReferenceVoiceCover": "YuE2 参考音色翻唱",
    "YuE2GenerateSemantic": "YuE2 生成语义 Tokens", "YuE2Synthesize": "YuE2 声学合成",
    "YuE2Decode": "YuE2 VAE 解码", "YuE2SaveArtifacts": "YuE2 导出工件", "YuE2Unload": "YuE2 卸载/取消",
}
