(() => {
  const kindNames = {song:'歌曲',work:'作品',vocal:'人声',instrumental:'伴奏',reference_voice:'参考音色',lyrics:'歌词',style:'曲风',score:'乐谱',model:'模型',other:'其他'};
  const kindIcons = {song:'bi-disc',work:'bi-music-note-beamed',vocal:'bi-mic',instrumental:'bi-soundwave',reference_voice:'bi-person-bounding-box',lyrics:'bi-file-text',style:'bi-tags',score:'bi-music-note-list',model:'bi-gpu-card',other:'bi-file-earmark'};
  let projects = [], assets = [], trainingAssets = [], lyricsAssets = [], styleAssets = [], trainingAssetGuidance = false;
  let currentProjectAssetRefs = new Set();
  let assetOffset = 0, assetTotal = 0, assetLoadRevision = 0; const assetPageSize = 24;
  let currentProjectId = savedValue('workbench-project') || '';
  let currentRun = null, trainingRuns = [], trainingJobId = savedValue('training-job') || '';
  let trainingModelPage = 0, trainingModelRenderRevision = 0; const trainingModelPageSize = 3;
  let trainingPreviewJobId = savedValue('training-preview-job') || '', trainingPreviewRunId = savedValue('training-preview-run') || '', auxiliaryTrainingJobId = '';
  const pollingTrainingJobs = new Set();
  const trainingJobsByRun = new Map();
  const audioKinds = new Set(['song','work','vocal','instrumental','reference_voice']);
  const exportAudioKinds = new Set(['song','work','vocal','instrumental']);
  const player = $('#global-player'), globalAudio = $('#global-audio');
  let dialogSequence = 0;

  function labelDialog(dialog, prefix) {
    const heading = dialog.querySelector('h3');
    if (!heading) return;
    heading.id = `${prefix}-${++dialogSequence}`;
    dialog.setAttribute('aria-labelledby', heading.id);
  }

  window.workbenchProjectId = () => currentProjectId;
  function contentUrl(asset, revisionId = '') {
    const query = revisionId ? `?revision_id=${encodeURIComponent(revisionId)}` : '';
    return `/api/workbench/assets/${encodeURIComponent(asset.id)}/content${query}`;
  }
  function showError(target, error) {
    target.innerHTML = `<div class="result-card failure-card"><b>操作没有完成</b><p>${escapeHtml(error.message || String(error))}</p></div>`;
  }
  function playAsset(asset, revisionId = '') {
    globalAudio.src = contentUrl(asset, revisionId);
    $('#global-player-title').textContent = asset.title;
    $('#global-player-kind').textContent = `正在试听 · ${kindNames[asset.kind] || asset.kind}`;
    player.classList.remove('hidden');
    globalAudio.play().catch(() => {});
  }
  $('#global-player-close').onclick = () => { globalAudio.pause(); globalAudio.removeAttribute('src'); player.classList.add('hidden'); };

  async function loadProjects() {
    await (window.assistantReady || Promise.resolve());
    projects = (await api('/api/workbench/projects')).projects;
    let nextProjectId = currentProjectId;
    if (nextProjectId && !projects.some(project => project.id === nextProjectId)) nextProjectId = '';
    if (!nextProjectId && projects.length) nextProjectId = projects[0].id;
    await window.assistantSwitchProject?.(nextProjectId);
    currentProjectId = nextProjectId;
    const select = $('#workbench-project-select');
    select.innerHTML = '<option value="">选择或新建项目</option>' + projects.map(project =>
      `<option value="${project.id}">${escapeHtml(project.title)} · ${project.asset_count} 项</option>`).join('');
    select.value = currentProjectId;
    const historyProject=$('#history-project');
    if(historyProject){const previous=historyProject.value;historyProject.innerHTML='<option value="">全部项目</option><option value="__global__">未归档 / 全局</option>'+projects.map(project=>`<option value="${project.id}">${escapeHtml(project.title)}</option>`).join('');if([...historyProject.options].some(option=>option.value===previous))historyProject.value=previous;}
    const selected = projects.find(project => project.id === currentProjectId);
    $('#sidebar-project-name').textContent = selected?.title || '未选择项目';
    $('#header-project-name').textContent = selected?.title || '未选择项目';
    savedValue('workbench-project', currentProjectId);
    await renderProject();
  }
  window.refreshWorkbenchProject = loadProjects;
  async function renderProject() {
    const timeline = $('#project-timeline'), inspector = $('#project-assets');
    const projectId = currentProjectId;
    if (!projectId) {
      currentProjectAssetRefs = new Set();
      if (assets.length) renderAssets();
      $('#project-title').textContent = '尚未选择项目';
      $('#project-state').textContent = '先选择项目，后续作品会自动归档到这里。';
      timeline.className = 'project-timeline empty-state';
      timeline.innerHTML = '<i class="bi bi-music-note-beamed"></i><b>项目还没有音乐版本</b><p>新建或选择项目后，从资产库加入素材，或生成第一首歌曲。</p>';
      inspector.innerHTML = '<p class="meta">选择项目后显示固定版本的素材。</p>';
      $('#export-project').disabled = true;
      return;
    }
    try {
      const project = await api(`/api/workbench/projects/${projectId}`);
      if (projectId !== currentProjectId) return;
      currentProjectAssetRefs = new Set(project.assets.map(item => `${item.id}:${item.revision_id}`));
      if (assets.length) renderAssets();
      $('#project-title').textContent = project.title;
      $('#project-state').textContent = `${project.assets.length} 项内容 · 生成结果会固定版本并自动加入`;
      const audio = project.assets.filter(item => audioKinds.has(item.kind));
      const configuredMaster = String(project.metadata?.master_asset_id || '');
      const masterRevision = String(project.metadata?.master_revision_id || '');
      const masterItem = audio.find(item => exportAudioKinds.has(item.kind) && item.id===configuredMaster && (!masterRevision || item.revision_id===masterRevision));
      const master = masterItem ? `${masterItem.id}:${masterItem.revision_id}` : '';
      timeline.className = 'project-timeline' + (audio.length ? '' : ' empty-state');
      timeline.innerHTML = audio.length ? audio.map(item => {const selected=master===`${item.id}:${item.revision_id}`,masterButton=exportAudioKinds.has(item.kind)?`<button class="${selected?'primary':'ghost'} compact" data-master-project="${item.id}" data-revision="${item.revision_id}">${selected?'已选主版本':'设为主版本'}</button>`:'';return `<article class="project-track"><span class="track-icon"><i class="bi ${kindIcons[item.kind]}"></i></span><div><b>${escapeHtml(item.title)}</b><small>${escapeHtml(kindNames[item.kind] || item.kind)} · ${(Number(item.metadata?.duration)||0).toFixed(1)} 秒 · 固定版本${selected?' · 主版本':''}</small></div><div class="toolbar"><button class="ghost compact" data-play-project="${item.id}" data-revision="${item.revision_id}"><i class="bi bi-play-fill"></i> 试听</button>${masterButton}</div></article>`;}).join('') : '<i class="bi bi-music-note-beamed"></i><b>这个项目还没有音乐版本</b><p>生成、音色转换和从资产库加入的内容会显示在这里。</p>';
      inspector.innerHTML = project.assets.length ? project.assets.map(item => `<div class="inspector-asset"><b>${escapeHtml(item.title)}</b><small>${escapeHtml(kindNames[item.kind] || item.kind)} · ${escapeHtml(item.role)}</small><button class="ghost compact" type="button" data-remove-project-asset="${item.id}" data-revision="${item.revision_id}" data-role="${escapeHtml(item.role)}">移出</button></div>`).join('') : '<p class="meta">还没有关联内容。</p>';
      inspector.querySelectorAll('[data-remove-project-asset]').forEach(button => button.onclick = async () => {
        await api(`/api/workbench/projects/${projectId}/remove-asset`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:button.dataset.removeProjectAsset,revision_id:button.dataset.revision,role:button.dataset.role})});
        await loadProjects();
      });
      const kinds = new Set(project.assets.map(item => item.kind));
      const stage = kinds.has('work') ? 4 : (kinds.has('vocal') || kinds.has('reference_voice')) ? 3 : kinds.has('song') ? 2 : (kinds.has('lyrics') || kinds.has('style') || kinds.has('score')) ? 1 : 0;
      $$('.workflow-strip span').forEach((item,index) => { item.classList.toggle('active', index===stage); item.classList.toggle('done', index<stage); });
      timeline.querySelectorAll('[data-play-project]').forEach(button => button.onclick = () => {
        const item = project.assets.find(value => value.id === button.dataset.playProject);
        if (item) playAsset(item, button.dataset.revision);
      });
      timeline.querySelectorAll('[data-master-project]').forEach(button => button.onclick = async () => {
        try {
          await api(`/api/workbench/projects/${projectId}/update`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({metadata:{...(project.metadata||{}),master_asset_id:button.dataset.masterProject,master_revision_id:button.dataset.revision}})});
          await renderProject();
        } catch(error) { alert(error.message); }
      });
      $('#export-project').disabled = !master;
    } catch (error) { if(projectId===currentProjectId)showError(timeline, error); }
  }
  $('#workbench-project-select').onchange = async event => {
    const next = event.target.value;
    try { await window.assistantSwitchProject?.(next); currentProjectId = next; await loadProjects(); await refreshWorkspace(); }
    catch (error) { event.target.value = currentProjectId; alert(`切换项目前无法保存独立草稿：${error.message}`); }
  };
  $('#refresh-project').onclick = loadProjects;
  $('#rename-project').onclick = async () => {
    if (!currentProjectId) return alert('请先选择项目');
    const projectId=currentProjectId,current = projects.find(project => project.id === projectId), title = prompt('新的项目名称', current?.title || '');
    if (!title?.trim()) return;
    try { await api(`/api/workbench/projects/${projectId}/update`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title.trim()})}); await loadProjects(); } catch(error) { alert(error.message); }
  };
  $('#archive-project').onclick = async () => {
    if (!currentProjectId) return alert('请先选择项目');
    const projectId=currentProjectId;
    if (!confirm('归档这个项目？资产仍会保留在资产库。')) return;
    try { await api(`/api/workbench/projects/${projectId}/update`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'archived'})}); await window.assistantSwitchProject?.(''); currentProjectId=''; await loadProjects(); await refreshWorkspace(); } catch(error) { alert(error.message); }
  };
  $('#export-project').onclick = async () => {
    if (!currentProjectId) return alert('请先选择项目');
    const projectId=currentProjectId;
    try { const result=await api(`/api/workbench/projects/${projectId}/export`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); alert(`主版本和项目清单已导出到\n${result.destination}`); }
    catch(error) { alert(error.message); }
  };
  $('#new-project').onclick = async () => {
    const title = prompt('新项目名称', '我的歌曲项目');
    if (!title?.trim()) return;
    try { const project = await api('/api/workbench/projects', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title.trim()})}); await window.assistantSwitchProject?.(project.id); currentProjectId = project.id; await loadProjects(); await refreshWorkspace(); }
    catch (error) { alert(error.message); }
  };
  $('#sidebar-project-button').onclick = () => { $('.tab[data-tab="project"]').click(); $('#workbench-project-select').focus(); };
  $('#header-project-button').onclick = () => { $('.tab[data-tab="project"]').click(); $('#workbench-project-select').focus(); };
  $('#sidebar-settings').onclick = () => { $('#model-settings').open = true; $('#model-settings').scrollIntoView({behavior:'smooth',block:'center'}); };

  function miniWave(peaks) {
    const sampled = peaks.filter((_, index) => index % Math.max(1, Math.floor(peaks.length / 44)) === 0).slice(0,44);
    return sampled.map(pair => `<span style="height:${Math.max(2,Math.round(Math.max(Math.abs(pair[0]),Math.abs(pair[1]))*42))}px"></span>`).join('');
  }
  async function hydrateWaves() {
    for (const card of $$('#asset-grid [data-wave]')) {
      try { const wave = await api(`/api/workbench/assets/${card.dataset.wave}/waveform?bins=176`); card.innerHTML = miniWave(wave.peaks); }
      catch { card.innerHTML = '<span style="height:2px"></span>'; }
    }
  }
  async function assetFile(asset) {
    const response = await fetch(contentUrl(asset)); if(!response.ok) throw new Error(`读取素材失败：HTTP ${response.status}`);
    const blob = await response.blob(), suffix = asset.blob_suffix || (/audio\/flac/.test(blob.type)?'.flac':'.wav');
    return new File([blob], asset.title.toLowerCase().endsWith(suffix) ? asset.title : asset.title + suffix, {type:blob.type});
  }
  async function putAssetInInput(asset, selector, tab, configure=()=>{}, projectId=currentProjectId) {
    const file=await assetFile(asset);
    if(projectId!==currentProjectId)throw new Error('项目已切换，请在当前项目中重新发送这个素材');
    const input=$(selector), transfer=new DataTransfer(); transfer.items.add(file); input.files=transfer.files;
    configure(); input.dispatchEvent(new Event('change',{bubbles:true})); $(`.tab[data-tab="${tab}"]`).click();
  }
  async function useAsset(asset, action) {
    const projectId=currentProjectId;
    if(action==='cover-source') return putAssetInInput(asset,'#cover-file','cover',()=>window.setCoverMode?.('direct'),projectId);
    if(action==='reference') return putAssetInInput(asset,'#reference-file','cover',()=>{},projectId);
    if(action==='rvc') return putAssetInInput(asset,'#rvc-files','voices',()=>{},projectId);
    if(action==='model') return useStyleModel(asset.id);
    const text=await(await fetch(contentUrl(asset))).text();
    if(projectId!==currentProjectId)throw new Error('项目已切换，请在当前项目中重新发送这个素材');
    if(action==='plan_score') { window.importPlanAbc?.(text, '资产库乐谱 · 重新生成'); $('.tab[data-tab="plan"]').click(); return; }
    const targets={create_lyrics:['#create-form [name=lyrics]','create'],create_style:['#create-form [name=style]','create'],plan_lyrics:['#plan-form [name=lyrics]','plan'],plan_style:['#plan-form [name=style]','plan']};
    const target=targets[action]; if(!target)return; $(target[0]).value=text; $(target[0]).dispatchEvent(new Event('input',{bubbles:true})); $(`.tab[data-tab="${target[1]}"]`).click();
  }
  function openUseDialog(asset) {
    const actions=[];
    if(audioKinds.has(asset.kind)){actions.push(['cover-source','作为原曲去翻唱'],['reference','作为参考音色'],['rvc','加入 RVC 训练素材']);}
    if(asset.kind==='lyrics')actions.push(['create_lyrics','填入歌曲创作歌词'],['plan_lyrics','填入乐谱计划歌词']);
    if(asset.kind==='style')actions.push(['create_style','填入歌曲创作曲风'],['plan_style','填入乐谱计划曲风']);
    if(asset.kind==='score')actions.push(['plan_score','填入乐谱计划']);
    if(asset.kind==='model'&&asset.metadata?.model_type==='yue2_ar_lora')actions.push(['model','用于歌曲创作']);
    const dialog=document.createElement('dialog');dialog.innerHTML=`<form method="dialog"><h3>使用“${escapeHtml(asset.title)}”</h3><p class="meta">选择目标后会切换到对应工作区，仍由你确认并开始任务。</p><div class="asset-use-actions">${actions.map(([id,label])=>`<button class="ghost" type="button" data-use-action="${id}">${label}</button>`).join('')||'<p>这个素材暂时没有可用目标。</p>'}</div><div class="toolbar"><button class="ghost">关闭</button></div></form>`;labelDialog(dialog,'asset-use-dialog-title');document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.querySelectorAll('[data-use-action]').forEach(button=>button.onclick=async()=>{try{await useAsset(asset,button.dataset.useAction);dialog.close();}catch(error){alert(error.message);}});dialog.showModal();
  }
  async function editAsset(asset) {
    const text=['lyrics','style','score'].includes(asset.kind) ? await(await fetch(contentUrl(asset))).text() : null;
    const dialog=document.createElement('dialog');dialog.innerHTML=`<form><h3>编辑素材</h3><label>名称<input name="title" value="${escapeHtml(asset.title)}" required maxlength="200"></label><label>标签（逗号分隔）<input name="tags" value="${escapeHtml((asset.tags||[]).join(', '))}"></label>${text!==null?`<label>内容（保存后建立新版本）<textarea name="text" rows="16">${escapeHtml(text)}</textarea></label>`:''}<div class="toolbar"><button class="ghost" type="button" data-cancel>取消</button><button class="primary" type="submit">保存</button></div></form>`;labelDialog(dialog,'asset-edit-dialog-title');document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.currentTarget)),tags=data.tags.split(',').map(value=>value.trim()).filter(Boolean);try{await api(`/api/workbench/assets/${asset.id}/update`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:data.title,tags})});if(text!==null&&data.text!==text)await api('/api/workbench/assets/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:asset.id,parent_revision_id:asset.current_revision_id,kind:asset.kind,title:data.title,text:data.text,tags})});dialog.close();await Promise.all([loadAssets(),loadTrainingInputs(),loadProjects()]);}catch(error){alert(error.message);}};dialog.showModal();
  }
  function renderAssets() {
    const grid = $('#asset-grid');
    if (!assets.length) {
      const filtered=Boolean($('#asset-kind').value||$('#asset-query').value.trim());
      grid.innerHTML = `<div class="empty-state"><i class="bi bi-collection-play"></i><b>还没有符合条件的资产</b><p>${filtered?'清除筛选可查看资产库中的全部内容。':'导入音频，或从创作页面生成第一个作品。'}</p>${filtered?'<button id="clear-asset-filters" class="ghost compact" type="button">清除筛选</button>':''}</div>`;
      const clear=$('#clear-asset-filters');if(clear)clear.onclick=()=>{$('#asset-kind').value='';$('#asset-query').value='';assetOffset=0;loadAssets();};
      return;
    }
    grid.innerHTML = assets.map(asset => {
      const primary=audioKinds.has(asset.kind)?`<button class="ghost compact" data-play-asset="${asset.id}"><i class="bi bi-play-fill"></i> 试听</button>`:asset.kind==='model'&&asset.metadata?.model_type==='yue2_ar_lora'?`<button class="primary compact" data-use-style-model="${asset.id}"><i class="bi bi-music-note-beamed"></i> 用于创作</button>`:`<button class="ghost compact" data-read-asset="${asset.id}"><i class="bi bi-eye"></i> 查看</button>`;
      const linked=currentProjectId&&currentProjectAssetRefs.has(`${asset.id}:${asset.current_revision_id}`);
      const trainingAudio=trainingAssetGuidance&&['song','work','vocal'].includes(asset.kind);
      const projectAction=currentProjectId?(linked?`<button class="ghost compact" type="button" data-project-asset-state disabled title="当前版本已在项目中"><i class="bi bi-check-circle"></i> ${trainingAudio?'已加入项目 · 训练可用':'已在项目'}</button>`:`<button class="ghost compact" type="button" data-add-asset="${asset.id}"><i class="bi bi-plus-circle"></i> ${trainingAudio?'加入当前项目供训练':'加入项目'}</button>`):'';
      return `<article class="asset-card"><div class="asset-card-head"><i class="bi ${kindIcons[asset.kind] || kindIcons.other}"></i><div><b title="${escapeHtml(asset.title)}">${escapeHtml(asset.title)}</b><small>${escapeHtml(kindNames[asset.kind] || asset.kind)} · ${asset.size ? (asset.size/1048576).toFixed(1)+' MB' : '文本版本'}</small></div></div>${audioKinds.has(asset.kind) ? `<div class="wave-mini" data-wave="${asset.id}"></div>` : '<div class="wave-mini"><span style="height:2px;width:100%"></span></div>'}<div class="toolbar">${primary}<button class="ghost compact" data-use-asset="${asset.id}">发送到…</button><button class="ghost compact" data-edit-asset="${asset.id}">编辑</button>${projectAction}</div></article>`;
    }).join('');
    grid.querySelectorAll('[data-play-asset]').forEach(button => button.onclick = () => playAsset(assets.find(item => item.id === button.dataset.playAsset)));
    grid.querySelectorAll('[data-read-asset]').forEach(button => button.onclick = async () => {const asset=assets.find(item=>item.id===button.dataset.readAsset);try{const text=await(await fetch(contentUrl(asset))).text();const dialog=document.createElement('dialog');dialog.innerHTML=`<form method="dialog"><h3>${escapeHtml(asset.title)}</h3><textarea rows="18" readonly aria-label="素材内容">${escapeHtml(text)}</textarea><div class="toolbar"><button class="ghost">关闭</button></div></form>`;labelDialog(dialog,'asset-read-dialog-title');document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();}catch(error){alert(error.message);}});
    grid.querySelectorAll('[data-use-asset]').forEach(button=>button.onclick=()=>openUseDialog(assets.find(item=>item.id===button.dataset.useAsset)));
    grid.querySelectorAll('[data-edit-asset]').forEach(button=>button.onclick=()=>editAsset(assets.find(item=>item.id===button.dataset.editAsset)).catch(error=>alert(error.message)));
    grid.querySelectorAll('[data-add-asset]').forEach(button => button.onclick = async () => {
      const projectId=currentProjectId;
      try { if(!projectId)throw new Error('请先选择项目');await api(`/api/workbench/projects/${projectId}/assets`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:button.dataset.addAsset,role:'asset'})}); button.textContent=trainingAssetGuidance?'已加入项目 · 训练可用':'已加入'; button.disabled=true; await loadProjects(); }
      catch (error) { alert(error.message); }
    });
    grid.querySelectorAll('[data-use-style-model]').forEach(button => button.onclick = () => useStyleModel(button.dataset.useStyleModel));
    hydrateWaves();
  }
  async function loadAssets() {
    const revision=++assetLoadRevision;
    const query = new URLSearchParams();
    if ($('#asset-kind').value) query.set('kind',$('#asset-kind').value);
    if ($('#asset-query').value.trim()) query.set('q',$('#asset-query').value.trim());
    query.set('limit', assetPageSize); query.set('offset', assetOffset);
    try { const result=await api('/api/workbench/assets?' + query);if(revision!==assetLoadRevision)return;assets=result.assets;assetTotal=result.total;renderAssets();renderAssetPagination();await loadStyleModels(); }
    catch (error) { if(revision===assetLoadRevision)showError($('#asset-grid'), error); }
  }
  $('#asset-kind').onchange = () => { assetOffset=0; loadAssets(); };
  let queryTimer; $('#asset-query').oninput = () => { clearTimeout(queryTimer); assetOffset=0; queryTimer=setTimeout(loadAssets,250); };
  function renderAssetPagination(){const page=Math.floor(assetOffset/assetPageSize)+1,pages=Math.max(1,Math.ceil(assetTotal/assetPageSize));$('#asset-page-status').textContent=`第 ${page} / ${pages} 页 · ${assetTotal} 项`;$('#asset-prev').disabled=assetOffset<=0;$('#asset-next').disabled=assetOffset+assetPageSize>=assetTotal;}
  $('#asset-prev').onclick=()=>{assetOffset=Math.max(0,assetOffset-assetPageSize);loadAssets();};
  $('#asset-next').onclick=()=>{if(assetOffset+assetPageSize<assetTotal){assetOffset+=assetPageSize;loadAssets();}};
  $('#asset-upload').onchange = async event => {
    const files = [...event.target.files], status = $('#asset-import-status'), kind = $('#asset-import-kind').value, targetProjectId=currentProjectId;
    if (!files.length) return;
    let done = 0;
    try {
      for (const file of files) {
        status.textContent = `正在导入 ${done+1}/${files.length}：${file.name}`;
        const uploaded = await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
        const asset = await api('/api/workbench/assets/import', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_path:uploaded.path,kind,title:file.name,provenance:{source:'asset-library-upload'}})});
        if (targetProjectId) await api(`/api/workbench/projects/${targetProjectId}/assets`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:asset.id,role:'source'})});
        done++;
      }
      status.textContent = `已导入 ${done} 个文件，可立即试听。`; event.target.value=''; assetOffset=0; await Promise.all([loadAssets(),loadTrainingInputs(),loadProjects()]);
    } catch (error) { status.textContent = error.message; }
  };
  $('#new-text-asset').onclick = () => $('#text-asset-form').classList.remove('hidden');
  $('#migrate-assets').onclick = async () => { const button=$('#migrate-assets');button.disabled=true;button.textContent='正在整理…';try{const job=await submitTrainingJob('workbench_migrate',{});while(true){await new Promise(resolve=>setTimeout(resolve,700));const state=await api(`/api/jobs/${job.id}`);if(TERMINAL.has(state.status)){if(state.status!=='complete')throw new Error(state.error||'整理失败');break;}}await loadAssets();button.textContent='整理完成';}catch(error){alert(error.message);button.textContent='重新整理';}finally{button.disabled=false;} };
  $('#cancel-text-asset').onclick = () => $('#text-asset-form').classList.add('hidden');
  $('#text-asset-form').onsubmit = async event => {
    event.preventDefault(); const form = event.currentTarget, data = Object.fromEntries(new FormData(form)),targetProjectId=currentProjectId;
    try { const asset = await api('/api/workbench/assets/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); if(targetProjectId) await api(`/api/workbench/projects/${targetProjectId}/assets`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:asset.id,role:data.kind})}); form.reset(); form.classList.add('hidden'); assetOffset=0; await Promise.all([loadAssets(),loadTrainingInputs(),loadProjects()]); }
    catch(error){ alert(error.message); }
  };

  async function loadStyleModels() {
    let models = [];
    try { models = (await api('/api/workbench/assets?kind=model&limit=500')).assets.filter(asset=>asset.metadata?.model_type==='yue2_ar_lora'); } catch {}
    $$('[data-style-model]').forEach(select => {
      const previous=select.value; select.innerHTML='<option value="">使用原版 YuE2</option>'+models.map(model=>`<option value="${model.id}">${escapeHtml(model.title)} · 仅直接生成</option>`).join(''); select.value=previous;
      select.onchange=()=>{ if(select.value){ const mode=select.closest('form')?.querySelector('[name=cot]'); if(mode) mode.value='off'; } };
    });
  }

  function textOptions(values,label){return `<option value="">${label}</option>`+values.map(value=>`<option value="${value.current_revision_id}">${escapeHtml(value.title)}</option>`).join('');}
  function trainingLyricsOptions(){return '<option value="__manual__">直接粘贴本曲歌词</option>'+textOptions(lyricsAssets,'使用右侧公共歌词');}
  function trainingStyleOptions(){return '<option value="">使用右侧公共曲风</option><option value="__manual__">手动填写本曲曲风</option>'+styleAssets.map(value=>`<option value="${value.current_revision_id}">使用曲风资产：${escapeHtml(value.title)}</option>`).join('');}
  function updateTrainingLyricsControl(id){const source=$(`[data-training-lyrics="${id}"]`),manual=$(`[data-training-lyrics-text="${id}"]`),instrumental=$(`[data-training-instrumental="${id}"]`)?.checked;source.disabled=instrumental;manual.classList.toggle('hidden',instrumental||source.value!=='__manual__');}
  function updateTrainingStyleControl(id){const source=$(`[data-training-style="${id}"]`),manual=$(`[data-training-style-text="${id}"]`);manual.classList.toggle('hidden',source.value!=='__manual__');}
  function renderTrainingAssets() {
    const list = $('#training-assets'); if (!list) return;
    const retained=new Map($$('#training-assets [data-training-asset]').map(box=>{const id=box.dataset.trainingAsset;return[id,{checked:box.checked,split:$(`[data-training-split="${id}"]`)?.value||'train',lyrics:$(`[data-training-lyrics="${id}"]`)?.value||'__manual__',lyricsText:$(`[data-training-lyrics-text="${id}"] textarea`)?.value||'',style:$(`[data-training-style="${id}"]`)?.value||'',styleText:$(`[data-training-style-text="${id}"] textarea`)?.value||'',instrumental:$(`[data-training-instrumental="${id}"]`)?.checked||false}];}));
    const project=projects.find(item=>item.id===currentProjectId);
    $('#training-assets-scope').textContent=currentProjectId?`当前项目“${project?.title||'未命名项目'}”：这里只显示已加入该项目的歌曲。至少选择两首不同歌曲，一首用于训练，一首用于验证；同一首歌的衍生片段不会跨集合。`:'训练素材按歌曲项目管理。请先选择或新建项目，再加入至少两首不同歌曲。';
    list.innerHTML = trainingAssets.length ? trainingAssets.map(asset => `<div class="training-asset"><input type="checkbox" data-training-asset="${asset.id}" aria-label="选择 ${escapeHtml(asset.title)}"><span><b>${escapeHtml(asset.title)}</b><small>${kindNames[asset.kind]} · ${Number(asset.metadata.duration).toFixed(1)} 秒</small></span><select data-training-split="${asset.id}" aria-label="${escapeHtml(asset.title)} 的数据集"><option value="train">训练集</option><option value="validation">验证集</option></select><div class="training-asset-options"><label>这首歌的歌词来源<select data-training-lyrics="${asset.id}">${trainingLyricsOptions()}</select></label><label>这首歌的曲风<select data-training-style="${asset.id}">${trainingStyleOptions()}</select></label><label class="check"><input type="checkbox" data-training-instrumental="${asset.id}">纯器乐</label><label class="training-lyrics-text" data-training-lyrics-text="${asset.id}">粘贴“${escapeHtml(asset.title)}”的完整歌词<textarea rows="5" placeholder="按 [Verse] / [Chorus] 分段，填写与这首音频对应的歌词"></textarea></label><label class="training-style-text" data-training-style-text="${asset.id}">填写“${escapeHtml(asset.title)}”的曲风<textarea rows="4" maxlength="20000" placeholder="例如：warm acoustic folk, female vocal, slow tempo, guitar and strings"></textarea></label></div></div>`).join('') : `<div class="empty-state"><i class="bi bi-music-note-list"></i><b>${currentProjectId?'当前项目还没有可训练的歌曲':'尚未选择歌曲项目'}</b><p>${currentProjectId?'资产库中的音频不会自动进入训练；请把至少两首歌曲加入当前项目。':'先到歌曲项目选择或新建项目，再添加训练歌曲。'}</p><div class="toolbar"><button class="primary compact" type="button" data-open-training-assets>${currentProjectId?'去资产库选择歌曲':'去选择歌曲项目'}</button>${currentProjectId?'<button class="ghost compact" type="button" data-import-training-song>直接导入歌曲</button>':''}</div></div>`;
    for(const [id,state] of retained){const box=$(`[data-training-asset="${id}"]`);if(!box)continue;box.checked=state.checked;$(`[data-training-split="${id}"]`).value=state.split;if([...$(`[data-training-lyrics="${id}"]`).options].some(option=>option.value===state.lyrics))$(`[data-training-lyrics="${id}"]`).value=state.lyrics;$(`[data-training-lyrics-text="${id}"] textarea`).value=state.lyricsText;if([...$(`[data-training-style="${id}"]`).options].some(option=>option.value===state.style))$(`[data-training-style="${id}"]`).value=state.style;$(`[data-training-style-text="${id}"] textarea`).value=state.styleText;$(`[data-training-instrumental="${id}"]`).checked=state.instrumental;}
    for(const asset of trainingAssets){$(`[data-training-lyrics="${asset.id}"]`).onchange=()=>updateTrainingLyricsControl(asset.id);$(`[data-training-style="${asset.id}"]`).onchange=()=>updateTrainingStyleControl(asset.id);$(`[data-training-instrumental="${asset.id}"]`).onchange=()=>updateTrainingLyricsControl(asset.id);updateTrainingLyricsControl(asset.id);updateTrainingStyleControl(asset.id);}
    list.querySelector('[data-open-training-assets]')?.addEventListener('click',()=>currentProjectId?openTrainingAssetLibrary(false):($('.tab[data-tab="project"]').click(),$('#workbench-project-select').focus()));
    list.querySelector('[data-import-training-song]')?.addEventListener('click',()=>openTrainingAssetLibrary(true));
  }
  async function loadTrainingInputs(){try{if(!currentProjectId){trainingAssets=[];lyricsAssets=[];styleAssets=[];renderTrainingAssets();return;}const query=kind=>`/api/workbench/assets?kind=${kind}&project_id=${encodeURIComponent(currentProjectId)}&limit=500`,[songs,works,vocals,lyrics,styles]=await Promise.all(['song','work','vocal','lyrics','style'].map(kind=>api(query(kind))));trainingAssets=[...songs.assets,...works.assets,...vocals.assets].filter(asset=>Number(asset.metadata?.duration)>=1);lyricsAssets=lyrics.assets;styleAssets=styles.assets;renderTrainingAssets();}catch(error){showError($('#training-assets'),error);}}
  function openTrainingAssetLibrary(importNow=false){
    if(!currentProjectId){$('.tab[data-tab="project"]').click();$('#workbench-project-select').focus();return;}
    trainingAssetGuidance=true;$('#asset-training-guidance').classList.remove('hidden');
    const project=projects.find(item=>item.id===currentProjectId);$('#asset-training-guidance-copy').textContent=`当前项目：${project?.title||'未命名项目'}。点击歌曲卡片的“加入当前项目供训练”；已加入的歌曲会标为“训练可用”。`;
    $('#asset-kind').value='';$('#asset-query').value='';assetOffset=0;$('.tab[data-tab="assets"]').click();loadAssets();
    if(importNow){$('#asset-import-kind').value='song';$('#asset-upload').click();}
  }
  $('#training-add-songs').onclick=()=>openTrainingAssetLibrary(false);
  $('#asset-training-back').onclick=async()=>{trainingAssetGuidance=false;$('#asset-training-guidance').classList.add('hidden');await loadTrainingInputs();$('.tab[data-tab="training"]').click();};
  $('#training-select-all').onclick = () => { const boxes=$$('#training-assets [data-training-asset]'); boxes.forEach(box=>box.checked=true); if(boxes.length>1) $(`[data-training-split="${boxes.at(-1).dataset.trainingAsset}"]`).value='validation'; };
  $('#training-style-scale').oninput = event => $('#training-style-scale-value').textContent=Number(event.target.value).toFixed(2);
  function drawChart(history=[]) {
    const canvas=$('#training-chart'), context=canvas.getContext('2d'), width=canvas.width,height=canvas.height;
    context.clearRect(0,0,width,height); context.strokeStyle='#e3e8f0'; context.lineWidth=1;
    for(let i=1;i<4;i++){const y=i*height/4;context.beginPath();context.moveTo(0,y);context.lineTo(width,y);context.stroke();}
    const points=history.filter(item=>Number.isFinite(Number(item.train_loss))); if(points.length<2)return;
    const values=points.flatMap(item=>[Number(item.train_loss),Number(item.validation_loss)].filter(Number.isFinite)); const min=Math.min(...values),max=Math.max(...values),span=Math.max(.001,max-min);
    const plot=(key,color)=>{context.strokeStyle=color;context.lineWidth=2;context.beginPath();let started=false;points.forEach((item,index)=>{const value=Number(item[key]);if(!Number.isFinite(value))return;const x=index/(points.length-1)*width,y=height-10-(value-min)/span*(height-20);if(!started){context.moveTo(x,y);started=true}else context.lineTo(x,y)});context.stroke();};
    plot('train_loss','#c74280');plot('validation_loss','#597dff');
  }
  function setTrainingStage(index) {
    $$('.training-stage span').forEach((item, position) => {
      item.classList.toggle('done', position < index);
      item.classList.toggle('active', position === index);
    });
  }
  function updateTrainingJob(job, runId = '', channel = 'training') {
    if(!job || (runId && currentRun?.id !== runId))return;
    if(channel==='preview'){
      setTrainingStage(4);
      if(!TERMINAL.has(job.status))$('#training-result').innerHTML='<article class="result-card"><b>正在生成检查点短试听</b><p class="meta">训练任务保持原状态；试听完成后会在这里直接显示播放器。</p></article>';
      if(job.status==='complete'){const result=job.result||{},modelId=result.model_asset_id||'',disabled=modelId?'':' disabled',relative=relativeAudio(job,result.audio||result.candidates?.[0]?.audio),playerMarkup=relative?`<audio controls preload="metadata" aria-label="YuE2 检查点短试听" src="${audioUrl(job.id,relative)}"></audio><a class="ghost compact" href="${audioUrl(job.id,relative)}" download>下载试听</a>`:'<p class="meta">试听任务已完成，请到历史任务查看输出。</p>';$('#training-result').innerHTML=`<article class="result-card training-preview-result"><b>检查点短试听已生成</b><p class="meta">这段试听用于快速检查曲风模型，长度和编排不代表完整歌曲。</p>${playerMarkup}<div class="toolbar"><button class="primary" data-use-trained-model="${escapeHtml(modelId)}"${disabled}>发送到歌曲创作</button><button class="ghost" data-preview-training>重新生成试听</button></div></article>`;}
      if(job.status==='failed'||job.status==='cancelled')showError($('#training-result'),new Error(job.error||stageLabel(job.status)));
      return;
    }
    if(channel==='auxiliary'){
      if(job.kind==='yue2_training_assets'&&!TERMINAL.has(job.status))$('#training-result').innerHTML='<article class="result-card"><b>正在安装训练资源</b><p class="meta">完成后会自动重新检查。</p></article>';
      if(job.status==='failed'||job.status==='cancelled')showError($('#training-result'),new Error(job.error||stageLabel(job.status)));
      return;
    }
    const completed=Number(job.completed ?? job.result?.step ?? 0), total=Number(job.total || currentRun?.config?.steps || 0);
    $('#training-step').textContent=`${completed} / ${total} 步`; $('#training-progress-bar').style.width=`${total?Math.min(100,completed/total*100):0}%`;
    const metric=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value))?Number(value).toFixed(3):'—';
    $('#training-loss').textContent=metric(job.train_loss); $('#training-val-loss').textContent=metric(job.validation_loss);
    const history=job.history || job.result?.history || currentRun?.config?.history || []; drawChart(history);
    $('#training-run-state').textContent=stageLabel(job.stage || job.status); $('#training-pause').classList.toggle('hidden',!(job.kind==='yue2_train' && job.status==='running')); $('#training-resume').classList.toggle('hidden',job.status!=='paused');
    if(job.kind==='yue2_prepare') {
      setTrainingStage(job.status==='complete'?3:2);
      const button=$('#create-training-run'),status=$('#training-form-status');
      button.disabled=!TERMINAL.has(job.status);button.textContent=!TERMINAL.has(job.status)?'正在后台预处理…':'创建新的快照并预处理';
      status.dataset.state=job.status==='failed'||job.status==='cancelled'?'error':job.status==='complete'?'':'busy';
      status.textContent=job.status==='complete'?'预处理完成。现在可以点击“开始训练”。':job.status==='failed'||job.status==='cancelled'?(job.error||stageLabel(job.status)):'快照已创建，后台正在预处理；可以在页面顶部查看实时进度。';
    }
    if(job.kind==='yue2_train') setTrainingStage(job.status==='complete'?4:3);
    if(job.status==='complete'&&job.kind==='yue2_prepare') $('#start-training').disabled=false;
    if(job.status==='complete'&&job.kind==='yue2_train') { const modelId=job.result?.model_asset_id||job.result?.model_asset?.id||currentRun?.model_asset_id||'',disabled=modelId?'':' disabled';$('#training-result').innerHTML=`<article class="result-card"><b>歌曲风格模型已保存到资产库</b><p class="meta">生成时会自动配对固定的 v4 NAR。先生成一段约 10 秒的短试听检查模型，再决定是否用于完整歌曲。</p><div class="toolbar"><button class="primary" data-preview-training>生成短试听</button><button class="ghost" data-use-trained-model="${escapeHtml(modelId)}"${disabled}>发送到歌曲创作</button></div></article>`;}
    if(job.status==='failed'||job.status==='cancelled') showError($('#training-result'),new Error(job.error||stageLabel(job.status)));
  }
  function clearTrainingChannel(channel, jobId) {
    if(channel==='preview'&&trainingPreviewJobId===jobId){trainingPreviewJobId='';trainingPreviewRunId='';savedValue('training-preview-job','');savedValue('training-preview-run','');}
    if(channel==='auxiliary'&&auxiliaryTrainingJobId===jobId)auxiliaryTrainingJobId='';
    if(channel==='training'){for(const[runId,value]of trainingJobsByRun)if(value===jobId)trainingJobsByRun.delete(runId);if(trainingJobId===jobId){trainingJobId='';savedValue('training-job','');}}
  }
  async function pollTrainingJob(jobId = trainingJobId, runId = currentRun?.id || '', channel = 'training') {
    if(!jobId||pollingTrainingJobs.has(jobId))return; pollingTrainingJobs.add(jobId);
    try{const job=await api(`/api/jobs/${jobId}`);updateTrainingJob(job,runId,channel);if(TERMINAL.has(job.status)){if((job.status==='complete'||job.status==='paused')&&(!runId||currentRun?.id===runId)){await Promise.all([loadRuns(),loadAssets(),checkTrainingResources()]);}if(job.status!=='paused')clearTrainingChannel(channel,jobId);}}
    catch(error){if(!runId||currentRun?.id===runId){$('#training-run-state').textContent='连接中断';$('#training-result').innerHTML=`<p class="meta">训练状态刷新失败，将自动重试：${escapeHtml(error.message)}</p>`;}}finally{pollingTrainingJobs.delete(jobId);}
  }
  async function submitTrainingJob(kind,request){const runId=String(request.run_id||''),channel=kind==='yue2_preview'?'preview':['yue2_prepare','yue2_train'].includes(kind)?'training':'auxiliary',job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,request,source:'webui',client_request_id:crypto.randomUUID(),result_panel:'training'})});if(channel==='preview'){trainingPreviewJobId=job.id;trainingPreviewRunId=runId;savedValue('training-preview-job',job.id);savedValue('training-preview-run',runId);}else if(channel==='training'){trainingJobsByRun.set(runId,job.id);if(currentRun?.id===runId){trainingJobId=job.id;savedValue('training-job',job.id);}}else auxiliaryTrainingJobId=job.id;updateTrainingJob(job,runId,channel);return job;}
  async function loadRunCheckpoints(runId){
    const select=$('#training-checkpoint-select'),previous=select.dataset.runId===runId?select.value:'';select.dataset.runId=runId;select.disabled=true;select.innerHTML='<option value="">正在读取检查点…</option>';
    if(!runId){select.innerHTML='<option value="">尚无检查点</option>';return;}
    try{const result=await api(`/api/workbench/training-runs/${encodeURIComponent(runId)}/checkpoints`);if(currentRun?.id!==runId)return;select.innerHTML=result.checkpoints.length?result.checkpoints.map(item=>`<option value="${item.step}" ${!previous&&item.current?'selected':''}>第 ${item.step} 步${item.current?' · 当前':''}</option>`).join(''):'<option value="">尚无检查点</option>';if(previous&&result.checkpoints.some(item=>String(item.step)===previous))select.value=previous;select.disabled=!result.checkpoints.length;}
    catch(error){if(currentRun?.id===runId){select.innerHTML='<option value="">检查点读取失败</option>';select.title=error.message;}}
  }
  function trainingModelCard(run, model){
    const meta=model.metadata||{},history=run.config?.history||[],selectedStep=Number(meta.selected_validation_step??run.config?.selected_step??0),selectedPoint=history.find(item=>Number(item.step)===selectedStep)||history.at(-1)||{},metric=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value))?Number(value).toFixed(3):'—';
    const filename=(String(model.title||'YuE2-style-model').replace(/[<>:"/\\|?*\x00-\x1f]+/g,'_').trim()||'YuE2-style-model')+'.safetensors';
    return `<article class="training-model-card"><h4><i class="bi bi-gpu-card"></i> ${escapeHtml(model.title)}</h4><p class="meta">模型已保存，可直接用于歌曲创作，也可以下载备份。</p><div class="training-model-grid"><span><small>完成训练</small><b>${Number(meta.completed_training_steps??run.config?.completed_training_steps??run.config?.steps??0)} 步</b></span><span><small>采用检查点</small><b>第 ${selectedStep||'—'} 步</b></span><span><small>训练 loss</small><b>${metric(selectedPoint.train_loss)}</b></span><span><small>验证 loss</small><b>${metric(meta.selected_validation_loss??selectedPoint.validation_loss)}</b></span><span><small>LoRA rank</small><b>${Number(meta.rank??run.config?.rank??0)||'—'}</b></span><span><small>模型大小</small><b>${(Number(model.size)/1048576).toFixed(1)} MB</b></span><span><small>生成模式</small><b>直接生成</b></span><span><small>强度建议</small><b>从 0.2–0.4 试听</b></span></div><p class="meta">模型文件位置（资产库内部使用哈希名，下载文件会使用 .safetensors）</p><div class="training-model-path">${escapeHtml(model.path)}</div><div class="toolbar training-model-actions"><button class="primary" data-use-trained-model="${escapeHtml(model.id)}">用于歌曲创作</button><a class="ghost" href="/api/workbench/assets/${encodeURIComponent(model.id)}/content?revision_id=${encodeURIComponent(model.revision_id)}" download="${escapeHtml(filename)}">下载模型</a><button class="ghost" type="button" data-open-trained-model="${escapeHtml(run.id)}">打开文件夹</button><button class="ghost" type="button" data-copy-trained-model-path="${escapeHtml(model.path||'')}">复制位置</button></div></article>`;
  }
  async function renderTrainingModels(){
    const revision=++trainingModelRenderRevision,panel=$('#training-model-manager'),completedRuns=trainingRuns.filter(run=>run.model_asset_id),total=completedRuns.length,pages=Math.max(1,Math.ceil(total/trainingModelPageSize));
    trainingModelPage=Math.min(Math.max(0,trainingModelPage),pages-1);
    $('#training-model-count').textContent=`${total} 个`;
    $('#training-model-page-status').textContent=`第 ${trainingModelPage+1} / ${pages} 页 · ${total} 个`;
    $('#training-model-prev').disabled=trainingModelPage===0;
    $('#training-model-next').disabled=trainingModelPage+1>=pages;
    if(!total){panel.innerHTML='<div class="empty-state"><i class="bi bi-gpu-card"></i><b>还没有训练完成的模型</b><p>模型训练完成后会自动出现在这里。</p></div>';return;}
    panel.innerHTML='<p class="meta">正在读取训练模型信息…</p>';
    const pageRuns=completedRuns.slice(trainingModelPage*trainingModelPageSize,(trainingModelPage+1)*trainingModelPageSize);
    const results=await Promise.allSettled(pageRuns.map(run=>api(`/api/workbench/training-runs/${encodeURIComponent(run.id)}/artifacts`)));
    if(revision!==trainingModelRenderRevision)return;
    panel.innerHTML=results.map((result,index)=>result.status==='fulfilled'&&result.value.model?trainingModelCard(pageRuns[index],result.value.model):`<article class="training-model-card failure-card"><b>${escapeHtml(pageRuns[index].title)}</b><p>${escapeHtml(result.status==='rejected'?(result.reason?.message||String(result.reason)):'模型文件不存在')}</p></article>`).join('');
  }
  function renderRun(){
    const select=$('#training-run-select');select.innerHTML=trainingRuns.length?trainingRuns.map(run=>`<option value="${run.id}">${escapeHtml(run.title)} · ${stageLabel(run.state)}</option>`).join(''):'<option value="">尚无训练记录</option>';select.value=currentRun?.id||'';
    if(!currentRun){trainingJobId='';savedValue('training-job','');$('#training-run-title').textContent='尚未创建训练';$('#training-run-state').textContent='待设置';$('#start-training').disabled=true;$('#create-training-run').disabled=false;$('#create-training-run').textContent='创建快照并预处理';$('#training-preview-checkpoint').classList.add('hidden');$('#training-pause').classList.add('hidden');$('#training-resume').classList.add('hidden');loadRunCheckpoints('');return;}
    savedValue('training-run',currentRun.id);$('#training-run-title').textContent=currentRun.title;$('#start-training').disabled=!currentRun.config?.prepared;$('#training-run-state').textContent=stageLabel(currentRun.state);const preparing=currentRun.state==='preparing';$('#create-training-run').disabled=preparing;$('#create-training-run').textContent=preparing?'正在后台预处理…':'创建新的快照并预处理';if(preparing){$('#training-form-status').dataset.state='busy';$('#training-form-status').textContent='快照已创建，后台正在预处理；可以在页面顶部查看实时进度。';}
    const form=$('#training-form');if(!form.elements.style.value&&currentRun.config?.default_style)form.elements.style.value=currentRun.config.default_style;if(!form.elements.lyrics.value&&currentRun.config?.default_lyrics)form.elements.lyrics.value=currentRun.config.default_lyrics;
    const history=currentRun.config?.history||[],last=history.at(-1),metric=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value))?Number(value).toFixed(3):'—';$('#training-step').textContent=`${Number(last?.step||0)} / ${Number(currentRun.config?.steps||0)} 步`;$('#training-progress-bar').style.width=`${currentRun.config?.steps?Math.min(100,Number(last?.step||0)/Number(currentRun.config.steps)*100):0}%`;$('#training-loss').textContent=metric(last?.train_loss);$('#training-val-loss').textContent=metric(last?.validation_loss);setTrainingStage(currentRun.state==='complete'?4:currentRun.config?.prepared?3:currentRun.state==='preparing'?2:1);$('#training-preview-checkpoint').classList.toggle('hidden',!currentRun.model_asset_id&&!currentRun.config?.last_checkpoint);const mappedJob=trainingJobsByRun.get(currentRun.id)||'',linkedJob=mappedJob||(['preparing','running','paused'].includes(currentRun.state)?currentRun.current_job_id||'':'');if(linkedJob)trainingJobsByRun.set(currentRun.id,linkedJob);trainingJobId=linkedJob;savedValue('training-job',linkedJob);$('#training-pause').classList.toggle('hidden',currentRun.state!=='running');$('#training-resume').classList.toggle('hidden',currentRun.state!=='paused');drawChart(history);loadRunCheckpoints(currentRun.id);
  }
  async function loadRuns(){try{trainingRuns=(await api('/api/workbench/training-runs?training_kind=yue2_style')).runs;const saved=savedValue('training-run');currentRun=trainingRuns.find(run=>run.id===saved)||trainingRuns[0]||null;renderRun();await renderTrainingModels();}catch(error){showError($('#training-result'),error);}}
  $('#training-model-prev').onclick=()=>{if(trainingModelPage>0){trainingModelPage--;renderTrainingModels();}};
  $('#training-model-next').onclick=()=>{if((trainingModelPage+1)*trainingModelPageSize<trainingRuns.filter(run=>run.model_asset_id).length){trainingModelPage++;renderTrainingModels();}};
  $('#training-run-select').onchange=event=>{currentRun=trainingRuns.find(run=>run.id===event.target.value)||null;renderRun();};
  async function checkTrainingResources(){try{const health=await api('/api/health'),ready=health.ready?.training_resources?.ready,card=$('#training-resource-card');card.classList.toggle('ready',!!ready);card.querySelector('b').textContent=ready?'YuE2 训练资源已就绪':'需要安装 YuE2 训练资源';$('#install-training-resources').classList.toggle('hidden',!!ready);}catch{}}
  $('#install-training-resources').onclick=async()=>{try{await submitTrainingJob('yue2_training_assets',{});}catch(error){showError($('#training-result'),error);}};
  $('#training-form').onsubmit=async event=>{
    event.preventDefault();const button=$('#create-training-run'),status=$('#training-form-status');
    if(button.disabled)return;
    button.disabled=true;button.textContent='正在检查并创建…';status.dataset.state='busy';status.textContent='正在检查歌曲、歌词、数据集划分和使用授权。';
    try{
      const selected=$$('#training-assets [data-training-asset]:checked');
      if(selected.length<2)throw new Error('请至少选择两首不同歌曲，并留一首作为验证集');
      const selectedAssets=selected.map(box=>trainingAssets.find(value=>value.id===box.dataset.trainingAsset));
      const duplicateAudio=new Map();
      for(const asset of selectedAssets){
        const split=$(`[data-training-split="${asset.id}"]`).value,previous=duplicateAudio.get(asset.blob_sha256);
        if(previous&&previous.split!==split)throw new Error(`“${previous.asset.title}”与“${asset.title}”是同一音频的重复导入，不能分别作为训练集和验证集；请换一首真正不同的歌曲`);
        duplicateAudio.set(asset.blob_sha256,{asset,split});
      }
      const form=Object.fromEntries(new FormData(event.currentTarget));
      if(!String(form.title||'').trim())throw new Error('请填写训练名称');
      if(!String(form.style||'').trim())throw new Error('请填写公共曲风，例如乐器、节奏、演唱与编排风格');
      if(form.rights_confirmed!=='on')throw new Error('请先勾选“我确认有权使用所选音乐进行模型训练”');
      const items=selected.map(box=>{
        const asset=trainingAssets.find(value=>value.id===box.dataset.trainingAsset),instrumental=$(`[data-training-instrumental="${asset.id}"]`).checked,lyricsChoice=$(`[data-training-lyrics="${asset.id}"]`).value,manualLyrics=$(`[data-training-lyrics-text="${asset.id}"] textarea`).value.trim(),lyricsRevision=lyricsChoice==='__manual__'?'':lyricsChoice,styleChoice=$(`[data-training-style="${asset.id}"]`).value,manualStyle=$(`[data-training-style-text="${asset.id}"] textarea`).value.trim(),styleRevision=styleChoice==='__manual__'?'':styleChoice;
        if(!instrumental&&lyricsChoice==='__manual__'&&!manualLyrics)throw new Error(`请在“${asset.title}”下面粘贴这首歌的歌词`);
        if(!instrumental&&!lyricsRevision&&!manualLyrics&&!String(form.lyrics||'').trim())throw new Error(`“${asset.title}”需要本曲歌词；也可以选择歌词资产或明确标记为纯器乐`);
        if(styleChoice==='__manual__'&&!manualStyle)throw new Error(`请在“${asset.title}”下面填写本曲曲风，或选择使用右侧公共曲风`);
        return{asset_id:asset.id,revision_id:asset.current_revision_id,start:0,end:Number(asset.metadata.duration),track_group_id:asset.metadata?.track_group_id||asset.blob_sha256,split:$(`[data-training-split="${asset.id}"]`).value,lyrics_revision_id:lyricsRevision||null,lyrics:manualLyrics,style_revision_id:styleRevision||null,style:manualStyle,instrumental};
      });
      if(!items.some(item=>item.split==='train')||!items.some(item=>item.split==='validation'))throw new Error('训练集和验证集都不能为空；请把至少一首歌改为验证集');
      status.textContent='检查通过，正在固定素材快照并启动后台预处理…';
      const snapshot=await api('/api/workbench/snapshots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:`${form.title} · 固定素材`,training_kind:'yue2_style',items,options:{default_style:form.style,default_lyrics:form.lyrics||'',rights_confirmed:true,rights_statement:'用户确认有权使用所选音乐进行模型训练',rights_confirmed_at:new Date().toISOString()}})});
      const config={rank:Number(form.rank),steps:Number(form.steps),gradient_accumulation:Number(form.gradient_accumulation),learning_rate:Number(form.learning_rate),default_style:form.style,default_lyrics:form.lyrics||'',user_fraction:.7,warmup_steps:50,schedule_steps:Number(form.steps),validate_every:100,save_every:100,seed:831001};
      currentRun=await api('/api/workbench/training-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:form.title,training_kind:'yue2_style',snapshot_id:snapshot.id,config})});
      savedValue('training-run',currentRun.id);$('#training-run-title').textContent=currentRun.title;$('#start-training').disabled=true;await submitTrainingJob('yue2_prepare',{run_id:currentRun.id});
      status.dataset.state='busy';status.textContent='快照已创建，后台正在预处理。进度会显示在页面顶部和下方训练记录中。';
    }catch(error){status.dataset.state='error';status.textContent=error.message;showError($('#training-result'),error);status.scrollIntoView({behavior:'smooth',block:'nearest'});}
    finally{const preparing=Boolean(trainingJobId);button.disabled=preparing;button.textContent=preparing?'正在后台预处理…':'创建快照并预处理';}
  };
  $('#start-training').onclick=async()=>{if(!currentRun)return;try{await submitTrainingJob('yue2_train',{run_id:currentRun.id});}catch(error){showError($('#training-result'),error);}};
  $('#training-pause').onclick=async()=>{if(!trainingJobId||!currentRun)return;const runId=currentRun.id,jobId=trainingJobId;try{const job=await api(`/api/jobs/${jobId}/pause`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});updateTrainingJob(job,runId,'training');}catch(error){alert(error.message);}};
  $('#training-resume').onclick=async()=>{if(!trainingJobId||!currentRun)return;const runId=currentRun.id,jobId=trainingJobId;try{const job=await api(`/api/jobs/${jobId}/resume`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});trainingJobsByRun.set(runId,job.id);if(currentRun?.id===runId){trainingJobId=job.id;savedValue('training-job',job.id);}updateTrainingJob(job,runId,'training');}catch(error){alert(error.message);}};
  async function pollAllTrainingJobs(){await Promise.all([pollTrainingJob(trainingJobId,currentRun?.id||'','training'),pollTrainingJob(trainingPreviewJobId,trainingPreviewRunId,'preview'),pollTrainingJob(auxiliaryTrainingJobId,'','auxiliary')]);}
  $('#training-refresh').onclick=async()=>{await Promise.all([loadRuns(),loadTrainingInputs(),checkTrainingResources()]);await pollAllTrainingJobs();};

  async function previewTrainingRun(){
    try {
      if(!currentRun?.model_asset_id&&!currentRun?.config?.last_checkpoint) throw new Error('训练尚未保存可试听的检查点');
      const data=Object.fromEntries(new FormData($('#training-form'))),memory=Number($('[data-generation-memory]')?.value||23.5);
      const selectedStep=Number($('#training-checkpoint-select').value||0);
      await submitTrainingJob('yue2_preview',{run_id:currentRun.id,project_id:currentProjectId||undefined,checkpoint_step:selectedStep||undefined,model_asset_id:selectedStep?undefined:(currentRun.model_asset_id||undefined),style_model_scale:Number($('#training-style-scale').value),generate:{style:data.style||'instrumental music',lyrics:data.lyrics||'[instrumental]',cot:'off',seed:831001,cfg_scale:1.01,candidates:1,backend:'torch-eager',memory_budget_gib:memory,offload_ar:true,nar_attention:'sdpa',nar_query_chunk_size:256,semantic_sampling:{min_tokens:200,max_tokens:256}}});
    } catch(error) { showError($('#training-result'),error); }
  }
  $('#training-preview-checkpoint').onclick=previewTrainingRun;
  const trainingPresets={quick:{rank:8,steps:200,gradient_accumulation:1,learning_rate:.00015},standard:{rank:16,steps:800,gradient_accumulation:2,learning_rate:.0001},quality:{rank:32,steps:1600,gradient_accumulation:2,learning_rate:.00008}};
  $('#training-preset').onchange=event=>{const preset=trainingPresets[event.target.value];if(!preset)return;for(const[key,value]of Object.entries(preset))$('#training-form').elements[key].value=value;};
  function useStyleModel(assetId){
    const select=$('#create-form [data-style-model]');
    if(!assetId||!select?.querySelector(`option[value="${CSS.escape(assetId)}"]`)){showError($('#training-result'),new Error('歌曲风格模型尚未载入，请刷新资产库后重试'));return;}
    select.value=assetId;$('#create-form [name=cot]').value='off';$('.tab[data-tab="create"]').click();
  }
  document.addEventListener('click',async event=>{
    if(event.target.closest('[data-preview-training]'))previewTrainingRun();
    const trained=event.target.closest('[data-use-trained-model]');if(trained){const assetId=trained.dataset.useTrainedModel||'';loadAssets().then(()=>useStyleModel(assetId));}
    const openModel=event.target.closest('[data-open-trained-model]');if(openModel){try{await api(`/api/workbench/training-runs/${encodeURIComponent(openModel.dataset.openTrainedModel)}/open`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:'model'})});}catch(error){alert(error.message);}}
    const copyModel=event.target.closest('[data-copy-trained-model-path]');if(copyModel){const path=copyModel.dataset.copyTrainedModelPath||'';try{await navigator.clipboard.writeText(path);copyModel.textContent='已复制位置';}catch{alert(path);}}
  });

  document.addEventListener('click',event=>{const tab=event.target.closest('.tab[data-tab]');if(tab&&tab.dataset.tab==='assets')loadAssets();if(tab&&tab.dataset.tab==='project')renderProject();if(tab&&tab.dataset.tab==='training'){loadTrainingInputs();loadRuns();checkTrainingResources();pollAllTrainingJobs();}});
  setInterval(pollAllTrainingJobs,1500);
  async function initWorkbench(){await(window.assistantReady||Promise.resolve());await loadProjects();await Promise.all([loadAssets(),loadTrainingInputs(),loadRuns(),checkTrainingResources()]);await pollAllTrainingJobs();}
  initWorkbench();
})();
