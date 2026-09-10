const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let activeJob = null;
let planState = null;
let transcriptionState = null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { data = {error: await response.text()}; }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function formObject(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const key of ['seed','candidates']) if (data[key] !== undefined) data[key] = Number(data[key]);
  for (const key of ['cfg_scale','memory_budget_gib']) if (data[key] !== undefined) data[key] = Number(data[key]);
  data.offload_ar = form.querySelector('[name=offload_ar]')?.checked || false;
  return data;
}

function stageLabel(stage) {
  return ({queued:'排队中',starting:'启动运行时',candidate:'准备候选',planning:'规划 ABC 乐谱',semantic:'生成语义音乐',synthesis:'声学流合成',decoding:'VAE 解码',loading_transcriber:'加载转谱模型',transcribing:'音频转谱',encoding:'编码音频',notation:'整理乐谱',cancelling:'正在取消',complete:'完成',failed:'失败',cancelled:'已取消',doctor:'运行自检'})[stage] || stage;
}

async function health() {
  try {
    const data = await api('/api/health');
    const ready = data.ready.core_python && data.ready.transcribe_python && data.ready.upstream_source && Object.values(data.ready.models).every(Boolean);
    $('#health-dot').className = `dot ${ready ? 'ok' : 'bad'}`;
    $('#health-title').textContent = ready ? '核心环境已就绪' : '运行环境不完整';
    $('#health-detail').textContent = data.current_job ? `正在运行 ${data.current_job}，另有 ${data.queued} 个排队` : `${data.ready.core_python ? '核心运行时已安装' : '请先安装运行时'} · GPU 当前空闲`;
  } catch (error) {
    $('#health-dot').className = 'dot bad'; $('#health-title').textContent = '服务连接失败'; $('#health-detail').textContent = error.message;
  }
}

async function submit(kind, request, resultTarget) {
  const job = await api('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({kind, request})});
  activeJob = job.id; showDrawer(job); return poll(job.id, resultTarget);
}

function showDrawer(job) {
  $('#job-drawer').classList.remove('hidden'); $('#job-id').textContent = job.id; $('#job-stage').textContent = stageLabel(job.stage);
}

async function poll(id, resultTarget) {
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 900));
    const job = await api(`/api/jobs/${id}`); showDrawer(job);
    if (job.status === 'complete') {
      $('#job-drawer').classList.add('hidden'); activeJob = null; health(); loadHistory();
      if (resultTarget) renderJob(job, resultTarget); return job;
    }
    if (job.status === 'failed' || job.status === 'cancelled') {
      $('#job-drawer').classList.add('hidden'); activeJob = null; health(); loadHistory();
      throw new Error(job.error || stageLabel(job.status));
    }
  }
}

