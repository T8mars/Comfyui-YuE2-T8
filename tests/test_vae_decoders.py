import hashlib, importlib.util, json, os, queue, struct, sys, tempfile, threading, types, unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.yue2_app import core_worker, service, vae_decoders

ROOT = Path(__file__).resolve().parents[1]
V1 = "YuE2-VAE-RS-x3_v1.safetensors"
V1_FULL = "YuE2-VAE-RS-x3_v1-full.safetensors"


def safetensors_file(path: Path, tensors: dict, metadata: dict | None = None) -> bytes:
    """A small header-only safetensors file with zeroed data, enough for the header diagnosis."""
    header, offset = {}, 0
    for name, (dtype, count) in tensors.items():
        size = count * (2 if dtype in {"F16", "BF16"} else 4)
        header[name] = {"dtype": dtype, "shape": [count], "data_offsets": [offset, offset + size]}
        offset += size
    if metadata is not None:
        header["__metadata__"] = metadata
    raw = json.dumps(header).encode()
    data = struct.pack("<Q", len(raw)) + raw + b"\0" * offset
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def decoder_tensors(count=vae_decoders.DECODER_TENSORS, dtype="F32"):
    return {f"blocks.0.units.{index}.conv1.weight_v": (dtype, 1) for index in range(count)}


class VaeDecoderFiles(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.directory = self.root / "models" / vae_decoders.DIRECTORY

    def pin(self, filename, data):
        """Pretend a small file is the release so the success path is testable without 800 MB of weights."""
        entry = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        p = patch.dict(vae_decoders.DECODERS["rs_x3_v1"]["files"][filename], entry); p.start(); self.addCleanup(p.stop)
        return entry

    def test_vendored_loader_is_unmodified(self):
        vendor = ROOT / "vendor" / "yue2_vae_rs"
        upstream = json.loads((vendor / "UPSTREAM.json").read_text(encoding="utf-8"))
        self.assertEqual(upstream["license"], "Apache-2.0")
        for name, digest in upstream["files"].items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((vendor / name).read_bytes()).hexdigest(), digest)
        self.assertIn("Ruach Studio and Viktor Zhuromskyy", (vendor / "NOTICE").read_text(encoding="utf-8"))

    def test_choices_are_strict(self):
        self.assertEqual(vae_decoders.normalize(None), "standard")
        for name in ("standard", "rs_x3_v1", "rs_x3_v2"):
            self.assertEqual(vae_decoders.normalize(name), name)
        for value in ("", "RS_X3_V1", "x3", "rs_x3_v3", "rs_x4", 1, True, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "音频解码器"):
                vae_decoders.normalize(value)

    def test_standard_never_touches_the_model_directory(self):
        self.assertIsNone(vae_decoders.resolve(self.root / "missing", "standard"))
        self.assertIsNone(vae_decoders.resolve(self.root / "missing", None))

    def test_missing_release_names_folder_and_pinned_download(self):
        with self.assertRaises(FileNotFoundError) as caught:
            vae_decoders.resolve(self.root, "rs_x3_v1")
        message = str(caught.exception)
        self.assertIn(str(self.directory), message)
        self.assertIn(vae_decoders.download_url(V1), message)
        self.assertIn(vae_decoders.REVISION, message)
        self.assertEqual(vae_decoders.readiness(self.root), {"rs_x3_v1": False, "rs_x3_v2": False})

    def test_wrong_copies_are_refused_with_a_reason(self):
        cases = [
            ("half", decoder_tensors(dtype="F16"), {"step": "161953"}, "float32"),
            ("partial", decoder_tensors(count=417), {"step": "161953"}, "417"),
            ("swapped", decoder_tensors(), {"step": "155030"}, "rs_x3_v2 的文件"),
            ("other step", decoder_tensors(), {"step": "1"}, "训练步数 1"),
            ("standard vae", {"decoder.layers.0.weight_v": ("F32", 1)}, {}, "YuE2-Vae 目录"),
        ]
        for label, tensors, metadata, reason in cases:
            with self.subTest(label=label):
                safetensors_file(self.directory / V1, tensors, metadata)
                with self.assertRaisesRegex(ValueError, reason):
                    vae_decoders.resolve(self.root, "rs_x3_v1")
        (self.directory / V1).write_bytes(b"not a safetensors file")
        with self.assertRaisesRegex(ValueError, "safetensors"):
            vae_decoders.resolve(self.root, "rs_x3_v1")

    def test_release_resolves_and_a_changed_byte_is_refused(self):
        data = safetensors_file(self.directory / V1, decoder_tensors(), {"step": "161953"})
        entry = self.pin(V1, data)
        resolved = vae_decoders.resolve(self.root, "rs_x3_v1")
        self.assertEqual(resolved["file"], f"{vae_decoders.DIRECTORY}/{V1}")
        self.assertEqual(resolved["sha256"], entry["sha256"])
        self.assertEqual(resolved["halo_frames"], 60)
        self.assertEqual(vae_decoders.readiness(self.root)["rs_x3_v1"], True)
        changed = bytearray(data); changed[-1] = 1
        (self.directory / V1).write_bytes(bytes(changed))
        self.assertEqual(vae_decoders.resolve(self.root, "rs_x3_v1", verify=False)["sha256"], entry["sha256"])
        with self.assertRaisesRegex(ValueError, "不是发布版文件"):
            vae_decoders.resolve(self.root, "rs_x3_v1")

    def test_full_file_is_accepted_when_the_decoder_only_file_is_absent(self):
        data = safetensors_file(self.directory / V1_FULL, decoder_tensors(), {"step": "161953"})
        self.pin(V1_FULL, data)
        self.assertEqual(vae_decoders.resolve(self.root, "rs_x3_v1")["file"], f"{vae_decoders.DIRECTORY}/{V1_FULL}")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_is_refused(self):
        data = safetensors_file(self.root / "elsewhere.safetensors", decoder_tensors(), {"step": "161953"})
        self.pin(V1, data)
        self.directory.mkdir(parents=True)
        (self.directory / V1).symlink_to(self.root / "elsewhere.safetensors")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            vae_decoders.resolve(self.root, "rs_x3_v1")
        self.assertFalse(vae_decoders.readiness(self.root)["rs_x3_v1"])


