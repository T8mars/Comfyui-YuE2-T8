from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import client

CATEGORY = "YuE2 音乐"
NONE_CHOICE = "不使用"


def _record_choices(path: str, key: str, *, empty: str, predicate=None) -> list[str]:
    """Return refreshable ComfyUI combo labels without starting the service."""
    try:
        records = client.request(path, timeout=2).get(key, [])
    except Exception:
        return [empty]
    choices = []
    for item in records:
        if predicate is not None and not predicate(item):
            continue
        ident = str(item.get("id", ""))
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            continue
        title = str(item.get("title") or item.get("name") or ident)[:100]
        choices.append(f"{title} [{ident}]")
    return choices or [empty]


def style_model_choices() -> list[str]:
    return [NONE_CHOICE, *_record_choices(
        "/api/workbench/assets?kind=model&limit=500", "assets", empty="没有已训练的歌曲风格模型",
        predicate=lambda item: item.get("metadata", {}).get("model_type") == "yue2_ar_lora",
    )]


def rvc_voice_choices() -> list[str]:
    return _record_choices("/api/rvc", "voices", empty="没有已训练或导入的 RVC 音色")


def rvc_project_choices() -> list[str]:
    return _record_choices("/api/rvc", "projects", empty="请先在 YuE2 工作台建立 RVC 训练项目")


def yue2_training_choices() -> list[str]:
    return _record_choices(
        "/api/workbench/training-runs?training_kind=yue2_style", "runs",
        empty="请先在 YuE2 工作台建立歌曲风格训练",
        predicate=lambda item: item.get("state") in {"draft", "failed", "cancelled", "paused"},
    )


def _selected_id(value: str, label: str) -> str:
    match = re.search(r"\[([a-f0-9]{32})\]\s*$", str(value))
    if match is None:
        raise ValueError(f"{label}不可用；请先在 YuE2 工作台准备完成，然后刷新 ComfyUI 页面")
    return match.group(1)


def base_request(model: dict) -> dict:
    return {"backend": model["backend"], "memory_budget_gib": model["memory_budget_gib"],
            "offload_ar": model.get("offload_ar", True),
            "model_loading": model.get("model_loading", "auto"),
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
            "memory_budget_gib": ("FLOAT", {"default": 23.5, "min": 2.5, "max": 256.0, "step": 0.5}),
            "offload_ar": ("BOOLEAN", {"default": True}),
        }, "optional": {
            "model_loading": (["auto", "cpu-offload", "gpu"], {"default": "auto"}),
            "nar_attention": (["sdpa", "math", "cudnn"], {"default": "sdpa"}),
            "nar_query_chunk_size": ("INT", {"default": 256, "min": 1, "max": 1024}),
        }}
    RETURN_TYPES = ("YUE2_MODEL", "STRING")
    RETURN_NAMES = ("model", "status")
    FUNCTION = "load"
    CATEGORY = CATEGORY

    def load(self, backend, memory_budget_gib, offload_ar, nar_attention="sdpa", nar_query_chunk_size=256, model_loading="auto"):
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
                  "offload_ar": bool(offload_ar), "service": client.SERVICE, "model_loading": model_loading,
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
        }, "optional": {
            "abc": ("STRING", {"multiline": True, "default": ""}),
            "style_model": (style_model_choices(), {"default": NONE_CHOICE}),
            "style_model_scale": ("FLOAT", {"default": 0.4, "min": 0.0, "max": 2.0, "step": 0.05}),
            "trained_style_model": ("YUE2_STYLE_MODEL",),
        }}
    RETURN_TYPES = ("AUDIO", "YUE2_RESULT", "STRING", "STRING")
    RETURN_NAMES = ("audio", "result", "metadata", "output_directory")
    FUNCTION = "generate"
    CATEGORY = CATEGORY

    def generate(self, model, style, lyrics, cot, seed, cfg_scale, candidates, abc="",
                 style_model=NONE_CHOICE, style_model_scale=0.4, trained_style_model=None):
        if cot == "off" and abc.strip():
            raise ValueError("off 模式不能输入 ABC")
        payload = {**base_request(model), "style": style, "lyrics": lyrics, "cot": cot,
                   "seed": int(seed), "cfg_scale": float(cfg_scale), "candidates": int(candidates)}
        model_asset_id = str((trained_style_model or {}).get("asset_id") or "")
        if trained_style_model is not None and not model_asset_id:
            raise ValueError("连接的 YuE2 训练节点只完成了预处理，尚未产生歌曲风格模型")
        if not model_asset_id and style_model != NONE_CHOICE:
            model_asset_id = _selected_id(style_model, "歌曲风格模型")
        if model_asset_id:
            if cot != "off":
                raise ValueError("训练得到的歌曲风格 LoRA 当前只支持直接生成（cot=off）")
            payload.update(style_model_asset_id=model_asset_id,
                           style_model_scale=float(style_model_scale))
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


