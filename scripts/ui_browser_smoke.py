"""Run layout, interaction and accessibility smoke checks in a real browser."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import Page, expect, sync_playwright

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.yue2_app.asset_library import AssetLibrary
from app.yue2_app.io import atomic_json


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_service(url: str, process: subprocess.Popen, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"UI smoke service exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("UI smoke service did not become ready")


def assert_no_page_overflow(page: Page, viewport: str) -> None:
    widths = page.evaluate("""() => ({page: document.documentElement.scrollWidth, viewport: innerWidth})""")
    assert widths["page"] <= widths["viewport"] + 1, f"{viewport} page overflows horizontally: {widths}"


def assert_named_controls(page: Page) -> None:
    failures = page.evaluate(r"""() => {
      const visible = element => !element.closest('details:not([open])') && Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
      const name = element => {
        const labels = element.labels ? [...element.labels].map(label => label.innerText.trim()).filter(Boolean) : [];
        const labelled = (element.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
          .map(id => document.getElementById(id)?.innerText?.trim() || '').filter(Boolean);
        return (element.getAttribute('aria-label') || labelled.join(' ') || element.getAttribute('title') || labels.join(' ') || element.innerText || '').trim();
      };
      return [...document.querySelectorAll('button,input:not([type="hidden"]),select,textarea,audio')]
        .filter(visible).filter(element => !name(element))
        .map(element => `${element.tagName.toLowerCase()}#${element.id || '(no-id)'}`);
    }""")
    assert not failures, f"Visible controls without accessible names: {failures}"


def assert_text_contrast(page: Page, panel_id: str) -> None:
    failures = page.evaluate(r"""panelId => {
      const panel = document.getElementById(panelId);
      const visible = element => !element.closest('details:not([open])')
        && Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length)
        && getComputedStyle(element).visibility !== 'hidden';
      const rgba = value => {
        const match = value.match(/[\d.]+/g);
        return match ? [+match[0], +match[1], +match[2], match[3] === undefined ? 1 : +match[3]] : null;
      };
      const luminance = color => {
        const values = color.slice(0, 3).map(value => {
          value /= 255;
          return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
        });
        return .2126 * values[0] + .7152 * values[1] + .0722 * values[2];
      };
      const ratio = (foreground, background) => {
        const first = luminance(foreground), second = luminance(background);
        return (Math.max(first, second) + .05) / (Math.min(first, second) + .05);
      };
      const composite = (top, bottom) => {
        const alpha = top[3] + bottom[3] * (1 - top[3]);
        return [
          (top[0] * top[3] + bottom[0] * bottom[3] * (1 - top[3])) / alpha,
          (top[1] * top[3] + bottom[1] * bottom[3] * (1 - top[3])) / alpha,
          (top[2] * top[3] + bottom[2] * bottom[3] * (1 - top[3])) / alpha,
          alpha,
        ];
      };
      const background = element => {
        if (!element) return [255, 255, 255, 1];
        const below = background(element.parentElement), own = rgba(getComputedStyle(element).backgroundColor);
        return own && own[3] > 0 ? composite(own, below) : below;
      };
      const backgrounds = element => {
        const base = background(element);
        const colors = (getComputedStyle(element).backgroundImage.match(/rgba?\([^)]*\)/g) || [])
          .map(rgba).filter(Boolean);
        return colors.length ? colors.map(color => composite(color, base)) : [base];
      };
      const candidates = [...panel.querySelectorAll('h1,h2,h3,h4,p,small,b,span,label,summary,button,a,option,input,textarea,select')]
        .filter(visible)
        .filter(element => {
          if (element.disabled || Number(getComputedStyle(element).opacity) < .9) return false;
          if (['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)) return true;
          return (element.innerText || '').trim()
            && ![...element.children].some(child => visible(child) && (child.innerText || '').trim());
        });
      return candidates.map(element => {
        const style = getComputedStyle(element), foreground = rgba(style.color);
        const fontSize = parseFloat(style.fontSize), weight = parseInt(style.fontWeight) || 400;
        const required = fontSize >= 24 || (fontSize >= 18.66 && weight >= 700) ? 3 : 4.5;
        const actual = Math.min(...backgrounds(element).map(value => ratio(foreground, value)));
        return {tag: element.tagName, id: element.id || '', text: (element.innerText || element.value || '').trim().slice(0, 60), actual, required};
      }).filter(item => item.actual + .01 < item.required);
    }""", panel_id)
    assert not failures, f"Text contrast below WCAG AA in #{panel_id}: {failures}"


def assert_unique_ids(page: Page) -> None:
    duplicates = page.evaluate("""() => {
      const ids = [...document.querySelectorAll('[id]')].map(element => element.id);
      return [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
    }""")
    assert not duplicates, f"Duplicate DOM ids: {duplicates}"


def section_top(page: Page, selector: str) -> float:
    box = page.locator(selector).bounding_box()
    assert box, f"Missing visible section: {selector}"
    return float(box["y"])


def wait_for_ui(page: Page) -> None:
    page.locator("#health-title").wait_for(state="visible")
    page.wait_for_function("document.querySelector('#health-title').textContent !== '正在检查运行环境'")
    page.evaluate("() => window.assistantReady")
    page.evaluate("() => window.workbenchReady")
    assert page.locator("#assistant-config-form").get_attribute("aria-busy") == "false"
    assert page.locator("#assistant-form").get_attribute("aria-busy") == "false"
    page.locator("#model-settings").evaluate("element => { element.open = false; }")


def seed_browser_state(root: Path) -> None:
    library = AssetLibrary(root)
    library.create_project("空项目")
    archived = library.create_project("已归档回归项目")
    library.update_project(archived["id"], status="archived")
    project = library.create_project("浏览器回归项目")
    for index, kind in enumerate(("lyrics", "style", "score", "lyrics"), start=1):
        asset = library.create_text(kind=kind, title=f"回归素材 {index}", text=f"浏览器回归内容 {index}")
        if index <= 2:
            library.add_to_project(project["id"], asset["id"])
    training_songs = []
    for index in range(10):
        source = root / f"training-song-{index + 1}.wav"
        with wave.open(str(source), "wb") as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
            stream.writeframes((b"\0\0" if index == 0 else b"\1\0") * 48000)
        asset = library.import_file(source, kind="song", title=f"训练歌曲 {index + 1}")
        library.add_to_project(project["id"], asset["id"], role="song")
        training_songs.append(asset)
        source.unlink()

    snapshot = library.create_snapshot(
        title="浏览器模型分页快照", training_kind="yue2_style",
        items=[{"asset_id": asset["id"], "revision_id": asset["current_revision_id"],
                "start": 0, "end": 6, "split": "train" if index == 0 else "validation",
                "instrumental": True, "track_group_id": asset["id"]}
               for index, asset in enumerate(training_songs)],
        options={"rights_confirmed": True, "default_style": "browser folk"},
    )
    for index in range(4):
        model_source = root / f"browser-model-{index + 1}.safetensors"
        model_source.write_bytes(f"browser-model-{index + 1}".encode())
        model = library.import_file(
            model_source, kind="model", title=f"浏览器歌曲风格 {index + 1}",
            metadata={"model_type": "yue2_ar_lora", "completed_training_steps": 200,
                      "selected_validation_step": 200, "selected_validation_loss": 4.2,
                      "rank": 8},
        )
        run = library.create_training_run(
            title=model["title"], training_kind="yue2_style", snapshot_id=snapshot["id"],
            config={"steps": 200, "selected_step": 200,
                    "history": [{"step": 200, "train_loss": 4.0, "validation_loss": 4.2}]},
        )
        library.update_training_run(run["id"], state="complete", model_asset_id=model["id"])
        model_source.unlink()

    job_id = "20990101-000000-00000001"
    directory = root / "outputs" / "jobs" / job_id
    audio = directory / "artifacts" / "audio.wav"
    audio.parent.mkdir(parents=True)
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 800)
    now = time.time()
    request = {"style": "浏览器回归", "lyrics": "测试歌词", "project_id": project["id"]}
    job = {"id": job_id, "kind": "generate", "request": request, "source": "webui",
           "result_panel": "create", "project_id": project["id"]}
    status = {**job, "status": "complete", "stage": "complete", "progress": 1.0,
              "created_at": now, "updated_at": now, "finished_at": now,
              "summary": "浏览器回归歌曲", "result": {"audio": str(audio), "audio_seconds": .1}}
    atomic_json(directory / "job.json", job)
    atomic_json(directory / "status.json", status)

    assistant_id = "20990101-000001-00000002"
    assistant_directory = root / "outputs" / "jobs" / assistant_id
    assistant_result = {
        "style": "Persistent browser folk",
        "lyrics": "[Verse]\nPersistent browser lyrics",
        "abc": "X:1\nT:Persistent browser score\nM:4/4\nL:1/4\nK:C\nCDEF|",
        "cot": "full",
        "abc_status": "validated",
        "outcome": "success",
    }
    assistant_request = {"values": {"idea": "持久化回归"}, "project_id": project["id"]}
    assistant_job = {"id": assistant_id, "kind": "assistant", "request": assistant_request,
                     "source": "webui", "result_panel": "assistant", "project_id": project["id"]}
    assistant_status = {**assistant_job, "status": "complete", "stage": "complete", "progress": 1.0,
                        "created_at": now + 1, "updated_at": now + 1, "finished_at": now + 1,
                        "summary": "持久化浏览器回归", "result": assistant_result}
    atomic_json(assistant_directory / "job.json", assistant_job)
    atomic_json(assistant_directory / "status.json", assistant_status)
    failed_id = "20990101-000002-00000003"
    failed_directory = root / "outputs" / "jobs" / failed_id
    failed_job = {"id": failed_id, "kind": "doctor", "request": {}, "source": "webui"}
    atomic_json(failed_directory / "job.json", failed_job)
    atomic_json(failed_directory / "status.json", {
        **failed_job, "status": "failed", "created_at": now + 2, "finished_at": now + 2,
        "summary": "清理浏览器回归", "error": "回归测试占位失败", "result": {},
    })
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / f"{failed_id}.log").write_text("cleanup fixture", encoding="utf-8")


def assert_assistant_model_refresh(browser, url: str, output: Path) -> None:
    """Exercise the actual settings UI and HTTP model-list route, without paid APIs."""
    requested = []
    chat_models = []
    slow_started, slow_release = threading.Event(), threading.Event()

    class ModelProvider(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append((self.path, self.headers.get("Authorization")))
            if self.path == "/slow/v1/models":
                slow_started.set()
                slow_release.wait(10)
            status = 404 if self.path == "/no-list/v1/models" else 200
            payload = {"data": [{"id": "fixture/first"}, {"id": "fixture/second"},
                                {"id": "fixture/first"}]}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            chat_models.append(body["model"])
            raw = json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    original = page.request.get(url + "/api/assistant/config").json()["config"]
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page.goto(url, wait_until="domcontentloaded")
        wait_for_ui(page)
        page.locator('.studio-sidebar [data-tab="assistant"]').click()
        page.locator("#assistant-settings").evaluate("element => element.open = true")
        page.locator("#assistant-provider").select_option("compatible")
        page.locator("#assistant-base-url").fill(base + "/v1/")
        page.locator("#assistant-key").fill("fixture-model-list-secret")
        page.locator('#assistant-config-form button[type="submit"]').click()
        page.wait_for_function("document.querySelector('#assistant-model-choice').options.length === 3", timeout=7000)
        assert requested == [("/v1/models", "Bearer fixture-model-list-secret")]
        assert page.locator("#assistant-model-choice").input_value() == "fixture/first"
        assert "已读取 2 个模型" in page.locator("#assistant-model-list-status").inner_text()
        assert page.request.get(url + "/api/assistant/config").json()["config"]["model"] == "fixture/first"
        page.wait_for_function("!document.querySelector('#assistant-test').disabled")
        assert not chat_models, "Saving settings must not send a paid chat request"
        page.locator("#assistant-settings").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-assistant-model-list.png", full_page=False)

        page.reload(wait_until="domcontentloaded")
        wait_for_ui(page)
        page.locator('.studio-sidebar [data-tab="assistant"]').click()
        page.locator("#assistant-settings").evaluate("element => element.open = true")
        assert page.locator("#assistant-model-choice").input_value() == "fixture/first"
        assert page.locator("#assistant-model-choice option").count() == 3
        assert len(requested) == 1, "Reload must restore the list without an automatic API request"

        page.locator("#assistant-model-choice").select_option("__custom__")
        assert page.locator("#assistant-test").is_disabled()
        page.locator("#assistant-model").fill("fixture/manually-entered")
        page.locator("#assistant-refresh-models").click()
        page.wait_for_function("!document.querySelector('#assistant-refresh-models').disabled && document.querySelector('#assistant-model-list-status').textContent.includes('已读取 2 个模型')")
        assert len(requested) == 2
        assert page.locator("#assistant-model").input_value() == "fixture/manually-entered"

        page.locator("#assistant-base-url").fill(base + "/no-list/v1")
        assert page.locator("#assistant-model-choice option").count() == 1
        assert page.locator("#assistant-test").is_disabled(), "A key must be bound to its API address"
        page.locator("#assistant-key").fill("fixture-model-list-secret")
        page.locator('#assistant-config-form button[type="submit"]').click()
        page.wait_for_function("document.querySelector('#assistant-model-list-status').textContent.includes('HTTP 404')")
        assert "手动" in page.locator("#assistant-model-list-status").inner_text()
        assert "设置已保存" in page.locator("#assistant-config-status").inner_text()
        assert page.locator("#assistant-custom-model-field").is_visible()
        assert page.locator("#assistant-model").input_value() == "fixture/manually-entered"
        assert page.locator("#assistant-test").is_enabled()
        page.locator("#assistant-settings").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-assistant-model-list-unavailable.png", full_page=False)

        page.locator("#assistant-base-url").fill(base + "/slow/v1")
        page.locator("#assistant-key").fill("fixture-model-list-secret")
        page.locator('#assistant-config-form button[type="submit"]').click()
        page.wait_for_function("document.querySelector('#assistant-model-list-status').textContent.includes('正在读取')")
        assert slow_started.wait(5)
        assert page.locator('#assistant-config-form button[type="submit"]').is_disabled()
        page.locator("#assistant-base-url").fill(base + "/different/v1")
        slow_release.set()
        page.wait_for_function("!document.querySelector('#assistant-refresh-models').disabled")
        assert page.locator("#assistant-model-choice option").count() == 1
        assert "已读取" not in page.locator("#assistant-model-list-status").inner_text()
        assert not chat_models, "Fetching model lists must not send any paid chat requests"
        assert not errors, errors
    finally:
        slow_release.set()
        page.request.post(url + "/api/assistant/config", data=original)
        context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def assert_manual_cleanup(browser, url: str, output: Path) -> None:
    """Exercise irreversible controls only against the temporary smoke service."""
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    prefix = "cleanup-browser-fixture"
    fixtures = []
    for index in range(25):
        response = page.request.post(url + "/api/workbench/assets/text", data={
            "kind": "lyrics", "title": f"{prefix}-{index}", "text": f"cleanup fixture {index}",
        })
        assert response.status == 201, response.text()
        fixtures.append(response.json())
    page.goto(url, wait_until="domcontentloaded")
    wait_for_ui(page)
    page.locator('.studio-sidebar [data-tab="assets"]').click()
    page.locator("#asset-query").fill(prefix)
    expect(page.locator("#asset-grid .asset-card")).to_have_count(24)
    page.locator("#asset-select-page").check()
    expect(page.locator("#asset-selection-count")).to_contain_text("已选 24 项")
    page.locator("#asset-next").click()
    expect(page.locator("#asset-grid .asset-card")).to_have_count(1)
    page.locator("#asset-select-page").check()
    expect(page.locator("#asset-selection-count")).to_contain_text("已选 25 项")
    page.locator("#asset-clear-selection").click()
    expect(page.locator("#asset-selection-count")).to_contain_text("已选 0 项")
    page.locator("#asset-select-page").check()
    page.locator("#trash-selected-assets").click()
    expect(page.locator("#asset-grid .asset-card")).to_have_count(24)
    expect(page.locator("#asset-page-status")).to_contain_text("第 1 / 1 页")
    page.locator("#asset-status").select_option("trashed")
    expect(page.locator("#asset-grid .asset-card")).to_have_count(1)
    page.locator("#asset-select-page").check()
    page.locator("#restore-selected-assets").click()
    expect(page.locator("#asset-grid .asset-card")).to_have_count(0)
    page.locator("#asset-status").select_option("active")
    expect(page.locator("#asset-grid .asset-card")).to_have_count(24)
    selected = page.locator("[data-select-asset]").evaluate_all("els => els.slice(0,2).map(el=>el.dataset.selectAsset)")
    project = page.request.post(url + "/api/workbench/projects", data={"title": "Cleanup protected project"}).json()
    response = page.request.post(url + f'/api/workbench/projects/{project["id"]}/assets', data={"asset_id": selected[0]})
    assert response.status == 200, response.text()
    for asset_id in selected:
        page.locator(f'[data-select-asset="{asset_id}"]').check()
    page.locator("#trash-selected-assets").click()
    expect(page.locator("#asset-cleanup-status")).to_contain_text("已移入回收站 2 项")
    page.locator("#asset-status").select_option("trashed")
    expect(page.locator("#asset-grid .asset-card")).to_have_count(2)
    # Restore one item independently, then verify protected-project confirmation.
    page.locator(f'[data-restore-asset="{selected[1]}"]').click()
    expect(page.locator("#asset-grid .asset-card")).to_have_count(1)
    page.locator("#asset-select-page").check()
    page.locator("#purge-selected-assets").click()
    cleanup = page.locator("dialog[open]")
    expect(cleanup.locator("[data-preview]")).to_contain_text("Cleanup protected project")
    assert cleanup.locator("[data-confirm]").is_disabled()
    cleanup.locator('[name="detach_projects"]').check()
    expect(cleanup.locator("[data-confirm]")).to_be_enabled()
    cleanup.locator("[data-cancel]").click()
    assert page.request.get(url + f'/api/workbench/assets/{selected[0]}').status == 200
    page.locator("#purge-selected-assets").click()
    cleanup.locator('[name="detach_projects"]').check()
    expect(cleanup.locator("[data-confirm]")).to_be_enabled()
    cleanup.locator("[data-confirm]").click()
    expect(page.locator("#asset-cleanup-status")).to_contain_text("已彻底删除 1 项")
    assert page.request.get(url + f'/api/workbench/assets/{selected[0]}').status == 404
    assert page.request.get(url + f'/api/workbench/projects/{project["id"]}').json()["assets"] == []
    page.locator("#asset-status").select_option("active")
    expect(page.locator("#asset-grid .asset-card")).to_have_count(24)
    page.screenshot(path=output / "desktop-cleanup-assets.png", full_page=False)

    page.locator('.studio-sidebar [data-tab="history"]').click()
    failed_id = "20990101-000002-00000003"
    page.locator("#history-query").fill(failed_id)
    expect(page.locator("[data-select-job]")).to_have_count(1)
    page.locator("#history-select-page").check()
    page.locator("#cleanup-selected-jobs").click()
    confirmation = page.locator(".studio-dialog[open]")
    expect(confirmation).to_contain_text("永久删除 1 项")
    confirmation.locator('[value="cancel"]').click()
    expect(page.locator("#history-cleanup-status")).to_contain_text("已取消清理")
    assert page.request.get(url + f"/api/jobs/{failed_id}").status == 200
    page.locator("#history-clear-selection").click()
    assert page.locator("#cleanup-selected-jobs").is_disabled()
    page.locator("#cleanup-failed-jobs").click()
    expect(confirmation).to_contain_text(failed_id)
    confirmation.locator('[value="confirm"]').click()
    expect(page.locator("#history-cleanup-status")).to_contain_text("已清理 1 项任务")
    assert page.request.get(url + f"/api/jobs/{failed_id}").status == 404
    assert page.request.get(url + "/api/jobs/20990101-000000-00000001").status == 200
    page.screenshot(path=output / "desktop-cleanup-tasks.png", full_page=False)
    page.set_viewport_size({"width": 390, "height": 844})
    assert_no_page_overflow(page, "phone task cleanup")
    page.locator("#mobile-workspace-menu").click()
    page.locator('#workspace-menu-dialog [data-go-tab="assets"]').click()
    expect(page.locator("#asset-grid .asset-card")).to_have_count(8)
    assert_no_page_overflow(page, "phone asset cleanup")
    assert_named_controls(page)
    assert_text_contrast(page, "assets")
    page.locator("#assets .cleanup-controls").scroll_into_view_if_needed()
    page.screenshot(path=output / "phone-cleanup-assets.png", full_page=False)
    page.locator("#asset-next").click()
    expect(page.locator("#asset-page-status")).to_contain_text("第 2")
    page.set_viewport_size({"width": 1366, "height": 900})
    expect(page.locator("#asset-grid .asset-card")).to_have_count(24)
    expect(page.locator("#asset-page-status")).to_contain_text("第 1")
    assert not errors, errors
    context.close()


def assert_audit_races(browser, url: str, output: Path) -> None:
    """Delay real HTTP reads; all writes remain isolated in the smoke service."""
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url, wait_until="domcontentloaded")
    wait_for_ui(page)
    projects = page.request.get(url + "/api/workbench/projects").json()["projects"]
    old = next(item for item in projects if item["title"] == "浏览器回归项目")
    new = next(item for item in projects if item["title"] == "空项目")
    page.locator('.studio-sidebar [data-tab="project"]').click()
    page.locator("#workbench-project-select").select_option(old["id"])
    page.wait_for_function("id=>window.workbenchProjectId()===id", arg=old["id"])
    page.evaluate("""() => {
      const fetchOriginal=window.fetch;
      window.auditReleases=[];window.auditHold='';window.auditScope='';
      window.fetch=async(...args)=>{
        const response=await fetchOriginal(...args),u=new URL(String(args[0]),location.href);
        const hold=window.auditHold==='project'?u.searchParams.get('project_id')===window.auditScope:
          window.auditHold==='model'&&u.searchParams.get('kind')==='model';
        if(u.pathname==='/api/workbench/assets'&&hold)
          await new Promise(resolve=>window.auditReleases.push(resolve));
        return response;
      };
    }""")
    page.evaluate("id=>{window.auditHold='project';window.auditScope=id;}", old["id"])
    page.locator('.studio-sidebar [data-tab="training"]').click()
    page.wait_for_function("window.auditReleases.length>=4")
    page.locator('.studio-sidebar [data-tab="project"]').click()
    page.locator("#workbench-project-select").select_option(new["id"])
    page.wait_for_function("id=>window.workbenchProjectId()===id", arg=new["id"])
    page.locator('.studio-sidebar [data-tab="training"]').click()
    expect(page.locator("#training-assets")).to_contain_text("当前项目还没有可训练的歌曲")
    page.evaluate("window.auditHold='';window.auditReleases.splice(0).forEach(resolve=>resolve())")
    page.wait_for_timeout(300)
    expect(page.locator("[data-training-asset]")).to_have_count(0)
    page.screenshot(path=output / "desktop-training-project-race.png", full_page=False)

    model = next(item for item in page.request.get(url + "/api/workbench/assets?kind=model").json()["assets"]
                 if item["metadata"].get("model_type") == "yue2_ar_lora")
    page.evaluate("window.auditHold='model'")
    page.locator('.studio-sidebar [data-tab="assets"]').click()
    page.wait_for_function("window.auditReleases.length>=1")
    page.evaluate("window.auditHold=''")
    assert page.request.post(url + "/api/workbench/assets/batch-status", data={"ids": [model["id"]], "status": "trashed"}).ok
    page.locator('.studio-sidebar [data-tab="assets"]').click()
    selector = f'#create-form [data-style-model] option[value="{model["id"]}"]'
    expect(page.locator(selector)).to_have_count(0)
    page.evaluate("window.auditReleases.splice(0).forEach(resolve=>resolve())")
    page.wait_for_timeout(300)
    expect(page.locator(selector)).to_have_count(0)
    assert page.request.post(url + "/api/workbench/assets/batch-status", data={"ids": [model["id"]], "status": "active"}).ok

    fixtures = [page.request.post(url + "/api/workbench/assets/text", data={"kind": "lyrics", "title": f"audit-scope-{i}", "text": str(i)}).json() for i in range(2)]
    project = page.request.post(url + "/api/workbench/projects", data={"title": "audit-scope-project"}).json()
    protected = fixtures[1]
    assert page.request.post(url + f'/api/workbench/projects/{project["id"]}/assets', data={"asset_id": protected["id"], "role": "lyrics"}).ok
    assert page.request.post(url + "/api/workbench/assets/batch-status", data={"ids": [item["id"] for item in fixtures], "status": "trashed"}).ok
    page.locator("#asset-query").fill("audit-scope-")
    page.locator("#asset-status").select_option("trashed")
    expect(page.locator("#asset-grid .asset-card")).to_have_count(2)
    page.locator("#asset-select-page").check()
    page.locator("#purge-selected-assets").click()
    dialog = page.locator("dialog[open]").filter(has=page.locator("[data-preview]"))
    expect(dialog).to_contain_text("可删除 1 项")
    assert page.request.post(url + f'/api/workbench/projects/{project["id"]}/remove-asset', data={"asset_id": protected["id"], "revision_id": protected["current_revision_id"], "role": "lyrics"}).ok
    dialog.locator("[data-confirm]").click()
    expect(page.locator("#asset-cleanup-status")).to_contain_text("已彻底删除 1 项")
    assert page.request.get(url + f'/api/workbench/assets/{protected["id"]}').status == 200
    page.locator('.studio-sidebar [data-tab="training"]').click()
    page.locator("#training-add-songs").click()
    assert page.locator("#asset-status").input_value() == "active"

    page.locator('.studio-sidebar [data-tab="remix"]').click()
    page.locator("#remix-style-mode").select_option("reference")
    assert page.evaluate("new FormData(document.querySelector('#remix-form')).has('genre')") is False
    page.locator("#remix-style-mode").select_option("custom")
    page.locator('#remix-form [name="genre"]').fill("audit jazz")
    assert page.evaluate("new FormData(document.querySelector('#remix-form')).get('genre')") == "audit jazz"
    page.locator("#remix-style-mode").select_option("reference")
    assert page.evaluate("new FormData(document.querySelector('#remix-form')).has('genre')") is False
    page.screenshot(path=output / "desktop-remix-reference-style.png", full_page=False)

    page.locator('.studio-sidebar [data-tab="training"]').click()
    model_button = page.locator('[data-use-trained-model]').first
    expect(model_button).to_be_visible()
    selected_model = model_button.get_attribute("data-use-trained-model")
    model_button.click()
    expect(page.locator('#create-form [data-style-model]')).to_have_value(selected_model)
    page.wait_for_timeout(1000)
    page.reload(wait_until="domcontentloaded")
    wait_for_ui(page)
    expect(page.locator('#create-form [data-style-model]')).to_have_value(selected_model)
    expect(page.locator('#create-form [name="cot"]')).to_have_value("off")

    # The config save is deliberately held before a second submit can occur.
    # Paid chat endpoints are never contacted; only the browser's job POST is mocked.
    jobs = []
    fixture_id = "20990101-000099-00000099"
    def job_route(route):
        if route.request.method == "POST":
            jobs.append(route.request.post_data_json)
        route.fulfill(json={"id": fixture_id, "kind": "assistant", "status": "complete", "stage": "complete",
                            "project_id": new["id"], "created_at": time.time(), "result": {"connection": {"ok": True}}})
    page.route("**/api/jobs", job_route)
    page.route("**/api/jobs/" + fixture_id, job_route)
    page.locator('.studio-sidebar [data-tab="assistant"]').click()
    page.evaluate("""() => {
      document.querySelector('#assistant-provider').value='local';
      document.querySelector('#assistant-model').value='audit-model';
      saveAssistantConfig=()=>new Promise(resolve=>window.auditSubmitRelease=()=>resolve({provider:'local',model:'audit-model'}));
      startAssistant();startAssistant(true);
    }""")
    assert page.evaluate("assistant.starting")
    assert page.locator("#assistant-generate").is_disabled()
    assert page.locator("#assistant-test").is_disabled()
    page.evaluate("window.auditSubmitRelease()")
    page.wait_for_function("!assistant.starting&&!assistant.polling")
    assert len(jobs) == 1, jobs
    assert jobs[0]["request"]["project_id"] == new["id"]
    assert not errors, errors
    context.close()


def run_browser(url: str, output: Path) -> dict:
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        assert_assistant_model_refresh(browser, url, output)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.emulate_media(reduced_motion="reduce")
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: console_errors.append(str(error)))
        page.goto(url, wait_until="domcontentloaded")
        wait_for_ui(page)

        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.classList.contains('skip-link')")

        assert page.locator(".tool-nav").count() == 0
        sidebar_tabs = page.locator(".studio-sidebar .tab")
        expected_tabs = (
            "project", "assets", "create", "plan", "remix",
            "cover", "assistant", "training", "voices", "history",
        )
        assert sidebar_tabs.count() == len(expected_tabs)
        assert sidebar_tabs.evaluate_all(
            "elements => elements.map(element => element.dataset.tab)"
        ) == list(expected_tabs)
        expected_links = {
            "GitHub 源码": "https://github.com/T8mars/Comfyui-YuE2-T8",
            "ComfyUI 节点": "https://registry.comfy.org/nodes/yue2-t8",
            "模型权重": "https://huggingface.co/t8star/YuE2-Comfy",
            "B站": "https://space.bilibili.com/385085361",
            "YouTube": "https://www.youtube.com/@T8star-Aix/",
        }
        for label, href in expected_links.items():
            link = page.locator(".project-links a", has_text=label)
            assert link.is_visible(), label
            assert link.get_attribute("href") == href
        assert page.locator("#mobile-workspace-menu").is_hidden()
        assert page.locator("#workspace-menu-dialog [data-go-tab].active").count() == 1
        assert page.locator("[aria-selected]").count() == 0
        page.locator('.studio-sidebar [data-tab="project"]').click()
        assert page.locator("#new-user-guide").is_visible()
        assert page.locator("#new-user-guide").evaluate("element => element.open")
        page.locator("#new-project").click()
        studio_dialog = page.locator(".studio-dialog")
        studio_dialog.wait_for(state="visible")
        labelled_by = studio_dialog.get_attribute("aria-labelledby")
        assert labelled_by and studio_dialog.locator(f"#{labelled_by}").inner_text() == "新项目名称"
        studio_dialog.locator('button[value="cancel"]').click()
        page.locator(".archived-project-card").evaluate("element => { element.open = true; }")
        page.locator("[data-restore-project]").wait_for(state="visible")
        assert page.locator("[data-restore-project]").count() == 1
        assert_no_page_overflow(page, "desktop")
        assert_unique_ids(page)
        assert_named_controls(page)
        page.screenshot(path=output / "desktop.png", full_page=False)

        page.evaluate("""() => renderTaskCenter({current_job: 'ui-smoke-running'}, [{
          id: 'ui-smoke-running', kind: 'generate', status: 'running', stage: 'semantic',
          progress: .42, created_at: Date.now() / 1000 - 12, source: 'webui', summary: '后台进度回归'
        }])""")
        workload = page.locator("#task-center-jump")
        workload.wait_for(state="visible")
        assert page.locator("#background-progress-title").inner_text() == "歌曲生成 · 正在生成音乐结构"
        assert page.locator("#background-progress-detail").inner_text() == "42%"
        assert page.locator("#background-progress-bar").get_attribute("style") == "width: 42%;"
        assert workload.evaluate("element => getComputedStyle(element).position") == "fixed"
        assert page.locator("#task-center").is_visible()
        page.screenshot(path=output / "desktop-progress.png", full_page=False)
        page.locator("#global-player").evaluate("element => element.classList.remove('hidden')")
        positions = page.evaluate("""() => {
          const task = document.querySelector('#task-center-jump').getBoundingClientRect();
          const player = document.querySelector('#global-player').getBoundingClientRect();
          return {taskBottom: task.bottom, playerTop: player.top};
        }""")
        assert positions["taskBottom"] <= positions["playerTop"] + 1, positions
        page.locator("#global-player").evaluate("element => element.classList.add('hidden')")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        task_top = page.evaluate("""() => {
          renderTaskCenter({current_job: 'ui-smoke-running'}, [{
            id: 'ui-smoke-running', kind: 'generate', status: 'running', stage: 'semantic',
            progress: .42, created_at: Date.now() / 1000 - 12, source: 'webui', summary: '后台进度回归'
          }]);
          document.querySelector('#task-center-jump').click();
          // Finish the reduced-motion navigation synchronously so the 1.2 s
          // workspace poll cannot clear the synthetic job mid-assertion.
          document.querySelector('#task-center').scrollIntoView({behavior: 'instant', block: 'start'});
          return document.querySelector('#task-center').getBoundingClientRect().top;
        }""")
        assert task_top < 900
        page.evaluate("renderTaskCenter({current_job: null}, []); window.scrollTo(0, 0)")

        for panel_id in expected_tabs:
            page.locator(f'.studio-sidebar [data-tab="{panel_id}"]').click()
            assert_text_contrast(page, panel_id)

        page.locator('.studio-sidebar [data-tab="assistant"]').click()
        page.wait_for_function("document.querySelector('#assistant-result-style').value === 'Persistent browser folk'")
        assert page.locator("#assistant-result-lyrics").input_value() == "[Verse]\nPersistent browser lyrics"
        assert "T:Persistent browser score" in page.locator("#assistant-result-abc").input_value()
        page.locator('.studio-sidebar [data-tab="create"]').click()
        page.locator('.studio-sidebar [data-tab="assistant"]').click()
        assert page.locator("#assistant-result-style").input_value() == "Persistent browser folk"
        page.reload(wait_until="domcontentloaded")
        wait_for_ui(page)
        page.locator('.studio-sidebar [data-tab="assistant"]').click()
        page.wait_for_function("document.querySelector('#assistant-result-style').value === 'Persistent browser folk'")
        assert page.locator("#assistant-result-lyrics").input_value() == "[Verse]\nPersistent browser lyrics"
        channel_status = page.locator("#assistant-channel-status")
        assert channel_status.get_attribute("data-state") == "missing"
        assert "设置 API Key" in page.locator("#assistant-channel-title").inner_text()
        assert page.locator("#assistant-settings").get_attribute("open") is not None
        assert page.locator("#assistant-status-signup").is_visible()
        model_choice = page.locator("#assistant-model-choice")
        assert model_choice.input_value() == "bytedance/doubao-seed-2.1-turbo"
        output_budget = page.locator('#assistant-config-form [name="max_tokens"]')
        assert output_budget.input_value() == "32768"
        assert output_budget.get_attribute("max") == "262144"
        assert page.locator("#assistant-custom-model-field").is_hidden()
        model_choice.select_option("__custom__")
        assert page.locator("#assistant-custom-model-field").is_visible()
        page.locator("#assistant-model").fill("custom/provider-model")
        assert page.locator("#assistant-model").input_value() == "custom/provider-model"
        page.locator("#assistant-custom-model-field").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-assistant-custom-model.png", full_page=False)
        model_choice.select_option("bytedance/doubao-seed-2.1-turbo")
        assert page.locator("#assistant-custom-model-field").is_hidden()
        assert page.locator("#assistant-abc-source").input_value() == "自动创作 ABC（T8 LLM）/ Compose"
        page.evaluate("showAssistantResult({style:'English folk',lyrics:'[Verse]\\nBrowser test',abc:'',cot:'full',abc_status:'downstream_yue2',outcome:'success'})")
        assert page.locator("#assistant-compose-abc").is_visible()
        assert page.locator("#assistant-compose-abc").inner_text() == "补写 ABC"
        page.locator("#assistant-compose-abc").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-assistant-abc-recovery.png", full_page=False)
        page.evaluate("showAssistantResult({style:'English folk',lyrics:'[Verse]\\nBrowser test',abc:'X:1\\ninvalid paid draft',cot:'full',abc_status:'failed',outcome:'partial_success',report:{abc:{error:'bar duration mismatch'}}})")
        assert page.locator("#assistant-result-abc").input_value() == "X:1\ninvalid paid draft"
        assert page.locator("#assistant-compose-abc").is_visible()
        assert page.locator("#assistant-compose-abc").inner_text() == "重新生成 ABC"
        assert "已保留模型返回的 ABC" in page.locator("#assistant-abc-status").inner_text()
        page.locator("#assistant-result-abc").fill("")
        assert page.locator("#assistant-abc-status").get_attribute("data-state") == "pending"
        page.locator("#assistant-validate").click()
        page.wait_for_function("document.querySelector('#assistant-abc-status').dataset.state === 'failed'")
        assert "仍可原样发送" in page.locator("#assistant-abc-status").inner_text()
        page.evaluate("showAssistantResult({style:'English folk',lyrics:'[Verse]\\nBrowser test',abc:'X:1\\nT:invalid\\nM:4/4\\nL:1/4\\nK:C\\nZ',cot:'full',abc_status:'failed',outcome:'partial_success',report:{abc:{error:'unsupported token Z'}}})")
        assert page.locator("#assistant-abc-status").bounding_box()["y"] < page.locator("#assistant-result-abc").bounding_box()["y"]
        page.locator("#assistant-abc-status").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-assistant-invalid-abc-retained.png", full_page=False)
        page.locator('[data-assistant-send="plan"]').click()
        assert page.locator('#assistant-send-dialog [name="abc"]').is_enabled()
        assert page.locator('#assistant-send-dialog [name="abc"]').is_checked()
        assert "完整原样填入" in page.locator("#assistant-send-details").inner_text()
        page.locator("#assistant-send-confirm").click()
        page.wait_for_function("document.body.dataset.activeTab === 'plan'")
        transfer_notice = page.locator("#assistant-transfer-notice")
        assert transfer_notice.is_visible()
        assert transfer_notice.evaluate("element => getComputedStyle(element).position") == "static"
        assert transfer_notice.evaluate("element => element.closest('#plan') !== null")
        assert page.locator("#plan-abc").input_value() == "X:1\nT:invalid\nM:4/4\nL:1/4\nK:C\nZ"
        assert "未校验导入谱" in page.locator("#plan-badge").inner_text()
        page.locator("#plan-workbench").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-plan-invalid-abc-received.png", full_page=False)
        page.locator('.studio-sidebar [data-tab="assistant"]').click()
        channel_status.scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-assistant-api-key-entry.png", full_page=False)
        page.locator("#assistant-open-settings").click()
        page.wait_for_function("document.activeElement.id === 'assistant-key'")
        assert page.evaluate("document.activeElement.id") == "assistant-key"
        page.evaluate("showAssistantError('API 凭据待补，请配置后重试')")
        recovery = page.locator(".assistant-credential-error button")
        assert recovery.is_visible() and recovery.inner_text() == "设置 API Key"
        recovery.click()
        page.wait_for_function("document.activeElement.id === 'assistant-key'")
        assert page.evaluate("document.activeElement.id") == "assistant-key"
        assert_no_page_overflow(page, "desktop assistant credential recovery")
        page.screenshot(path=output / "desktop-assistant-api-key.png", full_page=False)

        page.locator('.studio-sidebar [data-tab="training"]').click()
        page.wait_for_function("""() => document.querySelectorAll('.training-model-actions').length === 3
          && document.querySelector('#training-model-page-status').textContent.includes('第 1 / 2 页 · 4 个')""")
        assert page.locator(".training-model-card").count() == 3
        model_page_status = page.locator("#training-model-page-status").inner_text()
        assert "第 1 / 2 页 · 4 个" in model_page_status, repr(model_page_status)
        action_widths = page.evaluate("""() => [...document.querySelector('.training-model-actions').children]
          .map(item => Math.round(item.getBoundingClientRect().width))""")
        assert len(set(action_widths)) == 1, action_widths
        assert page.locator('[data-copy-trained-model-path]').first.evaluate(
            "item => !item.classList.contains('compact')")
        page.locator("#training-model-next").click()
        page.wait_for_function("""() => document.querySelectorAll('.training-model-card').length === 1
          && document.querySelector('#training-model-page-status').textContent.includes('第 2 / 2 页 · 4 个')""")
        assert page.locator(".training-model-card").count() == 1
        assert "第 2 / 2 页 · 4 个" in page.locator("#training-model-page-status").inner_text()
        page.locator("#training-model-prev").click()
        page.locator("#training-model-library").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-training-model-manager.png", full_page=False)
        assert "当前项目“浏览器回归项目”" in page.locator("#training-assets-scope").inner_text()
        assert page.locator("#training-preset").input_value() == "quick"
        assert page.locator("#training-form [name=steps]").input_value() == "200"
        style_input = page.locator("#training-form [name=style]")
        style_input.fill("warm song")
        assert style_input.evaluate("el => getComputedStyle(el).direction") == "ltr"
        assert style_input.evaluate("el => getComputedStyle(el).textAlign") == "left"
        style_input.fill("")
        assert page.locator("[data-training-asset]").count() == 8
        assert page.locator(".training-asset-body:not(.hidden)").count() == 0
        assert "第 1 / 2 页 · 共 10 首" in page.locator("#training-assets-page-status").inner_text()
        page.locator("[data-toggle-training-asset]").first.click()
        assert page.locator(".training-asset-body:not(.hidden)").count() == 1
        page.locator("#training-assets-next").click()
        assert page.locator("[data-training-asset]").count() == 2
        assert "第 2 / 2 页 · 共 10 首" in page.locator("#training-assets-page-status").inner_text()
        page.locator("#training-assets-prev").click()
        page.locator("#training-select-all").click()
        page.locator("#create-training-run").click()
        assert "请填写公共曲风" in page.locator("#training-form-status").inner_text()
        assert page.locator("#training-form-status").get_attribute("data-state") == "error"
        page.screenshot(path=output / "desktop-training-validation.png", full_page=False)
        page.locator("#training-assets-scope").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-training-assets.png", full_page=False)
        page.locator("#training-add-songs").click()
        assert page.locator("body").get_attribute("data-active-tab") == "assets"
        assert page.locator("#asset-training-guidance").is_visible()
        assert "加入当前项目" in page.locator("#asset-training-guidance-copy").inner_text()
        page.screenshot(path=output / "desktop-training-asset-guidance.png", full_page=False)
        page.locator("#asset-training-back").click()
        page.wait_for_function("document.body.dataset.activeTab === 'training'")
        assert page.locator("body").get_attribute("data-active-tab") == "training"
        style_input.fill("persistent project training style")
        page.locator("[data-training-asset]").first.check()
        assert_no_page_overflow(page, "desktop training asset guidance")
        empty_value = page.locator("#workbench-project-select option").filter(has_text="空项目").get_attribute("value")
        page.locator('.studio-sidebar [data-tab="project"]').click()
        page.locator("#workbench-project-select").select_option(empty_value)
        page.wait_for_function("document.querySelector('#header-project-name').textContent === '空项目'")
        page.locator('.studio-sidebar [data-tab="training"]').click()
        page.locator("[data-open-training-assets]").wait_for(state="visible")
        assert page.locator("#training-form [name=style]").input_value() == ""
        page.locator("#training-form [name=style]").fill("empty project training style")
        assert "当前项目还没有可训练的歌曲" in page.locator("#training-assets").inner_text()
        assert page.locator("[data-import-training-song]").is_visible()
        page.locator("#training-assets").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-training-empty.png", full_page=False)
        active_value = page.locator("#workbench-project-select option").filter(has_text="浏览器回归项目").get_attribute("value")
        page.locator('.studio-sidebar [data-tab="project"]').click()
        page.locator("#workbench-project-select").select_option(active_value)
        page.wait_for_function("document.querySelector('#header-project-name').textContent === '浏览器回归项目'")
        page.locator('.studio-sidebar [data-tab="training"]').click()
        page.wait_for_function("document.querySelectorAll('[data-training-asset]').length === 8")
        assert page.locator("#training-form [name=style]").input_value() == "persistent project training style"
        assert page.locator("[data-training-asset]").first.is_checked()

        page.locator('.studio-sidebar [data-tab="assets"]').click()
        page.locator(".asset-card").first.wait_for(state="visible")
        assert page.locator(".asset-card").count() == 18
        assert page.locator(".asset-card [data-add-asset]").count() == 6
        assert page.locator(".asset-card [data-project-asset-state]").count() == 12
        assert page.locator(".asset-card [data-project-asset-state]:disabled").count() == 12
        for card in page.locator(".asset-card").all():
            bounds = card.evaluate("""card => {
              const outer=card.getBoundingClientRect();
              const actions=[...card.querySelectorAll('.toolbar button')].map(button=>button.getBoundingClientRect());
              return {outer:{left:outer.left,right:outer.right},actions:actions.map(item=>({left:item.left,right:item.right}))};
            }""")
            assert all(item["left"] >= bounds["outer"]["left"] - 1 and item["right"] <= bounds["outer"]["right"] + 1
                       for item in bounds["actions"]), bounds
        assert_no_page_overflow(page, "desktop assets with project")
        page.screenshot(path=output / "desktop-assets.png", full_page=False)

        for trigger in ("[data-use-asset]", "[data-edit-asset]", "[data-read-asset]"):
            page.locator(f".asset-card {trigger}").first.click()
            dynamic_dialog = page.locator("body > dialog[open]").last
            dynamic_dialog.wait_for(state="visible")
            labelled_by = dynamic_dialog.get_attribute("aria-labelledby")
            assert labelled_by and dynamic_dialog.locator(f"#{labelled_by}").count() == 1
            assert_named_controls(page)
            dynamic_dialog.press("Escape")

        audio_card = page.locator(".asset-card").filter(has=page.locator("[data-wave]")).first
        audio_card.locator("[data-use-asset]").click()
        page.locator('[data-use-action="cover-source"]').click()
        page.wait_for_function("document.body.dataset.activeTab === 'cover'")
        asset_reference = page.locator("#cover-file").evaluate(
            "input => ({files: input.files.length, source: JSON.parse(input.dataset.localSource), preview: input.dataset.localPreview})")
        assert asset_reference["files"] == 0
        assert asset_reference["source"].get("$asset")
        assert asset_reference["preview"].startswith("/api/workbench/assets/")
        page.evaluate("sendJobAudioToCover('20990101-000000-00000001', 'artifacts/audio.wav')")
        job_reference = page.locator("#cover-file").evaluate(
            "input => ({files: input.files.length, source: JSON.parse(input.dataset.localSource)})")
        assert job_reference["files"] == 0
        assert job_reference["source"]["$job_file"] == {
            "job_id": "20990101-000000-00000001", "relative": "artifacts/audio.wav"}

        page.locator('.studio-sidebar [data-tab="create"]').click()
        page.locator("#create-result audio").wait_for(state="visible")
        assert_named_controls(page)
        assert "KeyError" not in page.evaluate("failureMarkup({message: `KeyError: 'source_path'`})")
        partial_text = page.evaluate("""() => {
          const target=document.createElement('div');
          renderJob({id:'partial-smoke',kind:'generate',status:'complete',result:{partial:true,completed_candidates:1,requested_candidates:2,candidates:[{audio:''}],failures:[{error:`KeyError: 'source_path'`}]}},target);
          return target.innerText;
        }""")
        assert "KeyError" not in partial_text
        style = page.locator('#create-form [name="style"]')
        original_style = style.input_value()
        style.fill("")
        page.locator("#create-button").click()
        assert style.evaluate("field => field.validationMessage").startswith("请填写风格提示")
        style.fill(original_style)

        page.locator('.studio-sidebar [data-tab="cover"]').click()
        page.locator("#cover-mode").select_option("direct")
        seed_pitch = page.locator("#seed-pitch-settings")
        seed_pitch.wait_for(state="visible")
        assert seed_pitch.locator("[data-seed-pitch]").count() == 3
        page.locator("#voice-auto-f0").evaluate("element => element.checked = true")
        seed_pitch.locator('[data-seed-pitch="-12"]').click()
        assert page.locator("#voice-shift").input_value() == "-12"
        assert not page.locator("#voice-auto-f0").is_checked()
        assert seed_pitch.locator('[data-seed-pitch="-12"]').get_attribute("aria-pressed") == "true"
        assert "女声原曲" in page.locator("#seed-pitch-summary").inner_text()
        assert page.evaluate("localStorage.getItem('yue2:seed-pitch-shift')") == "-12"
        page.locator("#voice-shift").fill("")
        assert not page.locator("#voice-shift").evaluate("element => element.checkValidity()")
        page.locator("#voice-shift").fill("-12")
        assert "-12 半音" in page.evaluate("voiceDescription({backend:'seed-vc',settings:{semi_tone_shift:-12}})")
        page.locator("#voice-backend").select_option("rvc")
        assert seed_pitch.is_hidden() and page.locator("#rvc-cover-settings").is_visible()
        page.locator('[data-rvc-pitch="-12"]').click()
        assert page.locator("#rvc-pitch-shift").input_value() == "-12"
        assert page.evaluate("localStorage.getItem('yue2:rvc-pitch-shift')") == "-12"
        page.locator("#voice-backend").select_option("compare")
        assert seed_pitch.is_visible() and page.locator("#rvc-cover-settings").is_visible()
        page.locator("#voice-backend").select_option("seed-vc")
        seed_pitch.scroll_into_view_if_needed()
        assert_no_page_overflow(page, "desktop cover pitch controls")
        page.screenshot(path=output / "desktop-cover-pitch.png", full_page=False)

        page.set_viewport_size({"width": 820, "height": 900})
        page.reload(wait_until="domcontentloaded")
        wait_for_ui(page)
        page.evaluate("window.scrollTo(0, 0)")
        page.locator("#mobile-workspace-menu").wait_for(state="visible")
        assert section_top(page, ".panel.active") < 560
        assert_no_page_overflow(page, "tablet")
        page.screenshot(path=output / "tablet.png", full_page=False)

        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="domcontentloaded")
        wait_for_ui(page)
        page.evaluate("window.scrollTo(0, 0)")
        page.locator('.studio-sidebar [data-tab="project"]').click()
        assert page.locator("#mobile-header-details").is_visible()
        assert page.locator("body > header .project-meta").is_hidden()
        page.locator("#mobile-header-details").click()
        assert page.locator("body > header .project-meta").is_visible()
        page.locator("#mobile-header-details").click()
        page.screenshot(path=output / "phone-project.png", full_page=False)
        page.locator('.studio-sidebar [data-tab="assets"]').click()
        page.locator("#asset-grid .asset-card").first.wait_for(state="visible")
        assert page.locator("#asset-grid .asset-card").count() <= 8
        assert page.locator("#asset-pagination-top").is_visible()
        page.screenshot(path=output / "phone-assets.png", full_page=False)
        menu = page.locator("#mobile-workspace-menu")
        menu.wait_for(state="visible")
        menu.click()
        page.locator('#workspace-menu-dialog [data-go-tab="cover"]').click()
        page.wait_for_function("document.body.dataset.activeTab === 'cover'")
        page.locator("#cover-mode").wait_for(state="visible")
        assert_no_page_overflow(page, "phone cover workspace")
        page.screenshot(path=output / "phone-cover-pitch.png", full_page=False)
        page.evaluate("window.scrollTo(0, 0)")
        page.evaluate("""() => renderTaskCenter({current_job: 'ui-smoke-running'}, [{
          id: 'ui-smoke-running', kind: 'generate', status: 'running', stage: 'semantic',
          progress: .42, created_at: Date.now() / 1000 - 12, source: 'webui', summary: '后台进度回归'
        }])""")
        workload = page.locator("#task-center-jump")
        workload.wait_for(state="visible")
        assert workload.evaluate("element => getComputedStyle(element).position") == "fixed"
        assert_no_page_overflow(page, "phone with background progress")
        page.screenshot(path=output / "phone-progress.png", full_page=False)
        page.evaluate("renderTaskCenter({current_job: null}, [])")
        assert page.locator(".studio-sidebar").evaluate("element => element.scrollWidth > element.clientWidth")
        menu.click()
        dialog = page.locator("#workspace-menu-dialog")
        dialog.wait_for(state="visible")
        menu_tabs = dialog.locator("[data-go-tab]")
        assert menu_tabs.count() == len(expected_tabs)
        assert menu_tabs.evaluate_all(
            "elements => elements.map(element => element.dataset.goTab)"
        ) == list(expected_tabs)
        page.screenshot(path=output / "phone-menu.png", full_page=False)
        dialog.locator('[data-go-tab="training"]').click()
        assert page.locator("body").get_attribute("data-active-tab") == "training"
        assert section_top(page, "#training") < 560
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        assert page.evaluate("window.scrollY") > 500
        menu.click()
        dialog.locator('[data-go-tab="history"]').click()
        switch_position = {"scroll_y": page.evaluate("window.scrollY"), "section_y": section_top(page, "#history")}
        assert switch_position["scroll_y"] <= 1 and switch_position["section_y"] < 560, switch_position
        page.locator("#header-project-button").click()
        assert page.locator("body").get_attribute("data-active-tab") == "project"
        assert page.evaluate("document.activeElement.id") == "workbench-project-select"

        menu.click()
        dialog.locator('[data-go-tab="history"]').click()
        assert page.locator("#cancel-active").is_disabled()
        page.locator("#history-query").fill("__ui_smoke_no_match__")
        page.locator("#clear-history-filters").wait_for(state="visible")
        page.locator("#clear-history-filters").click()
        assert page.locator("#history-query").input_value() == ""
        assert_no_page_overflow(page, "phone")
        assert_unique_ids(page)
        assert_named_controls(page)
        page.screenshot(path=output / "phone.png", full_page=False)
        assert_manual_cleanup(browser, url, output)
        assert_audit_races(browser, url, output)
        browser.close()

    assert not console_errors, f"Browser console/page errors: {console_errors}"
    return {
        "viewports": ["1366x900", "820x900", "390x844"],
        "scenarios": [
            "seeded project and asset cards stay within the desktop viewport",
            "asset use, edit and read dialogs expose accessible names",
            "asset and completed-job audio handoff uses server references without browser file copies",
            "completed generation stays playable with an accessible audio name on its originating page",
            "full and partial technical failures use a public summary and required fields use Chinese validation",
            "ordinary workspace buttons use current-page semantics without unsupported selected state",
            "creator, source, ComfyUI node and model links stay visible with verified destinations",
            "API credentials, Seedance 2.1 Turbo, explicit Custom model input and one-click ABC completion are visible and reachable",
            "settings save fetches real HTTP model lists, restores them on reload, preserves manual models and isolates credentials and delayed responses by API address",
            "assistant lyrics, style and ABC recover from the latest project job after tab switches and a browser reload",
            "YuE2 training exposes collapsed per-song settings, eight-song pagination, inline validation and three-model pagination",
            "backend-reported progress stays fixed across workspaces and opens the full task details",
            "Seed-VC and RVC expose independent remembered octave presets in the main cover flow",
            "mobile project maintenance stays collapsed and workspace switching resets long-page scroll position",
            "all ten workspaces meet WCAG AA contrast for visible normal-size text",
            "asset cross-page selection, trash, restore, protected purge, cancellation and task cleanup work through real HTTP routes on desktop and phone",
            "delayed project and model reads cannot overwrite newer state; deletion remains inside its preview and assistant double submits create one job without paid API calls",
        ],
        "console_errors": console_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    local_browser = root.parents[1] / "runtime" / "playwright"
    if local_browser.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(local_browser))

    port = free_port()
    with tempfile.TemporaryDirectory(prefix="yue2-ui-smoke-") as temporary:
        service_root = Path(temporary)
        shutil.copytree(root / "app" / "web", service_root / "app" / "web")
        seed_browser_state(service_root)
        environment = os.environ.copy()
        environment.update({"YUE2_HOME": str(service_root), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        log_path = output / "service.log"
        with log_path.open("w", encoding="utf-8") as log:
            bootstrap = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                         "runpy.run_module('app.yue2_app.service',run_name='__main__')")
            process = subprocess.Popen(
                [sys.executable, "-c", bootstrap, str(root), "--host", "127.0.0.1", "--port", str(port)],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                url = f"http://127.0.0.1:{port}"
                wait_for_service(url, process)
                report = run_browser(url, output)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
