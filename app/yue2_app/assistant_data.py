"""Assistant configuration, user-bound secrets, and revisioned text drafts."""
from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .io import atomic_json, within
from .settings import model_directory
from .assistant_rules import engine
from .assistant_rules.gguf_metadata import _cached_model_info
from .assistant_rules.provider_capabilities import normalize_extra_parameters

LOCK = threading.RLock()
PANELS = {"assistant", "create", "plan", "cover"}
PROVIDERS = {
    "seedance": {"label": "贞贞平价小屋", "base_url": "https://api.seedance.nz/v1"},
    "workshop": {"label": "贞贞的 AI 工坊", "base_url": "https://ai.t8star.org/v1"},
    "compatible": {"label": "OpenAI 兼容接口", "base_url": ""},
    "local": {"label": "本地 GGUF · 离线", "base_url": ""},
}
DEFAULT_CONFIG = {"provider": "seedance", "base_url": "https://api.seedance.nz/v1", "model": "",
                  "credential_id": "", "max_tokens": 4096, "context_size": 16384,
                  "gpu_layers": 24, "threads": 4, "think": False, "temperature_policy": "auto",
                  "extra_parameters": {}, "stream": True, "llm_directory": ""}


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def endpoint(config: dict) -> str:
    provider = config.get("provider")
    if provider not in PROVIDERS:
        raise ValueError("不支持的 LLM 渠道")
    if provider == "local":
        return ""
    value = (PROVIDERS[provider]["base_url"] if provider != "compatible" else str(config.get("base_url", ""))).strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("API 地址必须是无凭据、无查询参数的 HTTP(S) 地址")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 API 请使用 HTTPS；本机服务可以使用 HTTP")
    if value.endswith("/chat/completions"):
        return value
    return value + ("/chat/completions" if re.search(r"/v\d+$", parsed.path, re.I) else "/v1/chat/completions")


