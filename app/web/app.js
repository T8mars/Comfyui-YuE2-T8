const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const TERMINAL = new Set(['complete', 'failed', 'cancelled']);
const buttonBindings = new Map();
let planState = null;
let currentJobId = null;
let workspaceRefreshing = false;
let modelSettingsInitialized = false;
let displayedTerminal = null;
const panelStates = new Map();
const observedJobs = new Map();
function savedValue(key, value) {
  try {
    if (value !== undefined) localStorage.setItem(`yue2:${key}`, value);
    return localStorage.getItem(`yue2:${key}`);
  } catch { return null; }
}
function resultPanel(job) {
  return savedValue(`job-panel:${job.id}`) || job.result_panel ||
    (['reference_cover', 'voice_convert'].includes(job.kind) ? 'cover' : job.kind === 'render_plan' ? 'plan' : 'create');
}
function rememberResult(job, target) {
  const panel = target?.closest('.panel')?.id;
  if (panel) savedValue(`job-panel:${job.id}`, panel);
}

function renderPanelResults(jobs) {
  for (const panel of ['create', 'plan', 'cover']) {
    const job = jobs.find(item => ['generate', 'reference_cover', 'voice_convert', 'render_plan', 'decode'].includes(item.kind) && resultPanel(item) === panel);
    if (!job) continue;
    const target = $(`#${panel}-result`);
    const signature = `${job.id}:${job.status}`;
    const previous = observedJobs.get(job.id);
    observedJobs.set(job.id, job.status);
    if (panelStates.get(panel) === signature) continue;
    panelStates.set(panel, signature);
    target.dataset.jobId = job.id;
    if (!TERMINAL.has(job.status)) {
      target.innerHTML = `<div class="result-card"><b>作品正在制作中</b><p class="meta">完成后，播放器会直接显示在这里。</p><button class="ghost compact" onclick="openTaskCenter()">查看生成进度</button></div>`;
    } else {
      if (job.status === 'complete') renderJob(job, target);
      else target.innerHTML = failureMarkup({jobId: job.id, message: job.error, job});
      if (previous && !TERMINAL.has(previous) && target.closest('.panel').classList.contains('active')) {
        target.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
    }
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { data = {error: await response.text()}; }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function formObject(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (data.seed !== undefined) data.seed = safeSeed(data.seed);
  if (data.candidates !== undefined) data.candidates = Number(data.candidates);
  for (const key of ['cfg_scale', 'memory_budget_gib', 'nar_query_chunk_size']) if (data[key] !== undefined) data[key] = Number(data[key]);
  data.offload_ar = form.querySelector('[name=offload_ar]')?.checked || false;
  return data;
}

function safeSeed(value) {
  const seed = Number(value);
  if (!Number.isSafeInteger(seed) || seed < 0) throw new Error('随机种子必须是 0 到 9007199254740991 之间的整数');
  return seed;
}

function kindLabel(kind) {
  return ({
    generate: '歌曲生成', plan: '乐谱创作', render_plan: '从乐谱生成歌曲',
    transcribe: '音频转谱', semantic: '生成音乐结构', synthesize: '合成人声与伴奏',
    decode: '输出音频', doctor: '环境自检', voice_convert: '参考音色转换', reference_cover: '参考音色翻唱'
  })[kind] || kind;
}

function stageLabel(stage) {
  return ({
    queued: '等待开始', starting: '正在加载模型', candidate: '正在准备生成版本',
    planning: '正在创作旋律与和弦', semantic: '正在生成音乐结构',
    synthesis: '正在合成人声与伴奏', decoding: '正在输出音频',
    loading_transcriber: '正在加载转谱模型', transcribing: '正在从音频提取旋律',
    encoding: '正在读取音频', notation: '正在整理 ABC 与 MIDI 乐谱',
    separating_vocals: '正在分离人声与伴奏', loading_voice_model: '正在加载参考音色模型',
    converting_voice: '正在转换演唱音色', remixing: '正在重新混音',
    cancelling: '正在安全停止', complete: '已完成，可试听', failed: '任务失败',
    cancelled: '已取消', doctor: '正在验证运行环境', running: '正在执行'
  })[stage] || stage;
}

function stageHint(job) {
  const hints = {
    queued: '等待前面的任务完成后自动开始。', starting: '正在启动独立运行环境并加载所需模型。',
    candidate: '正在准备本轮生成参数。', planning: '正在根据歌词和风格安排旋律、节拍与和弦。',
    semantic: '正在创作歌曲结构、旋律走向与音乐语义。', synthesis: '正在合成人声、乐器和声学细节。',
    decoding: '正在输出 48 kHz 双声道音频，已经接近完成。', loading_transcriber: '正在将转谱模型载入 GPU。',
    transcribing: '正在从上传的音频中识别旋律和节拍。', encoding: '正在准备音频数据。',
    notation: '正在生成可编辑的 ABC、MIDI 和乐谱预览。', doctor: '正在检查 GPU、运行库和全部模型文件。',
    separating_vocals: '正在把新生成歌曲拆分为人声和伴奏。', loading_voice_model: '正在将 Seed-VC、声纹和声码器载入 GPU。',
    converting_voice: '保留新歌旋律与演唱节奏，把人声转换成参考音色。', remixing: '正在将转换后的人声与原伴奏合成为 48 kHz 双声道成品。',
    cancelling: '正在保存可用结果并安全释放 GPU。'
  };
  let hint = hints[job.stage] || '任务正在本机 GPU 上运行。';
  if (job.candidate && job.candidates) hint += ` 当前为第 ${job.candidate}/${job.candidates} 个版本。`;
  if (job.completed && job.total) hint += ` 当前阶段 ${job.completed}/${job.total}。`;
  if (job.window && job.windows) hint += ` 转谱进度 ${job.window}/${job.windows}。`;
  return hint;
}

function sourceLabel(source) { return ({webui: '本地工作室', comfyui: 'ComfyUI', api: '本地 API'})[source] || '本地任务'; }
function escapeHtml(value = '') { return String(value).replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character])); }
function shortId(id = '') { return String(id).split('-').at(-1) || id; }
function formatClock(seconds) { const value = Math.max(0, Math.floor(seconds || 0)); return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`; }
function elapsed(job) { return formatClock(Date.now() / 1000 - (job.started_at || job.created_at || Date.now() / 1000)); }
function submittedAt(job) { return new Date(job.created_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); }

function failureMarkup(error) {
  const id = error?.jobId;
  const job = error?.job || {};
  const oom = /out of memory/i.test(error?.message || '');
  const reason = oom ? '显存不足，任务已停止。可以使用保存的阶段结果重新运行。' : error?.message || '未知错误';
  const generatedAudio = job.generated_result?.audio;
  const generatedRel = generatedAudio && id ? relativeAudio(job, generatedAudio) : null;
  const intermediate = generatedRel ? `<p>歌曲已生成，可先试听：</p><audio controls preload="metadata" src="${audioUrl(id, generatedRel)}"></audio>` : '';
  const phase = job.failed_stage ? `<p>失败阶段：${escapeHtml(stageLabel(job.failed_stage))}</p>` : '';
  const retry = id && ['generate', 'reference_cover', 'voice_convert', 'render_plan'].includes(job.kind) ? `<button class="primary compact" onclick="resumeJob('${id}', this)">${job.resumable ? '从已保存阶段继续' : '重新运行'}</button>` : '';
  const actions = id ? `<div class="toolbar failure-actions">${retry}<button class="ghost compact" onclick="toggleJobLog('${id}', this)">查看任务日志</button></div><pre class="job-log hidden"></pre>` : '';
  return `<div class="result-card failure-card"><b class="status-failed">${job.status === 'cancelled' ? '任务已取消' : '任务失败'}</b>${phase}<p>${escapeHtml(reason)}</p>${intermediate}${actions}</div>`;
}

async function resumeJob(id, button) {
  button.disabled = true;
  try {
    const job = await api(`/api/jobs/${id}/resume`, {method: 'POST'});
    const panel = savedValue(`job-panel:${id}`) || button.closest('.panel')?.id;
    if (['create', 'plan', 'cover'].includes(panel)) savedValue(`job-panel:${job.id}`, panel);
    bindButton(button, job);
    await refreshWorkspace();
    $('#task-center').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) { button.disabled = false; alert(error.message); }
}
window.resumeJob = resumeJob;

function renderLatestTask(jobs) {
  const job = jobs.find(item => ['generate', 'reference_cover', 'voice_convert', 'render_plan', 'decode'].includes(item.kind));
  if (!job) return;
  const target = $('#latest-task');
  if (!TERMINAL.has(job.status)) { target.classList.add('hidden'); displayedTerminal = null; return; }
  const signature = `${job.id}:${job.status}`;
  if (signature === displayedTerminal) return;
  displayedTerminal = signature;
  target.classList.remove('hidden');
  if (job.status === 'complete') renderJob(job, target);
  else target.innerHTML = failureMarkup({jobId: job.id, message: job.error, job});
}

async function toggleJobLog(id, button) {
  const target = button.closest('.failure-card, .history-card')?.querySelector('.job-log');
  if (!target) return;
  if (!target.classList.contains('hidden')) {
    target.classList.add('hidden'); button.textContent = '查看任务日志'; return;
  }
  button.disabled = true; button.textContent = '正在读取…';
  try {
    const data = await api(`/api/jobs/${id}/log`);
    target.textContent = data.text || '日志为空'; target.classList.remove('hidden'); button.textContent = '收起任务日志';
  } catch (error) { alert(error.message); button.textContent = '查看任务日志'; }
  finally { button.disabled = false; }
}

async function openDirectory(directory) {
  try { await api('/api/open-directory', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({directory})}); }
  catch (error) { alert(error.message); }
}
window.toggleJobLog = toggleJobLog;
window.openDirectory = openDirectory;

function renderModelSettings(data) {
  $('#model-directory').value = data.model_directory || '';
  $('#model-path-summary').textContent = `${data.using_default ? '默认目录' : '自定义目录'} · ${data.model_directory}`;
  if (data.error) {
    $('#model-settings-result').textContent = `配置有误，已临时使用默认目录：${data.error}`;
    $('#model-settings').open = true;
  }
}

async function loadModelSettings() {
  try { renderModelSettings(await api('/api/settings')); }
  catch (error) { $('#model-settings-result').textContent = `模型路径读取失败：${error.message}`; }
}

async function saveModelDirectory(value) {
  const button = $('#save-model-directory');
  button.disabled = true;
  $('#model-settings-result').textContent = '正在保存并检查模型目录…';
  try {
    const data = await api('/api/settings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model_directory: value})
    });
    renderModelSettings(data);
    const ready = data.ready?.capabilities || {};
    const usable = ready.generation && ready.transcription && ready.voice_conversion;
    $('#model-settings-result').textContent = usable ? '模型目录已保存，生成、转谱和参考音色均可用。' : '路径已保存，但模型尚不完整；请按左侧说明放置全部文件后运行自检。';
    await refreshWorkspace();
  } catch (error) {
    $('#model-settings-result').textContent = `保存失败：${error.message}`;
  } finally { button.disabled = false; }
}

function stepsFor(job) {
  const steps = {
    generate: [['starting', '加载模型'], ['planning', '创作乐谱'], ['semantic', '生成结构'], ['synthesis', '合成人声与伴奏'], ['decoding', '输出音频']],
    render_plan: [['starting', '加载模型'], ['semantic', '生成结构'], ['synthesis', '合成人声与伴奏'], ['decoding', '输出音频']],
    plan: [['starting', '加载模型'], ['planning', '创作乐谱']],
    transcribe: [['starting', '准备音频'], ['loading_transcriber', '加载模型'], ['transcribing', '识别旋律'], ['notation', '整理乐谱']],
    semantic: [['starting', '加载模型'], ['planning', '检查乐谱'], ['semantic', '生成结构']],
    synthesize: [['starting', '加载模型'], ['synthesis', '合成声音']], decode: [['starting', '加载模型'], ['decoding', '输出音频']],
    doctor: [['starting', '启动检查'], ['doctor', '验证环境']],
    reference_cover: [['starting', '加载模型'], ['planning', '检查乐谱'], ['semantic', '生成结构'], ['synthesis', '合成歌曲'], ['decoding', '输出歌曲'], ['separating_vocals', '分离人声'], ['loading_voice_model', '加载音色'], ['converting_voice', '转换音色'], ['remixing', '混音']],
    voice_convert: [['starting', '准备音频'], ['separating_vocals', '分离人声'], ['loading_voice_model', '加载音色模型'], ['converting_voice', '转换音色'], ['remixing', '重新混音']]
  }[job.kind] || [['starting', '准备'], [job.stage, stageLabel(job.stage)]];
  const stage = job.stage === 'candidate' ? 'starting' : job.stage;
  const current = Math.max(0, steps.findIndex(([key]) => key === stage));
  return `<ol class="task-steps" aria-label="任务步骤">${steps.map(([, label], index) => `<li class="${index < current ? 'done' : index === current ? 'current' : ''}">${escapeHtml(label)}</li>`).join('')}</ol>`;
}

function setSubmitting(button) {
  if (!button) return;
  button.dataset.idleLabel ||= button.textContent.trim();
  button.disabled = true;
  button.textContent = '正在提交…';
}

function bindButton(button, job) {
  if (!button) return;
  button.dataset.jobId = job.id;
  buttonBindings.set(job.id, button);
  updateButton(button, job);
}

function restoreButton(button) {
  if (!button) return;
  const id = button.dataset.jobId;
  if (id) buttonBindings.delete(id);
  delete button.dataset.jobId;
  button.textContent = button.dataset.idleLabel || button.textContent;
  button.disabled = (button.id === 'transcribe-button' && !$('#cover-file').files[0]) ||
    (button.id === 'generate-reference-cover' && !$('#reference-file').files[0]);
}

function updateButton(button, job, queuePosition = 0) {
  if (!button || !job) return;
  button.disabled = !TERMINAL.has(job.status);
  if (job.status === 'queued') button.textContent = `已加入队列（第 ${queuePosition || '—'} 位）`;
  else if (job.status === 'cancelling') button.textContent = '正在取消…';
  else if (!TERMINAL.has(job.status)) button.textContent = `${kindLabel(job.kind)}中 · ${stageLabel(job.stage).replace(/^正在/, '')}`;
}

function updateBoundButtons(jobs, queued) {
  const byId = new Map(jobs.map(job => [job.id, job]));
  const positions = new Map(queued.map((job, index) => [job.id, index + 1]));
  for (const [id, button] of buttonBindings) {
    const job = byId.get(id);
    if (!job) continue;
    if (TERMINAL.has(job.status)) restoreButton(button); else updateButton(button, job, positions.get(id));
  }
}

function renderRunningJob(job) {
  const summary = job.summary ? `<p class="task-summary">${escapeHtml(job.summary)}</p>` : '';
  return `<article class="running-job"><div class="task-card-head"><div><span class="task-type">当前正在执行 · ${escapeHtml(kindLabel(job.kind))}</span><b>${escapeHtml(stageLabel(job.stage))}</b></div><button class="danger compact" type="button" data-cancel-job="${escapeHtml(job.id)}" ${job.status === 'cancelling' ? 'disabled' : ''}>${job.status === 'cancelling' ? '正在取消…' : '取消本任务'}</button></div><p class="task-hint">${escapeHtml(stageHint(job))}</p>${summary}${stepsFor(job)}<div class="task-meta"><span>${escapeHtml(sourceLabel(job.source))}</span><span>已运行 ${elapsed(job)}</span><span title="${escapeHtml(job.id)}">任务 ${escapeHtml(shortId(job.id))}</span></div></article>`;
}

function renderQueuedJob(job, index) {
  return `<li class="queue-job"><div class="queue-position"><b>第 ${index + 1} 位</b><span>等待开始</span></div><div class="queue-copy"><b>${escapeHtml(kindLabel(job.kind))}</b><p>${escapeHtml(job.summary || '等待前面的任务完成')}</p><small>${escapeHtml(sourceLabel(job.source))} · ${submittedAt(job)} 提交 · ${escapeHtml(shortId(job.id))}</small></div><button class="danger compact" type="button" data-cancel-job="${escapeHtml(job.id)}">取消排队</button></li>`;
}

function renderTaskCenter(healthData, jobs) {
  const active = jobs.filter(job => !TERMINAL.has(job.status));
  const current = jobs.find(job => job.id === healthData.current_job) || active.find(job => job.status !== 'queued');
  const queued = active.filter(job => job.id !== current?.id && job.status === 'queued').sort((a, b) => a.created_at - b.created_at);
  currentJobId = current?.id || null;
  $('#task-center').classList.toggle('hidden', !current && !queued.length);
  $('#running-section').classList.toggle('hidden', !current);
  $('#queue-section').classList.toggle('hidden', !queued.length);
  $('#running-job').innerHTML = current ? renderRunningJob(current) : '';
  $('#queue-title').textContent = `接下来 · ${queued.length} 个等待任务`;
  $('#queue-list').innerHTML = queued.map(renderQueuedJob).join('');
  const workload = $('#task-center-jump');
  workload.classList.toggle('hidden', !current && !queued.length);
  workload.textContent = current ? `GPU 工作中 · 1 个执行 / ${queued.length} 个等待` : `${queued.length} 个任务等待开始`;
  const cover = jobs.find(job => job.kind === 'reference_cover' && !TERMINAL.has(job.status));
  if (cover && !$('#generate-reference-cover').dataset.jobId) bindButton($('#generate-reference-cover'), cover);
  updateBoundButtons(jobs, queued);
}

function renderHealth(data) {
  const ready = data.ready.capabilities?.generation && data.ready.capabilities?.transcription;
  $('#health-dot').className = `dot ${ready ? 'ok' : 'bad'}`;
  $('#health-title').textContent = ready ? '运行环境已就绪' : '运行环境不完整';
  const renderer = data.ready.capabilities?.score_renderer ? '乐谱渲染可用' : '乐谱渲染器未安装';
  const voice = data.ready.capabilities?.voice_conversion ? '参考音色可用' : '参考音色组件未安装';
  const missing = [];
  if (!data.ready.capabilities?.generation) missing.push(data.ready.upstream_source ? '歌曲模型未就绪' : '缺少 YuE2 推理源码');
  if (!data.ready.capabilities?.transcription) missing.push('音频转谱未就绪');
  if (data.ready.settings_error) missing.unshift('模型路径配置有误');
  $('#health-detail').textContent = `${missing.length ? missing.join(' · ') : '歌曲生成与音频转谱可用'} · ${voice} · ${renderer}`;
  $('#model-path-summary').textContent = `当前目录 · ${data.ready.model_directory}`;
  if (!modelSettingsInitialized) {
    $('#model-settings').open = Boolean(data.ready.settings_error || !data.ready.capabilities?.generation);
    modelSettingsInitialized = true;
  }
}

async function refreshWorkspace() {
  if (workspaceRefreshing) return;
  workspaceRefreshing = true;
  try {
    const [healthData, listData] = await Promise.all([api('/api/health'), api('/api/jobs?limit=100')]);
    renderHealth(healthData); renderTaskCenter(healthData, listData.jobs); renderLatestTask(listData.jobs); renderPanelResults(listData.jobs);
  } catch (error) {
    $('#health-dot').className = 'dot bad'; $('#health-title').textContent = '服务连接中断';
    $('#health-detail').textContent = `正在重试 · ${error.message}`;
  } finally { workspaceRefreshing = false; }
}

async function submit(kind, request, resultTarget, button = null) {
  setSubmitting(button);
  try {
    const clientRequestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    const job = await api('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind, request, source: 'webui', client_request_id: clientRequestId, result_panel: resultTarget?.closest('.panel')?.id})});
    rememberResult(job, resultTarget);
    bindButton(button, job); await refreshWorkspace(); return await waitForJob(job.id, resultTarget);
  } catch (error) { restoreButton(button); throw error; }
}

async function waitForJob(id, resultTarget) {
  let connectionErrors = 0;
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 900));
    let job;
    try { job = await api(`/api/jobs/${id}`); connectionErrors = 0; }
    catch (error) { if (++connectionErrors >= 10) throw new Error(`服务连接中断：${error.message}`); continue; }
    await refreshWorkspace();
    if (job.status === 'complete') {
      restoreButton(buttonBindings.get(id));
      await Promise.all([refreshWorkspace(), loadHistory()]); return job;
    }
    if (job.status === 'failed' || job.status === 'cancelled') {
      restoreButton(buttonBindings.get(id));
      await Promise.all([refreshWorkspace(), loadHistory()]);
      const failure = new Error(job.error || stageLabel(job.status)); failure.jobId = job.id; failure.job = job; throw failure;
    }
  }
}

function audioUrl(jobId, relative) { return `/api/files/${jobId}/${relative.split('/').map(encodeURIComponent).join('/')}`; }
function relativeAudio(job, path) {
  const normalized = String(path || '').replaceAll('\\', '/'); const marker = `/jobs/${job.id}/`;
  const jobIndex = normalized.toLowerCase().indexOf(marker.toLowerCase()); if (jobIndex >= 0) return normalized.slice(jobIndex + marker.length);
  const artifactIndex = normalized.toLowerCase().lastIndexOf('/artifacts/'); return artifactIndex >= 0 ? normalized.slice(artifactIndex + 1) : null;
}

function renderJob(job, target) {
  const result = job.result || {}; const candidates = result.candidates || (result.audio ? [{seed: result.seed, audio: result.audio, audio_seconds: result.audio_seconds || result.audio_info?.duration_seconds, truncated: result.truncated}] : []);
  if (!candidates.length) { target.innerHTML = `<div class="result-card"><b>任务完成</b><pre class="meta">${escapeHtml(JSON.stringify(result, null, 2))}</pre></div>`; return; }
  const partial = result.partial ? `<div class="result-card status-failed">已完成 ${result.completed_candidates}/${result.requested_candidates} 个版本；后续版本失败：${escapeHtml(result.failures?.[0]?.error || '未知错误')}</div>` : '';
  target.innerHTML = partial + candidates.map((candidate, index) => {
    const rel = relativeAudio(job, candidate.audio); const truncated = candidate.truncated && Object.values(candidate.truncated).some(Boolean);
    const url = rel ? audioUrl(job.id, rel) : '';
    const player = url ? `<audio controls preload="metadata" src="${url}"></audio>` : '';
    const duration = Number(candidate.audio_seconds);
    const details = [candidates.length > 1 ? `版本 ${index + 1}` : '', Number.isFinite(duration) && duration > 0 ? `${duration.toFixed(1)} 秒` : '', candidate.seed != null ? `Seed ${escapeHtml(candidate.seed)}` : '', `任务 ${escapeHtml(shortId(job.id))}`].filter(Boolean).join(' · ');
    const download = url ? `<a class="ghost audio-download" href="${url}" download="YuE2-${job.id}-${index + 1}.flac">下载音频</a>` : '';
    return `<article class="result-card"><header><div><b>${escapeHtml(kindLabel(job.kind))}已完成 · 可试听</b><div class="meta">${details}</div></div><span class="badge">${truncated ? '已截断' : '完整'}</span></header>${player}<div class="toolbar">${download}<button class="ghost" onclick="exportJob('${job.id}')">导出全部文件</button></div></article>`;
  }).join('');
}

async function exportJob(id) {
  try { const data = await api('/api/export', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({job_id: id})}); alert(`已导出到\n${data.destination}`); }
  catch (error) { alert(error.message); }
}
window.exportJob = exportJob;

$$('.tab').forEach(button => button.onclick = () => {
  savedValue('active-tab', button.dataset.tab);
  $$('.tab').forEach(item => item.classList.toggle('active', item === button));
  $$('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.tab));
  if (button.dataset.tab === 'history') loadHistory();
});
const restoredTab = savedValue('active-tab');
if (['create', 'plan', 'cover', 'history'].includes(restoredTab)) $(`.tab[data-tab="${restoredTab}"]`).click();

function openHistory() { $('.tab[data-tab="history"]').click(); $('#history').scrollIntoView({behavior: 'smooth', block: 'start'}); }
function openTaskCenter() { $('#task-center').scrollIntoView({behavior: 'smooth', block: 'nearest'}); }
document.addEventListener('click', event => {
  const cancel = event.target.closest('[data-cancel-job]'); if (cancel) cancelJob(cancel.dataset.cancelJob, cancel);
});
$('#open-history').onclick = openHistory;
$('#task-center-jump').onclick = openTaskCenter;
$('#model-settings-form').onsubmit = event => {
  event.preventDefault();
  saveModelDirectory($('#model-directory').value);
};
$('#default-model-directory').onclick = async () => {
  try {
    const data = await api('/api/settings');
    $('#model-directory').value = data.default_model_directory;
    await saveModelDirectory(data.default_model_directory);
  } catch (error) { $('#model-settings-result').textContent = `恢复失败：${error.message}`; }
};

$('#create-form').onsubmit = async event => {
  event.preventDefault(); $('#create-result').innerHTML = '';
  try { await submit('generate', formObject(event.target), $('#create-result'), $('#create-button')); }
  catch (error) { $('#create-result').innerHTML = failureMarkup(error); }
};

$('#plan-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const request = formObject(event.target); request.backend = 'torch-eager'; request.memory_budget_gib = 23.5;
    const job = await submit('plan', request, null, $('#plan-button')); planState = {...job.result, request};
    $('#plan-abc').value = job.result.abc || ''; $('#plan-badge').textContent = job.result.truncated ? '计划已截断' : '原始计划'; $('#plan-workbench').classList.remove('hidden');
  } catch (error) { $('#plan-result').innerHTML = failureMarkup(error); }
};

$('#plan-exact').onchange = event => { $('#plan-abc').disabled = event.target.checked; };
$('#plan-abc').disabled = true;
$('#render-plan').onclick = async () => {
  if (!planState) return;
  const exact = $('#plan-exact').checked; const request = {plan_dir: planState.plan_dir, exact, backend: 'torch-eager', memory_budget_gib: 23.5};
  if (!exact) Object.assign(request, planState.request, {abc: $('#plan-abc').value, candidates: 1});
  try { await submit('render_plan', request, $('#plan-result'), $('#render-plan')); }
  catch (error) { $('#plan-result').innerHTML = failureMarkup(error); }
};
$('#download-abc').onclick = () => { const blob = new Blob([$('#plan-abc').value], {type: 'text/plain;charset=utf-8'}); const anchor = document.createElement('a'); anchor.href = URL.createObjectURL(blob); anchor.download = 'score.abc'; anchor.click(); URL.revokeObjectURL(anchor.href); };

function bindUploadPreview(inputSelector, dropSelector, previewSelector, buttonSelector) {
  const input = $(inputSelector), drop = $(dropSelector), preview = $(previewSelector);
  const audio = preview.querySelector('audio'), status = preview.querySelector('[data-preview-status]');
  const defaultName = drop.querySelector('b').textContent, defaultStatus = status.textContent;
  let objectUrl = null;
  const release = () => {
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  };
  input.onchange = () => {
    release();
    const file = input.files[0], button = $(buttonSelector);
    button.disabled = !file || Boolean(button.dataset.jobId);
    drop.querySelector('b').textContent = file ? file.name : defaultName;
    preview.classList.toggle('hidden', !file);
    status.textContent = defaultStatus;
    if (file) {
      objectUrl = URL.createObjectURL(file);
      audio.src = objectUrl;
      audio.load();
    }
  };
  preview.querySelector('[data-replace]').onclick = () => input.click();
  audio.addEventListener('play', () => {
    $$('.upload-preview audio').forEach(other => { if (other !== audio) other.pause(); });
  });
  audio.addEventListener('error', () => {
    if (objectUrl && audio.error) status.textContent = '浏览器无法试听此文件，可换用 WAV、MP3 或 FLAC；仍可尝试上传处理。';
  });
  window.addEventListener('pagehide', event => { if (!event.persisted) release(); });
}
bindUploadPreview('#cover-file', '#drop-zone', '#cover-preview', '#transcribe-button');
$('#transcribe-button').onclick = async () => {
  const file = $('#cover-file').files[0]; if (!file) return; const button = $('#transcribe-button');
  try {
    setSubmitting(button); button.textContent = '正在上传…';
    const upload = await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file});
    const job = await submit('transcribe', {source_path: upload.path, melody_only: true, dtype: 'bf16', preset: 'default'}, null, button);
    $('#cover-abc').value = job.result.abc || ''; $('#cover-review').classList.remove('hidden');
  } catch (error) { restoreButton(button); $('#cover-result').innerHTML = failureMarkup(error); }
};
$('#generate-cover').onclick = async () => {
  let seed; try { seed = safeSeed($('#cover-seed').value); } catch (error) { return alert(error.message); }
  const request = {style: $('#cover-style').value, lyrics: $('#cover-lyrics').value, abc: $('#cover-abc').value, cot: 'melody', seed, cfg_scale: 1, backend: 'torch-eager', memory_budget_gib: 23.5, candidates: 1};
  if (!request.lyrics.trim()) return alert('请先填写并核对歌词');
  try { await submit('generate', request, $('#cover-result'), $('#generate-cover')); }
  catch (error) { $('#cover-result').innerHTML = failureMarkup(error); }
};

bindUploadPreview('#reference-file', '#reference-drop-zone', '#reference-preview', '#generate-reference-cover');

$('#generate-reference-cover').onclick = async () => {
  const reference = $('#reference-file').files[0];
  if (!reference) return alert('请先选择参考音色');
  let seed;
  try { seed = safeSeed($('#cover-seed').value); } catch (error) { return alert(error.message); }
  const generate = {style: $('#cover-style').value, lyrics: $('#cover-lyrics').value, abc: $('#cover-abc').value, cot: 'melody', seed, cfg_scale: 1, backend: 'torch-eager', memory_budget_gib: 23.5, candidates: 1, offload_ar: true, nar_query_chunk_size: 256, nar_attention: 'sdpa'};
  if (!generate.lyrics.trim()) return alert('请先填写并核对歌词');
  const button = $('#generate-reference-cover');
  setSubmitting(button);
  try {
    $('#cover-result').innerHTML = '';
    const upload = await api(`/api/uploads?filename=${encodeURIComponent(reference.name)}`, {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: reference});
    const voice = {reference_path: upload.path,
      diffusion_steps: Number($('#voice-steps').value), cfg_rate: Number($('#voice-cfg').value),
      semi_tone_shift: Number($('#voice-shift').value), auto_f0_adjust: $('#voice-auto-f0').checked,
      vocal_gain_db: Number($('#voice-gain').value), accompaniment_gain_db: Number($('#backing-gain').value)};
    await submit('reference_cover', {generate, voice}, $('#cover-result'), button);
  } catch (error) { restoreButton(button); $('#cover-result').innerHTML = failureMarkup(error); }
};

function firstResultAudio(job) {
  return job.result?.audio || job.result?.candidates?.[0]?.audio;
}

async function loadHistory() {
  try {
    const {jobs} = await api('/api/jobs?limit=100');
    $('#history-list').innerHTML = jobs.map(job => {
      const result = job.result || {}; const audio = relativeAudio(job, result.audio || result.candidates?.[0]?.audio);
      const exportButton = job.status === 'complete' && job.result ? `<button class="ghost" onclick="exportJob('${job.id}')">导出</button>` : '';
      const retryButton = ['failed', 'cancelled'].includes(job.status) && ['generate', 'reference_cover', 'voice_convert', 'render_plan'].includes(job.kind) ? `<button class="ghost compact" onclick="resumeJob('${job.id}', this)">${job.resumable ? '从已保存阶段继续' : '重新运行'}</button>` : '';
      const logButtons = job.status === 'failed' ? `<button class="ghost compact" onclick="toggleJobLog('${job.id}', this)">查看任务日志</button><button class="ghost compact" onclick="openDirectory('logs')">打开日志目录</button>` : '';
      return `<article class="history-card"><header><div><b>${escapeHtml(kindLabel(job.kind))}</b><div class="meta">${escapeHtml(job.id)} · ${new Date(job.created_at * 1000).toLocaleString()} · ${escapeHtml(sourceLabel(job.source))}</div></div></header><b class="status-${job.status}">${escapeHtml(job.status === 'running' ? stageLabel(job.stage) : stageLabel(job.status))}</b>${job.error ? `<div class="meta">${escapeHtml(job.error)}</div>` : ''}${audio ? `<audio controls preload="none" src="${audioUrl(job.id, audio)}"></audio>` : ''}<div class="toolbar">${exportButton}${retryButton}${logButtons}</div><pre class="job-log hidden"></pre></article>`;
    }).join('') || '<p class="meta">还没有任务。</p>';
  } catch (error) { $('#history-list').innerHTML = `<p class="status-failed">${escapeHtml(error.message)}</p>`; }
}

async function loadRetention() {
  try {
    const data = await api('/api/retention'); const total = ['jobs', 'uploads', 'logs'].reduce((sum, key) => sum + (data.usage[key]?.gib || 0), 0);
    $('#storage-usage').textContent = `受管存储 ${total.toFixed(2)} GiB · 导出永久保留`;
  } catch (error) { $('#storage-usage').textContent = `存储状态失败：${error.message}`; }
}

async function cleanupStorage() {
  try {
    const report = await api('/api/retention/cleanup', {method: 'POST'}); const deleted = Object.values(report.deleted || {}).reduce((sum, items) => sum + items.length, 0);
    alert(`清理完成：删除 ${deleted} 项；重要作品请保存在 exports`); await Promise.all([loadHistory(), loadRetention()]);
  } catch (error) { alert(error.message); }
}

async function cancelJob(id, button = null, force = false) {
  if (!id) return;
  if (button) { button.disabled = true; button.textContent = '正在取消…'; }
  try { await api(`/api/jobs/${id}/cancel`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({force})}); await refreshWorkspace(); }
  catch (error) { if (button) button.disabled = false; alert(error.message); }
}
window.cancelJob = cancelJob;

$('#cancel-active').onclick = () => cancelJob(currentJobId);
$('#refresh-history').onclick = () => { loadHistory(); loadRetention(); };
$('#cleanup-storage').onclick = cleanupStorage;
$('#doctor-button').onclick = async () => {
  const button = $('#doctor-button'); const action = $('.doctor-action');
  try { const job = await submit('doctor', {verify_hashes: true}, null, button); action.dataset.result = `自检通过 · ${job.result.gpu} · CUDA ${job.result.torch_cuda}`; }
  catch (error) { action.dataset.result = `自检未通过 · ${error.message}`; }
};

refreshWorkspace(); loadModelSettings(); loadHistory(); loadRetention(); setInterval(refreshWorkspace, 1200);