class YuE2RVCVoiceLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "voice": (rvc_voice_choices(),),
            "speaker_id": ("INT", {"default": 0, "min": 0, "max": 109}),
        }}
    RETURN_TYPES = ("YUE2_RVC_VOICE", "STRING")
    RETURN_NAMES = ("voice", "metadata")
    FUNCTION = "load"
    CATEGORY = CATEGORY + "/RVC"

    def load(self, voice, speaker_id):
        health = client.ensure_service()
        if not health["ready"].get("capabilities", {}).get("rvc_inference"):
            raise RuntimeError("RVC 推理组件或底模尚未安装完整")
        voice_id = _selected_id(voice, "RVC 音色")
        records = client.request("/api/rvc")["voices"]
        selected = next((item for item in records if item["id"] == voice_id), None)
        if selected is None:
            raise ValueError("RVC 音色已被移动或删除，请刷新 ComfyUI 页面")
        sid = int(speaker_id)
        if sid not in {int(item["id"]) for item in selected.get("speakers", [])}:
            names = "、".join(f"{item['name']}（{item['id']}）" for item in selected.get("speakers", []))
            raise ValueError(f"该音色没有说话人 {sid}；可用说话人：{names or '无'}")
        handle = {"voice_id": voice_id, "speaker_id": sid, "name": selected.get("name", voice_id)}
        return (handle, json.dumps(selected, ensure_ascii=False))


class YuE2RVCCover:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "voice": ("YUE2_RVC_VOICE",), "song_audio": ("AUDIO",),
            "semitone_shift": ("INT", {"default": 0, "min": -12, "max": 12}),
            "index_rate": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05}),
            "protect": ("FLOAT", {"default": 0.33, "min": 0.0, "max": 0.5, "step": 0.01}),
            "vocal_gain_db": ("FLOAT", {"default": 0.0, "min": -18.0, "max": 12.0, "step": 0.5}),
            "accompaniment_gain_db": ("FLOAT", {"default": 0.0, "min": -18.0, "max": 12.0, "step": 0.5}),
        }}
    RETURN_TYPES = ("AUDIO", "YUE2_RESULT", "STRING")
    RETURN_NAMES = ("audio", "result", "metadata")
    FUNCTION = "convert"
    CATEGORY = CATEGORY + "/RVC"

    def convert(self, voice, song_audio, semitone_shift, index_rate, protect,
                vocal_gain_db, accompaniment_gain_db):
        ready = client.ensure_service()["ready"]
        capabilities = ready.get("capabilities", {})
        if not capabilities.get("rvc_inference") or not capabilities.get("vocal_separation"):
            raise RuntimeError("RVC 推理或人声分离组件尚未安装完整")
        source = save_comfy_audio(song_audio, "comfy-rvc-song")
        payload = {
            "backend": "rvc", "source_path": str(source),
            "voice_id": voice["voice_id"], "speaker_id": int(voice["speaker_id"]),
            "rvc_pitch_shift": int(semitone_shift), "index_rate": float(index_rate),
            "protect": float(protect), "vocal_gain_db": float(vocal_gain_db),
            "accompaniment_gain_db": float(accompaniment_gain_db),
        }
        status = client.run("voice_convert", payload)
        result = {"job_id": status["id"], **status["result"]}
        return (audio_value(status["result"]["audio"]), result,
                json.dumps(status, ensure_ascii=False))


