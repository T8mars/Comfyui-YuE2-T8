(() => {
  let project = null, lastJob = savedValue('rvc-job'), lastStatus = '', polling = false, voices = [];
  let storageDirty = false, storagePlan = null;
  const storageDirectories = () => Object.fromEntries(['projects','datasets','voices'].map(kind => [kind,$(`#rvc-directory-${kind}`).value.trim()]));
  const e = escapeHtml;
  const post = (path, value) => api(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  const notice = (text, bad = false) => { $('#rvc-result').innerHTML = `<div class="result-card${bad ? ' status-failed' : ''}">${e(text)}</div>`; };
  async function update(value) {
    if (!project) throw new Error('请先新建或选择训练项目');
    project = await post(`/api/rvc/projects/${project.id}`, value); renderProject();
  }
  function renderProject() {
    $('#rvc-workbench').classList.toggle('hidden', !project);
    if (!project) return;
    for (const [id, field] of Object.entries({epochs:'epochs',batch:'batch_size',version:'version',rate:'sample_rate',f0:'f0_method',save:'save_every',gpu:'gpu',workers:'num_workers'})) $(`#rvc-${id}`).value = project.options[field];
    $('#rvc-import-speaker').innerHTML = project.speakers.map(s => `<option value="${s.id}">${e(s.name)}（${s.id}）</option>`).join('');
    $('#rvc-speakers').innerHTML = project.speakers.map(s => `<span>${s.id}：${e(s.name)} <button class="ghost compact" data-remove-speaker="${s.id}" ${project.speakers.length < 2 ? 'disabled' : ''}>移除说话人</button></span>`).join(' ');
    const chosen = project.materials.filter(m => m.enabled), seconds = chosen.reduce((sum,m) => sum + m.duration, 0);
    $('#rvc-duration').textContent = `选中 ${chosen.length} 段，合计 ${(seconds / 60).toFixed(1)} 分钟。${seconds < 300 ? '不足 5 分钟，音色可能不稳定；建议补充干净素材。' : '请逐段试听并排除不合适的素材。'}`;
    $('#rvc-materials').innerHTML = project.materials.map(m => `<article class="rvc-material" data-material="${m.id}">
      <div class="rvc-material-title"><b>${e(m.name)}</b><button class="ghost compact" data-remove="${m.id}">移除</button></div>
      <div class="meta">${m.duration.toFixed(1)} 秒 · ${m.sample_rate} Hz · ${m.channels} 声道${m.warnings.length ? ' · ' + e(m.warnings.join('；')) : ''}</div>
      <audio controls preload="metadata" src="/api/rvc/projects/${project.id}/audio/${m.id}/original"></audio>
      ${m.separated_path ? `<label>分离后人声<audio controls preload="metadata" src="/api/rvc/projects/${project.id}/audio/${m.id}/vocal"></audio></label><p class="meta">伴奏能量估计 ${(100 * m.accompaniment_energy_ratio).toFixed(1)}%，请以试听结果为准。</p>` : ''}
      <div class="toolbar"><label class="rvc-check"><input type="checkbox" data-field="enabled" ${m.enabled ? 'checked' : ''}>用于训练 / 分离</label><label class="rvc-check"><input type="checkbox" data-field="reviewed" ${m.reviewed ? 'checked' : ''}>已试听，确认素材合适</label>
      <label>说话人<select data-field="speaker_id">${project.speakers.map(s => `<option value="${s.id}" ${s.id === m.speaker_id ? 'selected' : ''}>${e(s.name)}</option>`).join('')}</select></label></div>
      ${m.accompaniment === 'present' && !m.separated_path ? '<p class="meta">此素材含伴奏，请先分离人声。</p>' : ''}</article>`).join('') || '<p class="meta">还没有素材。导入后可逐段试听、确认和分配说话人。</p>';
  }
  async function refresh() {
    const data = await api('/api/rvc'), selected = project?.id || savedValue('rvc-project');
    voices = data.voices;
    if (!storageDirty) for (const kind of ['projects','datasets','voices']) $(`#rvc-directory-${kind}`).value = data.locations[kind];
    $('#rvc-project').innerHTML = data.projects.map(p => `<option value="${p.id}">${e(p.name)}</option>`).join('');
    project = data.projects.find(p => p.id === selected) || data.projects[0] || null;
    if (project) { $('#rvc-project').value = project.id; savedValue('rvc-project',project.id); }
    renderProject();
    renderCoverModels();
    $('#rvc-library').innerHTML = data.voices.map(v => `<article class="result-card"><b>${e(v.name)}</b><p class="meta">${e(v.version)} · ${v.sample_rate / 1000} kHz · ${v.speakers.length} 位说话人</p>${v.preview ? `<audio controls preload="metadata" src="/api/rvc/voices/${v.id}/preview"></audio>` : '<p class="meta">暂无试听样例</p>'}<div class="toolbar"><button class="ghost" data-use-voice="${v.id}">用于翻唱</button>${[['export','导出音色包'],['rename','改名'],['open','打开文件夹'],['remove','移除']].map(([action,label]) => `<button class="ghost compact" data-voice-action="${action}" data-voice-id="${v.id}">${label}</button>`).join('')}</div></article>`).join('') || '<div class="result-card"><b>还没有专属音色</b><p class="meta">在上方导入素材并训练，或导入已有模型。训练成功后，音色会自动出现在这里。</p></div>';
  }
  function renderCoverModels() {
    const selected = $('#rvc-cover-model').value || savedValue('rvc-voice');
    $('#rvc-cover-model').innerHTML = '<option value="">请选择已训练音色</option>' + voices.map(v => `<option value="${v.id}">${e(v.name)}</option>`).join('');
    if (voices.some(v => v.id === selected)) $('#rvc-cover-model').value = selected;
    renderCoverSpeakers();
  }
  function renderCoverSpeakers() {
    const voice = voices.find(v => v.id === $('#rvc-cover-model').value), selected = $('#rvc-cover-speaker').value;
    $('#rvc-cover-speaker').innerHTML = (voice?.speakers || []).map(s => `<option value="${s.id}">${e(s.name)}（${s.id}）</option>`).join('');
    if (voice?.speakers.some(s => String(s.id) === selected)) $('#rvc-cover-speaker').value = selected;
    updateCover();
  }
  function updateCover() {
    const rvc = $('#voice-backend').value === 'rvc', compare = $('#voice-backend').value === 'compare';
    $('#rvc-cover-settings').classList.toggle('hidden', !(rvc || compare));
    $('#voice-compare-hint').classList.toggle('hidden', !compare);
    $('#reference-drop-zone').classList.toggle('hidden', rvc);
    $('#reference-preview').classList.toggle('hidden', rvc || !$('#reference-file').files[0]);
    if (rvc) $('#reference-preview audio').pause();
    for (const id of ['voice-steps','voice-cfg','voice-auto-f0']) $(`#${id}`).closest('label').classList.toggle('hidden', rvc);
    if (!$('#generate-reference-cover').dataset.jobId) restoreButton($('#generate-reference-cover'));
    savedValue('voice-backend', $('#voice-backend').value);
  }
  async function start(kind, request) {
    const modelJob = kind.startsWith('rvc_model_') || kind === 'rvc_storage_move';
    if (!project && !modelJob) throw new Error('请先新建训练项目');
    const job = await post('/api/jobs',{kind,request:{...(modelJob ? {} : {project_id:project.id}),...request},source:'webui',result_panel:'voices',client_request_id:crypto.randomUUID()});
    lastJob = job.id; lastStatus = ''; savedValue('rvc-job',job.id); await poll(); await refreshWorkspace();
  }
  async function poll() {
    if (!lastJob || polling) return;
    polling = true;
    try {
      const job = await api(`/api/jobs/${lastJob}`), active = !TERMINAL.has(job.status);
      $('#rvc-job-tools').classList.remove('hidden');
      $('#rvc-resume').classList.toggle('hidden',!(['failed','cancelled'].includes(job.status) && ['rvc_train','rvc_import','rvc_separate','rvc_storage_move'].includes(job.kind)));
      $('#rvc-cancel').classList.toggle('hidden',!active);
      for (const id of ['rvc-import','rvc-separate','rvc-train','rvc-check-training','rvc-create','rvc-add-speaker','rvc-model-import']) $(`#${id}`).disabled = active;
      $('#rvc-storage-preview').disabled = active;
      $('#rvc-storage-apply').disabled = active || !storagePlan?.changes.length;
      if (active) {
        const loss = job.stage === 'rvc_train' && job.losses ? Object.entries(job.losses).map(([key,value]) => `${key}: ${Number(value).toFixed(3)}`).join(' · ') : '';
        const progress = job.completed != null && job.total ? (job.kind === 'rvc_storage_move' ? `（${(job.completed/1024**3).toFixed(2)} / ${(job.total/1024**3).toFixed(2)} GB）` : `（${job.completed}/${job.total}）`) : '';
        notice(`${kindLabel(job.kind)}：${stageLabel(job.stage)}${job.epoch ? ` · 第 ${job.epoch}${job.epochs ? ` / ${job.epochs}` : ''} 轮` : ''}${progress}${loss ? `。最近训练损失：${loss}` : ''}。${job.message || '可查看日志或取消，已保存的检查点会保留。'}`);
      }
      else if (lastStatus !== job.status) {
        if (job.status === 'complete') {
          if (job.kind === 'rvc_storage_move') { storageDirty = false; storagePlan = null; $('#rvc-storage-apply').disabled = true; }
          const result = job.result || {}, extra = result.errors?.length ? `；${result.errors.length} 个文件未能导入：${result.errors.map(x => `${x.name}：${x.error}`).join('；')}` : '';
          notice(`${kindLabel(job.kind)}已完成${extra}${result.review_required ? '。请试听分离后人声，再确认用于训练。' : ''}`); await refresh();
          if (result.backups?.length) $('#rvc-result').insertAdjacentHTML('beforeend',`<div class="result-card">原目录备份（尚未删除）：${result.backups.map(path => `<p>${e(path)}</p>`).join('')}</div>`);
          if (result.download_url && /^\/api\/rvc\/download\/[a-f0-9-]+\.zip$/.test(result.download_url)) $('#rvc-result').insertAdjacentHTML('beforeend',`<a class="ghost" href="${result.download_url}" download>下载音色 ZIP</a>`);
          if (result.voice?.id && savedValue('rvc-applied-job') !== job.id) {
            savedValue('rvc-voice',result.voice.id);
            savedValue('rvc-applied-job',job.id);
            window.dispatchEvent(new CustomEvent('rvc-voice-selected',{detail:{id:result.voice.id}}));
            if (job.kind === 'rvc_train') $('.tab[data-tab="cover"]').click();
          }
        } else notice(job.error || stageLabel(job.status),true);
      }
      lastStatus = job.status;
    } catch (error) { notice(error.message,true); }
    finally { polling = false; }
  }
  const handle = fn => async event => { try { await fn(event); } catch (error) { notice(error.message,true); } };
  for (const kind of ['projects','datasets','voices']) $(`#rvc-directory-${kind}`).oninput = () => { storageDirty = true; storagePlan = null; $('#rvc-storage-apply').disabled = true; $('#rvc-storage-plan').textContent = '路径已更改，请重新预览迁移。'; };
  $('#rvc-storage-preview').onclick = handle(async () => {
    storagePlan = await post('/api/rvc/storage/preview',{directories:storageDirectories()});
    $('#rvc-storage-plan').innerHTML = storagePlan.changes.map(item => `<p>${e(item.source)} → ${e(item.target)}<br>${item.files} 个文件，约 ${(item.bytes / 1024**3).toFixed(2)} GB</p>`).join('') || '目录未发生变化。';
    $('#rvc-storage-apply').disabled = !storagePlan.changes.length;
  });
  $('#rvc-storage-apply').onclick = handle(async () => {
    if (!storagePlan?.changes.length) throw new Error('请先预览迁移');
    await start('rvc_storage_move',{directories:storageDirectories()});
  });
  $('#rvc-create').onclick = handle(async () => { project = await post('/api/rvc/projects',{name:$('#rvc-new-name').value || '我的音色'}); await refresh(); });
  $('#rvc-project').onchange = handle(async () => { project = await api(`/api/rvc/projects/${$('#rvc-project').value}`); savedValue('rvc-project',project.id); renderProject(); });
  $('#rvc-refresh').onclick = handle(refresh);
  $('#rvc-open').onclick = handle(() => post('/api/rvc/open',{kind:'projects'}));
  $('#rvc-add-speaker').onclick = handle(() => update({speakers:[...project.speakers,{id:Number($('#rvc-speaker-id').value),name:$('#rvc-speaker-name').value}]}));
  $('#rvc-speakers').onclick = handle(event => { const button = event.target.closest('[data-remove-speaker]'); if (button) return update({speakers:project.speakers.filter(s => s.id !== Number(button.dataset.removeSpeaker))}); });
  $('#rvc-import').onclick = handle(async () => {
    if (!project) throw new Error('请先选择训练项目');
    const paths = [], names = {};
    for (const file of $('#rvc-files').files) {
      notice(`正在上传：${file.name}`);
      const upload = await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file}); paths.push(upload.path); names[upload.path] = file.name;
    }
    await start('rvc_import',{paths,names,folder:$('#rvc-folder').value.trim(),speaker_id:Number($('#rvc-import-speaker').value),source_type:$('#rvc-source-type').value});
  });
  $('#rvc-materials').onchange = handle(async event => {
    const field = event.target.dataset.field, id = event.target.closest('[data-material]')?.dataset.material;
    if (id && field) await update({materials:[{id,[field]:field === 'speaker_id' ? Number(event.target.value) : event.target.checked}]});
  });
  $('#rvc-materials').onclick = handle(event => { const id = event.target.closest('[data-remove]')?.dataset.remove; if (id) return update({remove_material_ids:[id]}); });
  $('#rvc-separate').onclick = handle(() => start('rvc_separate',{material_ids:project.materials.filter(m => m.enabled).map(m => m.id)}));
  async function checkTraining() {
    const options = {epochs:Number($('#rvc-epochs').value),batch_size:Number($('#rvc-batch').value),version:$('#rvc-version').value,sample_rate:$('#rvc-rate').value,f0_method:$('#rvc-f0').value,save_every:Number($('#rvc-save').value),gpu:Number($('#rvc-gpu').value),num_workers:Number($('#rvc-workers').value)};
    await update({options});
    const result = await api(`/api/rvc/projects/${project.id}/preflight`), gib = value => (value / 1024**3).toFixed(1);
    $('#rvc-preflight').textContent = `额外空间预算约 ${gib(result.required_free_bytes_estimate)} GB，可用磁盘 ${gib(result.disk_free_bytes)} GB，可用内存 ${gib(result.ram_available_bytes)} GB${result.gpu ? `，GPU ${result.gpu.id} 可用显存 ${(result.gpu.free_mib / 1024).toFixed(1)} GB` : ''}。${[...result.errors,...result.warnings].join('；')}`;
    if (!result.ready) throw new Error(result.errors.join('；'));
  }
  $('#rvc-check-training').onclick = handle(checkTraining);
  $('#rvc-train').onclick = handle(async () => { await checkTraining(); await start('rvc_train',{}); });
  $('#rvc-cancel').onclick = handle(() => cancelJob(lastJob));
  $('#rvc-show-log').onclick = handle(async () => {
    const value = await api(`/api/jobs/${lastJob}/log`);
    $('#rvc-job-log').textContent = value.text || '日志暂为空';
    $('#rvc-job-log').classList.remove('hidden');
  });
  $('#rvc-resume').onclick = handle(async () => {
    const job = await post(`/api/jobs/${lastJob}/resume`,{});
    lastJob = job.id; lastStatus = ''; savedValue('rvc-job',job.id);
    $('#rvc-job-log').classList.add('hidden');
    await poll(); await refreshWorkspace();
  });
  $('#rvc-model-import').onclick = handle(async () => {
    const files = [...$('#rvc-model-files').files], zip = files.filter(f => /\.zip$/i.test(f.name)), models = files.filter(f => /\.pth$/i.test(f.name)), indexes = files.filter(f => /\.index$/i.test(f.name));
    if (!(zip.length === 1 && files.length === 1) && !(zip.length === 0 && models.length === 1 && indexes.length > 0)) throw new Error('请选择一个 ZIP，或一个 PTH 与对应的 INDEX');
    if (new Set(files.map(f => f.name)).size !== files.length) throw new Error('文件名重复，请为不同说话人的 index 使用不同文件名');
    let mapping = {};
    if (!zip.length) {
      const value = $('#rvc-model-indices').value.trim();
      if (value) mapping = JSON.parse(value);
      else if (indexes.length === 1) mapping = {'0':indexes[0].name};
      else throw new Error('多个 INDEX 需要填写说话人 ID 与文件名的映射');
      if (!mapping || Array.isArray(mapping) || typeof mapping !== 'object' || !Object.keys(mapping).length || Object.entries(mapping).some(([sid,name]) => !/^\d+$/.test(sid) || !indexes.some(f => f.name === name))) throw new Error('index 映射中的 ID 或文件名无效');
    }
    const uploaded = {};
    for (const file of files) {
      notice(`正在上传模型文件：${file.name}`);
      uploaded[file.name] = (await api(`/api/rvc-upload?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file})).path;
    }
    await start('rvc_model_import',{name:$('#rvc-model-name').value.trim() || undefined,...(zip.length ? {archive:uploaded[zip[0].name]} : {model:uploaded[models[0].name],indices:Object.fromEntries(Object.entries(mapping).map(([sid,name]) => [sid,uploaded[name]]))})});
  });
  $('#rvc-library').onclick = handle(async event => {
    const id = event.target.closest('[data-use-voice]')?.dataset.useVoice;
    if (id) { savedValue('rvc-voice',id); window.dispatchEvent(new CustomEvent('rvc-voice-selected',{detail:{id}})); $('.tab[data-tab="cover"]').click(); }
    const button = event.target.closest('[data-voice-action]');
    if (!button) return;
    const voiceId = button.dataset.voiceId, action = button.dataset.voiceAction, voice = voices.find(v => v.id === voiceId);
    if (action === 'export') return start('rvc_model_export',{voice_id:voiceId});
    let data = {};
    if (action === 'rename') { const name = prompt('音色名称',voice.name); if (name === null) return; data = {name}; }
    if (action === 'remove' && !confirm(`移除「${voice.name}」？模型会保留在音色库的 .trash 文件夹，训练素材不受影响。`)) return;
    const result = await post(`/api/rvc/voices/${voiceId}/${action}`,data);
    if (result.recovery_directory) notice(`音色已移除，可从此目录恢复：${result.recovery_directory}`);
    await refresh();
  });
  $('#voice-backend').value = ['rvc','compare'].includes(savedValue('voice-backend')) ? savedValue('voice-backend') : 'seed-vc';
  $('#voice-backend').onchange = updateCover;
  $('#rvc-cover-model').onchange = () => { savedValue('rvc-voice',$('#rvc-cover-model').value); renderCoverSpeakers(); };
  $('#rvc-cover-train').onclick = () => $('.tab[data-tab="voices"]').click();
  window.addEventListener('rvc-voice-selected', event => {
    $('#voice-backend').value = 'rvc'; $('#rvc-cover-model').value = event.detail.id;
    if (!$('#cover-abc').value.trim()) window.setCoverMode?.('direct');
    renderCoverSpeakers();
  });
  $('.tab[data-tab="voices"]').addEventListener('click',handle(refresh));
  refresh().catch(error => notice(error.message,true));
  setInterval(() => { if ($('#voices').classList.contains('active') || (lastStatus && !TERMINAL.has(lastStatus))) poll(); },1500);
})();
