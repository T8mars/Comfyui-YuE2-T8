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
import time
import urllib.request
import wave
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

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
    page.locator("#model-settings").evaluate("element => { element.open = false; }")


def seed_browser_state(root: Path) -> None:
    library = AssetLibrary(root)
    library.create_project("空项目")
    project = library.create_project("浏览器回归项目")
    for index, kind in enumerate(("lyrics", "style", "score", "lyrics"), start=1):
        asset = library.create_text(kind=kind, title=f"回归素材 {index}", text=f"浏览器回归内容 {index}")
        if index <= 2:
            library.add_to_project(project["id"], asset["id"])
    training_songs = []
    for index in range(2):
        source = root / f"training-song-{index + 1}.wav"
        with wave.open(str(source), "wb") as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
            stream.writeframes((b"\0\0" if index == 0 else b"\1\0") * 8000)
        asset = library.import_file(source, kind="song", title=f"训练歌曲 {index + 1}")
        library.add_to_project(project["id"], asset["id"], role="song")
        training_songs.append(asset)
        source.unlink()

    snapshot = library.create_snapshot(
        title="浏览器模型分页快照", training_kind="yue2_style",
        items=[{"asset_id": asset["id"], "revision_id": asset["current_revision_id"],
                "start": 0, "end": 1, "split": "train" if index == 0 else "validation",
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


def run_browser(url: str, output: Path) -> dict:
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.emulate_media(reduced_motion="reduce")
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: console_errors.append(str(error)))
        page.goto(url, wait_until="domcontentloaded")
        wait_for_ui(page)

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
        workload.click()
        page.wait_for_timeout(250)
        assert section_top(page, "#task-center") < 900
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
        assert page.locator("#assistant-abc-status").bounding_box()["y"] < page.locator("#assistant-result-abc").bounding_box()["y"]
        page.locator("#assistant-abc-status").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-assistant-invalid-abc-retained.png", full_page=False)
        page.locator('[data-assistant-send="plan"]').click()
        assert page.locator('#assistant-send-dialog [name="abc"]').is_enabled()
        assert page.locator('#assistant-send-dialog [name="abc"]').is_checked()
        assert "完整原样填入" in page.locator("#assistant-send-details").inner_text()
        page.locator("#assistant-send-confirm").click()
        page.wait_for_function("document.body.dataset.activeTab === 'plan'")
        assert page.locator("#plan-abc").input_value() == "X:1\ninvalid paid draft"
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
        assert "第 1 / 2 页 · 4 个" in page.locator("#training-model-page-status").inner_text()
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
        assert page.locator("[data-training-asset]").count() == 2
        assert page.locator(".training-lyrics-text:not(.hidden) textarea").count() == 2
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
        assert_no_page_overflow(page, "desktop training asset guidance")
        empty_value = page.locator("#workbench-project-select option").filter(has_text="空项目").get_attribute("value")
        page.locator('.studio-sidebar [data-tab="project"]').click()
        page.locator("#workbench-project-select").select_option(empty_value)
        page.wait_for_function("document.querySelector('#header-project-name').textContent === '空项目'")
        page.locator('.studio-sidebar [data-tab="training"]').click()
        page.locator("[data-open-training-assets]").wait_for(state="visible")
        assert "当前项目还没有可训练的歌曲" in page.locator("#training-assets").inner_text()
        assert page.locator("[data-import-training-song]").is_visible()
        page.locator("#training-assets").scroll_into_view_if_needed()
        page.screenshot(path=output / "desktop-training-empty.png", full_page=False)
        active_value = page.locator("#workbench-project-select option").filter(has_text="浏览器回归项目").get_attribute("value")
        page.locator('.studio-sidebar [data-tab="project"]').click()
        page.locator("#workbench-project-select").select_option(active_value)
        page.wait_for_function("document.querySelector('#header-project-name').textContent === '浏览器回归项目'")

        page.locator('.studio-sidebar [data-tab="assets"]').click()
        page.locator(".asset-card").first.wait_for(state="visible")
        assert page.locator(".asset-card").count() == 10
        assert page.locator(".asset-card [data-add-asset]").count() == 6
        assert page.locator(".asset-card [data-project-asset-state]").count() == 4
        assert page.locator(".asset-card [data-project-asset-state]:disabled").count() == 4
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
        browser.close()

    assert not console_errors, f"Browser console/page errors: {console_errors}"
    return {
        "viewports": ["1366x900", "820x900", "390x844"],
        "scenarios": [
            "seeded project and ten asset cards stay within the desktop viewport",
            "asset use, edit and read dialogs expose accessible names",
            "completed generation stays playable with an accessible audio name on its originating page",
            "full and partial technical failures use a public summary and required fields use Chinese validation",
            "ordinary workspace buttons use current-page semantics without unsupported selected state",
            "creator, source, ComfyUI node and model links stay visible with verified destinations",
            "API credentials, Seedance 2.1 Turbo, explicit Custom model input and one-click ABC completion are visible and reachable",
            "assistant lyrics, style and ABC recover from the latest project job after tab switches and a browser reload",
            "YuE2 training exposes per-song lyrics, inline validation, guided asset selection and three-model pagination",
            "backend-reported progress stays fixed across workspaces and opens the full task details",
            "Seed-VC and RVC expose independent remembered octave presets in the main cover flow",
            "mobile workspace switching resets a long-page scroll position",
            "all ten workspaces meet WCAG AA contrast for visible normal-size text",
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
