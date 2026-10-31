"""Exercise cover seed controls through browser submissions without GPU calls."""
import io
import json
import wave

from playwright.sync_api import expect


def assert_cover_random_seeds(browser, url, output):
    from scripts.ui_browser_smoke import wait_for_ui, assert_no_page_overflow
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    page = context.new_page()
    errors, submitted, jobs = [], [], {}
    control = {'hold': False}
    page.on('pageerror', lambda error: errors.append(str(error)))
    def create_job(route):
        if route.request.method != 'POST':
            return route.continue_()
        data = route.request.post_data_json
        submitted.append(data)
        identifier = f'20261031-120000-{len(submitted):08x}'
        generation = data['request'].get('generate', data['request'])
        result = {'candidates': [{'seed': generation.get('seed'), 'audio': ''}]}
        job = {'id': identifier, 'kind': data['kind'], 'status': 'queued' if control['hold'] else 'complete',
               'result_panel': 'cover', 'result': result,
               'project_id': generation.get('project_id', '')}
        jobs[identifier] = job
        route.fulfill(json=job)
    page.route('**/api/jobs', create_job)
    def get_job(route):
        job = jobs[route.request.url.rsplit('/',1)[-1]]
        if not control['hold']:
            job['status'] = 'complete'
        route.fulfill(json=job)
    page.route('**/api/jobs/20261031-120000-*', get_job)
    page.goto(url, wait_until='domcontentloaded')
    wait_for_ui(page)
    page.locator('.studio-sidebar [data-tab="cover"]').click()
    page.evaluate("""() => {
      setCoverMode('generate'); document.querySelector('#cover-review').classList.remove('hidden');
    }""")
    page.locator('#cover-abc').fill('L:1/8\nM:4/4\nK:C\nC D E F G A B c|')
    page.locator('#cover-lyrics').fill('[Verse]\nMorning light')
    checkbox, seed = page.locator('#cover-random-seed'), page.locator('#cover-seed')
    button = page.locator('#generate-cover')
    def submit_melody():
        count = len(submitted)
        button.evaluate('button => { button.click(); button.click(); }')
        expect(page.locator('#cover-seed-used')).to_be_visible()
        page.wait_for_function('() => document.querySelector("#generate-cover").disabled === false')
        assert len(submitted) == count+1
        return submitted[-1]['request']['seed']
    checkbox.check()
    expect(seed).to_be_disabled()
    first = submit_melody()
    second = submit_melody()
    assert first > 0 and second > 0 and first != second
    assert page.locator('#cover-seed-used').get_attribute('data-seed') == str(second)
    page.wait_for_timeout(900)
    page.reload(wait_until='domcontentloaded'); wait_for_ui(page)
    page.locator('.studio-sidebar [data-tab="cover"]').click()
    expect(checkbox).to_be_checked()
    expect(seed).to_be_disabled()
    assert page.locator('#cover-seed-used').get_attribute('data-seed') == str(second)
    original_project = page.evaluate('window.workbenchProjectId()')
    other_project = context.request.post(url+'/api/workbench/projects',data={'title':'随机种子独立草稿'}).json()
    page.evaluate('window.refreshWorkbenchProject()')
    page.locator('.studio-sidebar [data-tab="project"]').click()
    page.locator('#workbench-project-select').select_option(other_project['id'])
    page.wait_for_function('(id) => window.workbenchProjectId() === id',arg=other_project['id'])
    expect(checkbox).not_to_be_checked()
    assert page.locator('#cover-seed-used').get_attribute('data-seed') == ''
    page.locator('#workbench-project-select').select_option(original_project or '__global__')
    page.wait_for_function('(id) => window.workbenchProjectId() === id',arg=original_project)
    page.locator('.studio-sidebar [data-tab="cover"]').click()
    expect(checkbox).to_be_checked()
    assert page.locator('#cover-seed-used').get_attribute('data-seed') == str(second)
    page.locator('#cover-reuse-seed').click()
    expect(checkbox).not_to_be_checked()
    expect(seed).to_have_value(str(second))
    assert submit_melody() == second
    assert submit_melody() == second
    seed.fill('0')
    expect(page.locator('#cover-seed-hint')).to_contain_text('当前为每次随机')
    zero_first, zero_second = submit_melody(), submit_melody()
    assert zero_first > 0 and zero_second > 0 and zero_first != zero_second
    expect(seed).to_have_value('0')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
        stream.writeframes(b'\0\0'*8000)
    page.locator('#reference-file').set_input_files({'name':'seed-reference.wav','mimeType':'audio/wav','buffer':buffer.getvalue()})
    voice_button = page.locator('#generate-reference-cover')
    expect(voice_button).to_be_enabled()
    voice_button.click()
    page.wait_for_function('document.querySelector("#generate-reference-cover").disabled === false')
    voice_first = submitted[-1]['request']['generate']['seed']
    voice_button.click()
    page.wait_for_function('document.querySelector("#generate-reference-cover").disabled === false')
    voice_second = submitted[-1]['request']['generate']['seed']
    assert submitted[-1]['kind'] == 'reference_cover'
    assert voice_first > 0 and voice_second > 0 and voice_first != voice_second
    assert submitted[-1]['request']['generate']['seed'] != 0
    control['hold'] = True
    voice_button.click()
    expect(voice_button).to_be_disabled()
    checkbox.check()
    expect(voice_button).to_be_disabled()
    count = len(submitted)
    voice_button.evaluate('button => button.click()')
    assert len(submitted) == count
    control['hold'] = False
    page.wait_for_function('document.querySelector("#generate-reference-cover").disabled === false')
    checkbox.uncheck()
    page.locator('#cover-file').set_input_files({'name':'seed-source.wav','mimeType':'audio/wav','buffer':buffer.getvalue()})
    page.locator('#cover-mode').select_option('direct')
    expect(voice_button).to_be_enabled()
    voice_button.click()
    page.wait_for_function('document.querySelector("#generate-reference-cover").disabled === false')
    assert submitted[-1]['kind'] == 'voice_convert'
    assert 'generate' not in submitted[-1]['request'] and 'seed' not in submitted[-1]['request']
    page.locator('#cover-mode').select_option('generate')
    seed.fill('-1')
    count = len(submitted)
    button.click()
    expect(page.locator('.studio-dialog')).to_contain_text('随机种子必须')
    page.locator('.studio-dialog button').click()
    assert len(submitted) == count
    seed.fill('0')
    page.locator('.cover-seed-settings').scroll_into_view_if_needed()
    page.screenshot(path=str(output/'cover-random-seed-desktop.png'),full_page=False)
    page.set_viewport_size({'width':390,'height':844})
    checkbox.check()
    page.locator('.cover-seed-settings').scroll_into_view_if_needed()
    assert_no_page_overflow(page, 'phone cover random seed')
    page.screenshot(path=str(output/'cover-random-seed-phone.png'),full_page=False)
    assert not errors,errors
    report = {'checks':['secure random checkbox creates distinct positive seeds',
        'zero input requests a fresh actual seed on every submission',
        'reference-cover generation uses the same random selection',
        'fixed and reused seed are reproducible', 'mode and actual seed survive reload and project switching',
        'toggling randomness cannot resubmit a running voice task', 'direct conversion keeps its existing request',
        'invalid input does not submit', 'desktop and phone layout'],
        'submitted_seeds':[item['request'].get('generate',item['request']).get('seed') for item in submitted],
        'page_errors':errors}
    (output/'cover-random-seeds.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    context.close()
    return report