class YuE2RVCTrain:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "training_project": (rvc_project_choices(),),
            "confirmed_materials_and_rights": ("BOOLEAN", {"default": False}),
        }}
    RETURN_TYPES = ("YUE2_RVC_VOICE", "STRING")
    RETURN_NAMES = ("voice", "metadata")
    FUNCTION = "train"
    CATEGORY = CATEGORY + "/RVC"
    OUTPUT_NODE = True

    def train(self, training_project, confirmed_materials_and_rights):
        if not confirmed_materials_and_rights:
            raise ValueError("请先在工作台逐段试听素材、确认训练权利，再勾选确认")
        health = client.ensure_service()
        if not health["ready"].get("capabilities", {}).get("rvc_training"):
            raise RuntimeError("RVC 训练组件或底模尚未安装完整")
        project_id = _selected_id(training_project, "RVC 训练项目")
        preflight = client.request(f"/api/rvc/projects/{project_id}/preflight")
        if not preflight.get("ready"):
            raise ValueError("RVC 训练检查未通过：" + "；".join(preflight.get("errors", [])))
        status = client.run("rvc_train", {"project_id": project_id})
        voice = status["result"]["voice"]
        speaker_id = int(voice.get("speakers", [{"id": 0}])[0]["id"])
        handle = {"voice_id": voice["id"], "speaker_id": speaker_id,
                  "name": voice.get("name", voice["id"])}
        return (handle, json.dumps(status, ensure_ascii=False))


class YuE2TrainStyle:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "training_run": (yue2_training_choices(),),
            "action": (["预处理并训练", "仅预处理", "训练或继续训练"], {"default": "预处理并训练"}),
            "confirmed_materials_and_rights": ("BOOLEAN", {"default": False}),
        }}
    RETURN_TYPES = ("YUE2_STYLE_MODEL", "STRING")
    RETURN_NAMES = ("style_model", "metadata")
    FUNCTION = "train"
    CATEGORY = CATEGORY + "/训练"
    OUTPUT_NODE = True

    def train(self, training_run, action, confirmed_materials_and_rights):
        if not confirmed_materials_and_rights:
            raise ValueError("请先在工作台核对训练集、验证集与使用权利，再勾选确认")
        health = client.ensure_service()
        if not health["ready"].get("capabilities", {}).get("yue2_training"):
            raise RuntimeError("YuE2 训练资源、MERT 模型或 CUDA 环境尚未安装完整")
        run_id = _selected_id(training_run, "YuE2 训练记录")
        run = client.request(f"/api/workbench/training-runs/{run_id}")
        metadata = []
        if action in {"预处理并训练", "仅预处理"}:
            prepared = client.run("yue2_prepare", {"run_id": run_id})
            metadata.append(prepared)
        if action == "仅预处理":
            return ({"run_id": run_id, "asset_id": ""}, json.dumps(metadata[-1], ensure_ascii=False))
        if action == "训练或继续训练" and not run.get("config", {}).get("prepared"):
            raise ValueError("该训练记录还未预处理；请选择“预处理并训练”")
        trained = client.run("yue2_train", {"run_id": run_id})
        metadata.append(trained)
        asset = trained["result"]["model_asset"]
        return ({"run_id": run_id, "asset_id": asset["id"], "title": asset.get("title", run_id)},
                json.dumps(metadata, ensure_ascii=False))


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
    "YuE2RVCVoiceLoader": YuE2RVCVoiceLoader, "YuE2RVCCover": YuE2RVCCover,
    "YuE2RVCTrain": YuE2RVCTrain, "YuE2TrainStyle": YuE2TrainStyle,
    "YuE2GenerateSemantic": YuE2GenerateSemantic, "YuE2Synthesize": YuE2Synthesize,
    "YuE2Decode": YuE2Decode, "YuE2SaveArtifacts": YuE2SaveArtifacts, "YuE2Unload": YuE2Unload,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "YuE2ModelLoader": "YuE2 模型服务", "YuE2GenerateSong": "YuE2 生成歌曲",
    "YuE2PlanSong": "YuE2 生成乐谱计划", "YuE2RenderPlan": "YuE2 渲染乐谱计划",
    "YuE2Transcribe": "YuE2 音频转谱", "YuE2GenerateCover": "YuE2 旋律重制",
    "YuE2ReferenceVoiceCover": "YuE2 参考音色翻唱",
    "YuE2RVCVoiceLoader": "YuE2 加载 RVC 音色", "YuE2RVCCover": "YuE2 RVC 翻唱",
    "YuE2RVCTrain": "YuE2 训练 RVC 音色", "YuE2TrainStyle": "YuE2 训练歌曲风格",
    "YuE2GenerateSemantic": "YuE2 生成语义 Tokens", "YuE2Synthesize": "YuE2 声学合成",
    "YuE2Decode": "YuE2 VAE 解码", "YuE2SaveArtifacts": "YuE2 导出工件", "YuE2Unload": "YuE2 卸载/取消",
}
