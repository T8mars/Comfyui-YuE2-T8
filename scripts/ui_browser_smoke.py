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
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


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
    failures = page.evaluate("""() => {
      const visible = element => !element.closest('details:not([open])') && Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
      const name = element => {
        const labels = element.labels ? [...element.labels].map(label => label.innerText.trim()).filter(Boolean) : [];
        return (element.getAttribute('aria-label') || element.getAttribute('title') || labels.join(' ') || element.innerText || '').trim();
      };
      return [...document.querySelectorAll('button,input:not([type="hidden"]),select,textarea')]
        .filter(visible).filter(element => !name(element))
        .map(element => `${element.tagName.toLowerCase()}#${element.id || '(no-id)'}`);
    }""")
    assert not failures, f"Visible controls without accessible names: {failures}"


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
        assert_no_page_overflow(page, "desktop")
        assert_unique_ids(page)
        assert_named_controls(page)
        page.screenshot(path=output / "desktop.png", full_page=False)

        page.set_viewport_size({"width": 820, "height": 900})
        page.reload(wait_until="domcontentloaded")
        wait_for_ui(page)
        page.locator("#mobile-workspace-menu").wait_for(state="visible")
        assert section_top(page, ".panel.active") < 560
        assert_no_page_overflow(page, "tablet")
        page.screenshot(path=output / "tablet.png", full_page=False)

        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="domcontentloaded")
        wait_for_ui(page)
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
    return {"viewports": ["1366x900", "820x900", "390x844"], "console_errors": console_errors}


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
        environment = os.environ.copy()
        environment.update({"YUE2_HOME": str(service_root), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        log_path = output / "service.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "app.yue2_app.service", "--host", "127.0.0.1", "--port", str(port)],
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
