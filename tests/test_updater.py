import hashlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.yue2_app import updater
from app.yue2_app import service


def manifest(version="1.2.2", digest="a" * 64):
    tag = "v" + version
    asset = f"Comfyui-YuE2-T8-{tag}-code.zip"
    return {
        "schema": 1, "channel": "stable", "version": version, "tag": tag,
        "published_at": "2026-09-12", "asset": asset,
        "download_url": f"https://github.com/T8mars/Comfyui-YuE2-T8/releases/download/{tag}/{asset}",
        "sha256": digest, "archive_root": f"Comfyui-YuE2-T8-{tag}",
    }


class UpdaterTests(unittest.TestCase):
    def test_manifest_is_bound_to_project_version_and_asset(self):
        valid = updater.validate_manifest(manifest())
        self.assertEqual(valid["version"], "1.2.2")
        for key, value in (
            ("download_url", "https://example.com/evil.zip"),
            ("archive_root", "../outside"),
            ("sha256", "short"),
        ):
            changed = manifest()
            changed[key] = value
            with self.assertRaises(ValueError):
                updater.validate_manifest(changed)

    def test_check_reports_only_strictly_newer_versions(self):
        with patch.object(updater, "fetch_manifest", return_value=manifest("1.2.2")):
            info, _ = updater.check_update("1.2.1")
            self.assertTrue(info["update_available"])
            self.assertEqual(info["latest_version"], "1.2.2")
        with patch.object(updater, "fetch_manifest", return_value=manifest("1.2.1")):
            info, _ = updater.check_update("1.2.1")
            self.assertFalse(info["update_available"])

    def test_prepare_verifies_hash_and_extracts_one_safe_root(self):
        version = "1.2.2"
        root_name = f"Comfyui-YuE2-T8-v{version}"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(root_name + "/pyproject.toml", f'[project]\nname="yue2-t8"\nversion = "{version}"\n')
            archive.writestr(root_name + "/app/yue2_app/__init__.py", f'__version__ = "{version}"\n')
            archive.writestr(root_name + "/app/yue2_app/service.py", "# service\n")
            archive.writestr(root_name + "/app/yue2_app/updater.py", "# updater\n")
            archive.writestr(root_name + "/scripts/apply_update.py", "# helper\n")
        content = buffer.getvalue()
        data = manifest(version, hashlib.sha256(content).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(updater, "fetch_manifest", return_value=data), patch.object(
                updater, "_read_url", return_value=content
            ):
                prepared = updater.prepare_update(root, "1.2.1")
            source = Path(prepared["source"])
            self.assertEqual((source / "pyproject.toml").read_text().splitlines()[-1], f'version = "{version}"')
            self.assertEqual(json.loads(Path(prepared["manifest"]).read_text())["sha256"], data["sha256"])

    def test_prepare_rejects_archive_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            updater, "fetch_manifest", return_value=manifest()
        ), patch.object(updater, "_read_url", return_value=b"wrong"):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                updater.prepare_update(Path(directory), "1.2.1")

    def test_http_update_routes_block_active_jobs_then_schedule_install(self):
        class Store:
            busy = True

            def state(self):
                return {"current_job": {"id": "active"} if self.busy else None, "queued": 0}

        previous_store = service.STORE
        store = Store()
        service.STORE = store
        with tempfile.TemporaryDirectory() as log_directory, patch.object(service, "LOGS", Path(log_directory)):
            server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"
            try:
                with patch.object(service.updater, "check_update", return_value=(
                    {"current_version": "1.2.2", "latest_version": "1.2.3", "update_available": True}, {}
                )):
                    with urllib.request.urlopen(origin + "/api/update/check", timeout=3) as response:
                        self.assertTrue(json.load(response)["update_available"])
                request = urllib.request.Request(origin + "/api/update/install", data=b"{}", method="POST",
                                                 headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(rejected.exception.code, 400)
                self.assertIn("任务正在运行", rejected.exception.read().decode("utf-8"))
                store.busy = False
                prepared = {"current_version": "1.2.2", "latest_version": "1.2.3",
                            "source": "staged", "manifest": "manifest"}
                with patch.object(service.updater, "prepare_update", return_value=prepared), patch.object(
                    service.updater, "launch_update", return_value={"accepted": True, "target_version": "1.2.3"}
                ) as launch:
                    with urllib.request.urlopen(request, timeout=3) as response:
                        result = json.load(response)
                    self.assertTrue(result["accepted"])
                    self.assertEqual(result["target_version"], "1.2.3")
                    launch.assert_called_once()
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()
                service.STORE = previous_store


if __name__ == "__main__":
    unittest.main()