class VaeDecoderRequests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); (self.root / "outputs/jobs").mkdir(parents=True)
        self.store = service.JobStore.__new__(service.JobStore)
        self.store.lock = threading.RLock(); self.store.storage_lock = threading.RLock()
        self.store.jobs = {}; self.store.updating = False; self.store.pending = queue.Queue()
        for name, value in [("ROOT", self.root), ("OUTPUTS", self.root / "outputs/jobs")]:
            p = patch.object(service, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(service, "runtime_ready", return_value={"capabilities": {"generation": True}})
        p.start(); self.addCleanup(p.stop)

    def test_invalid_or_missing_decoder_is_rejected_before_queue(self):
        for value, reason in (("bad", "音频解码器"), (7, "音频解码器"), ("rs_x3_v1", "huggingface.co")):
            for kind in ("generate", "render_plan", "decode"):
                with self.subTest(value=value, kind=kind), self.assertRaisesRegex(ValueError, reason):
                    self.store.create(kind, {"memory_budget_gib": 8, "vae_decoder": value})
        self.assertFalse(self.store.jobs)
        self.assertTrue(self.store.pending.empty())

    def test_selected_decoder_survives_queue_and_reaches_the_pipeline(self):
        data = safetensors_file(self.root / "models" / vae_decoders.DIRECTORY / V1, decoder_tensors(), {"step": "161953"})
        entry = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        pipeline = Mock()
        with patch.dict(vae_decoders.DECODERS["rs_x3_v1"]["files"][V1], entry), \
             patch.dict(sys.modules, {"yue2": types.SimpleNamespace(YuE2Pipeline=pipeline)}), \
             patch.object(core_worker, "model_paths", return_value={"model": "model", "vae": "vae"}):
            for value, expected in ((None, None), ("standard", None), ("rs_x3_v1", entry["sha256"])):
                with self.subTest(value=value):
                    request = {"memory_budget_gib": 8}
                    if value is not None:
                        request["vae_decoder"] = value
                    job = self.store.create("generate", request)
                    saved = json.loads((self.root / "outputs/jobs" / job["id"] / "job.json").read_text())["request"]
                    self.assertEqual(saved.get("vae_decoder"), value)
                    core_worker.create_pipe(self.root, saved)
                    decoder = pipeline.from_pretrained.call_args.kwargs["vae_decoder"]
                    self.assertEqual(None if decoder is None else decoder["sha256"], expected)


def load_pipeline_module():
    sys.path.insert(0, str(ROOT / "vendor"))
    from yue2 import pipeline
    return pipeline


class FakeDecoder:
    def __init__(self):
        self.halos = []

    def to(self, device):
        return self

    def decode_tiled(self, z, core_frames, halo_frames, output_device, on_progress):
        self.halos.append(halo_frames)
        import torch
        return torch.zeros(1, 2, z.shape[-1] * 1920)


class VaeDecoderPipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = load_pipeline_module()
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        pipe = self.pipeline.YuE2Pipeline.__new__(self.pipeline.YuE2Pipeline)
        import torch
        pipe.progress, pipe._model, pipe._vae, pipe.device = False, None, FakeDecoder(), torch.device("cpu")
        pipe.vae_core_frames, pipe.on_stage_progress, pipe.vae_decoder = 128, None, None
        self.pipe = pipe
        self.file = self.root / V1
        self.file.write_bytes(b"decoder")
        self.decoder = {"release": "rs_x3_v1", "path": str(self.file), "sha256": hashlib.sha256(b"decoder").hexdigest(),
                        "halo_frames": 60, "file": f"YuE2-VAE-RS/{V1}", "bytes": 7, "source": vae_decoders.SOURCE,
                        "revision": vae_decoders.REVISION}

    def test_standard_decode_keeps_its_halo(self):
        audio = self.pipe.decode([[0.0] * 64] * 300)
        self.assertEqual(self.pipe._vae.halos, [16])
        self.assertEqual(audio.shape, (300 * 1920, 2))

    def test_vae_rs_decode_reverifies_the_file_and_uses_the_exact_halo(self):
        self.pipe.vae_decoder = self.decoder
        fake = FakeDecoder()
        with patch.dict(sys.modules, {"yue2_vae_rs": types.SimpleNamespace(load_decoder=Mock(return_value=fake))}) as modules:
            self.pipe.decode([[0.0] * 64] * 300)
            self.assertEqual(fake.halos, [60])
            self.assertEqual(modules["yue2_vae_rs"].load_decoder.call_args.kwargs["allow_standard"], False)
            self.file.write_bytes(b"decoder v2")
            with self.assertRaisesRegex(ValueError, "changed after verification"):
                self.pipe.decode([[0.0] * 64] * 300)
        self.assertEqual(self.pipe._vae.halos, [])

    def test_effective_config_is_unchanged_for_standard(self):
        import torch
        (self.root / "config.json").write_text(json.dumps({"release_variant": "standard"}))
        pipe = self.pipe
        pipe.generation_config = self.pipeline.GenerationConfig()
        pipe.backend, pipe.quantization, pipe.cpu_offload_enabled, pipe.memory_budget_gib = "torch-eager", "none", False, 24.0
        pipe.offload_ar, pipe.runtime_sha256, pipe.nar_attention, pipe.nar_query_chunk_size = True, "x", "sdpa", 256
        pipe.vae_dir, pipe.device = self.root, torch.device("cpu")
        request = types.SimpleNamespace(guidance=1.0, cot="full")
        standard = pipe.effective_config(request)
        self.assertNotIn("vae_decoder", standard)
        self.assertEqual((standard["vae_halo_frames"], standard["decoder_release"]), (16, "standard"))
        pipe.vae_decoder = self.decoder
        selected = pipe.effective_config(request)
        self.assertEqual({key: value for key, value in selected.items() if key not in {"vae_decoder", "vae_halo_frames",
                          "decoder_release"}}, {key: value for key, value in standard.items() if key not in {
                          "vae_halo_frames", "decoder_release"}})
        self.assertEqual((selected["vae_halo_frames"], selected["decoder_release"]), (60, "rs_x3_v1"))
        self.assertNotIn("path", selected["vae_decoder"])


class VaeDecoderNodes(unittest.TestCase):
    def test_loader_offers_decoders_and_default_requests_are_unchanged(self):
        for relative in ("nodes.py", "comfyui_nodes/nodes.py"):
            with self.subTest(relative=relative):
                package_name = "yue2_vae_node_test_" + relative.replace("/", "_").replace(".", "_")
                package = types.ModuleType(package_name)
                package.__path__ = [str((ROOT / relative).parent)]
                with patch.dict(sys.modules, {package_name: package}):
                    spec = importlib.util.spec_from_file_location(package_name + ".nodes", ROOT / relative)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = module
                    try:
                        spec.loader.exec_module(module)
                        choices = module.YuE2ModelLoader.INPUT_TYPES()["optional"]["vae_decoder"]
                        self.assertEqual(choices, (["standard", "rs_x3_v1", "rs_x3_v2"], {"default": "standard"}))
                        model = {"backend": "torch-eager", "memory_budget_gib": 8}
                        self.assertNotIn("vae_decoder", module.base_request(model))
                        self.assertNotIn("vae_decoder", module.base_request({**model, "vae_decoder": "standard"}))
                        self.assertEqual(module.base_request({**model, "vae_decoder": "rs_x3_v2"})["vae_decoder"], "rs_x3_v2")
                        health = {"ready": {"models": {"model": True, "vae": True}, "capabilities": {"generation": True},
                                            "vae_decoders": {"rs_x3_v1": True, "rs_x3_v2": False}}}
                        with patch.object(module.client, "ensure_service", return_value=health):
                            handle, _ = module.YuE2ModelLoader().load("torch-eager", 8, True)
                            self.assertNotIn("vae_decoder", handle)
                            handle, _ = module.YuE2ModelLoader().load("torch-eager", 8, True, vae_decoder="rs_x3_v1")
                            self.assertEqual(handle["vae_decoder"], "rs_x3_v1")
                            with self.assertRaisesRegex(RuntimeError, "rs_x3_v2"):
                                module.YuE2ModelLoader().load("torch-eager", 8, True, vae_decoder="rs_x3_v2")
                    finally:
                        sys.modules.pop(spec.name, None)


@unittest.skipUnless(os.environ.get("YUE2_VAE_RS_TEST_MODELS") and os.environ.get("YUE2_VAE_RS_TEST_REFERENCE"),
                     "set YUE2_VAE_RS_TEST_MODELS (a models folder with YuE2-VAE-RS/) and YUE2_VAE_RS_TEST_REFERENCE "
                     "(VAE-RS/kit/reference/yue2_vae_rs_reference.npz) to decode the released weights")
class VaeDecoderReleasedWeights(unittest.TestCase):
    """Opt-in: the released x3 files through this pipeline, against Ruach Studio's published reference (CPU, about a minute)."""

    def test_released_decoders_match_the_published_reference(self):
        import numpy as np
        import torch
        pipeline = load_pipeline_module()
        reference = np.load(os.environ["YUE2_VAE_RS_TEST_REFERENCE"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text(json.dumps({"schema": 1, "model_directory": os.environ["YUE2_VAE_RS_TEST_MODELS"]}))
            for name, key in (("rs_x3_v1", "v1"), ("rs_x3_v2", "v2")):
                decoder = vae_decoders.resolve(root, name)
                for core in (128, 512, 1024):
                    with self.subTest(name=name, core=core):
                        pipe = pipeline.YuE2Pipeline.__new__(pipeline.YuE2Pipeline)
                        pipe.progress, pipe._model, pipe._vae, pipe.device = False, None, None, torch.device("cpu")
                        pipe.vae_core_frames, pipe.on_stage_progress, pipe.vae_decoder = core, None, decoder
                        audio = pipe.decode(reference["latents"])
                        self.assertEqual(audio.shape, (1600 * 1920, 2))
                        for excerpt, offset in zip(reference[f"{key}_excerpts"], reference["offsets"]):
                            expected = np.clip(excerpt.T, -1, 1)
                            self.assertLess(float(np.abs(audio[offset:offset + 4096] - expected).max()), 1e-4)


if __name__ == "__main__":
    unittest.main()