function audioUrl(jobId, relative) { return `/api/files/${jobId}/${relative.split('/').map(encodeURIComponent).join('/')}`; }
function escapeHtml(value='') { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function relativeAudio(job, path) {
  const normalized = String(path || '').replaceAll('\\', '/');
  const marker = `/jobs/${job.id}/`;
  const jobIndex = normalized.toLowerCase().indexOf(marker.toLowerCase());
  if (jobIndex >= 0) return normalized.slice(jobIndex + marker.length);
  const artifactIndex = normalized.toLowerCase().lastIndexOf('/artifacts/');
  return artifactIndex >= 0 ? normalized.slice(artifactIndex + 1) : null;
}

function renderJob(job, target) {
  const result = job.result || {};
  const candidates = result.candidates || (result.audio ? [{seed:'—', audio:result.audio, truncated:result.truncated}] : []);
  if (!candidates.length) { target.innerHTML = `<div class="result-card"><b>任务完成</b><pre class="meta">${escapeHtml(JSON.stringify(result,null,2))}</pre></div>`; return; }
  target.innerHTML = candidates.map((candidate,index) => {
    const rel = relativeAudio(job, candidate.audio);
    const truncated = candidate.truncated && Object.values(candidate.truncated).some(Boolean);
    const player = rel ? `<audio controls preload="metadata" src="${audioUrl(job.id,rel)}"></audio>` : '';
    return `<article class="result-card"><header><div><b>版本 ${index+1}</b><div class="meta">Seed ${candidate.seed} · ${candidate.audio_seconds ? candidate.audio_seconds.toFixed(1)+' 秒' : ''}</div></div><span class="badge">${truncated?'已截断':'完整'}</span></header>${player}<div class="toolbar"><button class="ghost" onclick="exportJob('${job.id}')">导出工件</button></div></article>`;
  }).join('');
}

async function exportJob(id) {
  try { const data = await api('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:id})}); alert(`已导出到\n${data.destination}`); }
  catch (error) { alert(error.message); }
}
window.exportJob = exportJob;

$$('.tab').forEach(button => button.onclick = () => {
  $$('.tab').forEach(x=>x.classList.toggle('active',x===button));
  $$('.panel').forEach(x=>x.classList.toggle('active',x.id===button.dataset.tab));
  if (button.dataset.tab === 'history') loadHistory();
});

$('#create-form').onsubmit = async event => {
  event.preventDefault(); $('#create-result').innerHTML='';
  try { await submit('generate', formObject(event.target), $('#create-result')); } catch(error){ $('#create-result').innerHTML=`<div class="result-card status-failed">${escapeHtml(error.message)}</div>`; }
};

$('#plan-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const request=formObject(event.target); request.backend='torch-eager'; request.memory_budget_gib=23.5;
    const job=await submit('plan',request,null); planState={...job.result, request};
    $('#plan-abc').value=job.result.abc||''; $('#plan-badge').textContent=job.result.truncated?'计划已截断':'原始计划';
    $('#plan-workbench').classList.remove('hidden');
  } catch(error){ $('#plan-result').innerHTML=`<div class="result-card status-failed">${escapeHtml(error.message)}</div>`; }
};

$('#plan-exact').onchange = event => { $('#plan-abc').disabled=event.target.checked; };
$('#plan-abc').disabled=true;
$('#render-plan').onclick = async () => {
  if (!planState) return;
  const exact=$('#plan-exact').checked; const request={plan_dir:planState.plan_dir,exact,backend:'torch-eager',memory_budget_gib:23.5};
  if (!exact) Object.assign(request,planState.request,{abc:$('#plan-abc').value,candidates:1});
  try { const job=await submit('render_plan',request,null); renderJob(job,$('#plan-result')); } catch(error){ $('#plan-result').innerHTML=`<div class="result-card status-failed">${escapeHtml(error.message)}</div>`; }
};
$('#download-abc').onclick = () => { const blob=new Blob([$('#plan-abc').value],{type:'text/plain;charset=utf-8'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='score.abc'; a.click(); URL.revokeObjectURL(a.href); };

$('#cover-file').onchange = event => { const file=event.target.files[0]; $('#transcribe-button').disabled=!file; if(file) $('#drop-zone b').textContent=file.name; };
$('#transcribe-button').onclick = async () => {
  const file=$('#cover-file').files[0]; if(!file)return;
  try {
    $('#transcribe-button').disabled=true; $('#transcribe-button').textContent='正在上传…';
    const upload=await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
    const job=await submit('transcribe',{source_path:upload.path,melody_only:true,dtype:'bf16',preset:'default'},null);
    transcriptionState=job.result; $('#cover-abc').value=job.result.abc||''; $('#cover-review').classList.remove('hidden');
  } catch(error){ $('#cover-result').innerHTML=`<div class="result-card status-failed">${escapeHtml(error.message)}</div>`; }
  finally { $('#transcribe-button').disabled=false; $('#transcribe-button').textContent='上传并转谱'; }
};
$('#generate-cover').onclick = async () => {
  const request={style:$('#cover-style').value,lyrics:$('#cover-lyrics').value,abc:$('#cover-abc').value,cot:'melody',seed:Number($('#cover-seed').value),cfg_scale:1,backend:'torch-eager',memory_budget_gib:23.5,candidates:1};
  if(!request.lyrics.trim()) return alert('请先填写并核对歌词');
  try { await submit('generate',request,$('#cover-result')); } catch(error){ $('#cover-result').innerHTML=`<div class="result-card status-failed">${escapeHtml(error.message)}</div>`; }
};

async function loadHistory() {
  try {
    const {jobs}=await api('/api/jobs?limit=100');
    $('#history-list').innerHTML=jobs.map(job=>{
      const result=job.result||{}; const audio=relativeAudio(job,result.audio || result.candidates?.[0]?.audio);
      const exportButton=job.status==='complete' && job.result ? `<button class="ghost" onclick="exportJob('${job.id}')">导出</button>` : '';
      return `<article class="history-card"><header><div><b>${escapeHtml(job.kind)}</b><div class="meta">${escapeHtml(job.id)} · ${new Date(job.created_at*1000).toLocaleString()}</div></div></header><b class="status-${job.status}">${stageLabel(job.status)}</b>${job.error?`<div class="meta">${escapeHtml(job.error)}</div>`:''}${audio?`<audio controls preload="none" src="${audioUrl(job.id,audio)}"></audio>`:''}<div class="toolbar">${exportButton}</div></article>`;
    }).join('')||'<p class="meta">还没有任务。</p>';
  } catch(error){ $('#history-list').innerHTML=`<p class="status-failed">${escapeHtml(error.message)}</p>`; }
}

async function cancelActive(force=false){ if(!activeJob)return; await api(`/api/jobs/${activeJob}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force})}); }
$('#drawer-cancel').onclick=()=>cancelActive(false); $('#cancel-active').onclick=()=>cancelActive(false); $('#refresh-history').onclick=loadHistory;
$('#doctor-button').onclick=async()=>{ try{const job=await submit('doctor',{verify_hashes:true},null); alert(`自检通过\nGPU: ${job.result.gpu}\nTorch: ${job.result.versions.torch}\nCUDA: ${job.result.torch_cuda}`);}catch(error){alert(error.message);} };

health(); loadHistory(); setInterval(health,5000);
