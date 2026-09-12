import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.yue2_app import assistant_data as data
from app.yue2_app.assistant_rules import engine
from app.yue2_app.assistant_worker import Runner, execute
from app.yue2_app.worker_common import JobContext


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def request(self, **values):
        return data.normalize_request(self.root, {"values": {"music_idea": "A warm song about home", "lyrics_language": "English", **values},
            "config": {"provider": "compatible", "base_url": "http://127.0.0.1:9999/v1", "model": "fixture"}})

    def context(self, number=1):
        path = self.root / f"outputs/jobs/20260911-000000-{number:08d}"
        path.mkdir(parents=True)
        (path / "status.json").write_text(json.dumps({"id": path.name, "kind": "assistant", "status": "running"}))
        return JobContext(path)

    def test_official_snapshot_and_no_comfy_import(self):
        self.assertEqual(engine.official_snapshot()["commit"], engine.SOURCE_COMMIT)
        self.assertNotIn("from comfy", Path(engine.__file__).read_text(encoding="utf-8"))

    def test_local_completion_uses_field_schema_and_keeps_general_json_stages(self):
        # Exercise complete -> _local, so losing result_key at the call site fails
        # this test. Inject an already-loaded Llama mock; no runtime or weights.
        cases = [("lyrics", "lyrics", {"lyrics": "[Verse]\nCome home"}),
                 ("style", "style", {"style": "English folk, warm guitar"}),
                 ("abc", "abc", {"abc": "X:1\nT:"}),
                 ("review", None, {"scores": {}, "revision_needed": False}),
                 ("connection", None, {"ok": True})]
        for index, (stage, result_key, answer) in enumerate(cases, 100):
            with self.subTest(stage=stage):
                request = self.request()
                request["config"]["provider"] = "local"
                llm = Mock()
                raw = json.dumps(answer)
                split = len(raw) // 2
                llm.create_chat_completion.return_value = iter([
                    {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
                    {"choices": [{"delta": {"content": raw[:split]}, "finish_reason": None}]},
                    {"choices": [{"delta": {"content": raw[split:]}, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ])
                with patch("app.yue2_app.assistant_worker.local_model_identity", return_value=[]), \
                     patch.object(Runner, "_load_local", side_effect=AssertionError("must not load weights")):
                    runner = Runner(self.root, self.context(index), request)
                    runner.llm = llm
                    try:
                        result = runner.complete(stage, "Fixture system", {"brief": stage}, result_key=result_key)
                        self.assertEqual(result, answer)
                        options = llm.create_chat_completion.call_args.kwargs
                        self.assertTrue(options["stream"])
                        response_format = options["response_format"]
                        if result_key:
                            schema = response_format["schema"]
                            self.assertEqual(response_format["type"], "json_object")
                            self.assertEqual(schema["type"], "object")
                            self.assertEqual(schema["required"], [result_key])
                            self.assertIs(schema["additionalProperties"], False)
                            self.assertEqual(schema["properties"], {
                                result_key: {"type": "string", "minLength": 1}})
                        else:
                            self.assertEqual(response_format, {"type": "json_object"})
                        self.assertEqual(len(runner.cache), 1)
                    finally:
                        runner.close()
                    llm.close.assert_called_once()

    def test_local_valid_json_without_complete_stop_is_not_cached(self):
        # A syntactically complete JSON body may still be a truncated generation;
        # only a terminal stop permits it to become a resumable stage result.
        for index, finish in enumerate((None, "length", "content_filter"), 200):
            with self.subTest(finish=finish):
                request = self.request()
                request["config"]["provider"] = "local"
                llm = Mock()
                llm.create_chat_completion.return_value = iter([
                    {"choices": [{"delta": {"content": '{"style":"English folk"}'},
                                  "finish_reason": finish}]},
                ])
                with patch("app.yue2_app.assistant_worker.local_model_identity", return_value=[]), \
                     patch.object(Runner, "_load_local", side_effect=AssertionError("must not load weights")):
                    runner = Runner(self.root, self.context(index), request)
                    runner.llm = llm
                    try:
                        with self.assertRaisesRegex(engine.YuE2PromptError, "未完整结束"):
                            runner.complete("style", "Fixture system", {}, result_key="style")
                        self.assertEqual(runner.cache, {})
                        self.assertFalse(runner.cache_path.exists())
                        self.assertEqual(runner.stages[-1]["status"], "failed")
                    finally:
                        runner.close()

    def test_preserve_crlf_and_style_only_no_extra_paid_calls(self):
        lyrics = "[Verse]\r\nHome is where I go\r\n\r\n[Chorus]\r\nKeep the light aglow\r\n"
        calls = []
        def transport(stage, *args):
            calls.append(stage)
            return {"style": "English folk, warm guitar and gentle drums"}
        result = execute(self.root, self.context(), self.request(lyrics=lyrics, lyrics_mode=engine.PRESERVE), transport=transport)
        self.assertEqual(result["lyrics"], lyrics)
        self.assertEqual(calls, ["style"])
        self.assertEqual(result["abc_status"], "downstream_yue2")

    def test_bad_abc_keeps_text_and_retry_does_not_rebill_style(self):
        request = self.request(lyrics="[Verse]\nCome home to me", lyrics_mode=engine.PRESERVE, abc_source=engine.ABC_GENERATE)
        calls = []
        def transport(stage, *args):
            calls.append(stage)
            return {"style": "English folk, warm guitar"} if stage == "style" else {"abc": "bad score"}
        ctx = self.context()
        result = execute(self.root, ctx, request, transport=transport)
        self.assertEqual(result["outcome"], "partial_success")
        self.assertTrue(result["style"])
        self.assertEqual(result["abc"], "")
        self.assertEqual(calls, ["style", "abc", "abc_repair"])
        calls.clear()
        request["resume_from"] = str(ctx.job_dir)
        execute(self.root, self.context(2), request, transport=transport)
        self.assertEqual(calls, ["abc", "abc_repair"])

    def test_style_failure_keeps_valid_lyrics_and_resume_reuses_lyrics(self):
        request, ctx, calls = self.request(lyrics_mode=engine.GENERATE), self.context(), []
        def transport(stage, *args):
            calls.append(stage)
            if stage == "style":
                raise engine.YuE2PromptError("fixture failure")
            return {"lyrics": "[Verse]\nThe light will lead me home"}
        with self.assertRaises(engine.YuE2PromptError):
            execute(self.root, ctx, request, transport=transport)
        saved = json.loads((ctx.job_dir / "artifacts/assistant/result.json").read_text(encoding="utf-8"))
        self.assertIn("light", saved["lyrics"])
        request["resume_from"] = str(ctx.job_dir)
        calls.clear()
        execute(self.root, self.context(2), request, transport=lambda stage, *args: calls.append(stage) or {"style": "English folk"})
        self.assertEqual(calls, ["style"])

    def test_changed_inputs_do_not_reuse_old_style(self):
        request, ctx = self.request(lyrics="[Verse]\nCome home"), self.context()
        execute(self.root, ctx, request, transport=lambda *args: {"style": "English folk"})
        request["resume_from"] = str(ctx.job_dir)
        request["values"]["lyrics"] = "[Verse]\nA different life"
        calls = []
        execute(self.root, self.context(2), request, transport=lambda stage, *args: calls.append(stage) or {"style": "English jazz"})
        self.assertEqual(calls, ["style"])

    def test_new_credential_and_larger_output_budget_keep_completed_stage_cache(self):
        request, ctx = self.request(lyrics="[Verse]\nCome home"), self.context()
        execute(self.root, ctx, request, transport=lambda *args: {"style": "English folk"})
        request["resume_from"] = str(ctx.job_dir)
        request["config"].update(credential_id="new-session-id", max_tokens=8192)
        calls = []
        execute(self.root, self.context(2), request, transport=lambda stage, *args: calls.append(stage) or {})
        self.assertEqual(calls, [])

    def test_local_cache_tracks_directory_and_all_shards(self):
        request, ctx = self.request(lyrics="[Verse]\nCome home"), self.context()
        directory = self.root / "llm-one"
        directory.mkdir()
        first = directory / "model-00001-of-00002.gguf"
        second = directory / "model-00002-of-00002.gguf"
        first.write_bytes(b"fixture first")
        second.write_bytes(b"fixture second")
        request["config"].update(provider="local", model=first.name, llm_directory=str(directory))
        execute(self.root, ctx, request, transport=lambda *args: {"style": "English folk"})
        request["resume_from"] = str(ctx.job_dir)
        calls = []
        execute(self.root, self.context(2), request, transport=lambda stage, *args: calls.append(stage) or {})
        self.assertEqual(calls, [])
        second.write_bytes(b"changed shard weights")
        execute(self.root, self.context(3), request, transport=lambda stage, *args: calls.append(stage) or {"style": "English jazz"})
        self.assertEqual(calls, ["style"])
        second.unlink()
        with self.assertRaisesRegex(ValueError, "分片缺失"):
            data.local_model_identity(self.root, request["config"])

    def test_catalog_reads_metadata_and_reports_missing_shards_without_loading_model(self):
        import struct
        directory = self.root / "models/LLM"
        directory.mkdir(parents=True)
        def string(value):
            raw = value.encode("utf-8")
            return struct.pack("<Q", len(raw)) + raw
        def write(path, architecture="qwen3"):
            values = {"general.architecture": architecture, "tokenizer.chat_template": "fixture chat template",
                      architecture + ".context_length": 32768}
            body = b"GGUF" + struct.pack("<IQQ", 3, 0, len(values))
            for key, value in values.items():
                body += string(key) + (struct.pack("<I", 8) + string(value) if isinstance(value, str) else struct.pack("<II", 4, value))
            path.write_bytes(body)
        write(directory / "normal.gguf")
        write(directory / "hidden-projector.gguf", "clip")
        write(directory / "split-00001-of-00002.gguf")
        info = {model["id"]: model for model in data.config_info(self.root)["models"]}
        self.assertNotIn("hidden-projector.gguf", info)
        self.assertEqual(info["normal.gguf"]["context_length"], 32768)
        self.assertEqual(info["normal.gguf"]["architecture"], "qwen3")
        self.assertEqual(info["normal.gguf"]["error"], "")
        self.assertIn("分片缺失", info["split-00001-of-00002.gguf"]["error"])

    def test_review_revision_is_saved_before_cancelled_abc(self):
        from app.yue2_app.worker_common import Cancelled
        ctx = self.context()
        def transport(stage, *args):
            if stage == "abc":
                raise Cancelled("cancel fixture")
            return {"lyrics": "[Verse]\nOld home"} if stage == "lyrics" else {"style": "English folk"} if stage == "style" else {"scores": {k: 10 for k in engine.RUBRIC}, "issues": ["Revise"], "revision_needed": True} if stage == "review" else {"lyrics": "[Verse]\nNew home"}
        with self.assertRaises(Cancelled):
            execute(self.root, ctx, self.request(lyrics_mode=engine.GENERATE, quality_mode=engine.REVIEW, abc_source=engine.ABC_GENERATE), transport=transport)
        saved = data.read(ctx.job_dir / "artifacts/assistant/result.json", {})
        self.assertEqual(saved["lyrics"], "[Verse]\nNew home")

    def test_failed_review_repair_does_not_repeat_successful_paid_review(self):
        request = self.request(lyrics="[Verse]\nCome home", lyrics_mode=engine.PRESERVE, quality_mode=engine.REVIEW)
        ctx = self.context()
        def transport(stage, *args):
            if stage == "review_repair":
                raise engine.YuE2PromptError("connection interrupted")
            return {"style": "English folk"} if stage == "style" else {"scores": {k: 10 for k in engine.RUBRIC}, "issues": ["Add warm guitar"], "revision_needed": True}
        with self.assertRaises(engine.YuE2PromptError):
            execute(self.root, ctx, request, transport=transport)
        request["resume_from"] = str(ctx.job_dir)
        calls = []
        result = execute(self.root, self.context(2), request,
            transport=lambda stage, *args: calls.append(stage) or {"style": "English folk, warm acoustic guitar"})
        self.assertEqual(calls, ["review_repair"])
        self.assertIn("warm acoustic guitar", result["style"])

    def test_compatible_base_url_matches_original_node(self):
        for base in ("https://example.com", "https://example.com/v1", "https://example.com/v1/chat/completions"):
            self.assertEqual(data.endpoint({"provider": "compatible", "base_url": base}), "https://example.com/v1/chat/completions")

    def test_provider_defaults_signup_links_and_model_list_routes_match_reference_node(self):
        self.assertEqual(data.PROVIDERS["seedance"]["default_model"], "bytedance/doubao-seed-evolving")
        self.assertEqual(data.PROVIDERS["workshop"]["default_model"], "gemini-3.5-flash")
        self.assertEqual(data.PROVIDERS["seedance"]["signup_url"], "https://api.seedance.nz/sign-up?aff=5f4w")
        self.assertEqual(data.PROVIDERS["workshop"]["signup_url"], "https://ai.t8star.org/register?aff=dP7j")
        self.assertEqual(data.models_endpoint({"provider": "seedance"}), "https://api.seedance.nz/v1/models")
        self.assertEqual(data.models_endpoint({"provider": "workshop"}), "https://ai.t8star.org/v1/models")
        self.assertEqual(data.models_endpoint({"provider": "compatible", "base_url": "https://example.com/api/v3"}),
                         "https://example.com/api/v3/models")
        self.assertEqual(data.normalize_config({"provider": "workshop", "model": ""})["model"], "gemini-3.5-flash")

    def test_remote_model_list_is_bounded_deduplicated_and_does_not_follow_redirects(self):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [json.dumps({"data": [
            {"id": "model-b"}, {"id": "model-a"}, {"id": "model-b"}, {"missing": "id"}, "model-c"]}).encode()]
        session = Mock()
        session.get.return_value = response
        result = data.fetch_remote_models({"provider": "compatible", "base_url": "http://127.0.0.1:9999/v1",
                                           "model": "fixture"}, "fixture-secret", session=session)
        self.assertEqual(result["models"], ["model-b", "model-a", "model-c"])
        call = session.get.call_args
        self.assertEqual(call.args[0], "http://127.0.0.1:9999/v1/models")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer fixture-secret")
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertTrue(call.kwargs["stream"])

    def test_remote_model_list_rejects_errors_without_exposing_response_body(self):
        response = Mock(status_code=401)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        session = Mock()
        session.get.return_value = response
        with self.assertRaisesRegex(ValueError, "HTTP 401") as captured:
            data.fetch_remote_models({"provider": "seedance"}, "fixture-secret", session=session)
        self.assertNotIn("fixture-secret", str(captured.exception))
        self.assertNotIn("upstream", str(captured.exception))

    def test_retry_score_uses_manually_edited_text_even_original_mode_generate(self):
        request = self.request(lyrics_mode=engine.GENERATE, abc_source=engine.ABC_GENERATE)
        request["final_fields"] = {"lyrics": "[Verse]\nMy edited words", "style": "English folk, edited guitar", "instrumental": False}
        calls = []
        def transport(stage, messages, *args):
            calls.append(stage)
            content = json.loads(messages[1]["content"])
            self.assertEqual(content["final_lyrics"], "[Verse]\nMy edited words")
            self.assertEqual(content["final_style"], "English folk, edited guitar")
            return {"abc": "bad fixture"}
        result = execute(self.root, self.context(), request, transport=transport)
        self.assertEqual(calls, ["abc", "abc_repair"])
        self.assertEqual(result["lyrics"], request["final_fields"]["lyrics"])

    def test_instrumental_empty_lyrics(self):
        result = execute(self.root, self.context(), self.request(lyrics_mode=engine.INSTRUMENTAL), transport=lambda *args: {"style": "Instrumental piano, no vocals"})
        self.assertEqual(result["lyrics"], "")
        self.assertTrue(result["fields"]["instrumental"])

    def test_draft_compare_and_swap_and_secret_rejection(self):
        draft = {"panel": "plan", "revision": 0, "draft": {"abc": "text"}}
        saved = data.save_draft(self.root, draft)
        self.assertEqual(saved["revision"], 1)
        with self.assertRaisesRegex(ValueError, "其他页面"):
            data.save_draft(self.root, draft)
        with self.assertRaisesRegex(ValueError, "凭据"):
            data.save_draft(self.root, {"panel": "create", "revision": 0, "draft": {"api_key": "dummy"}})

    def test_unknown_draft_schema_is_not_applied_or_overwritten(self):
        path = self.root / "userdata/assistant/drafts/plan.json"
        path.parent.mkdir(parents=True)
        original = json.dumps({"schema": 999, "revision": 4, "draft": {"abc": "future format"}}).encode()
        path.write_bytes(original)
        result = data.drafts(self.root)
        self.assertEqual(result["plan"]["draft"], {})
        self.assertIn("error", result["plan"])
        self.assertNotIn("error", result["create"])
        with self.assertRaisesRegex(ValueError, "版本不支持"):
            data.save_draft(self.root, {"panel": "plan", "revision": 0, "draft": {}})
        self.assertEqual(path.read_bytes(), original)

    def test_api_capability_does_not_require_music_or_llm(self):
        info = data.config_info(self.root)
        self.assertFalse(info["local_runtime"])
        self.assertEqual(self.request()["config"]["provider"], "compatible")

    def test_replayed_assistant_submission_after_completion_is_not_rebilled(self):
        import queue
        import threading
        from app.yue2_app import service
        from app.yue2_app.io import atomic_json
        store = service.JobStore.__new__(service.JobStore)
        store.updating = False
        store.lock, store.storage_lock = threading.RLock(), threading.RLock()
        store.jobs, store.pending = {}, queue.Queue()
        with patch.object(service, "ROOT", self.root), patch.object(service, "OUTPUTS", self.root / "outputs/jobs"), \
             patch.object(service, "runtime_ready", return_value={"capabilities": {}}):
            request = self.request()
            first = store.create("assistant", request, client_request_id="one-click")
            status = dict(store.jobs[first["id"]], status="complete", stage="complete")
            store.jobs[first["id"]] = status
            atomic_json(service.job_directory(first["id"]) / "status.json", status)
            again = store.create("assistant", request, client_request_id="one-click")
            self.assertEqual(again["id"], first["id"])
            self.assertTrue(again["deduplicated"])
            self.assertEqual(store.pending.qsize(), 1)
            fresh = store.create("assistant", request, client_request_id="another-click")
            self.assertNotEqual(fresh["id"], first["id"])

    def test_secret_config_rejected_and_endpoint_no_credential_redirect(self):
        with self.assertRaises(ValueError):
            data.normalize_config({"api_key": "dummy"})
        for url in ("https://user:password@example.com", "https://example.com?key=x", "http://example.com/v1"):
            with self.assertRaises(ValueError):
                data.normalize_config({"provider": "compatible", "base_url": url})

    def test_dpapi_and_session_credentials_bound_to_endpoint(self):
        store = data.Credentials(self.root)
        ident = store.put("fixture-secret", "https://example.com/v1", remember=True)
        self.assertNotIn("fixture-secret", store.path.read_text())
        second = data.Credentials(self.root)
        self.assertEqual(second.get(ident, "https://example.com/v1"), "fixture-secret")
        with self.assertRaises(ValueError):
            second.get(ident, "https://elsewhere.com/v1")
        second.delete(ident)
        with self.assertRaises(ValueError):
            second.get(ident, "https://example.com/v1")
        ident = store.put("session-only", "https://example.com/v1")
        with self.assertRaises(ValueError):
            second.get(ident, "https://example.com/v1")

    def test_edit_only_second_repeated_chorus(self):
        original = "[Verse]\nOpening line\n\n[Chorus]\nFirst hook\n\n[Chorus]\nSecond hook\n\n[Outro]\nGoodbye"
        request = self.request(lyrics_mode=engine.EDIT, lyrics=original, edit_section="Chorus", edit_occurrence=2, edit_request="Give it hope")
        result = execute(self.root, self.context(), request, transport=lambda stage, *args: {"lyrics": "We will find our way"} if stage == "lyrics" else {"style": "English folk"})
        self.assertIn("First hook", result["lyrics"])
        self.assertNotIn("Second hook", result["lyrics"])
        self.assertTrue(result["lyrics"].endswith("[Outro]\nGoodbye"))


if __name__ == "__main__":
    unittest.main()
