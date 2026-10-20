"""Timing corrections must survive real UI edits, history, reload and MIDI export."""
from io import BytesIO
import json

import mido
from playwright.sync_api import expect

from app.yue2_app.midi_document import example_document


def assert_midi_timing(browser, url, output):
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    response=page.request.post(url+'/api/workbench/projects',data={'title':'MIDI 时间校正回归'})
    assert response.ok,response.text()
    project=response.json()['id']
    context.add_init_script(f"localStorage.setItem('yue2:workbench-project',{json.dumps(project)});")
    data=example_document()
    data['source']={'format':'audio','clip_start':.75}
    data['tracks'][0]['notes'][0]['tick']=1200
    response=page.request.post(url+'/api/workbench/midi/documents',data={'document':data,'project_id':project})
    assert response.ok,response.text()
    document=response.json()
    page.goto(url)
    page.locator('.tab[data-tab="midi"]').click()
    page.wait_for_function('id=>midiEditorState()?.id===id',arg=document['id'])
    page.get_by_text('校正位移与节拍',exact=True).click()
    expect(page.locator('#midi-tempo-mode')).to_have_value('preserve')
    original=page.evaluate('midiEditorState()')
    page.locator('#midi-bpm').fill('92')
    page.locator('#midi-shift-ms').click()
    corrected=page.evaluate('midiEditorState()')
    for a,b in zip(original['tracks'][0]['notes'],corrected['tracks'][0]['notes']):
        before=a['tick']/original['ppq']*original['tempos'][0]['tempo']
        after=b['tick']/corrected['ppq']*corrected['tempos'][0]['tempo']
        assert abs(before-after)<1000 # microseconds
    page.locator('#midi-undo').click()
    restored=page.evaluate('midiEditorState()')
    assert restored['tempos']==original['tempos'] and restored['tracks']==original['tracks']
    page.locator('#midi-shift-ms').fill('250')
    page.locator('#midi-shift').click()
    shifted=page.evaluate('midiEditorState()')
    assert shifted['tracks'][0]['notes'][0]['tick']==1440
    assert shifted['tracks'][1]['notes']==original['tracks'][1]['notes']
    page.locator('#midi-undo').click()
    page.locator('#midi-shift-ms').fill('-2000')
    page.locator('#midi-shift').click()
    expect(page.locator('#midi-message')).to_contain_text('起点之前')
    assert page.evaluate('midiEditorState().tracks')==original['tracks']
    page.locator('#midi-shift-ms').fill('10')
    page.locator('#midi-shift').click()
    page.locator('#midi-quantize-strength').fill('100')
    page.locator('#midi-quantize').click()
    quantized=page.evaluate('midiEditorState()')
    assert all(n['tick']%120==0 for n in quantized['tracks'][0]['notes'])
    assert [n['duration'] for n in quantized['tracks'][0]['notes']]==[n['duration'] for n in original['tracks'][0]['notes']]
    page.locator('#midi-save').click()
    expect(page.locator('#midi-save-state')).to_contain_text('已保存')
    frozen=page.evaluate('midiEditorState()')
    page.reload()
    page.wait_for_function('id=>midiEditorState()?.id===id',arg=document['id'])
    assert page.evaluate('midiEditorState().tracks')==frozen['tracks']
    response=page.request.get(url+f"/api/workbench/midi/documents/{document['id']}/download?version={frozen['version']}&role=combined")
    assert response.ok,response.text()
    events=[];clock=0
    for message in mido.MidiFile(file=BytesIO(response.body())):
        clock+=message.time
        if message.type=='note_on' and message.velocity and message.channel==0:
            events.append((message.note,round(clock,6)))
    expected=sorted((n['pitch'],round(n['tick']/frozen['ppq']*.5,6)) for n in frozen['tracks'][0]['notes'])
    assert sorted(events)==expected
    page.get_by_text('校正位移与节拍',exact=True).click()
    page.screenshot(path=str(output/'midi-timing-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path=str(output/'midi-timing-phone.png'),full_page=True)
    context.close()
    assert not errors,errors
    return {'checks':['BPM correction retains audio seconds and edited notes','undo restores timing',
                     'millisecond shift stays within selected track','negative shift refuses clipping',
                     'quantization preserves articulation','timing edits survive reload and exact MIDI export',
                     'desktop and phone layout'], 'page_errors':errors}
