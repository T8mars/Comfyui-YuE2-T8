/* Standalone WebUI only. Drafts are revisioned; credentials never enter them. */
const assistant = {config: null, defaults: {}, result: null, job: null, polling: false, pollToken: 0, pollingJobId: '', pollingProjectId: '', originalLyrics: '', resultEdited: false, resultJobId: null,
  drafts: {}, providers: {}, localModels: [], remoteModels: {}, providerSelections: {}, providerBaseUrls: {}, providerCredentials: {}, providerBudgets: {}, activeProvider: null, credential: null,
  ticks: {assistant: 0, create: 0, plan: 0, cover: 0}, queues: {}, timers: {}, undo: null, sending: null,
  projectId: '', baselines: {}, switchQueue: Promise.resolve()};
const assistantPost = (path, value) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
const assistantText = (id, text) => { $(id).textContent = text; };
const cloneText = value => JSON.parse(JSON.stringify(value));
const CUSTOM_MODEL = '__custom__';
const ABC_COMPOSE = '自动创作 ABC（T8 LLM）/ Compose';
const formFields = form => Object.fromEntries([...form.elements].filter(el => el.name).map(el => [el.name, el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value]));
function putFields(form, fields) {
  for (const [key, value] of Object.entries(fields || {})) {
    const el = form.elements.namedItem(key); if (!el) continue;
    if (el.type === 'checkbox') el.checked = Boolean(value); else el.value = value ?? '';
  }
}
function assistantValues() {
  const values = {...assistant.defaults, ...formFields($('#assistant-form'))};
  if ($('#assistant-form').elements.lyrics.value === assistant.originalLyrics.replace(/\r\n?/g, '\n')) values.lyrics = assistant.originalLyrics;
  return values;
}
function readAssistantResult() {
  if (!assistant.result) return null;
  const r = {...assistant.result};
  for (const key of ['style', 'lyrics', 'abc']) {
    const text = $(`#assistant-result-${key}`).value;
    r[key] = String(r[key] || '').replace(/\r\n?/g, '\n') === text ? r[key] : text;
  }
  return r;
}
function captureDraft(panel) {
  if (panel === 'assistant') return {defaults_version: 2, values: assistantValues(), result: readAssistantResult(), job_id: assistant.job?.id || null, result_edited: assistant.resultEdited, result_job_id: assistant.resultJobId};
  if (panel === 'create') return {form: formFields($('#create-form'))};
  if (panel === 'plan') return {form: formFields($('#plan-form')), abc: $('#plan-abc').value,
    exact: $('#plan-exact').checked, plan: planState ? {source: planState.source || 'saved_exact', abc_status: planState.abc_status || null, plan_dir: planState.plan_dir || null, request: planState.request || {}} : null};
  return {abc: $('#cover-abc').value, lyrics: $('#cover-lyrics').value, style: $('#cover-style').value,
    seed: $('#cover-seed').value, mode: $('#cover-mode').value, instrumental: $('#cover').dataset.instrumental === 'true', visible: !$('#cover-review').classList.contains('hidden')};
}
function applyDraft(panel, draft) {
  if (!draft || !Object.keys(draft).length) return;
  if (panel === 'assistant') {
    putFields($('#assistant-form'), draft.values);
    assistant.originalLyrics = draft.values?.lyrics || '';
    if (draft.result) showAssistantResult(draft.result);
    assistant.resultEdited = Boolean(draft.result_edited); assistant.resultJobId = draft.result_job_id || null;
    if (draft.job_id) assistant.job = {id: draft.job_id};
  } else if (panel === 'create') {
    putFields($('#create-form'), draft.form); updateInstrumental('create');
  } else if (panel === 'plan') {
    putFields($('#plan-form'), draft.form);
    planState = draft.plan || null;
    $('#plan-abc').value = draft.abc || '';
    const imported = planState?.source === 'imported_abc';
    $('#plan-exact').checked = Boolean(draft.exact && !imported);
    $('#plan-exact').disabled = imported;
    $('#plan-abc').disabled = $('#plan-exact').checked;
    $('#plan-badge').textContent = imported && planState?.abc_status && planState.abc_status !== 'validated' ? '未校验导入谱 · 请先修改' : imported ? '外部导入谱 · 重新生成' : '原始计划';
    $('#plan-workbench').classList.toggle('hidden', !planState);
    updateInstrumental('plan');
  } else {
    for (const key of ['abc', 'lyrics', 'style', 'seed']) if (draft[key] !== undefined) $(`#cover-${key}`).value = draft[key];
    $('#cover').dataset.instrumental = String(Boolean(draft.instrumental));
    $('#cover-review').classList.toggle('hidden', !draft.visible);
    window.setCoverMode?.(draft.mode || 'generate');
    updateInstrumental('cover');
  }
}
function savePanel(panel, value) {
  if (assistant.drafts[panel]?.error) return Promise.reject(new Error(assistant.drafts[panel].error));
  const snapshot = cloneText(value || captureDraft(panel));
  const operation = (assistant.queues[panel] || Promise.resolve()).catch(() => {}).then(async () => {
    const revision = assistant.drafts[panel]?.revision ?? 0;
    const saved = await assistantPost('/api/assistant/drafts', {panel, project_id: assistant.projectId, revision, draft: snapshot});
    assistant.drafts[panel] = saved;
    return saved;
  });
  assistant.queues[panel] = operation;
  return operation;
}
function changedDraft(panel) {
  assistant.ticks[panel]++;
  clearTimeout(assistant.timers[panel]);
  assistant.timers[panel] = setTimeout(() => savePanel(panel).then(() => {
    if (panel === 'assistant') assistantText('#assistant-draft-status', '草稿已保存');
  }).catch(error => assistantText('#assistant-draft-status', `草稿未保存：${error.message}`)), 650);
}
window.assistantDraftRevision = panel => assistant.ticks[panel];
window.assistantDraftChanged = changedDraft;
window.assistantCaptureDraft = captureDraft;
function draftEndpoint(projectId = assistant.projectId) {
  return '/api/assistant/drafts' + (projectId ? `?project_id=${encodeURIComponent(projectId)}` : '');
}
function resetDraftPanel(panel) {
  if (panel === 'assistant') {
    assistant.result = null; assistant.job = null; assistant.resultEdited = false; assistant.resultJobId = null;
    $('#assistant-result').classList.add('hidden'); $('#assistant-progress').replaceChildren();
  } else if (panel === 'plan') {
    planState = null; $('#plan-workbench').classList.add('hidden'); $('#plan-abc').value = '';
  } else if (panel === 'cover') {
    $('#cover-review').classList.add('hidden');
  }
  applyDraft(panel, cloneText(assistant.baselines[panel] || {}));
}
async function switchAssistantProject(projectId) {
  projectId = String(projectId || '');
  if (projectId === assistant.projectId) return;
  const previousProjectId=assistant.projectId,previousJobId=assistant.job?.id||'';
  assistant.pollToken++;
  assistant.polling = false;
  assistant.pollingJobId = '';
  assistant.pollingProjectId = '';
  applyAssistantActionState();
  const panels=['create','plan','cover','assistant'],elements=panels.map(panel=>$(`#${panel}`));
  elements.forEach(element=>element.inert=true);
  try {
    for (const panel of panels) clearTimeout(assistant.timers[panel]);
    await Promise.all(panels.map(panel => savePanel(panel)));
    const drafts = await api(draftEndpoint(projectId));
    assistant.projectId = projectId;
    assistant.queues = {};
    assistant.drafts = drafts;
    for (const selector of ['#cover-file','#reference-file']) {
      const input=$(selector); clearLocalInputReference(input); input.value='';
      input.dispatchEvent(new CustomEvent('local-source-change',{bubbles:true}));
    }
    for (const panel of panels) {
      resetDraftPanel(panel); applyDraft(panel, drafts[panel]?.draft); assistant.ticks[panel]++;
    }
    assistantText('#assistant-draft-status', projectId ? '已切换到当前项目的独立草稿' : '已切换到未归档草稿');
    await restoreAssistantJobForCurrentScope().catch(error=>assistantText('#assistant-progress',`任务状态读取失败：${error.message}，稍后可刷新恢复。`));
  } catch(error) {
    if(assistant.projectId===previousProjectId&&previousJobId){assistant.job={id:previousJobId};await restoreAssistantJobForCurrentScope().catch(()=>{});}
    throw error;
  } finally { elements.forEach(element=>element.inert=false); }
}
window.assistantSwitchProject = projectId => {
  const operation=assistant.switchQueue.catch(()=>{}).then(()=>switchAssistantProject(projectId));
  assistant.switchQueue=operation;
  return operation;
};
function updateInstrumental(panel) {
  if (panel === 'cover') {
    const instrumental = $('#cover').dataset.instrumental === 'true';
    const backend=$('#voice-backend').value,direct=$('#cover-mode').value==='direct';
    $('#generate-reference-cover').disabled = instrumental ||
      (backend!=='rvc'&&!inputHasSource($('#reference-file'))) ||
      (backend!=='seed-vc'&&!$('#rvc-cover-model').value) ||
      (direct&&!inputHasSource($('#cover-file')));
    $('#generate-reference-cover').title = instrumental ? '纯器乐没有可转换的人声，请使用旋律重制' : '';
    return;
  }
  const form = $(`#${panel}-form`);
  const instrumental = form.elements.instrumental?.checked || false;
  form.elements.lyrics.required = !instrumental;
}
function addInstrumentalControl(panel) {
  const form = $(`#${panel}-form`), label = document.createElement('label');
  label.className = 'check wide';
  label.innerHTML = '<input name="instrumental" type="checkbox">纯器乐（允许歌词为空）';
  form.append(label);
}
function showAssistantResult(result) {
  assistant.resultEdited = false;
  assistant.resultJobId = assistant.job?.id || null;
  assistant.result = {style: result.style || '', lyrics: result.lyrics || '', abc: result.abc || '',
    cot: result.cot || result.request?.cot || assistantValues().cot, instrumental: result.instrumental ?? result.fields?.instrumental ?? false,
    abc_status: result.abc_status || 'not_requested', outcome: result.outcome || 'in_progress',
    report: result.report || {}, requests: result.requests || 0};
  $('#assistant-result').classList.remove('hidden');
  for (const key of ['style', 'lyrics', 'abc']) $(`#assistant-result-${key}`).value = assistant.result[key];
  const partial = result.outcome === 'partial_success';
  assistantText('#assistant-outcome', partial ? '部分完成 · 已保留可用内容' : result.outcome === 'success' ? '创作完成' : '已保存的创作内容');
  assistantText('#assistant-result-note', '文本与谱面检查不等于听感验收。发送仅填入目标页面，不会自动开始制作音频。');
  renderAbcState();
  const compose = $('#assistant-compose-abc');
  compose.classList.toggle('hidden', (Boolean(assistant.result.abc.trim()) && assistant.result.abc_status !== 'failed') || assistant.result.cot === 'off');
  compose.textContent = assistant.result.abc_status === 'failed' ? '重新生成 ABC' : '补写 ABC';
  assistantText('#assistant-report', JSON.stringify(result.report || result, null, 2));
}
function renderAbcState() {
  const labels = {validated: 'ABC 已通过原生格式校验；编辑后需要重新校验。', failed: '已保留模型返回的 ABC，但未通过原生格式校验。仍可原样发送到乐谱计划的 ABC 提示词框，也可以下载、修改或点“重新生成 ABC”。',
    downstream_yue2: 'ABC 留空：将在目标页面交给 YuE2 规划。', off: '当前选择不用谱面。', pending: 'ABC 已修改，发送前将重新校验。',
    not_requested: '尚未生成 ABC。'};
  const status = assistant.result?.abc_status || 'not_requested';
  const reason = status === 'failed' ? String(assistant.result?.report?.abc?.error || '').trim().slice(0, 500) : '';
  $('#assistant-abc-status').dataset.state = status;
  assistantText('#assistant-abc-status', (labels[status] || '谱面状态待检查') + (reason ? ` 失败原因：${reason}` : ''));
}
function configFromForm() {
  const values = formFields($('#assistant-config-form'));
  values.credential_id = assistant.providerCredentials[values.provider] || '';
  values.extra_parameters = JSON.parse($('#assistant-extra').value || '{}');
  return values;
}
function assistantCredentialError(message) {
  return /API\s*Key|API\s*凭据|凭据不存在|凭据待补|重新填写/.test(String(message || ''));
}
function openAssistantSettings(focus = true) {
  const settings = $('#assistant-settings');
  settings.open = true;
  settings.scrollIntoView({behavior: 'smooth', block: 'start'});
  if (focus) requestAnimationFrame(() => ($('#assistant-provider').value === 'local' ? $('#assistant-model-choice') : $('#assistant-key')).focus());
}
function assistantChannelReady() {
  const provider=$('#assistant-provider').value||assistant.config?.provider||'seedance';
  if(provider==='local')return Boolean($('#assistant-model').value.trim());
  return Boolean($('#assistant-key').value.trim()||assistant.providerCredentials[provider]);
}
function applyAssistantActionState() {
  const ready=assistantChannelReady(),disabled=assistant.polling||!ready;
  for(const selector of ['#assistant-generate','#assistant-test','#assistant-retry','#assistant-compose-abc']){
    const button=$(selector);if(!button)continue;button.disabled=disabled;
    button.title=!ready?'请先设置当前渠道的 API Key 或本地 GGUF 模型':assistant.polling?'当前创作任务结束后可再次操作':'';
  }
  const remove=$('#assistant-delete-key'),provider=$('#assistant-provider').value;
  if(remove)remove.disabled=!assistant.providerCredentials[provider]||assistant.polling;
}
function renderAssistantChannelStatus() {
  const provider = $('#assistant-provider').value || assistant.config?.provider || 'seedance';
  const details = assistant.providers[provider] || {};
  const savedProvider = assistant.config?.provider === provider;
  const isLocal = provider === 'local';
  const pendingKey=Boolean($('#assistant-key').value.trim());
  const ready = isLocal ? Boolean($('#assistant-model').value.trim()) : Boolean(pendingKey||assistant.providerCredentials[provider]||(savedProvider&&assistant.credential?.available));
  const card = $('#assistant-channel-status'), signup = $('#assistant-status-signup');
  card.dataset.state = ready ? 'ready' : 'missing';
  if (isLocal) {
    assistantText('#assistant-channel-title', ready ? '本地 LLM 已选择' : '请先选择本地 GGUF 模型');
    assistantText('#assistant-channel-detail', ready ? `${$('#assistant-model').value}；保存后可测试模型连接。` : '本地模式无需 API Key，请选择 GGUF 文件并保存设置。');
    $('#assistant-open-settings').textContent = ready ? '更改本地模型' : '设置本地 GGUF';
  } else {
    assistantText('#assistant-channel-title', ready ? 'AI 创作渠道已配置' : '开始创作前，请先设置 API Key');
    assistantText('#assistant-channel-detail', ready ? `${details.label || '当前渠道'} · ${$('#assistant-model').value || details.default_model || '模型待选择'}；${pendingKey?'API Key 待随本次操作保存':'凭据已就绪'}。` : `当前渠道：${details.label || 'API'}。填写 API Key 后才能生成歌词、曲风或 ABC。`);
    $('#assistant-open-settings').textContent = ready ? '更改渠道 / API Key' : '设置 API Key';
  }
  signup.classList.toggle('hidden', isLocal || !details.signup_url);
  signup.href = details.signup_url || '#';
  signup.textContent = details.signup_url ? `获取${details.label || ''} API Key` : '';
  assistantText('#assistant-capability', ready ? (isLocal ? '本地模型待连接测试' : 'API 凭据已就绪') : (isLocal ? '尚未选择 GGUF' : '尚未配置 API Key'));
  if (!ready && !isLocal && savedProvider) $('#assistant-settings').open = true;
  applyAssistantActionState();
}
function appendAssistantError(target, message) {
  const box = document.createElement('div'); box.className = assistantCredentialError(message) ? 'assistant-credential-error' : 'assistant-inline-note';
  const error = document.createElement('p'); error.textContent = message; box.append(error);
  if (assistantCredentialError(message)) {
    assistant.credential = {...(assistant.credential || {}), required: true, available: false, reason: 'missing'};
    renderAssistantChannelStatus();
    const action = document.createElement('button'); action.type = 'button'; action.className = 'primary compact'; action.textContent = '设置 API Key'; action.onclick = () => openAssistantSettings(true); box.append(action);
  }
  target.append(box);
}
function showAssistantError(message) {
  const target = $('#assistant-progress'); target.replaceChildren(); appendAssistantError(target, message);
}
function renderAssistantModels() {
  const provider = $('#assistant-provider').value;
  const details = assistant.providers[provider] || {};
  const items = provider === 'local' ? assistant.localModels :
    (assistant.remoteModels[provider] || (details.models || []).map(id => ({id, label: id})));
  const seen = new Set(), options = [];
  for (const item of items) {
    const id = typeof item === 'string' ? item : item.id;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const opt = document.createElement('option'); opt.value = id;
    opt.textContent = typeof item === 'string' ? item : item.label || id;
    options.push(opt);
  }
  const suggestions = options.map(option => option.cloneNode(true));
  const custom = document.createElement('option'); custom.value = CUSTOM_MODEL; custom.textContent = 'Custom · 自定义输入模型'; options.push(custom);
  const current = $('#assistant-model').value.trim();
  $('#assistant-model-choice').replaceChildren(...options);
  $('#assistant-model-choice').value = current && seen.has(current) ? current : CUSTOM_MODEL;
  $('#assistant-models').replaceChildren(...suggestions);
  $('#assistant-custom-model-field').classList.toggle('hidden', $('#assistant-model-choice').value !== CUSTOM_MODEL);
}
function assistantModelChoiceChanged(focus = true) {
  const choice = $('#assistant-model-choice').value;
  const custom = choice === CUSTOM_MODEL;
  $('#assistant-custom-model-field').classList.toggle('hidden', !custom);
  $('#assistant-model').value = custom ? '' : choice;
  assistant.providerSelections[$('#assistant-provider').value] = $('#assistant-model').value;
  if (custom && focus) $('#assistant-model').focus();
  renderAssistantChannelStatus();
}
function providerChanged() {
  const provider = $('#assistant-provider').value;
  const previous = assistant.activeProvider;
  if (previous) {
    assistant.providerSelections[previous] = $('#assistant-model').value;
    assistant.providerBaseUrls[previous] = $('#assistant-base-url').value;
    assistant.providerBudgets[previous] = Number($('#assistant-config-form').elements.max_tokens.value);
  }
  assistant.activeProvider = provider;
  const details = assistant.providers[provider] || {};
  $$('[data-api-config]').forEach(el => el.classList.toggle('hidden', provider === 'local'));
  $$('[data-local-config]').forEach(el => el.classList.toggle('hidden', provider !== 'local'));
  $('#assistant-base-url').disabled = provider !== 'compatible';
  $('#assistant-base-url').value = details.base_url || assistant.providerBaseUrls[provider] || '';
  if (previous !== provider || !$('#assistant-model').value.trim()) {
    const localDefault = assistant.localModels.find(item => item.id === details.default_model)?.id || assistant.localModels[0]?.id;
    $('#assistant-model').value = assistant.providerSelections[provider] || details.default_model || (provider === 'local' ? localDefault || '' : '');
  }
  if (previous && previous !== provider) {
    $('#assistant-config-form').elements.max_tokens.value = assistant.providerBudgets[provider] ?? (provider === 'local' ? 4096 : 32768);
  }
  const signup = $('#assistant-signup');
  signup.classList.toggle('hidden', !details.signup_url);
  signup.href = details.signup_url || '#';
  signup.textContent = provider === 'seedance' ? '获取贞贞平价小屋 API Key' : provider === 'workshop' ? '获取贞贞 AI 工坊 API Key' : '';
  $('#assistant-refresh-models').textContent = provider === 'local' ? '刷新本地 GGUF' : '获取模型 LIST';
  renderAssistantModels();
  assistantText('#assistant-model-list-status', details.models?.length && provider !== 'local' ? `已提供 ${details.models.length} 个渠道默认模型` : '');
  if (previous && previous !== provider && assistant.config?.provider !== provider) {
    assistantText('#assistant-config-status', provider === 'local' ? '已切换为本地模式，请保存设置' : '已切换渠道，请填写该渠道对应的 API Key');
  }
  renderAssistantChannelStatus();
}
async function saveAssistantConfig() {
  let config = configFromForm();
  const key = $('#assistant-key').value;
  if (key) {
    const saved = await assistantPost('/api/assistant/credentials', {api_key: key, config, remember: $('#assistant-remember-key').checked});
    config.credential_id = saved.credential_id;
    assistant.providerCredentials[config.provider] = saved.credential_id;
    $('#assistant-key').value = '';
  }
  const info = await assistantPost('/api/assistant/config', config);
  assistant.config = info.config;
  assistant.credential = info.credential;
  updateModelInfo(info);
  renderAssistantChannelStatus();
  assistantText('#assistant-config-status', '设置已保存' + (config.credential_id ? ' · 已关联凭据' : ''));
  return config;
}
function updateModelInfo(info) {
  assistant.providers = info.providers || assistant.providers;
  assistant.localModels = info.models.map(model => ({id: model.id,
    label: [model.architecture, `${(model.bytes / 2**30).toFixed(1)} GiB`, model.context_length ? `上下文 ${model.context_length}` : '', model.shards > 1 ? `${model.shards} 个分片` : '', model.error].filter(Boolean).join(' · ')}));
  renderAssistantModels();
  assistantText('#assistant-capability', info.local_runtime ? `${info.local_gpu_offload ? '本地 CUDA 环境已就绪' : '本地环境仅通过 CPU 检查'}，需通过模型连接测试` : '本地 GGUF 环境未完成安装 · API 可用');
}
async function refreshAssistantModels() {
  const provider = $('#assistant-provider').value;
  const button = $('#assistant-refresh-models');
  button.disabled = true;
  try {
    const config = await saveAssistantConfig();
    if (provider === 'local') {
      assistantText('#assistant-model-list-status', `已扫描到 ${assistant.localModels.length} 个本地 GGUF`);
      return;
    }
    assistantText('#assistant-model-list-status', '正在读取渠道模型 LIST…');
    const result = await assistantPost('/api/assistant/models', {config});
    assistant.remoteModels[provider] = result.models.map(id => ({id, label: id}));
    if($('#assistant-provider').value!==provider)return;
    if (!$('#assistant-model').value.trim()) $('#assistant-model').value = result.models[0];
    renderAssistantModels();
    assistant.providerSelections[provider] = $('#assistant-model').value;
    assistantText('#assistant-model-list-status', `已读取 ${result.count} 个模型；仍可手动填写 ID`);
  } catch (error) {
    assistantText('#assistant-model-list-status', `${error.message}；已保留默认模型和手动填写`);
  } finally { button.disabled = false; }
}
function updateCostHint() {
  const v = assistantValues(), score = v.abc_source?.includes('Compose') && v.cot !== 'off';
  assistantText('#assistant-cost-hint', `${score ? '将调用当前 LLM 作谱，可能需要数分钟；失败最多修正一次。本地小模型的谱面可能无法通过校验。' : v.cot === 'off' ? '只生成文本，不使用 ABC。' : '本次先生成文本；ABC 交给目标页面的 YuE2 规划。'} 每项流程最多 8 次模型调用；网络异常不会自动重发。`);
  $('#assistant-generate').textContent = score ? '创作歌词、曲风与 ABC' : '创作歌词与曲风';
}
const assistantStages = {assistant_lyrics: '创作歌词', assistant_lyrics_language_repair: '修正歌词语言', assistant_style: '创作曲风', assistant_review: '审校文本', assistant_review_repair: '修订文本', assistant_abc: '创作 ABC', assistant_abc_repair: '修正 ABC', assistant_connection: '测试模型连接'};
window.assistantStageLabel = stage => assistantStages[stage];
async function pollAssistant(id, startingRevision, projectId = assistant.projectId) {
  if (assistant.polling && assistant.pollingJobId === id && assistant.pollingProjectId === projectId) return;
  const pollToken = ++assistant.pollToken;
  assistant.polling = true;
  assistant.pollingJobId = id;
  assistant.pollingProjectId = projectId;
  applyAssistantActionState();
  try {
    let lastResult = '';
    while (true) {
      const job = await api(`/api/jobs/${id}`);
      if (pollToken !== assistant.pollToken || assistant.projectId !== projectId) return;
      assistant.job = job;
      const seconds = Math.max(0, Math.round(Date.now() / 1000 - (job.started_at || job.created_at)));
      const progress = $('#assistant-progress'); progress.replaceChildren();
      const line = document.createElement('p');
      line.textContent = `${job.status === 'queued' ? '等待当前任务结束后开始' : assistantStages[job.stage] || stageLabel(job.stage)} · 已用 ${seconds} 秒${job.requests ? ` · ${job.requests} 次调用` : ''}`;
      progress.append(line);
      if (!TERMINAL.has(job.status)) {
        const cancel = document.createElement('button'); cancel.className = 'danger compact'; cancel.textContent = '取消本次创作';
        cancel.onclick = () => assistantPost(`/api/jobs/${id}/cancel`, {}); progress.append(cancel);
      }
      const signature = JSON.stringify(job.result || {});
      if (job.result && signature !== lastResult && assistant.ticks.assistant === startingRevision && !job.result.connection) {
        showAssistantResult(job.result); lastResult = signature;
        // Each paid model checkpoint is useful by itself. Persist it immediately so
        // a workspace switch, reload, or long-running later stage cannot hide it.
        await savePanel('assistant').catch(error => assistantText('#assistant-draft-status', `阶段结果未保存：${error.message}`));
      }
      if (TERMINAL.has(job.status)) {
        if (job.error) appendAssistantError(progress, job.error);
        if (job.result?.connection) line.textContent = '模型连接测试通过；这是文本请求测试，不代表作谱或音乐生成已验收。';
        if (assistant.ticks.assistant !== startingRevision && job.result && !job.result.connection) {
          const restore = document.createElement('button'); restore.className = 'ghost'; restore.textContent = '查看已完成结果（保留当前草稿前请先下载）';
          restore.onclick = async () => { if (await uiConfirm('用这个任务的结果替换当前编辑区？')) { showAssistantResult(job.result); changedDraft('assistant'); } }; progress.append(restore);
        }
        await savePanel('assistant'); refreshWorkspace(); loadHistory(); break;
      }
      await new Promise(resolve => setTimeout(resolve, 1200));
      if (pollToken !== assistant.pollToken) return;
    }
  } catch (error) { if(pollToken===assistant.pollToken)assistantText('#assistant-progress', `连接中断：${error.message}。任务可能仍在运行，刷新后可恢复查看。`); }
  finally { if(pollToken===assistant.pollToken){assistant.polling=false;assistant.pollingJobId='';assistant.pollingProjectId='';applyAssistantActionState();} }
}
async function latestAssistantJobForScope(projectId = assistant.projectId) {
  const scope=projectId||'__global__';
  const recent = await api(`/api/jobs?limit=1&kind=assistant&exclude_connection=1&project_id=${encodeURIComponent(scope)}`);
  return recent.jobs.find(job => job.kind === 'assistant' && !job.result?.connection && job.summary !== '测试 LLM 连接'
    && String(job.project_id || job.request?.project_id || '') === String(projectId || '')) || null;
}
async function restoreAssistantJobForCurrentScope() {
  const id=assistant.job?.id,projectId=assistant.projectId;
  let current=id ? await api(`/api/jobs/${id}`).catch(()=>null) : null;
  if(current&&String(current.project_id||current.request?.project_id||'')!==projectId)current=null;
  // The job store is authoritative. This also recovers when the browser left
  // before its draft could record the new job id.
  const latest=await latestAssistantJobForScope(projectId).catch(()=>null);
  if(latest&&(!current||Number(latest.created_at||0)>Number(current.created_at||0)))current=latest;
  if(!current){assistant.job=null;return;}
  assistant.job=current;
  if(current.result&&!current.result.connection&&!assistant.resultEdited){
    showAssistantResult(current.result);
    await savePanel('assistant').catch(error=>assistantText('#assistant-draft-status',`任务结果未保存：${error.message}`));
  } else if(current.result&&!current.result.connection&&assistant.resultEdited&&TERMINAL.has(current.status)){
    const notice=$('#assistant-progress'); notice.textContent='该任务已结束。当前编辑已保留，可查看任务的最新结果。';
    const button=document.createElement('button'); button.className='ghost'; button.textContent='查看最新任务结果';
    button.onclick=async()=>{if(await uiConfirm('替换当前编辑区？需要保留的内容请先下载。')){showAssistantResult(current.result);changedDraft('assistant');}};
    notice.append(button);
  }
  if(!TERMINAL.has(current.status)){pollAssistant(current.id,assistant.ticks.assistant,projectId);return;}
  if(current.error)appendAssistantError($('#assistant-progress'),current.error);
}
window.assistantRestoreCurrentScope = () => restoreAssistantJobForCurrentScope()
  .catch(error=>assistantText('#assistant-progress',`任务状态读取失败：${error.message}，稍后可刷新恢复。`));
