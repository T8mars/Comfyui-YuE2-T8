(() => {
  const kindNames = {song:'歌曲',work:'作品',vocal:'人声',instrumental:'伴奏',reference_voice:'参考音色',lyrics:'歌词',style:'曲风',score:'乐谱',model:'模型',other:'其他'};
  const kindIcons = {song:'bi-disc',work:'bi-music-note-beamed',vocal:'bi-mic',instrumental:'bi-soundwave',reference_voice:'bi-person-bounding-box',lyrics:'bi-file-text',style:'bi-tags',score:'bi-music-note-list',model:'bi-gpu-card',other:'bi-file-earmark'};
  let projects = [], assets = [], currentProjectId = savedValue('workbench-project') || '';
  let currentRun = null, trainingJobId = savedValue('training-job') || '', pollingTraining = false;
  const audioKinds = new Set(['song','work','vocal','instrumental','reference_voice']);
  const player = $('#global-player'), globalAudio = $('#global-audio');

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
    projects = (await api('/api/workbench/projects')).projects;
    if (currentProjectId && !projects.some(project => project.id === currentProjectId)) currentProjectId = '';
    if (!currentProjectId && projects.length) currentProjectId = projects[0].id;
    const select = $('#workbench-project-select');
    select.innerHTML = '<option value="">选择或新建项目</option>' + projects.map(project =>
      `<option value="${project.id}">${escapeHtml(project.title)} · ${project.asset_count} 项</option>`).join('');
    select.value = currentProjectId;
    const selected = projects.find(project => project.id === currentProjectId);
    $('#sidebar-project-name').textContent = selected?.title || '未选择项目';
    savedValue('workbench-project', currentProjectId);
    await renderProject();
  }
  async function renderProject() {
    const timeline = $('#project-timeline'), inspector = $('#project-assets');
    if (!currentProjectId) {
      $('#project-title').textContent = '尚未选择项目';
      $('#project-state').textContent = '先选择项目，后续作品会自动归档到这里。';
      timeline.className = 'project-timeline empty-state';
      timeline.innerHTML = '<i class="bi bi-music-note-beamed"></i><b>项目还没有音乐版本</b><p>新建或选择项目后，从资产库加入素材，或生成第一首歌曲。</p>';
      inspector.innerHTML = '<p class="meta">选择项目后显示固定版本的素材。</p>';
      return;
    }
    try {
      const project = await api(`/api/workbench/projects/${currentProjectId}`);
      $('#project-title').textContent = project.title;
      $('#project-state').textContent = `${project.assets.length} 项内容 · 生成结果会固定版本并自动加入`;
      const audio = project.assets.filter(item => audioKinds.has(item.kind));
      timeline.className = 'project-timeline' + (audio.length ? '' : ' empty-state');
      timeline.innerHTML = audio.length ? audio.map(item => `<article class="project-track"><span class="track-icon"><i class="bi ${kindIcons[item.kind]}"></i></span><div><b>${escapeHtml(item.title)}</b><small>${escapeHtml(kindNames[item.kind] || item.kind)} · ${(Number(item.metadata?.duration)||0).toFixed(1)} 秒 · 固定版本</small></div><button class="ghost compact" data-play-project="${item.id}" data-revision="${item.revision_id}"><i class="bi bi-play-fill"></i> 试听</button></article>`).join('') : '<i class="bi bi-music-note-beamed"></i><b>这个项目还没有音乐版本</b><p>生成、音色转换和从资产库加入的内容会显示在这里。</p>';
      inspector.innerHTML = project.assets.length ? project.assets.map(item => `<div class="inspector-asset"><b>${escapeHtml(item.title)}</b><small>${escapeHtml(kindNames[item.kind] || item.kind)} · ${escapeHtml(item.role)}</small></div>`).join('') : '<p class="meta">还没有关联内容。</p>';
      timeline.querySelectorAll('[data-play-project]').forEach(button => button.onclick = () => {
        const item = project.assets.find(value => value.id === button.dataset.playProject);
        if (item) playAsset(item, button.dataset.revision);
      });
    } catch (error) { showError(timeline, error); }
  }
  $('#workbench-project-select').onchange = async event => { currentProjectId = event.target.value; await loadProjects(); };
  $('#refresh-project').onclick = loadProjects;
  $('#new-project').onclick = async () => {
    const title = prompt('新项目名称', '我的歌曲项目');
    if (!title?.trim()) return;
    try { const project = await api('/api/workbench/projects', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title.trim()})}); currentProjectId = project.id; await loadProjects(); }
    catch (error) { alert(error.message); }
  };
  $('#sidebar-project-button').onclick = () => { $('.tab[data-tab="project"]').click(); $('#workbench-project-select').focus(); };
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
  function renderAssets() {
    const grid = $('#asset-grid');
    if (!assets.length) { grid.innerHTML = '<div class="empty-state"><i class="bi bi-collection-play"></i><b>还没有符合条件的资产</b><p>导入音频，或从创作页面生成第一个作品。</p></div>'; return; }
    grid.innerHTML = assets.map(asset => { const primary=audioKinds.has(asset.kind)?`<button class="ghost compact" data-play-asset="${asset.id}"><i class="bi bi-play-fill"></i> 试听</button>`:asset.kind==='model'&&asset.metadata?.model_type==='yue2_ar_lora'?`<button class="primary compact" data-use-style-model="${asset.id}"><i class="bi bi-music-note-beamed"></i> 用于创作</button>`:`<button class="ghost compact" data-read-asset="${asset.id}"><i class="bi bi-eye"></i> 查看</button>`; return `<article class="asset-card"><div class="asset-card-head"><i class="bi ${kindIcons[asset.kind] || kindIcons.other}"></i><div><b title="${escapeHtml(asset.title)}">${escapeHtml(asset.title)}</b><small>${escapeHtml(kindNames[asset.kind] || asset.kind)} · ${asset.size ? (asset.size/1048576).toFixed(1)+' MB' : '文本版本'}</small></div></div>${audioKinds.has(asset.kind) ? `<div class="wave-mini" data-wave="${asset.id}"></div>` : '<div class="wave-mini"><span style="height:2px;width:100%"></span></div>'}<div class="toolbar">${primary}${currentProjectId ? `<button class="ghost compact" data-add-asset="${asset.id}"><i class="bi bi-plus-circle"></i> 加入项目</button>` : ''}</div></article>`; }).join('');
    grid.querySelectorAll('[data-play-asset]').forEach(button => button.onclick = () => playAsset(assets.find(item => item.id === button.dataset.playAsset)));
    grid.querySelectorAll('[data-read-asset]').forEach(button => button.onclick = async () => {
      const asset = assets.find(item => item.id === button.dataset.readAsset);
      try { const text = await (await fetch(contentUrl(asset))).text(); const dialog = document.createElement('dialog'); dialog.innerHTML = `<form method="dialog"><h3>${escapeHtml(asset.title)}</h3><textarea rows="18" readonly>${escapeHtml(text)}</textarea><div class="toolbar"><button class="ghost">关闭</button></div></form>`; document.body.append(dialog); dialog.onclose=()=>dialog.remove(); dialog.showModal(); }
      catch (error) { alert(error.message); }
    });
    grid.querySelectorAll('[data-add-asset]').forEach(button => button.onclick = async () => {
      try { await api(`/api/workbench/projects/${currentProjectId}/assets`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:button.dataset.addAsset,role:'asset'})}); button.textContent='已加入'; button.disabled=true; await loadProjects(); }
      catch (error) { alert(error.message); }
    });
    grid.querySelectorAll('[data-use-style-model]').forEach(button => button.onclick = () => useStyleModel(button.dataset.useStyleModel));
    hydrateWaves();
  }
  async function loadAssets() {
    const query = new URLSearchParams();
    if ($('#asset-kind').value) query.set('kind',$('#asset-kind').value);
    if ($('#asset-query').value.trim()) query.set('q',$('#asset-query').value.trim());
    try { assets = (await api('/api/workbench/assets?' + query)).assets; renderAssets(); renderTrainingAssets(); await loadStyleModels(); }
    catch (error) { showError($('#asset-grid'), error); }
  }
  $('#asset-kind').onchange = loadAssets;
  let queryTimer; $('#asset-query').oninput = () => { clearTimeout(queryTimer); queryTimer=setTimeout(loadAssets,250); };
  $('#asset-upload').onchange = async event => {
    const files = [...event.target.files], status = $('#asset-import-status'), kind = $('#asset-import-kind').value;
    if (!files.length) return;
    let done = 0;
    try {
      for (const file of files) {
        status.textContent = `正在导入 ${done+1}/${files.length}：${file.name}`;
        const uploaded = await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
        const asset = await api('/api/workbench/assets/import', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_path:uploaded.path,kind,title:file.name,provenance:{source:'asset-library-upload'}})});
        if (currentProjectId) await api(`/api/workbench/projects/${currentProjectId}/assets`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:asset.id,role:'source'})});
        done++;
      }
      status.textContent = `已导入 ${done} 个文件，可立即试听。`; event.target.value=''; await Promise.all([loadAssets(),loadProjects()]);
    } catch (error) { status.textContent = error.message; }
  };
  $('#new-text-asset').onclick = () => $('#text-asset-form').classList.remove('hidden');
  $('#migrate-assets').onclick = async () => { const button=$('#migrate-assets');button.disabled=true;button.textContent='正在整理…';try{const job=await submitTrainingJob('workbench_migrate',{});while(true){await new Promise(resolve=>setTimeout(resolve,700));const state=await api(`/api/jobs/${job.id}`);if(TERMINAL.has(state.status)){if(state.status!=='complete')throw new Error(state.error||'整理失败');break;}}await loadAssets();button.textContent='整理完成';}catch(error){alert(error.message);button.textContent='重新整理';}finally{button.disabled=false;} };
  $('#cancel-text-asset').onclick = () => $('#text-asset-form').classList.add('hidden');
  $('#text-asset-form').onsubmit = async event => {
    event.preventDefault(); const form = event.currentTarget, data = Object.fromEntries(new FormData(form));
    try { const asset = await api('/api/workbench/assets/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); if(currentProjectId) await api(`/api/workbench/projects/${currentProjectId}/assets`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:asset.id,role:data.kind})}); form.reset(); form.classList.add('hidden'); await Promise.all([loadAssets(),loadProjects()]); }
    catch(error){ alert(error.message); }
  };

  async function loadStyleModels() {
    let models = assets.filter(asset => asset.kind === 'model' && asset.metadata?.model_type === 'yue2_ar_lora');
    if ($('#asset-kind').value || $('#asset-query').value) {
      try { models = (await api('/api/workbench/assets?kind=model&limit=500')).assets.filter(asset=>asset.metadata?.model_type==='yue2_ar_lora'); } catch {}
    }
    $$('[data-style-model]').forEach(select => {
      const previous=select.value; select.innerHTML='<option value="">使用原版 YuE2</option>'+models.map(model=>`<option value="${model.id}">${escapeHtml(model.title)} · 仅直接生成</option>`).join(''); select.value=previous;
      select.onchange=()=>{ if(select.value){ const mode=select.closest('form')?.querySelector('[name=cot]'); if(mode) mode.value='off'; } };
    });
  }

  function renderTrainingAssets() {
    const list = $('#training-assets'); if (!list) return;
    const choices = assets.filter(asset => ['song','work','vocal'].includes(asset.kind) && Number(asset.metadata?.duration) >= 1);
    list.innerHTML = choices.length ? choices.map(asset => `<label class="training-asset"><input type="checkbox" data-training-asset="${asset.id}"><span><b>${escapeHtml(asset.title)}</b><small>${kindNames[asset.kind]} · ${Number(asset.metadata.duration).toFixed(1)} 秒</small></span><select data-training-split="${asset.id}"><option value="train">训练集</option><option value="validation">验证集</option></select></label>`).join('') : '<div class="empty-state"><b>资产库里还没有可训练的歌曲</b><p>先导入至少两首不同歌曲；一首用于训练，一首用于验证。</p></div>';
  }
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
  function updateTrainingJob(job) {
    if(!job)return; const completed=Number(job.completed ?? job.result?.step ?? 0), total=Number(job.total || currentRun?.config?.steps || 0);
    $('#training-step').textContent=`${completed} / ${total} 步`; $('#training-progress-bar').style.width=`${total?Math.min(100,completed/total*100):0}%`;
    $('#training-loss').textContent=Number.isFinite(Number(job.train_loss))?Number(job.train_loss).toFixed(3):'—'; $('#training-val-loss').textContent=Number.isFinite(Number(job.validation_loss))?Number(job.validation_loss).toFixed(3):'—';
    const history=job.history || job.result?.history || currentRun?.config?.history || []; drawChart(history);
    $('#training-run-state').textContent=stageLabel(job.stage || job.status); $('#training-pause').classList.toggle('hidden',!(job.kind==='yue2_train' && job.status==='running')); $('#training-resume').classList.toggle('hidden',job.status!=='paused');
    if(job.kind==='yue2_prepare') setTrainingStage(job.status==='complete'?3:2);
    if(job.kind==='yue2_train') setTrainingStage(job.status==='complete'?4:3);
    if(job.kind==='yue2_preview') setTrainingStage(4);
    if(job.status==='complete'&&job.kind==='yue2_prepare') $('#start-training').disabled=false;
    if(job.status==='complete'&&job.kind==='yue2_train') $('#training-result').innerHTML=`<article class="result-card"><b>歌曲风格模型已保存到资产库</b><p class="meta">生成时会自动配对固定的 v4 NAR。先生成一段约 10 秒的短试听检查模型，再决定是否用于完整歌曲。</p><div class="toolbar"><button class="primary" data-preview-training>生成短试听</button><button class="ghost" data-use-trained-model>发送到歌曲创作</button></div></article>`;
    if(job.status==='complete'&&job.kind==='yue2_preview') { const result=job.result||{},relative=relativeAudio(job,result.audio||result.candidates?.[0]?.audio),playerMarkup=relative?`<audio controls preload="metadata" src="${audioUrl(job.id,relative)}"></audio><a class="ghost compact" href="${audioUrl(job.id,relative)}" download>下载试听</a>`:'<p class="meta">试听任务已完成，请到历史任务查看输出。</p>'; $('#training-result').innerHTML=`<article class="result-card training-preview-result"><b>检查点短试听已生成</b><p class="meta">这段试听用于快速检查曲风模型，长度和编排不代表完整歌曲。</p>${playerMarkup}<div class="toolbar"><button class="primary" data-use-trained-model>发送到歌曲创作</button><button class="ghost" data-preview-training>重新生成试听</button></div></article>`; }
    if(job.status==='failed'||job.status==='cancelled') showError($('#training-result'),new Error(job.error||stageLabel(job.status)));
  }
  async function pollTrainingJob() {
    if(pollingTraining||!trainingJobId)return; pollingTraining=true;
    try{const job=await api(`/api/jobs/${trainingJobId}`);updateTrainingJob(job);if(TERMINAL.has(job.status)){if(job.status==='complete'||job.status==='paused'){await Promise.all([loadRuns(),loadAssets(),checkTrainingResources()]);}if(job.status!=='paused') {trainingJobId='';savedValue('training-job','');}}}
    catch{}finally{pollingTraining=false;}
  }
  async function submitTrainingJob(kind,request){const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,request,source:'webui',client_request_id:crypto.randomUUID(),result_panel:'training'})});trainingJobId=job.id;savedValue('training-job',job.id);updateTrainingJob(job);return job;}
  async function loadRuns(){try{const runs=(await api('/api/workbench/training-runs?training_kind=yue2_style')).runs;const saved=savedValue('training-run');currentRun=runs.find(run=>run.id===saved)||runs[0]||null;if(currentRun){savedValue('training-run',currentRun.id);$('#training-run-title').textContent=currentRun.title;$('#start-training').disabled=!currentRun.config?.prepared;$('#training-run-state').textContent=stageLabel(currentRun.state);const form=$('#training-form');if(!form.elements.style.value&&currentRun.config?.default_style)form.elements.style.value=currentRun.config.default_style;if(!form.elements.lyrics.value&&currentRun.config?.default_lyrics)form.elements.lyrics.value=currentRun.config.default_lyrics;const history=currentRun.config?.history||[],last=history.at(-1);$('#training-step').textContent=`${Number(last?.step||0)} / ${Number(currentRun.config?.steps||0)} 步`;$('#training-progress-bar').style.width=`${currentRun.config?.steps?Math.min(100,Number(last?.step||0)/Number(currentRun.config.steps)*100):0}%`;$('#training-loss').textContent=Number.isFinite(Number(last?.train_loss))?Number(last.train_loss).toFixed(3):'—';$('#training-val-loss').textContent=Number.isFinite(Number(last?.validation_loss))?Number(last.validation_loss).toFixed(3):'—';setTrainingStage(currentRun.state==='complete'?4:currentRun.config?.prepared?3:currentRun.state==='preparing'?2:1);if(currentRun.current_job_id&&!trainingJobId){trainingJobId=currentRun.current_job_id;savedValue('training-job',trainingJobId);}drawChart(history);}}catch(error){showError($('#training-result'),error);}}
  async function checkTrainingResources(){try{const health=await api('/api/health'),ready=health.ready?.training_resources?.ready,card=$('#training-resource-card');card.classList.toggle('ready',!!ready);card.querySelector('b').textContent=ready?'YuE2 训练资源已就绪':'需要安装 YuE2 训练资源';$('#install-training-resources').classList.toggle('hidden',!!ready);}catch{}}
  $('#install-training-resources').onclick=async()=>{try{await submitTrainingJob('yue2_training_assets',{});}catch(error){showError($('#training-result'),error);}};
  $('#training-form').onsubmit=async event=>{event.preventDefault();try{const selected=$$('#training-assets [data-training-asset]:checked');if(selected.length<2)throw new Error('至少选择两首不同歌曲，并留一首作为验证集');const items=selected.map(box=>{const asset=assets.find(value=>value.id===box.dataset.trainingAsset);return{asset_id:asset.id,revision_id:asset.current_revision_id,start:0,end:Number(asset.metadata.duration),track_group_id:asset.id,split:$(`[data-training-split="${asset.id}"]`).value};});if(!items.some(item=>item.split==='train')||!items.some(item=>item.split==='validation'))throw new Error('训练集和验证集都不能为空');const form=Object.fromEntries(new FormData(event.currentTarget));if(form.rights_confirmed!=='on')throw new Error('请先确认你有权使用所选音乐进行训练');const snapshot=await api('/api/workbench/snapshots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:`${form.title} · 固定素材`,training_kind:'yue2_style',items,options:{default_style:form.style,default_lyrics:form.lyrics,rights_confirmed:true,rights_statement:'用户确认有权使用所选音乐进行模型训练',rights_confirmed_at:new Date().toISOString()}})});const config={rank:Number(form.rank),steps:Number(form.steps),gradient_accumulation:Number(form.gradient_accumulation),learning_rate:Number(form.learning_rate),default_style:form.style,default_lyrics:form.lyrics,user_fraction:.7,warmup_steps:50,schedule_steps:Number(form.steps),validate_every:100,save_every:100,seed:831001};currentRun=await api('/api/workbench/training-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:form.title,training_kind:'yue2_style',snapshot_id:snapshot.id,config})});savedValue('training-run',currentRun.id);$('#training-run-title').textContent=currentRun.title;$('#start-training').disabled=true;await submitTrainingJob('yue2_prepare',{run_id:currentRun.id});}catch(error){showError($('#training-result'),error);}};
  $('#start-training').onclick=async()=>{if(!currentRun)return;try{await submitTrainingJob('yue2_train',{run_id:currentRun.id});}catch(error){showError($('#training-result'),error);}};
  $('#training-pause').onclick=async()=>{if(!trainingJobId)return;try{const job=await api(`/api/jobs/${trainingJobId}/pause`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});updateTrainingJob(job);}catch(error){alert(error.message);}};
  $('#training-resume').onclick=async()=>{if(!trainingJobId)return;try{const job=await api(`/api/jobs/${trainingJobId}/resume`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});trainingJobId=job.id;savedValue('training-job',job.id);updateTrainingJob(job);}catch(error){alert(error.message);}};
  $('#training-refresh').onclick=async()=>{await Promise.all([loadRuns(),loadAssets(),checkTrainingResources()]);await pollTrainingJob();};

  async function previewTrainingRun(){
    try {
      await loadRuns();
      if(!currentRun?.model_asset_id) throw new Error('训练尚未生成可试听的模型资产');
      const data=Object.fromEntries(new FormData($('#training-form'))),memory=Number($('[data-generation-memory]')?.value||23.5);
      await submitTrainingJob('yue2_preview',{run_id:currentRun.id,model_asset_id:currentRun.model_asset_id,style_model_scale:Number($('#training-style-scale').value),generate:{style:data.style||'instrumental music',lyrics:data.lyrics||'[instrumental]',cot:'off',seed:831001,cfg_scale:1.01,candidates:1,backend:'torch-eager',memory_budget_gib:memory,offload_ar:true,nar_attention:'sdpa',nar_query_chunk_size:256,semantic_sampling:{min_tokens:200,max_tokens:256}}});
    } catch(error) { showError($('#training-result'),error); }
  }
  function useStyleModel(assetId){
    const select=$('#create-form [data-style-model]');
    if(!assetId||!select?.querySelector(`option[value="${CSS.escape(assetId)}"]`)){showError($('#training-result'),new Error('歌曲风格模型尚未载入，请刷新资产库后重试'));return;}
    select.value=assetId;$('#create-form [name=cot]').value='off';$('.tab[data-tab="create"]').click();
  }
  document.addEventListener('click',event=>{if(event.target.closest('[data-preview-training]'))previewTrainingRun();const trained=event.target.closest('[data-use-trained-model]');if(trained){loadRuns().then(()=>loadAssets()).then(()=>useStyleModel(currentRun?.model_asset_id));}});

  document.addEventListener('click',event=>{const tab=event.target.closest('.tab[data-tab]');if(tab&&tab.dataset.tab==='assets')loadAssets();if(tab&&tab.dataset.tab==='project')renderProject();if(tab&&tab.dataset.tab==='training'){loadRuns();checkTrainingResources();pollTrainingJob();}});
  setInterval(()=>{if(trainingJobId)pollTrainingJob();},1500);
  Promise.all([loadProjects(),loadAssets(),loadRuns(),checkTrainingResources()]).then(pollTrainingJob);
})();
