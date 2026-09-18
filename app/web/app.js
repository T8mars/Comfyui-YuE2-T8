const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const TERMINAL = new Set(['complete', 'failed', 'cancelled', 'paused']);
const buttonBindings = new Map();
let planState = null;
let currentJobId = null;
let workspaceRefreshing = false;
let modelSettingsInitialized = false;
let projectAssetSignature = '';
let historyOffset = 0, historyTotal = 0, historyLoadRevision = 0; const historyPageSize = 10;
let historyPageJobs = [], jobCleanupBusy = false; const selectedHistoryJobs = new Set();
let availableUpdate = null;
let updateInstalling = false;
const panelStates = new Map();
const observedJobs = new Map();
function savedValue(key, value) {
  try {
    if (value !== undefined) localStorage.setItem(`yue2:${key}`, value);
    return localStorage.getItem(`yue2:${key}`);
  } catch { return null; }
}

function studioDialog({title = '提示', message = '', confirmLabel = '确定', cancelLabel = '', value = null, placeholder = ''} = {}) {
  return new Promise(resolve => {
    const previous = document.querySelector('.studio-dialog');
    if (previous) previous.close('cancel');
    const dialog = document.createElement('dialog');
    dialog.className = 'studio-dialog';
    const form = document.createElement('form');
    form.method = 'dialog';
    const heading = document.createElement('h3');
    heading.id = `studio-dialog-title-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    dialog.setAttribute('aria-labelledby', heading.id);
    heading.textContent = title;
    const description = document.createElement('p');
    description.className = 'studio-dialog-message';
    description.textContent = String(message || '');
    form.append(heading, description);
    let input = null;
    if (value !== null) {
      input = document.createElement('input');
      input.type = 'text';
      input.value = String(value);
      input.placeholder = placeholder;
      input.setAttribute('aria-label', title);
      form.append(input);
    }
    const actions = document.createElement('div');
    actions.className = 'toolbar studio-dialog-actions';
    if (cancelLabel) {
      const cancel = document.createElement('button');
      cancel.className = 'ghost';
      cancel.value = 'cancel';
      cancel.textContent = cancelLabel;
      actions.append(cancel);
    }
    const confirm = document.createElement('button');
    confirm.className = 'primary';
    confirm.value = 'confirm';
    confirm.textContent = confirmLabel;
    actions.append(confirm);
    form.append(actions);
    dialog.append(form);
    document.body.append(dialog);
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      const accepted = dialog.returnValue === 'confirm';
      const result = input ? (accepted ? input.value : null) : accepted;
      dialog.remove();
      resolve(result);
    };
    dialog.addEventListener('close', finish, {once: true});
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      dialog.close('cancel');
    });
    dialog.showModal();
    (input || confirm).focus();
    input?.select();
  });
}
window.uiAlert = (message, title = '提示') => studioDialog({title, message, confirmLabel: '知道了'}).then(() => undefined);
window.uiConfirm = (message, title = '请确认', confirmLabel = '确定') => studioDialog({title, message, confirmLabel, cancelLabel: '取消'});
window.uiPrompt = (title, value = '', placeholder = '') => studioDialog({title, value, placeholder, confirmLabel: '保存', cancelLabel: '取消'});
window.alert = message => { void window.uiAlert(message); };
const mobileHeaderDetails = $('#mobile-header-details');
if (mobileHeaderDetails) mobileHeaderDetails.onclick = () => {
  const expanded = document.body.classList.toggle('mobile-header-expanded');
  mobileHeaderDetails.setAttribute('aria-expanded', String(expanded));
  mobileHeaderDetails.innerHTML = expanded ? '<i class="bi bi-chevron-up"></i>收起链接与维护' : '<i class="bi bi-info-circle"></i>链接与维护';
};
// Remember one editable budget across all YuE2 generation pages.
const memoryInputs = $$('[data-generation-memory]');
const rememberedBudget = Number(savedValue('generation-memory-gib'));
for (const input of memoryInputs) {
  if (Number.isFinite(rememberedBudget) && rememberedBudget > 2) input.value = String(rememberedBudget);
  input.addEventListener('input', () => {
    const value = Number(input.value);
    for (const other of memoryInputs) {
      other.value = input.value;
      other.setCustomValidity(Number.isFinite(value) && value > 2 ? '' : '显存预算必须大于 2 GiB');
    }
    if (Number.isFinite(value) && value > 2) savedValue('generation-memory-gib', input.value);
  });
}
function generationMemoryBudget() {
  const input = $('.panel.active [data-generation-memory]') || memoryInputs[0];
  const budget = Number(input.value);
  if (!Number.isFinite(budget) || budget <= 2) throw new Error('显存预算必须是大于 2 GiB 的有限数值');
  savedValue('generation-memory-gib', String(budget));
  return budget;
}
const loadingInputs = $$('[data-generation-loading]');
const loadingModes = new Set(['auto', 'gpu', 'cpu-offload']);
const rememberedLoading = savedValue('generation-model-loading');
for (const input of loadingInputs) {
  if (loadingModes.has(rememberedLoading)) input.value = rememberedLoading;
  input.addEventListener('change', () => {
    for (const other of loadingInputs) other.value = input.value;
    savedValue('generation-model-loading', input.value);
  });
}
function generationModelLoading() {
  const mode = ($('.panel.active [data-generation-loading]') || loadingInputs[0])?.value || 'auto';
  if (!loadingModes.has(mode)) throw new Error('请选择有效的模型加载方式');
  savedValue('generation-model-loading', mode);
  return mode;
}
window.generationModelLoading = generationModelLoading;
function resultPanel(job) {
  return savedValue(`job-panel:${job.id}`) || job.result_panel ||
    (['reference_cover', 'voice_convert'].includes(job.kind) ? 'cover' : job.kind === 'render_plan' ? 'plan' : 'create');
}
function rememberResult(job, target) {
  const panel = target?.closest('.panel')?.id;
  if (panel) savedValue(`job-panel:${job.id}`, panel);
}

function renderPanelResults(jobs) {
  const projectId = String(window.workbenchProjectId?.() || '');
  const scope = projectId || '__global__';
  const scopedProject = job => String(job.project_id || job.request?.project_id || job.request?.generate?.project_id || '');
  for (const panel of ['create', 'plan', 'remix', 'cover', 'midi']) {
    const target = $(panel==='midi'?'#midi-gen-result':`#${panel}-result`);
    if (!target) continue;
    const job = jobs.find(item => ['generate', 'reference_cover', 'voice_convert', 'render_plan', 'decode', 'mulacover_remix'].includes(item.kind) && resultPanel(item) === panel && scopedProject(item) === projectId);
    if (!job) {
      if (target.dataset.projectScope !== scope) {
        target.replaceChildren();
        delete target.dataset.jobId;
        panelStates.delete(panel);
      }
      target.dataset.projectScope = scope;
      continue;
    }
    const signature = `${scope}:${job.id}:${job.status}:${job.result?.completed_candidates || 0}`;
    const olderMidi = panel==='midi' && job.status!=='complete' ? jobs.find(item=>item.id!==job.id&&item.kind==='mulacover_remix'&&resultPanel(item)==='midi'&&scopedProject(item)===projectId&&item.status==='complete'&&item.result?.audio) : null;
    const previous = observedJobs.get(job.id);
    observedJobs.set(job.id, job.status);
    const resultPresent=target.hasChildNodes()&&(!olderMidi||target.querySelector('.midi-previous-result'));
    if (panelStates.get(panel) === signature && resultPresent) continue;
    panelStates.set(panel, signature);
    target.dataset.projectScope = scope;
    target.dataset.jobId = job.id;
    if (!TERMINAL.has(job.status)) {
      target.innerHTML = `<div class="result-card"><b>作品正在制作中</b><p class="meta">完成后，播放器会直接显示在这里。</p><button class="ghost compact" onclick="openTaskCenter()">查看生成进度</button></div>`;
      if (job.result?.comparison && job.result?.candidates?.length) {
        const ready = document.createElement('div'); renderJob(job, ready); target.append(ready);
      }
    } else {
      if (job.status === 'complete') renderJob(job, target);
      else target.innerHTML = failureMarkup({jobId: job.id, message: job.error, job});
      if (previous && !TERMINAL.has(previous) && target.closest('.panel').classList.contains('active')) {
        target.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
    }
    if(olderMidi){const preserved=document.createElement('section');preserved.className='midi-previous-result';const label=document.createElement('p');label.className='meta';label.textContent='上一次已完成作品（基于当时的 MIDI；本次任务不覆盖它）';preserved.append(label);const result=document.createElement('div');renderJob(olderMidi,result);preserved.append(result);target.append(preserved);}
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { data = {error: await response.text()}; }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function localInputReference(input) {
  try { return input?.dataset.localSource ? JSON.parse(input.dataset.localSource) : null; }
  catch { return null; }
}
function inputHasSource(input) { return Boolean(input?.files?.[0] || localInputReference(input)); }
function clearLocalInputReference(input) {
  delete input.dataset.localSource; delete input.dataset.localName; delete input.dataset.localPreview;
  if (input.title.startsWith('已选择本地素材：')) input.removeAttribute('title');
}
function setLocalInputSource(selector, reference, name, preview = '') {
  const input = typeof selector === 'string' ? $(selector) : selector;
  if (!input) throw new Error('目标输入框不存在');
  input.value = '';
  input.dataset.localSource = JSON.stringify(reference);
  input.dataset.localName = name || '本地素材';
  input.dataset.localPreview = preview || '';
  input.title = `已选择本地素材：${input.dataset.localName}`;
  input.dispatchEvent(new CustomEvent('local-source-change', {bubbles: true}));
}
async function inputSourceValue(input) {
  const local = localInputReference(input);
  if (local) return local;
  const file = input?.files?.[0];
  if (!file) return null;
  const uploaded = await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file
  });
  return uploaded.path;
}
Object.assign(window, {localInputReference, inputHasSource, clearLocalInputReference,
  setLocalInputSource, inputSourceValue});

function formObject(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (data.seed !== undefined) data.seed = safeSeed(data.seed);
  if (data.candidates !== undefined) data.candidates = Number(data.candidates);
  for (const key of ['cfg_scale', 'memory_budget_gib', 'nar_query_chunk_size', 'style_model_scale']) if (data[key] !== undefined) data[key] = Number(data[key]);
  const offload = form.querySelector('[name=offload_ar]');
  if (offload) data.offload_ar = offload.checked;
  return data;
}

function safeSeed(value) {
  const seed = Number(value);
  if (!Number.isSafeInteger(seed) || seed < 0) throw new Error('随机种子必须是 0 到 9007199254740991 之间的整数');
  return seed;
}

function kindLabel(kind) {
  return ({
    assistant: 'AI 创作助手',
    rvc_import: '导入训练素材', rvc_separate: '整理人声素材', rvc_train: '训练专属音色',
    rvc_model_import: '导入音色模型', rvc_model_export: '导出音色模型',
    rvc_storage_move: '迁移音色数据目录',
    generate: '歌曲生成', plan: '乐谱创作', render_plan: '从乐谱生成歌曲',
    transcribe: '音频转谱', semantic: '生成音乐结构', synthesize: '合成人声与伴奏',
    decode: '输出音频', doctor: '环境自检', voice_convert: '参考音色转换', reference_cover: '参考音色翻唱',
    midi_extract: '音乐提取 MIDI', mulacover_remix: 'MuLaCover 重新编曲'
    ,yue2_training_assets: '安装 YuE2 训练资源', yue2_prepare: '准备 YuE2 训练素材',
    yue2_train: '训练 YuE2 歌曲风格', yue2_preview: '试听 YuE2 风格模型'
  })[kind] || kind;
}

function stageLabel(stage) {
  return ({
    queued: '等待开始', starting: '正在加载模型', candidate: '正在准备生成版本',
    midi_decode: '正在解码音乐', midi_tempo: '正在估计速度', midi_notes: '正在识别旋律与鼓组',
    midi_chords: '正在识别和弦', midi_export: '正在保存三轨 MIDI',
    rvc_import: '正在导入训练素材', rvc_separate: '正在分离训练人声',
    rvc_model_import: '正在检查并导入音色', rvc_model_export: '正在导出音色包',
    rvc_storage_copy: '正在校验并复制音色数据', rvc_storage_switch: '正在切换音色数据目录',
    rvc_preflight: '正在检查训练条件', rvc_preprocess: '正在切分训练素材', rvc_f0: '正在提取音高',
    rvc_features: '正在提取人声特征', rvc_train: '正在训练音色模型',
    rvc_index: '正在生成音色索引', rvc_export: '正在加入音色库', rvc_infer: '正在生成音色试听',
    yue2_training_assets: '正在安装训练资源', yue2_prepare: '正在生成训练 token',
    yue2_loading: '正在加载 YuE2 基模', yue2_training: '正在训练歌曲风格',
    pausing: '正在完成当前训练步并保存', paused: '训练已安全暂停',
    planning: '正在创作旋律与和弦', semantic: '正在生成音乐结构',
    synthesis: '正在合成人声与伴奏', decoding: '正在输出音频',
    loading_transcriber: '正在加载转谱模型', transcribing: '正在从音频提取旋律',
    encoding: '正在读取音频', notation: '正在整理 ABC 与 MIDI 乐谱',
    separating_vocals: '正在分离人声与伴奏', loading_voice_model: '正在加载参考音色模型',
    converting_voice: '正在转换演唱音色', remixing: '正在重新混音',
    mulacover_loading: '正在加载重新编曲模型', mulacover_transcribing: '正在提取旋律与和弦',
    mulacover_style: '正在编码歌词与曲风', mulacover_generating: '正在生成重新编曲版本',
    mulacover_decoding: '正在解码完整歌曲',
    midi_decode:'正在解码音乐',midi_tempo:'正在估计速度',midi_notes:'正在识别旋律与鼓组',midi_chords:'正在识别和弦',midi_export:'正在保存 MIDI',
    cancelling: '正在安全停止', complete: '已完成', failed: '任务失败',
    cancelled: '已取消', doctor: '正在验证运行环境', running: '正在执行'
  })[stage] || window.assistantStageLabel?.(stage) || stage;
}

function stageHint(job) {
  if (job.kind === 'rvc_storage_move') return '正在本机复制并校验数据，全部通过后切换目录。原目录会保留为备份。';
  if (job.kind === 'assistant') return '文本创作与音乐任务串行。已完成的内容会保留在 AI 创作助手页面，可编辑后发送到其他页面。';
  if (job.comparison_backend && ['loading_voice_model','converting_voice'].includes(job.stage)) return `正在制作 ${job.comparison_backend === 'rvc' ? 'RVC' : 'Seed-VC'} 对比音频。两种转换依次执行，已完成的结果会保留。`;
  const hints = {
    queued: '等待前面的任务完成后自动开始。', starting: '正在启动任务进程并加载所需模型。',
    candidate: '正在准备本轮生成参数。', planning: '正在根据歌词和风格安排旋律、节拍与和弦。',
    semantic: '正在创作歌曲结构、旋律走向与音乐语义。', synthesis: '正在合成人声、乐器和声学细节。',
    decoding: '正在输出 48 kHz 双声道音频，已经接近完成。', loading_transcriber: '正在将转谱模型载入 GPU。',
    transcribing: '正在从上传的音频中识别旋律和节拍。', encoding: '正在准备音频数据。',
    notation: '正在生成可编辑的 ABC、MIDI 和乐谱预览。', doctor: '正在检查 GPU、运行库和全部模型文件。',
    separating_vocals: '正在把歌曲拆分为人声和伴奏；已有校验通过的分离结果会直接复用。', loading_voice_model: '正在加载所选音色转换模型。',
    converting_voice: '保留歌曲旋律与演唱节奏，把人声转换成所选音色。', remixing: '正在将转换后的人声与原伴奏合成为 48 kHz 双声道成品。',
    mulacover_loading: '正在按需载入 MuLaCover；各组件会依次使用显存。',
    mulacover_transcribing: '正在把参考歌曲转换成旋律、和弦与鼓组条件，并保存可复用 MIDI。',
    mulacover_style: '正在编码新歌词、流派、乐器、主题和情绪。',
    mulacover_generating: '正在沿用提取的旋律创作新的完整演唱与伴奏。',
    mulacover_decoding: '正在把音乐 token 解码为可试听的完整歌曲。',
    midi_decode:'仅处理选中的原曲范围，不按歌曲生成时长截断。',
    midi_tempo:'估计结果可在编辑器校正；原始识别时间会保留。',
    midi_notes:'识别主唱、候选乐器与鼓点；完成的轨道会及时保存。',
    midi_chords:'使用本地和弦模型识别和弦音级；已有旋律和鼓组继续保留。',
    midi_export:'整理轨道状态，保存可编辑 MIDI 和来源；没有音符与识别失败分开显示。',
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

function publicErrorSummary(value) {
  const detail = String(value || '').trim();
  if (/numpy(?:\._core)?[^\n]*multiarray/i.test(detail)) return '音频组件加载失败。请重启整合包后重试；若仍失败，请运行完整自检。';
  const technical = /Traceback|\b(?:NameError|KeyError|AttributeError|RuntimeError|FileNotFoundError|ModuleNotFoundError|OSError)\b|^['"][^'"]+['"]$|name ['"].+['"] is not defined|(?:object|module .+?) has no attribute|No module named|File ['"]|Path is outside allowed directory|[A-Za-z]:\\|\/(?:home|tmp|var)\//i.test(detail);
  return technical ? '任务运行时出现技术错误，请查看任务日志了解详情。' : (detail.split(/\r?\n/, 1)[0] || '未知错误');
}

function failureMarkup(error) {
  const id = error?.jobId;
  const job = error?.job || {};
  const oom = /out of memory/i.test(error?.message || '');
  const reason = oom ? '显存不足，任务已停止。请在高级设置选择“低显存 · CPU 分批加载”，关闭其他占用显卡的程序后，再使用保存的阶段结果重新运行。调低预算不会缩小模型；预算还包含 2 GiB 预留。' : publicErrorSummary(error?.message);
  const generatedAudio = job.generated_result?.audio;
  const generatedRel = generatedAudio && id ? relativeAudio(job, generatedAudio) : null;
  const intermediate = generatedRel ? `<p>歌曲已生成，可先试听：</p><audio controls preload="metadata" aria-label="已生成歌曲试听" src="${audioUrl(id, generatedRel)}"></audio>` : '';
  const phase = job.failed_stage ? `<p>失败阶段：${escapeHtml(stageLabel(job.failed_stage))}</p>` : '';
  const retry = id && ['generate', 'reference_cover', 'voice_convert', 'render_plan', 'mulacover_remix', 'midi_extract'].includes(job.kind) ? `<button class="primary compact" data-kind="${job.kind}" onclick="resumeJob('${id}', this)">${job.resumable ? '从已保存阶段继续' : '重新运行'}</button>` : '';
  const actions = id ? `<div class="toolbar failure-actions">${retry}<button class="ghost compact" onclick="toggleJobLog('${id}', this)">查看任务日志</button></div><pre class="job-log hidden"></pre>` : '';
  const retained = document.createElement('div');
  if (job.result?.comparison && job.result?.candidates?.length) renderJob(job, retained);
  return `<div class="result-card failure-card"><b class="status-failed">${job.status === 'cancelled' ? '任务已取消' : '任务失败'}</b>${phase}<p>${escapeHtml(reason)}</p>${intermediate}${actions}</div>` + retained.innerHTML;
}

async function resumeJob(id, button) {
  button.disabled = true;
  try {
    const resumableGeneration = ['generate', 'reference_cover', 'render_plan'].includes(button.dataset.kind);
    const options = {method: 'POST'};
    if (resumableGeneration) {
      options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify({memory_budget_gib: generationMemoryBudget(), model_loading: generationModelLoading()});
    }
    const job = await api(`/api/jobs/${id}/resume`, options);
    const panel = savedValue(`job-panel:${id}`) || button.closest('.panel')?.id;
    if (['create', 'plan', 'remix', 'cover'].includes(panel)) savedValue(`job-panel:${job.id}`, panel);
    bindButton(button, job);
    await refreshWorkspace();
    $('#task-center').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) { button.disabled = false; alert(error.message); }
}
window.resumeJob = resumeJob;

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
  $('#model-path-summary').textContent = `${data.using_default ? '共享模型库' : '自定义共享模型库'} · ${data.model_directory}（目录名不代表应用版本）`;
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

function taskSteps(job) {
  return {
    generate: [['starting', '加载模型'], ['planning', '创作乐谱'], ['semantic', '生成结构'], ['synthesis', '合成人声与伴奏'], ['decoding', '输出音频']],
    render_plan: [['starting', '加载模型'], ['semantic', '生成结构'], ['synthesis', '合成人声与伴奏'], ['decoding', '输出音频']],
    plan: [['starting', '加载模型'], ['planning', '创作乐谱']],
    transcribe: [['starting', '准备音频'], ['loading_transcriber', '加载模型'], ['transcribing', '识别旋律'], ['notation', '整理乐谱']],
    midi_extract: [['midi_decode','解码音乐'],['midi_tempo','估计速度'],['midi_notes','旋律与鼓组'],['midi_chords','和弦'],['midi_export','保存 MIDI']],
    semantic: [['starting', '加载模型'], ['planning', '检查乐谱'], ['semantic', '生成结构']],
    synthesize: [['starting', '加载模型'], ['synthesis', '合成声音']], decode: [['starting', '加载模型'], ['decoding', '输出音频']],
    doctor: [['starting', '启动检查'], ['doctor', '验证环境']],
    reference_cover: [['starting', '加载模型'], ['planning', '检查乐谱'], ['semantic', '生成结构'], ['synthesis', '合成歌曲'], ['decoding', '输出歌曲'], ['separating_vocals', '分离人声'], ['loading_voice_model', '加载音色'], ['converting_voice', '转换音色'], ['remixing', '混音']],
    voice_convert: [['starting', '准备音频'], ['separating_vocals', '分离人声'], ['loading_voice_model', '加载音色模型'], ['converting_voice', '转换音色'], ['remixing', '重新混音']],
    mulacover_remix: [['starting', '启动任务'], ['mulacover_loading', '加载模型'], ['mulacover_transcribing', '提取旋律'], ['mulacover_style', '编码曲风'], ['mulacover_generating', '重新编曲'], ['mulacover_decoding', '解码歌曲']]
  }[job.kind] || [['starting', '准备'], [job.stage, stageLabel(job.stage)]];
}

function taskProgress(job) {
  const explicit = Number(job.progress);
  if (job.progress !== null && job.progress !== undefined && Number.isFinite(explicit) && explicit >= 0) {
    const value = Math.max(0, Math.min(100, Math.round(explicit * 100)));
    return {value, label: `${value}%`};
  }
  const completed = Number(job.completed), total = Number(job.total);
  if (job.completed !== null && job.completed !== undefined && Number.isFinite(completed) && Number.isFinite(total) && total > 0) {
    return {value: Math.max(0, Math.min(100, completed / total * 100)), label: `${completed} / ${total}`};
  }
  const steps = taskSteps(job), stage = job.stage === 'candidate' ? 'starting' : job.stage;
  const current = steps.findIndex(([key]) => key === stage);
  if (current < 0 || steps.length < 2) return null;
  return {value: current / steps.length * 100, label: `阶段 ${current + 1} / ${steps.length}`};
}

function stepsFor(job) {
  if (job.kind === 'assistant') return '';
  const steps = taskSteps(job);
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
  button.disabled = (button.id === 'transcribe-button' && !inputHasSource($('#cover-file'))) ||
    (button.id === 'generate-reference-cover' &&
      (($('#voice-backend').value !== 'seed-vc' && !$('#rvc-cover-model').value) ||
       ($('#voice-backend').value !== 'rvc' && !inputHasSource($('#reference-file'))) ||
       ($('#cover-mode').value === 'direct' && !inputHasSource($('#cover-file')))));
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
    if(button.dataset.jobId!==id){buttonBindings.delete(id);continue;}
    const job = byId.get(id);
    if (!job) continue;
    if (TERMINAL.has(job.status)) restoreButton(button); else updateButton(button, job, positions.get(id));
  }
}

function renderRunningJob(job) {
  const summary = job.summary ? `<p class="task-summary">${escapeHtml(job.summary)}</p>` : '';
  return `<article class="running-job"><div class="task-card-head"><div><span class="task-type">当前正在执行 · ${escapeHtml(kindLabel(job.kind))}</span><b>${escapeHtml(stageLabel(job.stage))}</b></div><button class="danger compact" type="button" data-cancel-job="${escapeHtml(job.id)}" ${job.status === 'cancelling' ? 'disabled' : ''}>${job.status === 'cancelling' ? '正在取消…' : '取消本任务'}</button></div><p class="task-hint">${escapeHtml(stageHint(job))}</p>${summary}${stepsFor(job)}<div class="task-meta"><span>${escapeHtml(sourceLabel(job.source))}</span><span>已运行 ${elapsed(job)}</span><span title="${escapeHtml(job.id)}">任务 ${escapeHtml(shortId(job.id))}</span></div></article>`;
}

function renderQueuedJob(job, position) {
  return `<li class="queue-job"><div class="queue-position"><b>第 ${position + 1} 位</b><span>等待开始</span></div><div class="queue-copy"><b>${escapeHtml(kindLabel(job.kind))}</b><p>${escapeHtml(job.summary || '等待前面的任务完成')}</p><small>${escapeHtml(sourceLabel(job.source))} · ${submittedAt(job)} 提交 · ${escapeHtml(shortId(job.id))}</small></div><button class="danger compact" type="button" data-cancel-job="${escapeHtml(job.id)}">取消排队</button></li>`;
}

function renderTaskCenter(healthData, jobs) {
  const active = jobs.filter(job => !TERMINAL.has(job.status));
  const current = jobs.find(job => job.id === healthData.current_job) || active.find(job => job.status !== 'queued');
  const queued = active.filter(job => job.id !== current?.id && job.status === 'queued').sort((a, b) => a.created_at - b.created_at);
  const queuedTotal = Math.max(queued.length, Number(healthData.queued || 0));
  const hiddenQueued = Math.max(0, queuedTotal - queued.length);
  currentJobId = current?.id || null;
  const cancelActive = $('#cancel-active');
  cancelActive.disabled = !currentJobId;
  cancelActive.title = currentJobId ? '取消当前正在执行的任务' : '当前没有正在执行的任务';
  $('#task-center').classList.toggle('hidden', !current && !queued.length);
  $('#running-section').classList.toggle('hidden', !current);
  $('#queue-section').classList.toggle('hidden', !queued.length);
  $('#running-job').innerHTML = current ? renderRunningJob(current) : '';
  $('#queue-title').textContent = `接下来 · ${queuedTotal} 个等待任务${hiddenQueued ? `（显示最近 ${queued.length} 个）` : ''}`;
  $('#queue-list').innerHTML = queued.map((job,index)=>renderQueuedJob(job,hiddenQueued+index)).join('');
  const workload = $('#task-center-jump');
  workload.classList.toggle('hidden', !current && !queued.length);
  const progress = current ? taskProgress(current) : null;
  $('#background-progress-title').textContent = current ? `${kindLabel(current.kind)} · ${stageLabel(current.stage)}` : '后台任务正在排队';
  $('#background-progress-detail').textContent = current ? `${progress?.label || '处理中'}${queuedTotal ? ` · ${queuedTotal} 个等待` : ''}` : `${queuedTotal} 个任务等待开始`;
  $('#background-progress-track').classList.toggle('hidden', !progress);
  $('#background-progress-bar').style.width = `${progress?.value || 0}%`;
  workload.setAttribute('aria-label', `打开后台任务进度：${$('#background-progress-title').textContent}，${$('#background-progress-detail').textContent}`);
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
  $('#model-path-summary').textContent = `共享模型库 · ${data.ready.model_directory}（可跨应用版本复用）`;
  if (!modelSettingsInitialized) {
    $('#model-settings').open = Boolean(data.ready.settings_error || !data.ready.capabilities?.generation);
    modelSettingsInitialized = true;
  }
}

async function refreshWorkspace() {
  if (workspaceRefreshing) return;
  workspaceRefreshing = true;
  try {
    const projectId=String(window.workbenchProjectId?.()||''),projectScope=projectId||'__global__';
    const [healthData, listData, panelData] = await Promise.all([api('/api/health'),api('/api/jobs?limit=100&compact=1'),api(`/api/jobs?limit=20&compact=1&latest_by_panel=1&project_id=${encodeURIComponent(projectScope)}`)]);
    const jobs=[...listData.jobs];
    if(healthData.current_job&&!jobs.some(job=>job.id===healthData.current_job)){
      const current=await api(`/api/jobs/${encodeURIComponent(healthData.current_job)}`).catch(()=>null);
      if(current)jobs.unshift(current);
    }
    renderHealth(healthData); renderTaskCenter(healthData, jobs);
    if(String(window.workbenchProjectId?.()||'')===projectId)renderPanelResults(panelData.jobs);
    const assetJob = listData.jobs.find(job => job.asset_ids?.length);
    const assetSignature = assetJob ? `${assetJob.id}:${assetJob.asset_ids.length}` : '';
    if (assetSignature && assetSignature !== projectAssetSignature) { projectAssetSignature = assetSignature; window.refreshWorkbenchProject?.(); }
  } catch (error) {
    $('#health-dot').className = 'dot bad'; $('#health-title').textContent = '服务连接中断';
    $('#health-detail').textContent = `正在重试 · ${error.message}`;
  } finally { workspaceRefreshing = false; }
}

async function submit(kind, request, resultTarget, button = null, projectScope = String(window.workbenchProjectId?.() || '')) {
  setSubmitting(button);
  try {
    const generation = ['generate', 'plan', 'render_plan'].includes(kind) ? request : kind === 'reference_cover' ? request.generate : null;
    if (generation) {
      generation.memory_budget_gib = generationMemoryBudget();
      generation.model_loading = generationModelLoading();
    }
    const activeProject = projectScope;
    if (activeProject && ['generate','plan','render_plan','reference_cover','voice_convert','transcribe','mulacover_remix'].includes(kind)) {
      if (kind === 'reference_cover') request.generate.project_id = activeProject;
      else request.project_id = activeProject;
    }
    const clientRequestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    const job = await api('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind, request, source: 'webui', client_request_id: clientRequestId, result_panel: resultTarget?.closest('.panel')?.id})});
    rememberResult(job, resultTarget);
    bindButton(button, job); await refreshWorkspace(); return await waitForJob(job.id, resultTarget);
  } catch (error) { if(String(window.workbenchProjectId?.()||'')===projectScope&&(!error.jobId||button?.dataset.jobId===error.jobId))restoreButton(button); error.projectScope=projectScope; throw error; }
}

function renderScopedFailure(target, error, projectScope = error?.projectScope) {
  if(projectScope!==undefined&&String(window.workbenchProjectId?.()||'')!==String(projectScope||''))return;
  target.innerHTML = failureMarkup(error);
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
      await Promise.all([refreshWorkspace(), loadHistory(), window.refreshWorkbenchProject?.()]); return job;
    }
    if (job.status === 'paused') {
      restoreButton(buttonBindings.get(id)); await Promise.all([refreshWorkspace(), loadHistory()]); return job;
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

function voiceDescription(result) {
  if (!result.backend) return '';
  if (result.backend === 'compare') return 'Seed-VC / RVC 同曲对比';
  const shift = result.settings?.semi_tone_shift;
  const pitch = Number.isInteger(shift) ? ` · ${shift === 0 ? '原调' : (shift > 0 ? '+' : '') + shift + ' 半音'}` : '';
  return escapeHtml((result.backend === 'rvc' ? 'RVC 专属音色' : 'Seed-VC 参考音色') + (result.voice_name ? ` · ${result.voice_name}` : '') + pitch);
}

function stemPlayers(job, result) {
  const players = [['separated_vocal','分离后人声'],['converted_vocal','转换后人声'],['accompaniment','伴奏']].map(([key,label]) => {
    const relative = relativeAudio(job, result[key]);
    if (!relative) return '';
    const url = audioUrl(job.id, relative);
    return `<div class="stem-player"><b>${label}</b><audio controls preload="none" aria-label="${label}试听" src="${url}"></audio><a class="ghost compact" href="${url}" download>下载${label}</a></div>`;
  }).join('');
  return players ? `<details class="stem-previews"><summary>单独试听人声与伴奏</summary>${players}</details>` : '';
}

function renderJob(job, target) {
  const result = job.result || {}; const candidates = result.candidates || (result.audio ? [{seed: result.seed, audio: result.audio, audio_seconds: result.audio_seconds || result.audio_info?.duration_seconds, truncated: result.truncated}] : []);
  if (!candidates.length) { target.innerHTML = `<div class="result-card"><b>任务完成</b><pre class="meta">${escapeHtml(JSON.stringify(result, null, 2))}</pre></div>`; return; }
  const partial = result.partial ? `<div class="result-card">已保留 ${result.completed_candidates}/${result.requested_candidates} 个${result.comparison ? '转换结果' : '版本'}。${result.failures?.length ? `未完成原因：${escapeHtml(publicErrorSummary(result.failures[0].error))}` : '其余结果正在制作中。'}</div>` : '';
  target.innerHTML = partial + candidates.map((candidate, index) => {
    const rel = relativeAudio(job, candidate.audio); const truncated = candidate.truncated && Object.values(candidate.truncated).some(Boolean);
    const url = rel ? audioUrl(job.id, rel) : '';
    const playerLabel = `${kindLabel(job.kind)}${candidates.length > 1 ? `版本 ${index + 1}` : '结果'}试听`;
    const player = url ? `<audio controls preload="metadata" aria-label="${escapeHtml(playerLabel)}" src="${url}"></audio>` : '';
    const duration = Number(candidate.audio_seconds || candidate.audio_info?.duration_seconds);
    const midiVersion=result.midi_document_version?`MIDI v${Number(result.midi_document_version)}（生成时固定版本）`:'';
    const details = [voiceDescription({...result,...candidate}), midiVersion, candidates.length > 1 ? `版本 ${index + 1}` : '', Number.isFinite(duration) && duration > 0 ? `${duration.toFixed(1)} 秒` : '', candidate.seed != null ? `Seed ${escapeHtml(candidate.seed)}` : '', `任务 ${escapeHtml(shortId(job.id))}`].filter(Boolean).join(' · ');
    const download = url ? `<a class="ghost audio-download" href="${url}" download="YuE2-${job.id}-${index + 1}.flac">下载音频</a>` : '';
    const remixFiles = job.kind === 'mulacover_remix' ? [['melody_midi','旋律 MIDI'],['chord_midi','和弦 MIDI'],['drum_midi','鼓组 MIDI']].map(([key,label]) => { const file = relativeAudio(job,result[key]); return file ? `<a class="ghost compact" href="${audioUrl(job.id,file)}" download>${label}</a>` : ''; }).join('') : '';
    const remixVoice = job.kind === 'mulacover_remix' && rel ? `<button class="primary" type="button" onclick="sendJobAudioToCover('${job.id}','${rel}')">发送到音色转换</button>` : '';
    const midiEdit=job.kind==='mulacover_remix'&&result.melody_midi?`<button class="ghost" type="button" onclick="midiOpenJobCondition('${job.id}')">编辑本次 MIDI 条件</button>`:rel?`<button class="ghost" type="button" onclick="midiOpenJobAudio('${job.id}','${rel}')">提取并编辑 MIDI</button>`:'';
    return `<article class="result-card"><header><div><b>${escapeHtml(kindLabel(job.kind))}已完成 · 可试听</b><div class="meta">${details}</div></div><span class="badge">${truncated ? '已截断' : '完整'}</span></header>${player}<div class="toolbar">${download}${remixFiles}${remixVoice}${midiEdit}${TERMINAL.has(job.status) ? `<button class="ghost" onclick="exportJob('${job.id}')">导出全部文件</button>` : ''}</div>${stemPlayers(job,{...result,...candidate})}</article>`;
  }).join('');
}

async function exportJob(id) {
  try { const data = await api('/api/export', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({job_id: id})}); alert(`已导出到\n${data.destination}`); }
  catch (error) { alert(error.message); }
}
window.exportJob = exportJob;

async function sendJobAudioToCover(jobId, relative) {
  try {
    setLocalInputSource('#cover-file', {$job_file:{job_id:jobId,relative},name:`MuLaCover-${jobId}.flac`},
      `MuLaCover-${jobId}.flac`, audioUrl(jobId, relative));
    window.setCoverMode?.('direct');
    $('.tab[data-tab="cover"]').click();
  } catch (error) { alert(error.message); }
}
window.sendJobAudioToCover = sendJobAudioToCover;

function activateTab(button, userInitiated = true) {
  savedValue('active-tab', button.dataset.tab);
  document.body.dataset.activeTab = button.dataset.tab;
  $('#model-settings').open = false;
  if(userInitiated && button.closest('.studio-sidebar'))button.scrollIntoView({block:'nearest',inline:'center'});
  $$('.tab').forEach(item => { const active=item.dataset.tab===button.dataset.tab;item.classList.toggle('active',active);if(active)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current'); });
  $$('#workspace-menu-dialog [data-go-tab]').forEach(item => { const active=item.dataset.goTab===button.dataset.tab;item.classList.toggle('active',active);if(active)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current'); });
  $$('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.tab));
  window.scrollTo({top: 0, left: 0, behavior: 'auto'});
  if (button.dataset.tab === 'history') loadHistory();
  if (button.dataset.tab === 'assistant') window.assistantRestoreCurrentScope?.();
}
$$('.tab').forEach(button => button.onclick = () => activateTab(button, true));
const restoredTab = savedValue('active-tab');
const allowedTabs = ['project', 'assets', 'training', 'create', 'plan', 'remix', 'midi', 'cover', 'history', 'assistant', 'voices'];
const initialTab = allowedTabs.includes(restoredTab) ? restoredTab : document.body.dataset.activeTab;
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
activateTab($(`.tab[data-tab="${initialTab}"]`), false);
requestAnimationFrame(() => window.scrollTo({top: 0, left: 0, behavior: 'auto'}));

const workspaceMenuDialog = $('#workspace-menu-dialog');
$('#mobile-workspace-menu').onclick = () => workspaceMenuDialog.showModal();
workspaceMenuDialog.addEventListener('click', event => { if(event.target.closest('[data-go-tab]'))workspaceMenuDialog.close(); });

function openHistory() { $('.tab[data-tab="history"]').click(); $('#history').scrollIntoView({behavior: 'smooth', block: 'start'}); }
function openTaskCenter() { $('#task-center').scrollIntoView({behavior: 'smooth', block: 'nearest'}); }
document.addEventListener('click', event => {
  const goTab = event.target.closest('[data-go-tab]');
  if (goTab) {
    const target = $(`.tab[data-tab="${goTab.dataset.goTab}"]`);
    if (target) target.click();
  }
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
  catch (error) { renderScopedFailure($('#create-result'),error); }
};

function requiredFieldLabel(field) {
  const label = field.closest('label');
  if (!label) return '此项';
  const copy = label.cloneNode(true);
  copy.querySelectorAll('input,select,textarea,button,output').forEach(element => element.remove());
  return copy.textContent.replace(/（.*?）/g, '').trim() || '此项';
}
document.addEventListener('invalid', event => {
  const field = event.target;
  if (!field.validity || typeof field.setCustomValidity !== 'function') return;
  if (field.validity.customError && field.dataset.requiredMessageActive !== 'true') return;
  const label = requiredFieldLabel(field);
  let message = '';
  if (field.validity.valueMissing) message = field.type === 'checkbox' || field.type === 'radio'
    ? `请先确认：${label}` : field.tagName === 'SELECT' ? `请选择${label}` : `请填写${label}`;
  else if (field.validity.rangeUnderflow) message = `${label}不能小于 ${field.min}`;
  else if (field.validity.rangeOverflow) message = `${label}不能大于 ${field.max}`;
  else if (field.validity.tooLong) message = `${label}最多 ${field.maxLength} 个字符`;
  else if (field.validity.tooShort) message = `${label}至少 ${field.minLength} 个字符`;
  else if (field.validity.stepMismatch || field.validity.badInput || field.validity.typeMismatch || field.validity.patternMismatch) message = `请填写有效的${label}`;
  if (!message) return;
  field.dataset.requiredMessageActive = 'true';
  field.setCustomValidity(message);
}, true);
for (const eventName of ['input', 'change']) document.addEventListener(eventName, event => {
  const field = event.target;
  if (field.dataset?.requiredMessageActive === 'true') {
    delete field.dataset.requiredMessageActive;
    field.setCustomValidity('');
  }
}, true);

$('#plan-form').onsubmit = async event => {
  event.preventDefault();
  const revision = window.assistantDraftRevision?.('plan');
  const projectScope=String(window.workbenchProjectId?.()||'');
  try {
    const request = formObject(event.target); request.backend = 'torch-eager';
    const job = await submit('plan', request, null, $('#plan-button'), projectScope);
    if(String(window.workbenchProjectId?.()||'')!==projectScope)return;
    const apply = () => {
      planState = {...job.result, request, source: 'saved_exact'};
      $('#plan-abc').value = job.result.abc || ''; $('#plan-exact').disabled = false; $('#plan-exact').checked = true; $('#plan-abc').disabled = true;
      $('#plan-badge').textContent = job.result.truncated ? '计划已截断' : '原始计划'; $('#plan-workbench').classList.remove('hidden');
      window.assistantDraftChanged?.('plan');
    };
    if (window.assistantDraftRevision?.('plan') === revision) apply();
    else {
      const notice = document.createElement('div'); notice.className = 'result-card'; notice.textContent = '计划已完成，当前草稿已有新编辑，因此没有覆盖。';
      const button = document.createElement('button'); button.className = 'ghost'; button.textContent = '载入这份计划'; button.onclick = async () => { if (await uiConfirm('替换当前乐谱草稿？')) apply(); };
      notice.append(button); $('#plan-result').append(notice);
    }
  } catch (error) { renderScopedFailure($('#plan-result'),error,projectScope); }
};

$('#plan-exact').onchange = event => { $('#plan-abc').disabled = event.target.checked; };
$('#plan-abc').disabled = true;
window.importPlanAbc = (abc, badge = '外部导入谱 · 重新生成') => {
  const request = formObject($('#plan-form'));
  planState = {source: 'imported_abc', request: {style: request.style || '', lyrics: request.lyrics || '', cot: request.cot || 'full'}};
  $('#plan-abc').value = String(abc || '');
  $('#plan-abc').disabled = false;
  $('#plan-exact').checked = false;
  $('#plan-exact').disabled = true;
  $('#plan-badge').textContent = badge;
  $('#plan-workbench').classList.remove('hidden');
  window.assistantDraftChanged?.('plan');
};
$('#render-plan').onclick = async () => {
  if (!planState) return;
  const projectScope=String(window.workbenchProjectId?.()||'');
  if (planState.source === 'imported_abc') {
    try {
      const request = {...formObject($('#plan-form')), abc: $('#plan-abc').value, candidates: 1};
      await api('/api/assistant/validate-abc', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({abc: request.abc, cot: request.cot})});
      await submit('generate', request, $('#plan-result'), $('#render-plan'), projectScope);
    } catch (error) { renderScopedFailure($('#plan-result'),error,projectScope); }
    return;
  }
  const exact = $('#plan-exact').checked; const request = {plan_dir: planState.plan_dir, exact, backend: 'torch-eager'};
  if (!exact) Object.assign(request, planState.request, {abc: $('#plan-abc').value, candidates: 1});
  try { await submit('render_plan', request, $('#plan-result'), $('#render-plan'), projectScope); }
  catch (error) { renderScopedFailure($('#plan-result'),error,projectScope); }
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
  const render = () => {
    release();
    const file = input.files[0], local = localInputReference(input), button = $(buttonSelector);
    const present = Boolean(file || local), name = file?.name || input.dataset.localName;
    button.disabled = !present || Boolean(button.dataset.jobId);
    drop.querySelector('b').textContent = present ? name : defaultName;
    preview.classList.toggle('hidden', !present);
    status.textContent = local ? '来自本地资产库或任务结果；提交时由服务端直接引用，不占用浏览器内存。' : defaultStatus;
    if (file) {
      objectUrl = URL.createObjectURL(file);
      audio.src = objectUrl;
      audio.load();
    } else if (local && input.dataset.localPreview) {
      audio.src = input.dataset.localPreview;
      audio.load();
    } else { audio.removeAttribute('src'); audio.load(); }
  };
  input.onchange = () => { clearLocalInputReference(input); render(); };
  input.addEventListener('local-source-change', render);
  preview.querySelector('[data-replace]').onclick = () => input.click();
  audio.addEventListener('play', () => {
    $$('.upload-preview audio').forEach(other => { if (other !== audio) other.pause(); });
  });
  audio.addEventListener('error', () => {
    if (objectUrl && audio.error) status.textContent = '浏览器无法试听此文件，可换用 WAV、MP3 或 FLAC；仍可尝试上传处理。';
  });
  window.addEventListener('pagehide', event => { if (!event.persisted) release(); });
}

function updateMessage(message, state = '') {
  const action = $('.update-action');
  action.classList.toggle('available', state === 'available');
  action.classList.toggle('installing', state === 'installing');
  $('#update-status').textContent = message;
}

async function checkUpdate({quiet = false} = {}) {
  if (updateInstalling) return;
  const button = $('#update-button');
  button.disabled = true;
  if (!quiet) updateMessage('正在连接 GitHub 检查新版…');
  try {
    const result = await api('/api/update/check');
    availableUpdate = result.update_available ? result : null;
    if (availableUpdate) {
      button.textContent = `更新到 v${result.latest_version}`;
      updateMessage(`当前 v${result.current_version} · 新版已发布`, 'available');
    } else {
      button.textContent = '检查更新';
      updateMessage(`当前 v${result.current_version} · 已是最新版本`);
    }
  } catch (error) {
    availableUpdate = null;
    button.textContent = '重新检查';
    updateMessage(`自动检查失败 · ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function waitForUpdatedService(version) {
  const deadline = Date.now() + 60 * 60 * 1000;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 900));
    try {
      const health = await api('/api/health');
      const status = await api('/api/update/status');
      if (status.state === 'error') throw new Error(status.message || '更新失败');
      if (health.version === version && status.state === 'complete') {
        updateMessage(`已更新到 v${version}，正在刷新页面`, 'installing');
        setTimeout(() => location.reload(), 700);
        return;
      }
      if (status.message) updateMessage(status.message, 'installing');
      if (status.state === 'complete' && health.version !== version) {
        throw new Error(`服务版本仍为 v${health.version}`);
      }
    } catch (error) {
      if (!String(error.message).includes('Failed to fetch') && !String(error.message).includes('服务连接')) {
        throw error;
      }
      updateMessage(`正在安装 v${version} 并重启本地服务…`, 'installing');
    }
  }
  throw new Error('更新尚未完成，请查看已打开的升级进度页或 logs/update.stdout.log；请勿重复启动安装');
}

async function installUpdate() {
  if (!availableUpdate) return checkUpdate();
  if (!await uiConfirm(`更新会替换程序代码并保留模型、作品和设置。现有代码会先备份到 logs/backups，失败时自动恢复。\n\n确定更新到 v${availableUpdate.latest_version}？`, '安装更新', `更新到 v${availableUpdate.latest_version}`)) return;
  const button = $('#update-button');
  updateInstalling = true;
  button.disabled = true;
  button.textContent = '正在下载…';
  updateMessage(`正在下载并校验 v${availableUpdate.latest_version}…`, 'installing');
  try {
    const result = await api('/api/update/install', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    button.textContent = '正在重启…';
    await waitForUpdatedService(result.target_version);
  } catch (error) {
    updateInstalling = false;
    button.disabled = false;
    button.textContent = '重新检查';
    availableUpdate = null;
    updateMessage(`更新没有完成 · ${error.message}`);
  }
}
bindUploadPreview('#cover-file', '#drop-zone', '#cover-preview', '#transcribe-button');
function setCoverMode(mode) {
  const direct = mode === 'direct';
  $('#cover-mode').value = direct ? 'direct' : 'generate';
  $('#transcribe-button').classList.toggle('hidden', direct);
  $('#cover-generation-fields').classList.toggle('hidden', direct);
  $('#cover-regenerate-choice').classList.toggle('hidden', direct);
  $('#cover .creation-options').style.gridTemplateColumns = direct ? '1fr' : '';
  if (direct) $('#cover-review').classList.remove('hidden');
  $('#cover-mode-hint').textContent = direct ? '上传已有歌曲，分离人声后转换音色，再与原伴奏混音；无需转谱或填写歌词。' : '从原曲提取旋律，核对歌词和曲风后重新生成歌曲。';
  $('#cover-voice-hint').textContent = direct ? '为上方上传歌曲的人声转换音色，保留原伴奏。' : '先生成歌曲，再分离人声并转换音色，最后与伴奏重新混音。';
  const button = $('#generate-reference-cover');
  button.dataset.idleLabel = direct ? '转换上传歌曲的音色' : '生成参考音色翻唱';
  if (!button.dataset.jobId) restoreButton(button);
  savedValue('cover-mode', $('#cover-mode').value);
}
window.setCoverMode = setCoverMode;
$('#cover-mode').onchange = () => { setCoverMode($('#cover-mode').value); window.assistantDraftChanged?.('cover'); };
setCoverMode(savedValue('cover-mode') || 'generate');
$('#cover-file').addEventListener('change', () => {
  if (!$('#generate-reference-cover').dataset.jobId) restoreButton($('#generate-reference-cover'));
});
$('#transcribe-button').onclick = async () => {
  const input = $('#cover-file'); if (!inputHasSource(input)) return; const button = $('#transcribe-button');
  const revision = window.assistantDraftRevision?.('cover');
  const projectScope=String(window.workbenchProjectId?.()||'');
  try {
    setSubmitting(button); button.textContent = localInputReference(input) ? '正在读取本地素材…' : '正在上传…';
    const sourcePath = await inputSourceValue(input);
    const job = await submit('transcribe', {source_path: sourcePath, melody_only: true, dtype: 'bf16', preset: 'default'}, null, button, projectScope);
    if(String(window.workbenchProjectId?.()||'')!==projectScope)return;
    if (window.assistantDraftRevision?.('cover') === revision) {
      $('#cover-abc').value = job.result.abc || ''; $('#cover-review').classList.remove('hidden'); window.assistantDraftChanged?.('cover');
    } else {
      const notice = document.createElement('div'); notice.className = 'result-card'; notice.textContent = '转谱已完成，当前草稿已有新编辑，因此没有覆盖。';
      const apply = document.createElement('button'); apply.className = 'ghost'; apply.textContent = '载入转谱结果'; apply.onclick = async () => { if (await uiConfirm('替换当前旋律 ABC？')) { $('#cover-abc').value = job.result.abc || ''; $('#cover-review').classList.remove('hidden'); window.assistantDraftChanged?.('cover'); } };
      notice.append(apply); $('#cover-result').append(notice);
    }
  } catch (error) { restoreButton(button);renderScopedFailure($('#cover-result'),error,projectScope); }
};
$('#generate-cover').onclick = async () => {
  let seed; try { seed = safeSeed($('#cover-seed').value); } catch (error) { return alert(error.message); }
  const request = {style: $('#cover-style').value, lyrics: $('#cover-lyrics').value, abc: $('#cover-abc').value, cot: 'melody', seed, cfg_scale: 1, backend: 'torch-eager', candidates: 1};
  if (!request.lyrics.trim() && $('#cover').dataset.instrumental !== 'true') return alert('请先填写并核对歌词');
  if (!request.abc.trim()) return alert('请先转谱或导入有效的旋律 ABC');
  try { await submit('generate', request, $('#cover-result'), $('#generate-cover')); }
  catch (error) { renderScopedFailure($('#cover-result'),error); }
};

bindUploadPreview('#reference-file', '#reference-drop-zone', '#reference-preview', '#generate-reference-cover');
$('#reference-file').addEventListener('change', () => {
  if (!$('#generate-reference-cover').dataset.jobId) restoreButton($('#generate-reference-cover'));
});

$('#generate-reference-cover').onclick = async () => {
  const projectScope=String(window.workbenchProjectId?.()||'');
  const direct = $('#cover-mode').value === 'direct', sourceInput = $('#cover-file');
  if (direct && !inputHasSource(sourceInput)) return alert('请先选择要转换音色的歌曲');
  if (!direct && $('#cover').dataset.instrumental === 'true') return alert('纯器乐没有可转换的人声，请使用旋律重制');
  const referenceInput = $('#reference-file');
  const backend = $('#voice-backend').value;
  if (backend !== 'rvc' && !inputHasSource(referenceInput)) return alert('请先选择参考音色');
  if (backend !== 'seed-vc' && !$('#rvc-cover-model').value) return alert('请先到“我的音色 / 训练”创建或导入音色模型');
  if (backend !== 'seed-vc' && !$('#rvc-pitch-shift').reportValidity()) return;
  if (backend !== 'rvc' && !$('#voice-shift').reportValidity()) return;
  let seed;
  try { if (!direct) { seed = safeSeed($('#cover-seed').value); generationMemoryBudget(); } } catch (error) { return alert(error.message); }
  const generate = {style: $('#cover-style').value, lyrics: $('#cover-lyrics').value, abc: $('#cover-abc').value, cot: 'melody', seed, cfg_scale: 1, backend: 'torch-eager', candidates: 1, offload_ar: true, nar_query_chunk_size: 256, nar_attention: 'sdpa'};
  if (!direct && !generate.lyrics.trim()) return alert('请先填写并核对歌词');
  if (!direct && !generate.abc.trim()) return alert('请先转谱或导入有效的旋律 ABC');
  const button = $('#generate-reference-cover');
  setSubmitting(button);
  try {
    $('#cover-result').innerHTML = '';
    const referencePath = backend !== 'rvc' ? await inputSourceValue(referenceInput) : null;
    const voice = {backend, reference_path: referencePath,
      voice_id: $('#rvc-cover-model').value, speaker_id: Number($('#rvc-cover-speaker').value),
      index_rate: Number($('#rvc-index-rate').value), protect: Number($('#rvc-protect').value),
      rvc_pitch_shift: Number($('#rvc-pitch-shift').value),
      diffusion_steps: Number($('#voice-steps').value), cfg_rate: Number($('#voice-cfg').value),
      semi_tone_shift: Number($('#voice-shift').value), auto_f0_adjust: $('#voice-auto-f0').checked,
      vocal_gain_db: Number($('#voice-gain').value), accompaniment_gain_db: Number($('#backing-gain').value)};
    if (direct) {
      const sourcePath = await inputSourceValue(sourceInput);
      await submit('voice_convert', {...voice, source_path: sourcePath}, $('#cover-result'), button, projectScope);
    } else await submit('reference_cover', {generate, voice}, $('#cover-result'), button, projectScope);
  } catch (error) { restoreButton(button);renderScopedFailure($('#cover-result'),error,projectScope); }
};

function firstResultAudio(job) {
  return job.result?.audio || job.result?.candidates?.[0]?.audio;
}

async function loadHistory() {
  const revision=++historyLoadRevision;
  try {
    const query=new URLSearchParams({limit:historyPageSize,offset:historyOffset});
    if($('#history-status').value)query.set('status',$('#history-status').value);if($('#history-kind').value)query.set('kind',$('#history-kind').value);if($('#history-project').value)query.set('project_id',$('#history-project').value);if($('#history-query').value.trim())query.set('q',$('#history-query').value.trim());
    const {jobs,total} = await api('/api/jobs?' + query);if(revision!==historyLoadRevision)return;historyTotal=total;historyPageJobs=jobs;if(historyOffset>0&&historyOffset>=total){historyOffset=Math.max(0,Math.floor((total-1)/historyPageSize)*historyPageSize);return loadHistory();}
    const historyHasFilters=Boolean($('#history-status').value||$('#history-kind').value||$('#history-project').value||$('#history-query').value.trim());
    $('#history-list').innerHTML = jobs.map(job => {
      const result = job.result || {}; const audio = relativeAudio(job, result.audio || result.candidates?.[0]?.audio);
      const internalKinds=new Set(['assistant','doctor','yue2_training_assets','yue2_prepare','workbench_migrate','rvc_import','rvc_storage_move','midi_extract']);
      const canExport=(job.status === 'complete' || (TERMINAL.has(job.status) && result.comparison && result.candidates?.length)) && job.result && !internalKinds.has(job.kind);
      const jobName=`${kindLabel(job.kind)}任务 ${shortId(job.id)}`;
      const exportButton = canExport ? `<button class="ghost" onclick="exportJob('${job.id}')" aria-label="${job.kind==='yue2_train'?'导出模型包':'导出'}：${escapeHtml(jobName)}">${job.kind==='yue2_train'?'导出模型包':'导出'}</button>` : '';
      const retryButton = (['failed', 'cancelled'].includes(job.status)||job.kind==='midi_extract'&&result.partial) && ['generate', 'reference_cover', 'voice_convert', 'render_plan', 'mulacover_remix', 'midi_extract', 'rvc_train', 'rvc_import', 'rvc_separate', 'rvc_storage_move'].includes(job.kind) ? `<button class="ghost compact" data-kind="${job.kind}" onclick="resumeJob('${job.id}', this)" aria-label="继续：${escapeHtml(jobName)}">${job.kind==='midi_extract'&&result.partial?'重新识别失败轨':job.resumable || ['rvc_train','rvc_storage_move'].includes(job.kind) ? '从已保存阶段继续' : '重新运行'}</button>` : '';
      const midiResult=job.kind==='midi_extract'&&result.midi_document_id?`<button class="ghost" type="button" onclick="midiOpenExtractionResult('${job.id}')">编辑 / 下载 MIDI</button>`:'';
      const partialMidi=job.kind==='midi_extract'&&result.partial?'<div class="history-error"><b>部分完成</b><span>成功识别的轨道已保存，失败轨道可重新识别。</span></div>':'';
      const logButtons = (job.kind === 'assistant' ? `<button class="ghost compact" onclick="openAssistantJob('${job.id}')" aria-label="查看或继续：${escapeHtml(jobName)}">查看 / 继续创作</button>` : '') + (job.status === 'failed' ? `<button class="ghost compact" onclick="toggleJobLog('${job.id}', this)" aria-label="查看日志：${escapeHtml(jobName)}">查看任务日志</button><button class="ghost compact" onclick="openDirectory('logs')">打开日志目录</button>` : '');
      const comparison = result.comparison ? (result.candidates || []).map((candidate,index) => {
        const rel = relativeAudio(job, candidate.audio);
        const label=candidate.backend==='rvc'?'RVC 对比结果':candidate.backend==='seed-vc'?'Seed-VC 对比结果':`对比结果 ${index+1}`;
        return `<div class="comparison-result"><p class="meta">${voiceDescription(candidate)}</p>${rel ? `<audio controls preload="none" aria-label="${label}试听" src="${audioUrl(job.id,rel)}"></audio><a class="ghost compact" href="${audioUrl(job.id,rel)}" download>下载音频</a>` : ''}${stemPlayers(job,candidate)}</div>`;
      }).join('') : '';
      const errorDetail=String(job.error||'').trim(),errorSummary=publicErrorSummary(errorDetail);
      const errorBlock=errorDetail?`<div class="history-error"><b>任务未完成</b><span>${escapeHtml(errorSummary)}</span>${errorDetail!==errorSummary?`<details><summary>查看错误详情</summary><pre>${escapeHtml(errorDetail)}</pre></details>`:''}</div>`:'';
      const canClean=['complete','failed','cancelled'].includes(job.status);
      const select=`<input type="checkbox" class="cleanup-select" data-select-job="${job.id}" aria-label="选择清理：${escapeHtml(jobName)}" ${selectedHistoryJobs.has(job.id)?'checked':''} ${canClean?'':'disabled'} title="${canClean?'可选择清理；执行前检查引用':'正在运行、排队或暂停的任务会保留'}">`;
      const clean=canClean?`<button class="ghost compact" type="button" data-clean-job="${job.id}" aria-label="清理：${escapeHtml(jobName)}">清理任务</button>`:'';
      return `<article class="history-card"><header>${select}<div><b>${escapeHtml(kindLabel(job.kind))}</b><div class="meta">${escapeHtml(job.id)} · ${new Date(job.created_at * 1000).toLocaleString()} · ${escapeHtml(sourceLabel(job.source))}</div></div></header><b class="status-${job.status}">${escapeHtml(job.status === 'running' ? stageLabel(job.stage) : stageLabel(job.status))}</b>${errorBlock}${partialMidi}${!result.comparison && audio ? `<audio controls preload="none" aria-label="${escapeHtml(kindLabel(job.kind))}历史结果试听" src="${audioUrl(job.id, audio)}"></audio>` : ''}<p class="meta">${voiceDescription(result)}</p>${comparison || stemPlayers(job,result)}<div class="toolbar">${midiResult}${exportButton}${retryButton}${logButtons}${clean}</div><pre class="job-log hidden"></pre></article>`;
    }).join('') || `<div class="empty-state"><i class="bi bi-clock-history"></i><b>还没有符合条件的任务</b><p>${historyHasFilters?'清除筛选可查看全部任务记录。':'完成的任务会显示在这里。'}</p>${historyHasFilters?'<button id="clear-history-filters" class="ghost compact" type="button">清除筛选</button>':''}</div>`;
    const clearHistory=$('#clear-history-filters');if(clearHistory)clearHistory.onclick=()=>{$('#history-status').value='';$('#history-kind').value='';$('#history-project').value='';$('#history-query').value='';historyOffset=0;loadHistory();};
    $$('[data-select-job]').forEach(input=>input.onchange=()=>{if(input.checked&&selectedHistoryJobs.size>=500){input.checked=false;$('#history-cleanup-status').textContent='每次最多选择 500 项任务。';return;}input.checked?selectedHistoryJobs.add(input.dataset.selectJob):selectedHistoryJobs.delete(input.dataset.selectJob);updateHistorySelection();});
    $$('[data-clean-job]').forEach(button=>button.onclick=()=>cleanupJobs({ids:[button.dataset.cleanJob]}));
    updateHistorySelection();
    const page=Math.floor(historyOffset/historyPageSize)+1,pages=Math.max(1,Math.ceil(historyTotal/historyPageSize));$('#history-page-status').textContent=`第 ${page} / ${pages} 页 · ${historyTotal} 项`;$('#history-prev').disabled=historyOffset<=0;$('#history-next').disabled=historyOffset+historyPageSize>=historyTotal;
  } catch (error) { if(revision===historyLoadRevision)$('#history-list').innerHTML = `<p class="status-failed">${escapeHtml(error.message)}</p>`; }
}

function updateHistorySelection() {
  const eligible=historyPageJobs.filter(job=>['complete','failed','cancelled'].includes(job.status)).map(job=>job.id),selected=eligible.filter(id=>selectedHistoryJobs.has(id)).length;
  $('#history-selection-count').textContent=`已选 ${selectedHistoryJobs.size} 项（可跨页）`;
  $('#history-clear-selection').disabled=jobCleanupBusy||!selectedHistoryJobs.size;
  $('#history-select-page').checked=eligible.length>0&&selected===eligible.length;
  $('#history-select-page').indeterminate=selected>0&&selected<eligible.length;
  $('#history-select-page').disabled=jobCleanupBusy||!eligible.length;
  $('#cleanup-selected-jobs').disabled=jobCleanupBusy||!selectedHistoryJobs.size;
  $('#cleanup-failed-jobs').disabled=jobCleanupBusy;
  $$('[data-select-job]').forEach(input=>input.disabled=jobCleanupBusy||!eligible.includes(input.dataset.selectJob));
  $$('[data-clean-job]').forEach(button=>button.disabled=jobCleanupBusy);
}
async function cleanupJobs(data) {
  if(jobCleanupBusy)return;
  jobCleanupBusy=true;updateHistorySelection();
  const status=$('#history-cleanup-status');
  try {
    status.textContent='正在检查可清理任务与文件引用…';
    const preview=await api('/api/jobs/cleanup-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const protection=preview.skipped.slice(0,5).map(item=>`${shortId(item.id)}：${item.reason}`).join('\n');
    if(!preview.deletable.length){status.textContent='没有可清理的任务。'+(protection?' '+protection:'');return;}
    const message=`将永久删除 ${preview.deletable.length} 项任务及其结果、日志，约释放 ${(preview.bytes/1048576).toFixed(1)} MB。资产库、模型、训练数据和已导出文件独立保留。\n\n任务：${preview.deletable.slice(0,6).join('、')}${preview.deletable.length>6?'等':''}\n保留 ${preview.skipped.length} 项${protection?'：\n'+protection:'。'}\n\n每次最多 500 项。删除后不能继续这些任务，请先导出需要保留的结果。`;
    if(!await uiConfirm(message,'清理任务记录','确认清理')){status.textContent='已取消清理，任务和文件未删除。';return;}
    // Delete precisely the reviewed IDs, then check their current references again.
    const report=await api('/api/jobs/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:preview.deletable,confirmed:true})});
    report.deleted.forEach(id=>{selectedHistoryJobs.delete(id);observedJobs.delete(id);$$('[data-job-id]').filter(element=>element.dataset.jobId===id).forEach(element=>{element.querySelectorAll('audio').forEach(audio=>audio.pause());element.replaceChildren();delete element.dataset.jobId;});});
    status.textContent=`已清理 ${report.deleted.length} 项任务，释放 ${(report.released_bytes/1048576).toFixed(1)} MB；保留 ${preview.skipped.length+report.skipped.length} 项。`+(report.errors.length?' '+report.errors.map(item=>item.reason).join('；'):'');
    await Promise.all([loadHistory(),loadRetention(),refreshWorkspace()]);
  } catch(error){status.textContent=`清理失败：${error.message}`;}
  finally{jobCleanupBusy=false;updateHistorySelection();}
}
$('#history-select-page').onchange=event=>{historyPageJobs.filter(job=>['complete','failed','cancelled'].includes(job.status)).forEach(job=>{if(event.target.checked&&selectedHistoryJobs.size<500)selectedHistoryJobs.add(job.id);else if(!event.target.checked)selectedHistoryJobs.delete(job.id);});$$('[data-select-job]').forEach(input=>input.checked=selectedHistoryJobs.has(input.dataset.selectJob));updateHistorySelection();};
$('#history-clear-selection').onclick=()=>{selectedHistoryJobs.clear();$$('[data-select-job]').forEach(input=>input.checked=false);updateHistorySelection();};
$('#cleanup-selected-jobs').onclick=()=>cleanupJobs({ids:[...selectedHistoryJobs]});
$('#cleanup-failed-jobs').onclick=()=>cleanupJobs({mode:'failed_cancelled',filters:{kind:$('#history-kind').value,project_id:$('#history-project').value,query:$('#history-query').value.trim()}});

async function loadRetention() {
  try {
    const data = await api('/api/retention'); const total = ['jobs', 'uploads', 'logs'].reduce((sum, key) => sum + (data.usage[key]?.gib || 0), 0);
    $('#storage-usage').textContent = `受管存储 ${total.toFixed(2)} GiB · 导出永久保留`;
  } catch (error) { $('#storage-usage').textContent = `存储状态失败：${error.message}`; }
}

async function cleanupStorage() {
  if (!await uiConfirm('将按当前保留策略删除过期任务、临时上传和日志；正在使用的文件与 exports 导出作品会保留。\n\n确定开始清理？', '清理本地存储', '开始清理')) return;
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
$('#history-status').onchange=()=>{historyOffset=0;loadHistory();};
$('#history-kind').onchange=()=>{historyOffset=0;loadHistory();};
$('#history-project').onchange=()=>{historyOffset=0;loadHistory();};
let historyQueryTimer;$('#history-query').oninput=()=>{clearTimeout(historyQueryTimer);historyOffset=0;historyQueryTimer=setTimeout(loadHistory,250);};
$('#history-prev').onclick=()=>{historyOffset=Math.max(0,historyOffset-historyPageSize);loadHistory();};
$('#history-next').onclick=()=>{if(historyOffset+historyPageSize<historyTotal){historyOffset+=historyPageSize;loadHistory();}};
$('#cleanup-storage').onclick = cleanupStorage;
$('#doctor-button').onclick = async () => {
  const button = $('#doctor-button'); const action = $('.doctor-action');
  try { const job = await submit('doctor', {verify_hashes: true}, null, button); action.dataset.result = `自检通过 · ${job.result.gpu} · ${job.result.accelerator}`; }
  catch (error) { action.dataset.result = `自检未通过 · ${error.message}`; }
};
$('#update-button').onclick = () => availableUpdate ? installUpdate() : checkUpdate();

refreshWorkspace(); loadModelSettings(); loadHistory(); loadRetention();
setTimeout(() => checkUpdate({quiet: true}), 500);
setInterval(refreshWorkspace, 1200);
