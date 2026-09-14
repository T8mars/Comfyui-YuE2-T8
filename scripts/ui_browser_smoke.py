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
    project = library.create_project("浏览器回归项目")
    for index, kind in enumerate(("lyrics", "style", "score", "lyrics"), start=1):
        asset = library.create_text(kind=kind, title=f"回归素材 {index}", text=f"浏览器回归内容 {index}")
        if index <= 2:
            library.add_to_project(project["id"], asset["id"])

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
        assert page.locator(".studio-sidebar .tab").count() == 9
        assert page.locator("#mobile-workspace-menu").is_hidden()
        assert page.locator("#workspace-menu-dialog [data-go-tab].active").count() == 1
        assert page.locator("[aria-selected]").count() == 0
        assert_no_page_overflow(page, "desktop")
        assert_unique_ids(page)
        assert_named_controls(page)
        page.screenshot(path=output / "desktop.png", full_page=False)

        for panel_id in ("project", "assets", "create", "plan", "cover", "assistant", "training", "voices", "history"):
            page.locator(f'.studio-sidebar [data-tab="{panel_id}"]').click()
            assert_text_contrast(page, panel_id)

        page.locator('.studio-sidebar [data-tab="assets"]').click()
        page.locator(".asset-card").first.wait_for(state="visible")
        assert page.locator(".asset-card").count() == 4
        assert page.locator(".asset-card [data-add-asset]").count() == 2
        assert page.locator(".asset-card [data-project-asset-state]").count() == 2
        assert page.locator(".asset-card [data-project-asset-state]:disabled").count() == 2
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
        assert page.locator(".studio-sidebar").evaluate("element => element.scrollWidth > element.clientWidth")
        menu.click()
        dialog = page.locator("#workspace-menu-dialog")
        dialog.wait_for(state="visible")
        assert dialog.locator("[data-go-tab]").count() == 9
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
            "seeded project and four asset cards stay within the desktop viewport",
            "asset use, edit and read dialogs expose accessible names",
            "completed generation stays playable with an accessible audio name on its originating page",
            "full and partial technical failures use a public summary and required fields use Chinese validation",
            "ordinary workspace buttons use current-page semantics without unsupported selected state",
            "mobile workspace switching resets a long-page scroll position",
            "all nine workspaces meet WCAG AA contrast for visible normal-size text",
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
