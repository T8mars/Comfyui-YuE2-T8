"""Real MIDI browser regressions against the disposable UI-smoke service."""
from __future__ import annotations

import io
import json
import wave

import mido

from app.yue2_app.midi_document import example_document


def assert_midi_races(browser, url, output):
    errors = []
    context = browser.new_context(viewport={"width": 1366, "height": 950})
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    prefix = "/api/workbench/midi"

    def post(path, data):
        response = page.request.post(url + path, data=data)
        assert response.ok, response.text()
        return response.json()

    project = post("/api/workbench/projects", {"title": "MIDI 并发回归"})["id"]
    document = example_document()
    document["tracks"][0]["channel"] = 5
    document["tracks"][0]["events"] = [{"tick": 0, "message": {
        "type": "control_change", "channel": 5, "control": 64, "value": 127, "time": 0}}]
    document = post(prefix + "/documents", {"project_id": project, "document": document})
    context.add_init_script(f"localStorage.setItem('yue2:workbench-project',{json.dumps(project)});"
                            "localStorage.setItem('yue2:active-tab','midi');")
    page.goto(url)
    page.wait_for_function("id=>window.midiEditorState?.()?.id===id", arg=document["id"])
    track = document["tracks"][0]["id"]
    page.locator(f'[data-midi-role="{track}"]').select_option("drums")
    assert page.evaluate("midiEditorState().tracks[0].channel") == 9
    assert page.evaluate("midiEditorState().tracks[0].events[0].message.channel") == 9
    page.locator(f'[data-midi-role="{track}"]').select_option("melody")
    with page.expect_download() as event:
        page.locator("#midi-download-combined").click()
    destination = output / "midi-role-roundtrip.mid"
    event.value.save_as(destination)
    messages = mido.MidiFile(destination).tracks[1]
    assert all(message.channel == 5 for message in messages if hasattr(message, "channel"))
    assert sum(message.type == "note_on" and bool(message.velocity) for message in messages) == 8

    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 80000)
    upload = page.request.post(url + "/api/uploads?filename=midi-smoke-source.wav", data=audio.getvalue(),
                               headers={"Content-Type": "application/octet-stream"})
    assert upload.ok, upload.text()
    source = post(prefix + "/source", {"project_id": project, "source_path": upload.json()["path"]})
    asset = source["source_path"]["$asset"]
    page.evaluate("id=>window.midiOpenAudio(id)", asset)
    for selector, value in (("#midi-clip-start", "1"), ("#midi-clip-end", "5"), ("#midi-extract-bpm", "140")):
        page.locator(selector).fill(value)
        page.locator(selector).dispatch_event("change")
    page.locator("#midi-save").click()
    page.wait_for_function("document.querySelector('#midi-save-state').textContent.startsWith('已保存')")
    page.evaluate("id=>window.midiOpenAudio(id)", asset)
    page.locator("#midi-save").click()
    page.wait_for_function("document.querySelector('#midi-save-state').textContent.startsWith('已保存')")
    page.reload()
    page.wait_for_function("id=>window.midiEditorState?.()?.id===id", arg=document["id"])
    assert [page.locator(selector).input_value() for selector in
            ("#midi-clip-start", "#midi-clip-end", "#midi-extract-bpm")] == ["0", "", ""]
    assert page.locator("#midi-source-player").get_attribute("aria-label")

    requests = []
    job = {"id": "20990101-000005-00000005", "kind": "midi_extract", "status": "failed",
           "stage": "failed", "created_at": 1, "error": "isolated fixture; no GPU", "result": {}}

    def jobs(route):
        if route.request.method == "POST" and route.request.url.endswith("/api/jobs"):
            requests.append(route.request.post_data_json)
            route.fulfill(json=job)
        elif "/api/jobs/" + job["id"] in route.request.url:
            route.fulfill(json=job)
        else:
            route.continue_()

    page.route("**/api/jobs**", jobs)
    page.evaluate("""()=>{const fetch=window.fetch;window.fetch=async(...args)=>{
      const response=await fetch(...args);if(String(args[0]).endsWith('/save')){
        window.midiSaveHeld=true;await new Promise(resolve=>window.releaseMidiSave=resolve);
      }return response;};}""")
    page.locator("#midi-title").fill("MIDI slow-save extraction")
    page.evaluate("document.querySelector('#midi-extract').click();document.querySelector('#midi-extract').click()")
    page.wait_for_function("window.midiSaveHeld")
    page.evaluate("window.releaseMidiSave()")
    page.wait_for_function("document.querySelector('#midi-extract').disabled===false")
    assert len(requests) == 1, f"Duplicate extraction requests: {len(requests)}"
    context.close()

    # Two real same-origin pages share storage, but failed local drafts remain independent.
    context = browser.new_context(viewport={"width": 1366, "height": 950})
    context.add_init_script(f"localStorage.setItem('yue2:workbench-project',{json.dumps(project)});"
                            "localStorage.setItem('yue2:active-tab','midi');")
    a, b = context.new_page(), context.new_page()
    for tab in (a, b):
        tab.on("pageerror", lambda error: errors.append(str(error)))
        tab.goto(url)
        tab.wait_for_function("id=>window.midiEditorState?.()?.id===id", arg=document["id"])
    a.route("**/api/workbench/midi/documents/*/save", lambda route:
            route.fulfill(status=503, json={"error": "isolated tab A save failure"}))
    a.locator("#midi-title").fill("A 未保存的旋律")
    a.locator("#midi-save").click()
    a.wait_for_function("document.querySelector('#midi-save-state').textContent.includes('保存失败')")
    a.close()
    b.locator("#midi-title").fill("B 正常保存的新版")
    b.locator("#midi-save").click()
    b.wait_for_function("document.querySelector('#midi-save-state').textContent.startsWith('已保存')")
    a = context.new_page()
    a.on("pageerror", lambda error: errors.append(str(error)))
    a.goto(url)
    a.wait_for_function("id=>window.midiEditorState?.()?.id===id", arg=document["id"])
    b.route("**/api/workbench/midi/documents/*/save", lambda route:
            route.fulfill(status=503, json={"error": "isolated newer tab B save failure"}))
    b.locator("#midi-title").fill("B 另一份未保存的草稿")
    b.locator("#midi-save").click()
    b.wait_for_function("document.querySelector('#midi-save-state').textContent.includes('保存失败')")
    a.get_by_role("button", name="恢复本机备份", exact=True).click()
    a.wait_for_function("id=>window.midiEditorState?.()?.id!==id", arg=document["id"])
    assert a.locator("#midi-title").input_value() == "A 未保存的旋律"
    a.reload()
    a.wait_for_function("window.midiEditorState?.()?.id")
    assert a.locator("#midi-message button").count() == 1, "Other tab's draft was lost or recovered draft appeared again"
    pending = a.evaluate("""()=>Object.values(localStorage).map(value=>{try{return JSON.parse(value);}catch{return null;}})
      .filter(value=>value?.dirty&&value?.document).map(value=>value.document.title)""")
    assert pending and set(pending) == {"B 另一份未保存的草稿"}, pending
    original = a.request.get(url + prefix + "/documents/" + document["id"]).json()
    assert original["title"] == "B 正常保存的新版"
    a.screenshot(path=output / "midi-recovered-tab.png", full_page=False)
    context.close()
    assert not errors, errors
    return {"checks": ["role roundtrip preserves imported note/controller channel",
                       "source range and BPM reset persist", "slow-save extraction submits once",
                       "closed failed tab draft survives other-tab save; recovery preserves a different unsaved draft without repeats"],
            "console_errors": errors}