def normalize_config(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULT_CONFIG):
        raise ValueError("LLM 配置包含未知字段；密钥请使用独立凭据入口")
    config = {**DEFAULT_CONFIG, **value}
    endpoint(config)
    for key, low, high in (("max_tokens", 64, 32768), ("context_size", 512, 131072),
                           ("gpu_layers", -1, 200), ("threads", 1, 64)):
        item = config[key]
        if type(item) is not int or not low <= item <= high:
            raise ValueError(f"{key} 必须在 {low}–{high} 范围内")
    for key in ("think", "stream"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔值")
    for key in ("model", "credential_id", "llm_directory", "base_url"):
        if not isinstance(config[key], str) or len(config[key]) > 2048:
            raise ValueError(f"{key} 格式不正确")
        if engine.API_KEY_PATTERN.search(config[key]):
            raise ValueError(f"请从 {key} 移除密钥，使用独立 API Key 输入")
    if config["temperature_policy"] not in {"auto", "send", "omit"}:
        raise ValueError("温度策略无效")
    config["extra_parameters"] = normalize_extra_parameters(config["extra_parameters"])
    if config["provider"] == "local" and config["max_tokens"] >= config["context_size"]:
        raise ValueError("输出 Token 上限必须小于上下文，并留出提示词空间")
    return config


def llm_directory(root: Path, config: dict) -> Path:
    raw = config.get("llm_directory") or str(model_directory(root) / "LLM")
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def local_model_identity(root: Path, config: dict) -> list:
    """Bind resumable responses to the selected files, including every GGUF shard."""
    directory = llm_directory(root, config)
    model = within(directory, directory / config["model"])
    match = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", model.name)
    if match and (int(match[1]) != 1 or not 1 <= int(match[2]) <= 999):
        raise ValueError("请选择 GGUF 第一分片，分片数必须在 1–999")
    paths = [model] if not match else [model.with_name(model.name.replace("-00001-of-", f"-{i:05d}-of-"))
                                              for i in range(1, int(match[2]) + 1)]
    identity = []
    for path in paths:
        path = within(directory, path)
        if not path.is_file():
            raise ValueError(f"GGUF 文件或分片缺失：{path.name}")
        stat = path.stat()
        identity.append({"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                         "ctime_ns": stat.st_ctime_ns})
    return identity


def config_info(root: Path) -> dict:
    config = normalize_config(read(root / "userdata/assistant/config.json", {}))
    try:
        runtime_probe = read(root / "runtime/llm/installed.json", {}).get("probe", {})
    except (OSError, ValueError, AttributeError):
        runtime_probe = {}
    directory = llm_directory(root, config)
    models = []
    if directory.is_dir():
        for path in sorted(directory.rglob("*.gguf"))[:500]:
            if path.is_symlink() or "mmproj" in path.name.lower():
                continue
            if re.search(r"-\d{5}-of-\d{5}\.gguf$", path.name) and "-00001-of-" not in path.name:
                continue
            identifier = path.relative_to(directory).as_posix()
            try:
                stat = path.stat()
                info = _cached_model_info(str(path), identifier, stat.st_size, stat.st_mtime_ns)
                if info.is_projector:
                    continue
                shards = local_model_identity(root, {**config, "model": identifier})
                error = "" if info.metadata_readable and info.has_chat_template else (
                    "GGUF 元数据尚不可读，请确认下载完整" if not info.metadata_readable else "模型缺少聊天模板")
                models.append({"id": identifier, "bytes": sum(item["bytes"] for item in shards),
                               "architecture": info.architecture, "context_length": info.context_length,
                               "has_chat_template": info.has_chat_template, "shards": len(shards), "error": error})
            except (OSError, ValueError) as exc:
                models.append({"id": identifier, "bytes": 0, "error": str(exc)})
    return {"config": config, "providers": PROVIDERS, "defaults": engine.DEFAULTS,
            "options": {"lyrics_modes": engine.LYRIC_MODES, "quality_modes": [engine.STANDARD, engine.REVIEW],
                        "abc_sources": [engine.ABC_DOWNSTREAM, engine.ABC_GENERATE]},
            "llm_directory": str(directory), "models": models,
            "local_runtime": (root / "runtime/llm/python.exe").is_file() and bool(runtime_probe),
            "local_gpu_offload": runtime_probe.get("gpu_offload") is True,
            "official_source": engine.official_snapshot()}


def save_config(root: Path, value: dict) -> dict:
    config = normalize_config(value)
    atomic_json(root / "userdata/assistant/config.json", config)
    return config_info(root)


def normalize_request(root: Path, value: dict) -> dict:
    allowed = {"values", "config", "variant_id", "resume_from", "test_connection", "retry_stages", "final_fields"}
    if set(value) - allowed:
        raise ValueError("助手请求包含未知字段；不要把 API Key 放入任务")
    values = value.get("values", {})
    if not isinstance(values, dict) or set(values) - set(engine.DEFAULTS):
        raise ValueError("创作参数包含未知字段")
    values = {**engine.DEFAULTS, **values}
    for key, default in engine.DEFAULTS.items():
        v = values[key]
        if isinstance(default, str) and (not isinstance(v, str) or len(v) > 200000):
            raise ValueError(f"{key} 必须是有限长度文本")
        if isinstance(default, (int, float)) and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise ValueError(f"{key} 必须是有限数值")
        if isinstance(v, str) and engine.API_KEY_PATTERN.search(v):
            raise ValueError(f"请从 {key} 移除密钥")
    for key in ("seed", "yue2_seed"):
        if type(values[key]) is not int or not 0 <= values[key] <= 2**53 - 1:
            raise ValueError("随机种子超出可精确表示范围")
    if type(values["edit_occurrence"]) is not int or not 1 <= values["edit_occurrence"] <= 100:
        raise ValueError("段落序号必须在 1–100")
    if not 0 <= values["bpm"] <= 400 or not 0 <= values["target_duration_seconds"] <= 3600:
        raise ValueError("BPM 或目标时长超出范围")
    if values["cot"] not in {"full", "melody", "off"}:
        raise ValueError("无效的乐谱模式")
    config = normalize_config(value.get("config", {}))
    if not config["model"].strip():
        raise ValueError("请填写 API 模型名或选择本地 GGUF")
    result = {"values": values, "config": config, "variant_id": str(value.get("variant_id") or uuid.uuid4().hex)[:100],
              "test_connection": value.get("test_connection") is True}
    if not result["test_connection"] and not values["music_idea"].strip():
        raise ValueError("请填写歌曲想法")
    if config["provider"] == "local":
        path = within(llm_directory(root, config), llm_directory(root, config) / config["model"])
        if not path.is_file() or path.suffix.lower() != ".gguf" or "mmproj" in path.name.lower():
            raise ValueError("请选择存在的 GGUF 语言模型")
        if not (root / "runtime/llm/python.exe").is_file():
            raise ValueError("本地 LLM 环境未安装，请先运行安装脚本；API 创作仍可用")
        local_model_identity(root, config)
    if value.get("resume_from"):
        path = within(root / "outputs/jobs", Path(value["resume_from"]))
        result["resume_from"] = str(path)
    stages = value.get("retry_stages", [])
    if not isinstance(stages, list) or len(stages) > 10 or any(s not in {"lyrics", "style", "review", "abc"} for s in stages):
        raise ValueError("无效的重试阶段")
    result["retry_stages"] = stages
    final = value.get("final_fields", {})
    if not isinstance(final, dict) or set(final) - {"lyrics", "style", "instrumental"}:
        raise ValueError("重试固定字段无效")
    for name, field in final.items():
        if name == "instrumental":
            if type(field) is not bool:
                raise ValueError("纯器乐标记无效")
        elif not isinstance(field, str) or len(field) > 200000 or engine.API_KEY_PATTERN.search(field):
            raise ValueError("固定文本包含无效内容")
    if "style" in final and not final["style"].strip():
        raise ValueError("固定曲风不能为空")
    if "lyrics" in final and not final["lyrics"].strip() and not final.get("instrumental"):
        raise ValueError("空歌词只适用于明确选择的纯器乐")
    result["final_fields"] = final
    return result


def save_draft(root: Path, data: dict) -> dict:
    panel = data.get("panel")
    if panel not in PANELS:
        raise ValueError("无效草稿页面")
    payload = data.get("draft")
    if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False)) > 800000:
        raise ValueError("草稿格式或大小不正确")
    def inspect(item):
        if isinstance(item, dict):
            for key, val in item.items():
                if re.search(r"api.?key|token|secret|authorization|password", str(key), re.I):
                    raise ValueError("草稿不能包含凭据")
                inspect(val)
        elif isinstance(item, list):
            for val in item:
                inspect(val)
        elif isinstance(item, str) and engine.API_KEY_PATTERN.search(item):
            raise ValueError("草稿不能包含密钥")
    inspect(payload)
    path = root / "userdata/assistant/drafts" / (panel + ".json")
    with LOCK:
        current = read(path, {"schema": 1, "revision": 0, "draft": {}})
        if not isinstance(current, dict) or current.get("schema") != 1 or type(current.get("revision")) is not int:
            raise ValueError("草稿版本不支持，原文件已保留")
        if data.get("revision") != current["revision"]:
            raise ValueError("草稿已在其他页面更新，请重新载入后合并")
        if path.exists():
            atomic_json(path.with_suffix(".previous.json"), current)
        result = {"schema": 1, "panel": panel, "revision": current["revision"] + 1, "draft": payload}
        atomic_json(path, result)
        return result


def drafts(root: Path) -> dict:
    result = {}
    for panel in PANELS:
        empty = {"schema": 1, "panel": panel, "revision": 0, "draft": {}}
        try:
            value = read(root / "userdata/assistant/drafts" / (panel + ".json"), empty)
            if (not isinstance(value, dict) or value.get("schema") != 1 or type(value.get("revision")) is not int
                    or value["revision"] < 0 or not isinstance(value.get("draft"), dict)):
                raise ValueError("unsupported draft")
            result[panel] = value
        except (OSError, ValueError):
            result[panel] = {**empty, "error": "草稿版本或格式不支持，原文件已保留；请使用匹配版本或从 previous 备份恢复"}
    return result


def _protect(value: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise ValueError("记住密钥仅支持 Windows；可以使用本次会话凭据")
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, output = Blob(len(value), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    fn.restype = ctypes.c_int
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("无法读取本机加密凭据，请重新填写")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(output.data)


class Credentials:
    def __init__(self, directory: Path | None = None):
        self.path = (directory or Path(os.environ.get("LOCALAPPDATA", str(Path.home())))) / "YuE2-T8/credentials.json"
        self.session = {}

    def put(self, secret: str, url: str, remember=False) -> str:
        if not isinstance(secret, str) or not secret.strip() or len(secret) > 4096 or any(x in secret for x in "\r\n"):
            raise ValueError("API Key 必须是非空单行文本")
        ident = uuid.uuid4().hex
        value = {"secret": secret.strip(), "endpoint": url}
        with LOCK:
            if remember:
                data = read(self.path, {})
                data[ident] = base64.b64encode(_protect(json.dumps(value).encode())).decode()
                atomic_json(self.path, data)
            else:
                self.session[ident] = value
        return ident

    def get(self, ident: str, url: str) -> str:
        if not ident:
            # Unauthenticated local compatible servers are supported.
            if urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}:
                return ""
            raise ValueError("API 凭据待补，请配置后重试")
        with LOCK:
            item = self.session.get(ident)
            if item is None:
                stored = read(self.path, {}).get(ident)
                if stored:
                    item = json.loads(_protect(base64.b64decode(stored), decrypt=True))
            if not item or item["endpoint"] != url:
                raise ValueError("API 凭据不存在或与当前地址不匹配，请重新填写")
            return item["secret"]

    def delete(self, ident: str):
        with LOCK:
            self.session.pop(ident, None)
            data = read(self.path, {})
            if ident in data:
                del data[ident]
                atomic_json(self.path, data)