async function startAssistant(test = false, retry = false) {
  if (assistant.polling) return;
  try {
    const config = await saveAssistantConfig(), values = assistantValues();
    if (config.provider !== 'local' && !assistant.credential?.available) {
      openAssistantSettings(true);
      throw new Error('请先填写并保存 API Key，再开始创作。');
    }
    await savePanel('assistant');
    const body = {values, config, client_request_id: crypto.randomUUID()};
    let job;
    if (retry) {
      if (!assistant.job?.id) throw new Error('请先选择一个助手任务');
      body.retry_stages = $('#assistant-retry-stage').value ? [$('#assistant-retry-stage').value] : [];
      const edited = readAssistantResult(), stage = $('#assistant-retry-stage').value;
      body.final_fields = {};
      if (edited && stage !== 'lyrics') {
        if (stage || edited.lyrics !== assistant.result.lyrics) Object.assign(body.final_fields, {lyrics: edited.lyrics, instrumental: edited.instrumental});
        if (stage !== 'style' && (stage || edited.style !== assistant.result.style)) body.final_fields.style = edited.style;
      }
      if (stage === 'abc') body.values.quality_mode = assistant.defaults.quality_mode;
      job = await assistantPost(`/api/jobs/${assistant.job.id}/retry-assistant`, body);
    } else job = await assistantPost('/api/jobs', {kind: 'assistant', source: 'webui', result_panel: 'assistant', client_request_id: body.client_request_id,
      request: {values, config, project_id: assistant.projectId, variant_id: crypto.randomUUID(), test_connection: test}});
    assistant.job = job;
    await savePanel('assistant');
    refreshWorkspace();
    pollAssistant(job.id, assistant.ticks.assistant);
  } catch (error) { showAssistantError(error.message); }
}
async function validateAssistantAbc(strip = false, cot) {
  const result = readAssistantResult();
  if (!result?.abc.trim()) throw new Error('没有可用 ABC；可以先发送歌词与曲风');
  const checked = await assistantPost('/api/assistant/validate-abc', {abc: result.abc, cot: cot || result.cot, strip_chords: strip});
  return checked;
}
function prepareTransfer(panel) {
  const r = readAssistantResult(); if (!r) return;
  assistant.sending = {panel, result: cloneText(r), sourceTick: assistant.ticks.assistant, targetTick: assistant.ticks[panel]};
  const dialog = $('#assistant-send-dialog');
  assistantText('#assistant-send-title', `发送到${{create: '歌曲创作', plan: '乐谱计划', cover: '旋律重制'}[panel]}`);
  for (const name of ['style', 'lyrics', 'abc']) {
    const input = dialog.querySelector(`[name=${name}]`);
    input.disabled = name === 'abc' && (panel === 'create' || !r.abc.trim() || (panel === 'cover' && r.abc_status !== 'validated'));
    input.checked = !input.disabled;
  }
  $('#assistant-send-mode').value = r.cot === 'off' ? '' : panel === 'cover' ? 'melody' : r.cot;
  $('#assistant-send-mode-label').classList.toggle('hidden', panel !== 'plan');
  assistantText('#assistant-send-warning', '将替换目标页面中选中的字段，已有种子与上传音频保留。填入后可撤销。');
  assistantText('#assistant-send-details', panel === 'plan' && r.abc_status !== 'validated' ? '这份 ABC 未通过校验，但会完整原样填入乐谱计划的 ABC 提示词框并标明状态；修改后可再开始生成。' : panel === 'cover' && r.abc ? '发送谱面时会去除和弦，并验证两个声部的音高与节奏不变。只选 ABC 可能与目标已有歌词不匹配。' : panel === 'create' && r.abc ? '该页只接收歌词、曲风及规划模式；如需使用这份 ABC，请发送到乐谱计划。' : '有 ABC 时建议整套发送，避免谱面与目标页面的旧歌词错配。');
  dialog.showModal();
}
async function confirmTransfer() {
  const pending = assistant.sending; if (!pending) return;
  const {panel, result: r} = pending, dialog = $('#assistant-send-dialog');
  const selected = name => dialog.querySelector(`[name=${name}]`).checked && !dialog.querySelector(`[name=${name}]`).disabled;
  const before = captureDraft(panel), after = cloneText(before);
  const mode = panel === 'plan' ? $('#assistant-send-mode').value : panel === 'cover' ? 'melody' : r.cot;
  try {
    if (!['style', 'lyrics', 'abc'].some(selected)) throw new Error('至少选择一个字段');
    if (panel === 'plan' && !mode) throw new Error('乐谱页需要明确选择旋律或完整谱面；也可取消并发送到歌曲创作，保留 off');
    let abc = null;
    if (selected('abc')) abc = r.abc_status === 'validated'
      ? (await assistantPost('/api/assistant/validate-abc', {abc: r.abc, cot: mode, strip_chords: panel === 'cover'})).abc
      : r.abc;
    if (panel === 'cover') {
      if (selected('style')) after.style = r.style;
      if (selected('lyrics')) { after.lyrics = r.lyrics; after.instrumental = r.instrumental && !r.lyrics.trim(); }
      if (abc !== null) after.abc = abc;
      after.visible = true;
      after.mode = 'generate';
    } else {
      if (selected('style')) after.form.style = r.style;
      if (selected('lyrics')) { after.form.lyrics = r.lyrics; after.form.instrumental = r.instrumental && !r.lyrics.trim(); }
      after.form.cot = mode;
      if (panel === 'plan') {
        if (abc !== null) { after.abc = abc; after.exact = false; after.plan = {source: 'imported_abc', abc_status: r.abc_status, request: {style: after.form.style, lyrics: after.form.lyrics, cot: mode}}; }
        else if (after.plan) { after.exact = false; after.plan.source = 'imported_abc'; }
      }
    }
    if (assistant.ticks.assistant !== pending.sourceTick || assistant.ticks[panel] !== pending.targetTick) throw new Error('内容已发生变化，请关闭后重新发送，避免覆盖新编辑');
    clearTimeout(assistant.timers[panel]);
    await savePanel(panel, after);
    if (assistant.ticks[panel] !== pending.targetTick) { await savePanel(panel); throw new Error('目标页在发送期间有新编辑，已保留新编辑，请重新发送'); }
    applyDraft(panel, after); assistant.ticks[panel]++;
    assistant.undo = {panel, before, tick: assistant.ticks[panel]};
    dialog.close(); $(`.tab[data-tab=${panel}]`).click();
    const target = panel === 'cover' ? $('#cover-review') : panel === 'plan' && abc ? $('#plan-workbench') : $(`#${panel}-form`);
    target.scrollIntoView({behavior: 'smooth', block: 'start'});
    $('#assistant-transfer-notice').classList.remove('hidden');
    $('#assistant-transfer-notice span').textContent = '已填入草稿，尚未开始生成音频。';
  } catch (error) { assistantText('#assistant-send-details', error.message); }
}
function downloadText(name, text) {
  const url = URL.createObjectURL(new Blob([text], {type: 'text/plain;charset=utf-8'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
window.openAssistantJob = async id => {
  try {
    const data = await api(`/api/assistant/jobs/${id}`);
    if(String(data.request.project_id||'')!==assistant.projectId)throw new Error('这个助手任务属于另一个项目，请先切换到对应项目再载入');
    if (assistant.result && !await uiConfirm('载入该任务？当前内容可先保存或下载。')) return;
    putFields($('#assistant-form'), data.request.values); assistant.originalLyrics = data.request.values.lyrics || '';
    if (data.job.result) showAssistantResult(data.job.result);
    assistant.job = data.job; changedDraft('assistant'); $('.tab[data-tab=assistant]').click();
    if (!TERMINAL.has(data.job.status)) pollAssistant(id, assistant.ticks.assistant);
  } catch (error) { alert(error.message); }
};
function addAdvanced(defaults, options) {
  const fields = [['quality_mode', '创作审校', options.quality_modes], ['style_language', '曲风描述语言', ['English', '中文']],
    ['structure', '歌曲结构'], ['genre', '曲风'], ['vocal', '人声'], ['instruments', '乐器'], ['bpm', 'BPM（0 为自动）', 'number'],
    ['meter', '拍号'], ['key_scale', '调性'], ['target_duration_seconds', '时长意向（秒）', 'number'],
    ['constraints', '其他要求'], ['creativity', '创作自由度', ['strict', 'balanced', 'creative']], ['edit_section', '要改写的段落'],
    ['edit_occurrence', '第几次出现', 'number'], ['edit_request', '改词要求'], ['abc', '已有 ABC（优先保留）', 'textarea'],
    ['abc_action', '已有谱面处理', ['保留 / Preserve', '去和弦，保留双声部旋律 / Strip chords']], ['seed', '文字随机种子', 'number']];
  const target = $('#assistant-advanced');
  for (const [name, caption, type] of fields) {
    const label = document.createElement('label'); label.textContent = caption;
    const input = document.createElement(Array.isArray(type) ? 'select' : type === 'textarea' ? 'textarea' : 'input'); input.name = name;
    if (Array.isArray(type)) for (const text of type) { const opt = document.createElement('option'); opt.value = text; opt.textContent = text; input.append(opt); }
    else if (input.tagName === 'INPUT') input.type = type || 'text';
    if (type === 'textarea') { input.rows = 7; label.className = 'wide'; }
    input.value = defaults[name] ?? ''; label.append(input); target.append(label);
  }
}
async function initAssistant() {
  for (const panel of ['create', 'plan']) addInstrumentalControl(panel);
  try {
    assistant.projectId = window.workbenchProjectId?.() || '';
    const [info, drafts] = await Promise.all([api('/api/assistant/config'), api(draftEndpoint())]);
    assistant.config = info.config; assistant.credential = info.credential; assistant.defaults = info.defaults; assistant.drafts = drafts; assistant.providers = info.providers;
    assistant.providerSelections[info.config.provider] = info.config.model;
    assistant.providerBaseUrls[info.config.provider] = info.config.base_url;
    assistant.providerCredentials[info.config.provider] = info.config.credential_id || '';
    assistant.providerBudgets[info.config.provider] = info.config.max_tokens;
    for (const [id, provider] of Object.entries(info.providers)) { const opt = document.createElement('option'); opt.value = id; opt.textContent = provider.label; $('#assistant-provider').append(opt); }
    for (const [selector, values] of [['#assistant-lyrics-mode', info.options.lyrics_modes], ['#assistant-abc-source', info.options.abc_sources]]) {
      for (const value of values) { const opt = document.createElement('option'); opt.value = value; opt.textContent = value; $(selector).append(opt); }
    }
    addAdvanced(info.defaults, info.options); putFields($('#assistant-form'), info.defaults);
    putFields($('#assistant-config-form'), info.config); $('#assistant-extra').value = JSON.stringify(info.config.extra_parameters || {});
    updateModelInfo(info); providerChanged();
    assistant.baselines = Object.fromEntries(['create', 'plan', 'cover', 'assistant'].map(panel => [panel, cloneText(captureDraft(panel))]));
    for (const panel of ['create', 'plan', 'cover', 'assistant']) applyDraft(panel, drafts[panel]?.draft);
    const draftErrors = Object.entries(drafts).filter(([, draft]) => draft.error);
    if (draftErrors.length) assistantText('#assistant-draft-status', draftErrors.map(([panel, draft]) => `${panel}：${draft.error}`).join('；'));
    if (planState?.source === 'saved_exact' && planState.plan_dir) {
      const state = await assistantPost('/api/assistant/check-plan', {plan_dir: planState.plan_dir}).catch(() => ({available: false}));
      if (!state.available) {
        planState.source = 'imported_abc'; $('#plan-exact').checked = false; $('#plan-exact').disabled = true; $('#plan-abc').disabled = false;
        assistantText('#plan-badge', '原计划不可用 · 可用当前 ABC 重新生成');
        await savePanel('plan');
      }
    }
    for (const panel of ['create', 'plan', 'cover', 'assistant']) {
      $(`#${panel}`).addEventListener('input', event => {
        if (event.target.closest('#assistant-config-form') || event.target.type === 'file') return;
        if (event.target.type === 'checkbox') updateInstrumental(panel);
        if (panel === 'cover' && event.target.id === 'cover-lyrics' && event.target.value.trim()) { $('#cover').dataset.instrumental = 'false'; updateInstrumental('cover'); }
        if (panel === 'assistant') {
          if (event.target.id.startsWith('assistant-result-')) assistant.resultEdited = true;
          if (event.target.id === 'assistant-result-abc' && assistant.result) { assistant.result.abc_status = 'pending'; renderAbcState(); $('#assistant-compose-abc').classList.toggle('hidden', Boolean(event.target.value.trim()) || assistant.result.cot === 'off'); }
          if (['assistant-result-lyrics', 'assistant-result-style'].includes(event.target.id)) assistantText('#assistant-result-note', '文本已修改；已有谱面未随文本更新，发送前请确认词谱对应。');
          updateCostHint();
        }
        changedDraft(panel);
      });
    }
    updateCostHint();
    await restoreAssistantJobForCurrentScope();
  } catch (error) { assistantText('#assistant-progress', `助手初始化失败：${error.message}`); }
}
$('#assistant-config-form').onsubmit = event => { event.preventDefault(); saveAssistantConfig().catch(error => assistantText('#assistant-config-status', error.message)); };
$('#assistant-provider').onchange = providerChanged;
$('#assistant-model-choice').onchange = () => assistantModelChoiceChanged(true);
$('#assistant-model').oninput = () => { $('#assistant-model-choice').value = CUSTOM_MODEL; assistant.providerSelections[$('#assistant-provider').value] = $('#assistant-model').value; renderAssistantChannelStatus(); };
$('#assistant-key').oninput = renderAssistantChannelStatus;
$('#assistant-open-settings').onclick = () => openAssistantSettings(true);
$('#assistant-refresh-models').onclick = refreshAssistantModels;
$('#assistant-delete-key').onclick = async () => { try { const provider = $('#assistant-provider').value, credential = assistant.providerCredentials[provider] || ''; await assistantPost('/api/assistant/credentials', {delete: true, credential_id: credential}); assistant.providerCredentials[provider] = ''; if (assistant.config?.provider === provider) assistant.config.credential_id = ''; await saveAssistantConfig(); } catch (error) { assistantText('#assistant-config-status', error.message); } };
$('#assistant-form').onsubmit = event => { event.preventDefault(); startAssistant(); };
$('#assistant-test').onclick = () => startAssistant(true);
$('#assistant-retry').onclick = () => startAssistant(false, true);
$('#assistant-compose-abc').onclick = () => {
  $('#assistant-abc-source').value = ABC_COMPOSE;
  $('#assistant-retry-stage').value = 'abc';
  updateCostHint(); changedDraft('assistant');
  startAssistant(false, true);
};
$('#assistant-save-draft').onclick = () => savePanel('assistant').then(() => assistantText('#assistant-draft-status', '草稿已保存')).catch(error => assistantText('#assistant-draft-status', error.message));
$('#assistant-validate').onclick = async () => { const revision = assistant.ticks.assistant; try { await validateAssistantAbc(); if (assistant.ticks.assistant !== revision) throw new Error('校验期间内容有修改，请重新校验当前 ABC'); assistant.result.abc_status = 'validated'; renderAbcState(); changedDraft('assistant'); } catch (error) { assistantText('#assistant-abc-status', error.message); } };
$$('[data-assistant-send]').forEach(button => button.onclick = () => prepareTransfer(button.dataset.assistantSend));
$('#assistant-send-confirm').onclick = confirmTransfer;
$('#assistant-dismiss').onclick = () => $('#assistant-transfer-notice').classList.add('hidden');
$('#assistant-undo').onclick = async () => { const undo = assistant.undo; if (!undo) return; try {
  if (assistant.ticks[undo.panel] !== undo.tick) throw new Error('填入后已有新编辑，不能覆盖。请手动恢复需要的字段。');
  await savePanel(undo.panel, undo.before);
  if (assistant.ticks[undo.panel] !== undo.tick) { await savePanel(undo.panel); throw new Error('撤销期间有新编辑，已保留新编辑；没有覆盖。'); }
  applyDraft(undo.panel, undo.before); assistant.ticks[undo.panel]++;
  assistant.undo = null; $('#assistant-transfer-notice').classList.add('hidden');
} catch (error) { $('#assistant-transfer-notice span').textContent = error.message; } };
$('#assistant-download').onclick = () => { const r = readAssistantResult(); if (!r) return; const request = {style: r.style, lyrics: r.lyrics, cot: r.cot, seed: assistantValues().yue2_seed}; if (r.abc && r.abc_status === 'validated') request.abc = r.abc;
  downloadText('YuE2-creation.txt', `曲风\n${r.style}\n\n歌词\n${r.lyrics}\n`); downloadText('YuE2-request.json', JSON.stringify(request, null, 2)); };
$('#assistant-download-abc').onclick = () => { const r = readAssistantResult(); if (r?.abc) downloadText('score.abc', r.abc); };
$('#assistant-copy').onclick = () => { const r = readAssistantResult(); if (r) navigator.clipboard.writeText(`曲风\n${r.style}\n\n歌词\n${r.lyrics}`).catch(error => assistantText('#assistant-result-note', error.message)); };
window.assistantReady = initAssistant();
