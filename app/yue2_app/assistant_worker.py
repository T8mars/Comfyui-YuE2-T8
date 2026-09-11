"""Serial, process-isolated text creation. API path never imports llama_cpp/Torch."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sys
import threading
import time
from pathlib import Path

from .assistant_data import endpoint, llm_directory, local_model_identity, normalize_request, read
from .assistant_rules import engine
from .assistant_rules.provider_capabilities import apply_chat_request_options
from .io import atomic_json, within
from .worker_common import Cancelled, JobContext


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Runner:
    def __init__(self, root: Path, ctx: JobContext, request: dict, secret="", transport=None):
        self.root, self.ctx, self.request = root, ctx, request
        self.values, self.config = request["values"], request["config"]
        self.fixed_fields = request.get("final_fields", {})
        self.secret, self.transport = secret, transport
        self.model = self.config["model"]
        self.provider_name = self.config["provider"]
        self.model_identity = local_model_identity(root, self.config) if self.provider_name == "local" else None
        self.stages, self.cache, self.result = [], {}, {"schema": 1, "outcome": "in_progress", "fields": {}, "abc_status": "not_requested"}
        self.stage, self.last_stage = "starting", None
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.llm, self.session = None, None
        self.calls = 0
        self.cache_path = ctx.job_dir / "artifacts/assistant/checkpoints.json"
        if request.get("resume_from"):
            source = within(root / "outputs/jobs", Path(request["resume_from"]))
            cached = read(source / "artifacts/assistant/checkpoints.json", {})
            self.cache = cached.get("responses", {}) if cached.get("schema") == 1 else {}
        invalid = set(request.get("retry_stages", []))
        if "lyrics" in invalid:
            invalid.update({"style", "review", "abc"})
        if "style" in invalid:
            invalid.update({"review", "abc"})
        if "review" in invalid:
            invalid.add("abc")
        self.cache = {k: v for k, v in self.cache.items() if v.get("stage", "").split("_")[0] not in invalid}
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()

    def _heartbeat(self):
        while not self.stop.wait(2):
            with self.lock:
                self.ctx.update(self.stage, result=self.result, requests=self.calls, heartbeat_at=time.time())

    def publish(self, **fields):
        with self.lock:
            self.result["fields"].update(fields)
            self.result.update(style=self.result["fields"].get("style", ""), lyrics=self.result["fields"].get("lyrics", ""), abc=self.result["fields"].get("abc", ""))
            atomic_json(self.ctx.job_dir / "artifacts/assistant/result.json", self.result)
            self.ctx.update(self.stage, result=self.result)

    def _save_cache(self):
        atomic_json(self.cache_path, {"schema": 1, "responses": self.cache})

    def invalidate(self, prefixes, *, exact=False):
        self.cache = {k: v for k, v in self.cache.items() if not any(
            v.get("stage", "") == p if exact else v.get("stage", "").startswith(p) for p in prefixes)}
        self._save_cache()

    def complete(self, stage, system, content, temperature=.6, result_key="lyrics"):
        self.ctx.check_cancelled()
        self.stage = "assistant_" + stage
        self.last_stage = stage
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(content, ensure_ascii=False)}]
        # The entire dependency input, rules, provider settings and seed bind cached responses.
        semantic_config = {k: self.config[k] for k in ("provider", "model", "think", "temperature_policy")}
        semantic_config["endpoint"] = endpoint(self.config)
        if self.model_identity is not None:
            if local_model_identity(self.root, self.config) != self.model_identity:
                raise engine.YuE2PromptError("任务期间 GGUF 文件已变化，请等待下载完成后重新运行")
            semantic_config["model_files"] = self.model_identity
        semantic_config["extra_parameters"] = {k: v for k, v in self.config["extra_parameters"].items() if k not in {"max_tokens", "max_completion_tokens"}}
        key = digest({"messages": messages, "config": semantic_config, "seed": self.values["seed"],
                      "source": engine.SOURCE_COMMIT, "engine": "standalone-v1", "temperature": temperature})
        if key in self.cache:
            self.stages.append({"stage": stage, "attempts": 0, "restored": True})
            return self.cache[key]["response"]
        if self.calls >= 8:
            raise engine.YuE2PromptError("已达到本次最多 8 次模型调用，请保留结果后单独重试失败步骤")
        self.calls += 1
        started = time.monotonic()
        with self.lock:
            self.ctx.update(self.stage, result=self.result, requests=self.calls)
        try:
            if self.transport is not None:
                data = self.transport(stage, messages, temperature, result_key)
            else:
                text = self._local(messages, temperature, result_key) if self.provider_name == "local" else self._api(messages, temperature)
                data = engine._json_object(text)
            if engine.API_KEY_PATTERN.search(json.dumps(data, ensure_ascii=False)) or (self.secret and self.secret in json.dumps(data)):
                raise engine.YuE2PromptError("响应包含凭据样式内容，未保存该响应")
            self.cache[key] = {"stage": stage, "response": data}
            self._save_cache()
            self.stages.append({"stage": stage, "attempts": 1, "seconds": round(time.monotonic() - started, 3)})
            return data
        except Cancelled:
            raise
        except Exception as exc:
            self.stages.append({"stage": stage, "attempts": 1, "status": "failed", "seconds": round(time.monotonic() - started, 3)})
            if isinstance(exc, engine.YuE2PromptError):
                raise
            raise engine.YuE2PromptError(f"{stage} 未完成（{type(exc).__name__}），已保留前序结果") from None

    def _api(self, messages, temperature):
        import requests
        from .assistant_rules.provider_transport import request_chat_completion, ChatCompletionTruncatedError
        if self.session is None:
            self.session = requests.Session()
            self.session.trust_env = False
        url = endpoint(self.config)
        payload = apply_chat_request_options({"model": self.model, "messages": messages, "stream": self.config["stream"]},
                                            chat_url=url, temperature=temperature, options=self.config)
        if "max_tokens" not in payload and "max_completion_tokens" not in payload:
            payload["max_tokens"] = self.config["max_tokens"]
        payload.setdefault("seed", self.values["seed"] % (2**31))
        def error(message):
            return engine.YuE2PromptError(message)
        def http_error(response, attempt):
            raise error(f"模型服务返回 HTTP {response.status_code}；未自动重发，请检查渠道设置")
        def checkpoint(text, done, response_id):
            self.ctx.check_cancelled()
            with self.lock:
                self.ctx.update(self.stage, result=self.result, received_characters=len(text), heartbeat_at=time.time())
        try:
            result = request_chat_completion(session=self.session, url=url, api_key=self.secret, payload=payload,
                timeout=(15, 900), retry_delays=(), retryable_status_codes=frozenset(),
                route_kwargs=lambda *_: {"allow_redirects": False}, is_retryable_network_error=lambda _: False,
                sleep=time.sleep, network_error=lambda *_: error("连接中断或超时，远端结果与计费状态未知；未自动重发"),
                http_error=http_error, invalid_json_error=lambda: error("渠道响应不是有效 JSON"),
                missing_content_error=lambda: error("渠道未返回完整正文"), empty_content_error=lambda: error("渠道仅返回思考或空内容"),
                on_checkpoint=checkpoint, require_complete=True)
            return result.text
        except ChatCompletionTruncatedError:
            raise error("输出被截断或拦截，未将半段内容当作完成；请调整 Token 上限") from None

    def _load_local(self):
        from llama_cpp import Llama, llama_supports_gpu_offload
        from .llm_runtime import initialize_backends
        initialize_backends()
        model = within(llm_directory(self.root, self.config), llm_directory(self.root, self.config) / self.model)
        match = re.search(r"-00001-of-(\d{5})\.gguf$", model.name)
        if match:
            for i in range(1, int(match[1]) + 1):
                sibling = model.with_name(model.name.replace("-00001-of-", f"-{i:05d}-of-"))
                if not sibling.is_file():
                    raise engine.YuE2PromptError(f"GGUF 分片缺失：{sibling.name}")
        if self.config["gpu_layers"] != 0 and not llama_supports_gpu_offload():
            raise engine.YuE2PromptError("当前轮子没有 GPU offload 支持；可显式改为 CPU，或安装匹配的 CUDA 轮子")
        options = {"model_path": str(model), "n_ctx": self.config["context_size"], "n_gpu_layers": self.config["gpu_layers"],
                   "n_threads": self.config["threads"], "n_batch": 256, "verbose": True}
        if "chat_template_kwargs" in inspect.signature(Llama).parameters:
            options["chat_template_kwargs"] = {"enable_thinking": self.config["think"], "preserve_thinking": False}
        try:
            self.llm = Llama(**options)
        except Exception as exc:
            raise engine.YuE2PromptError(f"GGUF 加载失败（{type(exc).__name__}）；请检查架构/分片/显存，降低 GPU 层数或上下文后重试") from None
        meta = self.llm.metadata
        if not meta.get("tokenizer.chat_template"):
            raise engine.YuE2PromptError("模型缺少 chat template，不能猜测聊天格式；请选择带模板的 GGUF")
        arch = meta.get("general.architecture", "")
        trained = int(meta.get(f"{arch}.context_length", self.config["context_size"]))
        if self.config["context_size"] > trained:
            raise engine.YuE2PromptError(f"所选模型训练上下文为 {trained}，请降低上下文设置")
        original = self.llm.create_completion
        def bounded_completion(*args, **kwargs):
            prompt = kwargs.get("prompt", args[0] if args else "")
            tokens = self.llm.tokenize(prompt.encode(), special=True) if isinstance(prompt, str) else prompt
            available = self.config["context_size"] - len(tokens) - 32
            if available < self.config["max_tokens"]:
                raise engine.YuE2PromptError(f"上下文不足：提示 {len(tokens)} + 输出 {self.config['max_tokens']} + 余量 32 超过 {self.config['context_size']}；未截断原文")
            return original(*args, **kwargs)
        self.llm.create_completion = bounded_completion
        self.result["runtime"] = {"gpu_offload_supported": bool(llama_supports_gpu_offload()), "gpu_layers": self.config["gpu_layers"],
                                  "architecture": arch, "context_size": self.config["context_size"]}

    def _local(self, messages, temperature, result_key=None):
        from .assistant_rules.provider_transport import strip_inline_reasoning
        if self.llm is None:
            self._load_local()
        response_format = {"type": "json_object"}
        if result_key in {"lyrics", "style", "abc"}:
            response_format["schema"] = {"type": "object", "properties": {
                result_key: {"type": "string", "minLength": 1}}, "required": [result_key], "additionalProperties": False}
        iterator = self.llm.create_chat_completion(messages=messages, temperature=temperature,
                    max_tokens=self.config["max_tokens"], seed=self.values["seed"] % (2**31), stream=True,
                    response_format=response_format)
        text, finish = "", None
        for chunk in iterator:
            self.ctx.check_cancelled()
            for choice in chunk.get("choices", []):
                text += choice.get("delta", {}).get("content") or ""
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        if finish != "stop":
            raise engine.YuE2PromptError("本地模型输出未完整结束；请调整输出上限，未保存坏谱")
        text = strip_inline_reasoning(text)
        if not text.strip():
            raise engine.YuE2PromptError("本地模型没有最终正文；关闭思考或调整输出上限")
        return text

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        if self.llm is not None:
            self.llm.close()
            self.llm = None
        if self.session is not None:
            self.session.close()
        self.secret = ""


def execute(root, ctx, request, secret="", transport=None):
    runner = Runner(root, ctx, request, secret, transport)
    try:
        if request.get("test_connection"):
            answer = runner.complete("connection", 'Return only JSON {"ok":true}.', {"test": "connection"}, result_key=None)
            if answer.get("ok") is not True:
                raise engine.YuE2PromptError("模型连通，但没有正确返回测试 JSON")
            runner.result.update(outcome="success", connection=True, message="模型请求成功")
        else:
            style, lyrics, abc, request_json, report_json = engine.enhance_yue2_prompt(runner=runner, **request["values"])
            report = json.loads(report_json)
            abc_report = report["abc"]
            if abc_report.get("status") == "failed":
                runner.invalidate(["abc"])
            runner.result.update(outcome=report["status"], report=report, request=json.loads(request_json),
                                 abc_status="validated" if abc else "failed" if abc_report.get("status") == "failed" else abc_report.get("source", "not_requested"))
            runner.publish(style=style, lyrics=lyrics, abc=abc)
        runner.result["stages"] = runner.stages
        runner.result["requests"] = runner.calls
        runner.close()
        atomic_json(ctx.job_dir / "artifacts/assistant/result.json", runner.result)
        ctx.finish(result=runner.result)
        return runner.result
    except BaseException:
        if runner.last_stage:
            runner.invalidate([runner.last_stage], exact=True)
        raise
    finally:
        runner.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    ctx = JobContext(args.job_dir)
    try:
        request = normalize_request(args.root, read(args.job_dir / "job.json", {})["request"])
        envelope = json.loads(sys.stdin.readline())
        execute(args.root, ctx, request, str(envelope.get("secret", "")))
        return 0
    except BaseException as exc:
        cancelled = isinstance(exc, (Cancelled, KeyboardInterrupt))
        message = str(exc) if isinstance(exc, (engine.YuE2PromptError, ValueError, Cancelled)) else f"助手任务失败（{type(exc).__name__}）"
        message = engine.API_KEY_PATTERN.sub("[已隐藏]", message)
        message = re.sub(r"https?://\S+", "[渠道地址]", message)[:1400]
        status = read(ctx.status_path, {})
        status.update(status="cancelled" if cancelled else "failed", failed_stage=status.get("stage"), stage="cancelled" if cancelled else "failed", error=message, finished_at=time.time())
        atomic_json(ctx.status_path, status)
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
