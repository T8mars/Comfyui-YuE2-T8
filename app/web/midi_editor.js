/* Project-scoped piano roll, WebAudio preview and immutable MuLaCover recipes. */
(() => {
  'use strict';
  const root = $('#midi-editor-root'), prefix = '/api/workbench/midi';
  const labels = {melody:'旋律',chord:'和弦',drums:'鼓组',other:'仅保留 / 其他'};
  const names = ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'];
  const drumNames = {35:'底鼓',36:'底鼓',38:'军鼓',40:'军鼓',42:'闭镲',44:'踏镲',46:'开镲',49:'吊镲',51:'叮叮镲'};
  root.innerHTML = `<div class="midi-heading"><div><p class="eyebrow">MIDI WORKSPACE</p><h2>MIDI 编辑器</h2><p>编辑、免费试听，再用旋律与和弦生成完整歌曲。</p></div><div class="midi-actions"><button id="midi-new" class="ghost" type="button">新建 MIDI</button><button id="midi-import" class="ghost" type="button">导入 MIDI</button><button id="midi-audio" class="primary" type="button">上传音乐 · 提取 MIDI</button><button id="midi-choose-asset" class="ghost" type="button">从资产库选择</button></div></div>
  <input id="midi-upload" class="hidden" type="file" accept=".mid,.midi"><input id="midi-audio-upload" class="hidden" type="file" accept="audio/*,.wav,.flac,.mp3,.m4a">
  <div class="midi-import-card hidden" id="midi-audio-card"><b>提取音乐的旋律、和弦与鼓组</b><p class="midi-source-preview" id="midi-source-name"></p><audio id="midi-source-player" controls preload="metadata"></audio><div class="midi-import-options"><label>起点（秒）<input id="midi-clip-start" type="number" min="0" step="0.1" value="0"></label><label>终点（空白 = 整首）<input id="midi-clip-end" type="number" min="0" step="0.1"></label><label>BPM（可选）<input id="midi-extract-bpm" type="number" min="20" max="400" placeholder="自动估计"></label></div><p class="meta">本地提取，无需歌词或 API。每次范围最多 15 分钟；下载的是识别乐谱，不是音频分轨。识别出的和弦以中心八度呈现，可编辑。</p><div class="toolbar"><button id="midi-extract" class="primary" type="button">提取三类 MIDI</button><button id="midi-cancel-extract" class="ghost hidden" type="button">取消提取</button></div><div id="midi-extract-progress" class="midi-status" role="status"></div><div id="midi-extract-tracks" class="midi-track-results"></div></div>
  <div class="toolbar"><input id="midi-title" class="midi-document-title" aria-label="MIDI 名称" maxlength="200"><select id="midi-documents" class="midi-documents" aria-label="打开已保存的 MIDI"></select><button id="midi-save" class="ghost" type="button">保存</button><button id="midi-reload" class="ghost" type="button">重新载入</button><button id="midi-save-copy" class="ghost" type="button">另存副本</button><span id="midi-save-state" class="midi-status" role="status">正在载入…</span></div>
  <div class="midi-mobile-switch"><button class="ghost" data-midi-view="edit" type="button">编辑与试听</button><button class="ghost" data-midi-view="generate" type="button">生成歌曲</button></div>
  <div class="midi-layout" data-view="edit"><aside id="midi-tracks" class="midi-tracks"></aside><section class="midi-editor-card"><div class="midi-transport"><button id="midi-play" class="primary" type="button">▶ 试听</button><button id="midi-stop" class="ghost" type="button">停止</button><label>BPM<input id="midi-bpm" type="number" min="20" max="400" value="120"></label><label><input id="midi-loop" type="checkbox">循环</label><label><input id="midi-metronome" type="checkbox">节拍器</label><label>起始拍<input id="midi-loop-start" type="number" min="0" value="0"></label><label>结束拍<input id="midi-loop-end" type="number" min="0.25" value="16"></label></div>
  <div class="midi-tools"><button id="midi-draw-mode" class="ghost active" type="button">画音符</button><button id="midi-select-mode" class="ghost" type="button">选择</button><button id="midi-undo" class="ghost" type="button">撤销</button><button id="midi-redo" class="ghost" type="button">重做</button><button id="midi-copy" class="ghost" type="button">复制</button><button id="midi-paste" class="ghost" type="button">粘贴</button><button id="midi-delete" class="ghost" type="button">删除</button><label>网格<select id="midi-snap"><option value="16">1/16</option><option value="8">1/8</option><option value="4">1/4</option><option value="32">1/32</option><option value="0">不吸附</option></select></label><label>缩放<select id="midi-zoom"><option value="0.5">50%</option><option value="1" selected>100%</option><option value="2">200%</option><option value="4">400%</option></select></label><label>显示起始拍<input id="midi-view-beat" type="number" min="0" value="0"></label><label>最低音<input id="midi-pitch-low" type="number" min="0" max="92" value="48"></label></div>
  <div class="midi-chord-box"><b>添加和弦</b><select id="midi-chord-root" aria-label="和弦根音">${names.map((n,i)=>`<option value="${i}">${n}</option>`).join('')}</select><select id="midi-chord-quality" aria-label="和弦性质"><option value="major">大三和弦</option><option value="minor">小三和弦</option><option value="seventh">属七和弦</option><option value="maj7">大七和弦</option><option value="sus4">挂四和弦</option></select><label>起始拍<input id="midi-chord-start" type="number" min="0" value="0"></label><label>拍数<input id="midi-chord-duration" type="number" min="0.25" step="0.25" value="4"></label><button id="midi-add-chord" class="ghost" type="button">加入和弦轨</button></div>
  <canvas id="midi-roll" class="midi-roll" tabindex="0" role="application" aria-label="钢琴卷帘：点击画音符，拖动音符移动，拖动右侧改变长度"></canvas><p class="midi-roll-help">点空白处画音符；拖动移动，右侧拖动改长度。「选择」可框选，Shift 可多选。快捷键仅在卷帘中生效：Delete 删除、Ctrl/Cmd Z 撤销、C/V 复制粘贴、空格试听。试听音源为本地合成器。</p>
  <div class="midi-note-properties"><label>音高（0–127）<input id="midi-note-pitch" type="number" min="0" max="127"></label><label>起点（拍）<input id="midi-note-start" type="number" min="0" step="0.0625"></label><label>长度（拍）<input id="midi-note-duration" type="number" min="0.001" step="0.0625"></label><label>力度（1–127）<input id="midi-note-velocity" type="number" min="1" max="127"></label></div>
  <div class="midi-tools"><label>移调<select id="midi-transpose-scope"><option value="melody">仅旋律</option><option value="whole">旋律 + 和弦</option><option value="selection">所选音符</option></select></label><button class="ghost" type="button" data-midi-transpose="-12">降八度</button><button class="ghost" type="button" data-midi-transpose="12">升八度</button><button class="ghost" type="button" data-midi-transpose="-1">−1 半音</button><button class="ghost" type="button" data-midi-transpose="1">+1 半音</button><button id="midi-repeat" class="ghost" type="button">重复所选到下一段</button><button id="midi-trim" class="ghost" type="button">按试听范围裁剪副本</button></div>
  <div class="midi-bottom"><button id="midi-confirm-mapping" class="ghost" type="button">确认轨道用途</button><button id="midi-publish" class="ghost" type="button">保存此版到资产库</button><a id="midi-download-combined" class="ghost" download="combined.mid">下载多轨 MIDI</a><a id="midi-download-zip" class="ghost" download="midi-tracks.zip">下载三轨 ZIP</a></div><p class="midi-status" id="midi-message" role="status"></p><button id="midi-correct-bpm" class="ghost hidden" type="button">按原始识别时间校正 BPM</button></section>
  <aside class="midi-generator"><details open><summary>用 MuLaCover 生成完整歌曲</summary><form id="midi-generation-form"><label>歌词<textarea id="midi-gen-lyrics" data-midi-gen="lyrics" rows="5" placeholder="[Verse]\n填写要演唱的歌词" required></textarea></label><label>流派<input id="midi-gen-genre" data-midi-gen="genre" placeholder="例如 acoustic folk"></label><label>乐器<input data-midi-gen="instrument" placeholder="例如 guitar, piano"></label><label>情绪<input data-midi-gen="mood" placeholder="例如 warm, hopeful"></label><label>主题<input data-midi-gen="topic" placeholder="例如 home"></label><div class="midi-row"><label>时长上限（秒）<input data-midi-gen="duration_seconds" type="number" min="5" max="300" value="30"></label><label>采样种子<input data-midi-gen="seed" type="number" min="0" max="9007199254740991" value="831001"></label></div><details><summary>解码设置</summary><label>解码种子<input data-midi-gen="decode_seed" type="number" min="0" max="9007199254740991" value="831002"></label></details><label class="check"><input id="midi-empty-chords" type="checkbox">使用空和弦条件（控制较弱）</label><p id="midi-generate-note" class="meta">BPM 用于试听和导出；模型生成的速度、音色与演奏不保证精确一致。生成会固定当前 MIDI，之后的编辑不改变该任务。</p><button id="midi-generate" class="primary" type="submit">生成带伴奏的完整歌曲</button></form></details></aside></div><div id="midi-gen-result" class="midi-result-section"></div>`;
  let doc=null, trackId='', scope=String(window.workbenchProjectId?.()||''), scopeRevision=0, dirty=0, saved=0, saving=null, timer=null;
  let undo=[],redo=[],selection=new Set(),clipboard=[],mode='draw',drag=null,viewBeat=0,playBeat=null,generationBusy=false;
  let audioSource=null,extractJob='',generationJob='',audioContext=null,voices=new Set(),playTimer=null,playStart=0,playStartBeat=0,pausedBeat=null;
  const clone=v=>structuredClone(v), uid=()=>crypto.randomUUID().replaceAll('-',''), post=(url,data)=>api(prefix+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const actionContext=()=>({project:scope,revision:scopeRevision,document:doc?.id});
  function guard(context){if(context.revision!==scopeRevision||context.project!==scope||context.document!==doc?.id)throw new Error('项目或编辑文档已切换，原操作的素材保留在原项目，请重新打开。');}
  async function saveContext(context){finishPointer();await save();guard(context);}
  async function postContext(context,url,data={}){
    guard(context);const result=await post(url,{...data,project_id:context.project});guard(context);
    // Save edits made while a document-changing request was in flight.
    finishPointer();
    if(dirty>saved){
      await saveContext(context);
      if(result.id===context.document){const latest=await api(`${prefix}/documents/${result.id}`);guard(context);return latest;}
    }
    return result;
  }
  async function copyContext(context,makeDocument){
    guard(context);finishPointer();clearTimeout(timer);root.inert=true;
    try{
      // A copy resolves a rejected save; do not require that same old save to succeed.
      if(saving){try{await saving;}catch{}guard(context);}
      backup();
      const document=makeDocument(),result=await post('/documents',{document,project_id:context.project});
      guard(context);return result;
    }finally{root.inert=false;}
  }
  const currentTrack=()=>doc?.tracks.find(t=>t.id===trackId)||doc?.tracks[0];
  const exampleButton=document.createElement('button');exampleButton.id='midi-example';exampleButton.className='ghost';exampleButton.type='button';exampleButton.textContent='打开练习示例';$('.midi-actions').append(exampleButton);
  let documentsPage=0,documentsLoadRevision=0;
  const documentNavigation=document.createElement('span');documentNavigation.className='midi-document-navigation';
  documentNavigation.innerHTML='<button id="midi-doc-prev" class="ghost" type="button">上一页</button><span id="midi-doc-page" class="meta" role="status"></span><button id="midi-doc-next" class="ghost" type="button">下一页</button>';
  $('#midi-documents').after(documentNavigation);
  $('#midi-doc-prev').onclick=()=>loadDocuments(documentsPage-1).catch(e=>message(e.message,true));
  $('#midi-doc-next').onclick=()=>loadDocuments(documentsPage+1).catch(e=>message(e.message,true));
  const management=document.createElement('details');management.className='midi-management';
  management.innerHTML='<summary>文档与缓存管理</summary><p class="meta">归档可收起不用的文档，随时恢复。识别缓存可单独清理；保存的 MIDI 和歌曲均保留。资产删除请到资产库回收站。</p><div class="toolbar"><button id="midi-archive" class="ghost" type="button">归档当前文档</button><button id="midi-open-archive" class="ghost" type="button">查看归档文档</button><button id="midi-clear-cache" class="ghost" type="button">清理识别缓存</button></div>';
  $('.midi-mobile-switch').before(management);
  exampleButton.onclick=async()=>{const context=actionContext();try{await saveContext(context);install(await postContext(context,'/examples/simple'));await loadDocuments();message('已打开练习示例。先改几个音并试听，再填写自己的歌词生成歌曲。');}catch(e){if(context.revision===scopeRevision)message(e.message,true);}};
  let generationModelsReady=false;
  const modelState=document.createElement('p');modelState.id='midi-model-state';modelState.className='meta';modelState.setAttribute('role','status');$('#midi-generation-form').prepend(modelState);
  function readiness(){
    const count=doc?.tracks.filter(t=>t.role==='melody').reduce((n,t)=>n+t.notes.length,0)||0;
    const reason=!generationModelsReady?'完整歌曲生成模型尚未就绪，请到“模型与设置”检查':!count?'先添加旋律音符，或把真实候选乐器轨道指定为旋律':!doc?.mapping_confirmed?'先确认左侧轨道用途':'';
    modelState.textContent=reason||'MuLaCover 模型已就绪；试听音源与生成歌曲音色不同。';
    $('#midi-generate').disabled=generationBusy||Boolean(reason);
  }
  async function checkModels(){try{const health=await api('/api/health');generationModelsReady=Boolean(health.ready?.capabilities?.mulacover);readiness();}catch{generationModelsReady=false;readiness();}}
  const backupKey=(s=scope)=>`yue2:midi-backup:${s||'__global__'}`;
  const documentBackupKey=(s,id)=>`${backupKey(s)}:document:${id}`;
  function readBackup(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}}
  function message(text,error=false){$('#midi-message').textContent=text;$('#midi-message').classList.toggle('error',error);}
  function setSave(text,error=false){$('#midi-save-state').textContent=text;$('#midi-save-state').classList.toggle('error',error);}
  function backup(){
    if(!doc)return;
    try{
      const value={document:doc,dirty:dirty>saved},key=documentBackupKey(scope,doc.id);
      const previous=readBackup(backupKey());
      if(value.dirty)localStorage.setItem(key,JSON.stringify(value));else if(dirty>0)localStorage.removeItem(key);
      if(!previous?.dirty||previous.document?.id===doc.id&&value.dirty||previous.document?.id===doc.id&&dirty>0)
        localStorage.setItem(backupKey(),JSON.stringify(value));
    }catch{setSave('本机备份空间不足，服务端保存仍可使用',true);}
  }
  function pendingBackups(){
    const values=new Map();
    try{
      const start=`${backupKey()}:document:`,recovery=`${backupKey()}:recovery:`;
      const add=(key)=>{const value=readBackup(key);if(value?.dirty&&value.document?.project_id===scope){
        const identity=JSON.stringify(value.document),existing=values.get(identity);
        if(!existing||key.startsWith(recovery))values.set(identity,{...value,backup_key:key});
      }};
      add(backupKey());
      for(let index=0;index<localStorage.length;index++){
        const key=localStorage.key(index);if(key.startsWith(start)||key.startsWith(recovery))add(key);
      }
      // Pending recovery is immutable even if this same server document is edited again.
      for(const value of values.values())if(!value.backup_key.startsWith(recovery)){
        value.backup_key=recovery+uid();localStorage.setItem(value.backup_key,JSON.stringify(value));
      }
    }catch{}
    return [...values.values()];
  }
  function changed(){
    const previous=undo.at(-1);
    if(doc.extraction&&previous){for(const role of ['melody','chord','drums']){
      const notes=value=>value.tracks.filter(t=>t.role===role).flatMap(t=>t.notes);
      const current=notes(doc),state=doc.extraction.tracks[role];
      if(state&&JSON.stringify(current)!==JSON.stringify(notes(previous))){state.recognition_status ||=state.status;state.status='edited';state.note_count=current.length;}
    }}
    dirty++;backup();setSave('正在等待自动保存…');clearTimeout(timer);timer=setTimeout(()=>save().catch(()=>{}),650);draw();properties();renderExtractTracks();readiness();$('#midi-generate-note').textContent=generationJob?'当前 MIDI 已编辑；下面的音频基于生成时固定的旧版 MIDI。':'生成会固定当前 MIDI；编辑与试听免费，不调用 API。';
  }
  function beforeEdit(){undo.push(clone(doc));if(undo.length>60)undo.shift();redo=[];stop();}
  async function save(){
    clearTimeout(timer);
    if(saving){await saving;if(dirty>saved)return save();return doc;}
    if(!doc||dirty<=saved)return doc;
    const captured=clone(doc),rev=dirty,localScope=scope,token=scopeRevision;
    setSave('正在保存…');
    saving=post(`/documents/${captured.id}/save`,{document:captured,version:captured.version,project_id:localScope}).then(result=>{
      if(token===scopeRevision&&doc?.id===captured.id){doc.version=result.version;doc.updated_at=result.updated_at;saved=rev;setSave(dirty===saved?`已保存 · v${doc.version}`:'正在保存新编辑…');backup();downloads();renderExtractTracks();}
      return result;
    }).catch(error=>{if(token===scopeRevision){backup();setSave(`保存失败：${error.message}；可另存副本`,true);}throw error;}).finally(()=>{saving=null;});
    const result=await saving;if(token===scopeRevision&&dirty>saved)return save();return result;
  }
  function noteName(p){return `${names[p%12]}${Math.floor(p/12)-1}`;}
  function install(value){if(value.project_id!==scope)throw new Error('MIDI 文档与当前项目不一致');stop();drag=null;$('.midi-layout').inert=false;doc=value;dirty=saved=0;undo=[];redo=[];selection.clear();trackId=doc.tracks[0].id;viewBeat=Number(doc.settings?.view_beat||0);$('#midi-title').value=doc.title;$('#midi-bpm').value=(60e6/doc.tempos[0].tempo).toFixed(2);$('#midi-snap').value=String(doc.settings?.snap??16);$('#midi-zoom').value=String(doc.settings?.zoom||1);$('#midi-pitch-low').value=String(Math.max(0,Math.min(92,doc.settings?.pitch_low??48)));$('#midi-view-beat').value=viewBeat;
    trackId=doc.tracks.some(t=>t.id===doc.settings?.active_track_id)?doc.settings.active_track_id:trackId;
    audioSource=null;$('#midi-source-player').pause();$('#midi-source-player').removeAttribute('src');$('#midi-audio-card').classList.add('hidden');$('#midi-extract-tracks').replaceChildren();
    root.querySelectorAll('[data-midi-gen]').forEach(input=>{input.value=doc.generation?.[input.dataset.midiGen]??input.defaultValue??'';});
    if(doc.settings?.audio_source){
      const source=doc.settings.audio_source;setAudio(source,source.url||'');
      $('#midi-clip-start').value=doc.settings.clip_start??0;$('#midi-clip-end').value=doc.settings.clip_end??'';$('#midi-extract-bpm').value=doc.settings.extract_bpm??'';
    }else if(doc.source?.format==='audio'&&doc.source.asset_id){
      const ref={$asset:doc.source.asset_id,revision_id:doc.source.revision_id};
      setAudio({source_path:ref,title:doc.title.replace(/ · 提取 MIDI$/,'')},`/api/workbench/assets/${doc.source.asset_id}/content?revision_id=${doc.source.revision_id||''}`);
      $('#midi-clip-start').value=doc.source.clip_start||0;
      $('#midi-clip-end').value=doc.source.clip_end??'';
    }
    $('#midi-loop-start').value=doc.settings?.loop_start??0;
    $('#midi-loop-end').value=doc.settings?.loop_end??Math.max(16,...doc.tracks.flatMap(t=>t.notes.map(n=>(n.tick+n.duration)/doc.ppq)));
    $('#midi-loop').checked=Boolean(doc.settings?.loop);
    $('#midi-metronome').checked=Boolean(doc.settings?.metronome);$('#midi-volume').value=doc.settings?.volume??70;
    $('#midi-empty-chords').checked=Boolean(doc.generation?.allow_empty_chords);
    setSave(`已保存 · v${doc.version}${scope?' · 当前项目':' · 独立草稿'}`);$('#midi-correct-bpm').classList.toggle('hidden',!doc.raw_transcription);renderTracks();properties();downloads();renderExtractTracks();draw();backup();readiness();
  }
  async function loadDocuments(page=0){
    const token=scopeRevision,revision=++documentsLoadRevision;page=Math.max(0,page);
    const result=await api(`${prefix}/documents?project_id=${encodeURIComponent(scope)}&limit=20&offset=${page*20}`);
    if(token!==scopeRevision||revision!==documentsLoadRevision)return;
    const pages=Math.max(1,Math.ceil(result.total/20));
    if(page>=pages)return loadDocuments(pages-1);
    documentsPage=page;
    const items=[...result.documents];
    if(doc&&!items.some(item=>item.id===doc.id))items.unshift({...doc,title:`当前：${doc.title}`});
    $('#midi-documents').innerHTML=items.map(d=>`<option value="${d.id}">${escapeHtml(d.title)} · v${d.version}</option>`).join('');
    $('#midi-documents').value=doc?.id||'';
    $('#midi-doc-page').textContent=`${page+1}/${pages} 页 · ${result.total} 份`;
    $('#midi-doc-prev').disabled=page===0;$('#midi-doc-next').disabled=page+1>=pages;
  }
  async function restore(next=String(window.workbenchProjectId?.()||'')){
    finishPointer();
    if(doc&&dirty>saved){try{await save();}catch{backup();}}
    scopeRevision++;scope=next;doc=null;drag=null;undo=[];redo=[];dirty=saved=0;generationBusy=false;restoreButton($('#midi-generate'));selection.clear();stop();properties();$('.midi-layout').inert=true;audioSource=null;extractJob='';generationJob='';$('#midi-gen-result').replaceChildren();$('#midi-audio-card').classList.add('hidden');$('#midi-extract').disabled=false;const token=scopeRevision;
    const pending=pendingBackups();
    try{const result=await api(`${prefix}/current?project_id=${encodeURIComponent(scope)}`);if(token!==scopeRevision)return;
      let value=result.document;if(!value)value=await post('/documents',{project_id:scope});if(token!==scopeRevision||doc)return;install(value);
      if(pending.length){
        message('发现未保存的本机编辑。恢复会另存副本，不覆盖服务端新版；未恢复的备份继续保留。');
        for(const local of pending){
          const button=document.createElement('button');button.className='ghost';button.type='button';
          button.textContent=pending.length===1?'恢复本机备份':`恢复：${local.document.title}`;
          button.onclick=async()=>{const context=actionContext();try{
            const result=await copyContext(context,()=>clone(local.document));
            if(local.backup_key)localStorage.removeItem(local.backup_key);
            const key=documentBackupKey(context.project,local.document.id),current=readBackup(key);
            if(current?.dirty&&JSON.stringify(current.document)===JSON.stringify(local.document))localStorage.removeItem(key);
            const previous=readBackup(backupKey(context.project));
            if(previous?.dirty&&JSON.stringify(previous.document)===JSON.stringify(local.document))localStorage.removeItem(backupKey(context.project));
            install(result);await loadDocuments();message('本机编辑已恢复为独立副本并保存。');
          }catch(e){message(`恢复失败：${e.message}；备份仍保留。`,true);}};
          $('#midi-message').append(button);
        }
      }
      await loadDocuments();await restoreJobs(token);
    }catch(error){setSave(`载入失败：${error.message}`,true);}
  }
  function renderTracks(){if(!doc)return;$('#midi-tracks').innerHTML='<h3>轨道用途</h3>'+doc.tracks.map(t=>`<div class="midi-track ${t.id===trackId?'active':''}"><button type="button" class="ghost" data-midi-track="${t.id}" title="${escapeHtml(t.name)}">${escapeHtml(t.name)} · ${t.notes.length}</button><select data-midi-role="${t.id}" aria-label="${escapeHtml(t.name)}的用途">${Object.entries(labels).map(([role,label])=>`<option value="${role}" ${t.role===role?'selected':''}>${label}</option>`).join('')}</select><div class="midi-track-flags"><label><input type="checkbox" data-midi-mute="${t.id}" ${t.muted?'checked':''}>静音</label><label><input type="checkbox" data-midi-solo="${t.id}" ${t.solo?'checked':''}>独奏</label></div></div>`).join('');
    root.querySelectorAll('[data-midi-track]').forEach(b=>b.onclick=()=>chooseTrack(b.dataset.midiTrack));
    root.querySelectorAll('[data-midi-role]').forEach(input=>input.onchange=()=>{beforeEdit();doc.tracks.find(t=>t.id===input.dataset.midiRole).role=input.value;if(input.value==='drums')doc.tracks.find(t=>t.id===input.dataset.midiRole).channel=9;doc.mapping_confirmed=false;changed();renderTracks();});
    for(const key of ['mute','solo'])root.querySelectorAll(`[data-midi-${key}]`).forEach(input=>input.onchange=()=>{beforeEdit();doc.tracks.find(t=>t.id===input.dataset[key==='mute'?'midiMute':'midiSolo'])[key==='mute'?'muted':'solo']=input.checked;changed();});
    $('#midi-confirm-mapping').textContent=doc.mapping_confirmed?'轨道用途已确认':'确认轨道用途';
  }
  function chooseTrack(id){
    const track=doc?.tracks.find(t=>t.id===id);if(!track)return;
    trackId=id;selection.clear();
    const low=track.role==='drums'?28:track.notes.length?Math.min(...track.notes.map(n=>n.pitch))-4:48;
    $('#midi-pitch-low').value=Math.max(0,Math.min(92,low));doc.settings.pitch_low=Number($('#midi-pitch-low').value);
    doc.settings.active_track_id=id;changed();renderTracks();properties();draw();
  }
  function downloads(){if(!doc)return;for(const role of ['combined','zip'])$('#midi-download-'+role).href=`${prefix}/documents/${doc.id}/download?version=${doc.version}&role=${role}`;}
  function properties(){const note=currentTrack()?.notes.find(n=>selection.has(n.id));for(const [key,prop]of [['pitch','pitch'],['start','tick'],['duration','duration'],['velocity','velocity']]){const input=$('#midi-note-'+key);input.disabled=!note;input.value=note?(key==='start'||key==='duration'?note[prop]/doc.ppq:note[prop]):'';}$('#midi-undo').disabled=!undo.length;$('#midi-redo').disabled=!redo.length;}
  const canvas=$('#midi-roll'), ctx=canvas.getContext('2d');
  const volumeLabel=document.createElement('label');volumeLabel.innerHTML='试听音量<input id="midi-volume" type="number" min="0" max="100" value="70" aria-label="试听音量百分比">';$('.midi-transport').append(volumeLabel);
  $('.midi-roll-help').append(document.createTextNode(' 若无声，先检查试听音量与系统音频输出设备。'));
  $('#midi-volume').onchange=()=>{if(doc){doc.settings.volume=Math.max(0,Math.min(100,Number($('#midi-volume').value)||0));$('#midi-volume').value=doc.settings.volume;changed();}};
  function geometry(){const width=canvas.clientWidth,height=canvas.clientHeight;return{width,height,key:48,top:24,row:(height-24)/36,beat:48*Number($('#midi-zoom').value||1),low:Math.max(0,Math.min(92,Number($('#midi-pitch-low').value)))};}
  function rect(n,g){return{x:g.key+(n.tick/doc.ppq-viewBeat)*g.beat,y:g.top+(g.low+35-n.pitch)*g.row,w:Math.max(3,n.duration/doc.ppq*g.beat),h:g.row-1};}
  function draw(){if(!doc||!canvas.clientWidth)return;const g=geometry(),dpr=Math.min(2,devicePixelRatio||1);if(canvas.width!==Math.round(g.width*dpr)||canvas.height!==Math.round(g.height*dpr)){canvas.width=Math.round(g.width*dpr);canvas.height=Math.round(g.height*dpr);}ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,g.width,g.height);ctx.font='11px sans-serif';
    for(let i=0;i<36;i++){const p=g.low+35-i,y=g.top+i*g.row,black=[1,3,6,8,10].includes(p%12);ctx.fillStyle=black?'#f0f2f9':'#fafbff';ctx.fillRect(g.key,y,g.width-g.key,g.row);ctx.fillStyle=black?'#dde2ef':'#fff';ctx.fillRect(0,y,g.key,g.row);ctx.fillStyle='#67738e';ctx.fillText(currentTrack().role==='drums'?(drumNames[p]||String(p)):noteName(p),3,y+g.row*.8);ctx.strokeStyle='#e2e7f1';ctx.beginPath();ctx.moveTo(0,y+g.row);ctx.lineTo(g.width,y+g.row);ctx.stroke();}
    const meter=doc.meters[0],bar=meter.numerator*4/meter.denominator,snap=Number($('#midi-snap').value),step=snap?4/snap:1;
    for(let beat=Math.ceil(viewBeat/step)*step;beat<=viewBeat+(g.width-g.key)/g.beat;beat+=step){const x=g.key+(beat-viewBeat)*g.beat;ctx.strokeStyle=Math.abs(beat/bar-Math.round(beat/bar))<.001?'#a7b2cb':Math.abs(beat-Math.round(beat))<.001?'#ccd3e4':'#e8ecf5';ctx.beginPath();ctx.moveTo(x,g.top);ctx.lineTo(x,g.height);ctx.stroke();if(Math.abs(beat-Math.round(beat))<.001){ctx.fillStyle='#667491';ctx.fillText(String(Math.round(beat)),x+3,16);}}
    for(const n of currentTrack().notes){const r=rect(n,g);if(r.x+r.w<g.key||r.x>g.width||r.y<g.top||r.y>g.height)continue;ctx.save();ctx.beginPath();ctx.rect(g.key,g.top,g.width-g.key,g.height-g.top);ctx.clip();ctx.fillStyle=selection.has(n.id)?'#b54177':currentTrack().role==='drums'?'#4a9c99':'#788be0';ctx.fillRect(r.x,r.y,r.w,r.h);ctx.fillStyle='#fff';if(r.w>22)ctx.fillText(currentTrack().role==='drums'?(drumNames[n.pitch]||n.pitch):noteName(n.pitch),r.x+3,r.y+r.h*.8);ctx.fillStyle='#ffffffaa';ctx.fillRect(r.x+r.w-3,r.y+2,2,Math.max(1,r.h-4));ctx.restore();}
    if(playBeat!==null){const x=g.key+(playBeat-viewBeat)*g.beat;ctx.strokeStyle='#d04879';ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,g.height);ctx.stroke();}
    if(drag?.type==='box'){ctx.fillStyle='#ad4b7722';ctx.strokeStyle='#ad4b77';ctx.fillRect(drag.x,drag.y,drag.cx-drag.x,drag.cy-drag.y);ctx.strokeRect(drag.x,drag.y,drag.cx-drag.x,drag.cy-drag.y);}
  }
  function point(event){const r=canvas.getBoundingClientRect(),g=geometry();return{x:event.clientX-r.left,y:event.clientY-r.top,g};}
  function snapTick(t){const snap=Number($('#midi-snap').value),unit=snap?doc.ppq*4/snap:1;return Math.max(0,Math.round(t/unit)*unit);}
  canvas.onpointerdown=event=>{if(!doc||event.button!==0)return;event.preventDefault();canvas.focus();const p=point(event);if(p.x<p.g.key||p.y<p.g.top)return;const track=currentTrack(),note=[...track.notes].reverse().find(n=>{const r=rect(n,p.g);return p.x>=r.x&&p.x<=r.x+r.w&&p.y>=r.y&&p.y<=r.y+r.h;});
    if(note){if(event.shiftKey&&!selection.has(note.id))selection.add(note.id);else if(!selection.has(note.id))selection=new Set([note.id]);const r=rect(note,p.g);drag={type:p.x>r.x+r.w-7?'resize':'move',x:p.x,y:p.y,original:clone(doc),notes:track.notes.filter(n=>selection.has(n.id)).map(clone)};}
    else if(mode==='select'){if(!event.shiftKey)selection.clear();drag={type:'box',x:p.x,y:p.y,cx:p.x,cy:p.y};}
    else{beforeEdit();const n={id:uid(),tick:snapTick((viewBeat+(p.x-p.g.key)/p.g.beat)*doc.ppq),duration:Math.max(1,Math.round(doc.ppq*4/(Number($('#midi-snap').value)||16))),pitch:Math.max(0,Math.min(127,p.g.low+35-Math.floor((p.y-p.g.top)/p.g.row))),velocity:100};track.notes.push(n);selection=new Set([n.id]);changed();renderTracks();}
    canvas.setPointerCapture(event.pointerId);properties();draw();};
  canvas.onpointermove=event=>{if(!drag)return;const p=point(event);if(drag.type==='box'){drag.cx=p.x;drag.cy=p.y;draw();return;}const dt=(p.x-drag.x)/p.g.beat*doc.ppq,dp=Math.round((drag.y-p.y)/p.g.row),track=currentTrack();let valid=true;const values=drag.notes.map(n=>({...n,tick:drag.type==='move'?snapTick(n.tick+dt):n.tick,duration:drag.type==='resize'?Math.max(1,snapTick(n.tick+n.duration+dt)-n.tick):n.duration,pitch:drag.type==='move'?n.pitch+dp:n.pitch}));if(values.some(n=>n.pitch<0||n.pitch>127))valid=false;if(valid){for(const v of values)Object.assign(track.notes.find(n=>n.id===v.id),v);draw();properties();}};
  function finishPointer(cancel=false){if(!drag)return;if(drag.type==='box'){const a={x:Math.min(drag.x,drag.cx),y:Math.min(drag.y,drag.cy),w:Math.abs(drag.x-drag.cx),h:Math.abs(drag.y-drag.cy)},g=geometry();for(const n of currentTrack().notes){const r=rect(n,g);if(r.x<a.x+a.w&&r.x+r.w>a.x&&r.y<a.y+a.h&&r.y+r.h>a.y)selection.add(n.id);}}else if(cancel){doc=drag.original;}else if(JSON.stringify(doc)!==JSON.stringify(drag.original)){undo.push(drag.original);redo=[];stop();changed();renderTracks();}drag=null;properties();draw();}
  canvas.onpointerup=()=>finishPointer();canvas.onpointercancel=()=>finishPointer(true);
  function remove(){if(!selection.size)return;beforeEdit();currentTrack().notes=currentTrack().notes.filter(n=>!selection.has(n.id));selection.clear();changed();renderTracks();}
  function copy(){clipboard=currentTrack().notes.filter(n=>selection.has(n.id)).map(clone);message(`已复制 ${clipboard.length} 个音符`);}
  function paste(repeat=false){if(!clipboard.length)return;beforeEdit();const first=Math.min(...clipboard.map(n=>n.tick)),end=Math.max(...clipboard.map(n=>n.tick+n.duration));const offset=repeat?end-first:Math.round(viewBeat*doc.ppq)-first;selection.clear();for(const source of clipboard){const n={...source,id:uid(),tick:Math.max(0,source.tick+offset)};currentTrack().notes.push(n);selection.add(n.id);}changed();renderTracks();}
  function historyStep(direction){const from=direction==='undo'?undo:redo,to=direction==='undo'?redo:undo;if(!from.length)return;stop();const identity={id:doc.id,version:doc.version,project_id:doc.project_id,created_at:doc.created_at,updated_at:doc.updated_at,asset_id:doc.asset_id};to.push(clone(doc));doc={...from.pop(),...identity};selection.clear();changed();renderTracks();properties();}
  for(const key of ['pitch','start','duration','velocity'])$('#midi-note-'+key).onchange=event=>{const selected=currentTrack()?.notes.filter(n=>selection.has(n.id))||[],value=Number(event.target.value),prop={pitch:'pitch',start:'tick',duration:'duration',velocity:'velocity'}[key],converted=['start','duration'].includes(key)?Math.round(value*doc.ppq):value;if(!Number.isFinite(converted)||!Number.isInteger(converted)||(key==='pitch'&&(converted<0||converted>127))||(key==='velocity'&&(converted<1||converted>127))||(key==='start'&&converted<0)||(key==='duration'&&converted<1)){message('请输入有效的音高、拍位置、长度或力度',true);properties();return;}beforeEdit();selected.forEach(n=>n[prop]=converted);changed();renderTracks();};
  $('#midi-delete').onclick=remove;$('#midi-copy').onclick=copy;$('#midi-paste').onclick=()=>paste();$('#midi-repeat').onclick=()=>{copy();paste(true);};$('#midi-undo').onclick=()=>historyStep('undo');$('#midi-redo').onclick=()=>historyStep('redo');
  canvas.onkeydown=event=>{if(event.isComposing||event.keyCode===229)return;const mod=event.ctrlKey||event.metaKey;if(event.key==='Delete'||event.key==='Backspace'){event.preventDefault();remove();}else if(mod&&event.key.toLowerCase()==='z'){event.preventDefault();historyStep(event.shiftKey?'redo':'undo');}else if(mod&&event.key.toLowerCase()==='c'){event.preventDefault();copy();}else if(mod&&event.key.toLowerCase()==='v'){event.preventDefault();paste();}else if(event.code==='Space'){event.preventDefault();togglePlay().catch(e=>message(e.message,true));}};
  for(const key of ['draw','select'])$('#midi-'+key+'-mode').onclick=()=>{mode=key;$('#midi-draw-mode').classList.toggle('active',mode==='draw');$('#midi-select-mode').classList.toggle('active',mode==='select');};
  for(const id of ['midi-snap','midi-zoom','midi-pitch-low','midi-view-beat'])$('#'+id).onchange=()=>{if(!doc)return;viewBeat=Math.max(0,Number($('#midi-view-beat').value)||0);$('#midi-pitch-low').value=Math.max(0,Math.min(92,Number($('#midi-pitch-low').value)||0));doc.settings={...doc.settings,snap:Number($('#midi-snap').value),zoom:Number($('#midi-zoom').value),pitch_low:Number($('#midi-pitch-low').value),view_beat:viewBeat};changed();};
  $('#midi-title').oninput=event=>{if(doc){doc.title=event.target.value||'未命名 MIDI';changed();}};
  for(const [id,key] of [['midi-loop-start','loop_start'],['midi-loop-end','loop_end'],['midi-loop','loop'],['midi-metronome','metronome']])$('#'+id).onchange=event=>{if(!doc)return;doc.settings[key]=event.target.type==='checkbox'?event.target.checked:Number(event.target.value);stop();changed();};
  $('#midi-empty-chords').onchange=event=>{if(doc){doc.generation.allow_empty_chords=event.target.checked;changed();}};
  $('#midi-bpm').onchange=event=>{const bpm=Number(event.target.value);if(!doc||bpm<20||bpm>400){message('BPM 应为 20–400',true);return;}beforeEdit();doc.tempos[0].tempo=Math.round(60e6/bpm);changed();message('已调整试听速度和导出速度；音符拍位置不变，原始识别仍保留。');};
  root.querySelectorAll('[data-midi-transpose]').forEach(button=>button.onclick=()=>{const shift=Number(button.dataset.midiTranspose),scopeKey=$('#midi-transpose-scope').value;const tracks=doc.tracks.filter(t=>scopeKey==='selection'?t.id===trackId:t.role==='melody'||scopeKey==='whole'&&t.role==='chord');const notes=tracks.flatMap(t=>t.notes.filter(n=>scopeKey!=='selection'||selection.has(n.id)));if(notes.some(n=>n.pitch+shift<0||n.pitch+shift>127)){message('移调会超出 MIDI 音高 0–127，请缩小范围；原音符保留。',true);return;}beforeEdit();notes.forEach(n=>n.pitch+=shift);changed();});
  $('#midi-add-chord').onclick=()=>{const start=Number($('#midi-chord-start').value),duration=Number($('#midi-chord-duration').value);if(!Number.isFinite(start)||start<0||!Number.isFinite(duration)||duration<=0)return message('请填写有效的和弦起点和长度',true);beforeEdit();let track=doc.tracks.find(t=>t.role==='chord');if(!track){track={id:uid(),name:'和弦',role:'chord',channel:1,program:0,notes:[],events:[]};doc.tracks.push(track);}const intervals={major:[0,4,7],minor:[0,3,7],seventh:[0,4,7,10],maj7:[0,4,7,11],sus4:[0,5,7]}[$('#midi-chord-quality').value],base=60+Number($('#midi-chord-root').value);trackId=track.id;selection.clear();for(const interval of intervals){const n={id:uid(),tick:Math.round(start*doc.ppq),duration:Math.round(duration*doc.ppq),pitch:base+interval,velocity:90};track.notes.push(n);selection.add(n.id);}changed();renderTracks();};
  $('#midi-trim').onclick=()=>{
    if(!doc)return;
    const start=Math.round(Number($('#midi-loop-start').value)*doc.ppq),end=Math.round(Number($('#midi-loop-end').value)*doc.ppq);
    if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start)return message('试听范围无效',true);
    beforeEdit();
    for(const track of doc.tracks){
      track.notes=track.notes.filter(n=>n.tick<end&&n.tick+n.duration>start).map(n=>({...n,tick:Math.max(0,n.tick-start),duration:Math.min(end,n.tick+n.duration)-Math.max(start,n.tick)}));
      const controls=new Map();
      for(const event of [...(track.events||[])].sort((a,b)=>a.tick-b.tick)){
        const msg=event.message;
        if(event.tick>=start)continue;
        if(!event.meta&&['program_change','control_change','pitchwheel','aftertouch','polytouch'].includes(msg.type))
          controls.set(`${msg.type}:${msg.channel}:${msg.control??msg.note??''}`,event);
        else if(event.meta&&msg.type==='key_signature')controls.set('key_signature',event);
      }
      track.events=[...[...controls.values()].sort((a,b)=>a.tick-b.tick).map(e=>({...e,tick:0})),...(track.events||[]).filter(e=>e.tick>=start&&e.tick<end).map(e=>({...e,tick:e.tick-start}))];
    }
    for(const key of ['tempos','meters']){
      const initial=[...doc[key]].reverse().find(e=>e.tick<=start)||doc[key][0];
      doc[key]=[{...initial,tick:0},...doc[key].filter(e=>e.tick>start&&e.tick<end).map(e=>({...e,tick:e.tick-start}))];
    }
    doc.source={...doc.source,trim_origin_ticks:(doc.source?.trim_origin_ticks||0)+start};viewBeat=0;
    $('#midi-view-beat').value=0;$('#midi-loop-start').value=0;$('#midi-loop-end').value=(end-start)/doc.ppq;
    Object.assign(doc.settings,{view_beat:0,loop_start:0,loop_end:(end-start)/doc.ppq});
    changed();renderTracks();message('已裁剪编辑副本并保留起点的控制状态，可撤销；原始素材仍保留。');
  };
  $('#midi-confirm-mapping').onclick=()=>{beforeEdit();doc.mapping_confirmed=true;changed();renderTracks();};

  function secondsAt(tick){let time=0,previous=0,tempo=doc.tempos[0].tempo;for(const e of doc.tempos){if(e.tick>tick)break;time+=(e.tick-previous)/doc.ppq*tempo/1e6;previous=e.tick;tempo=e.tempo;}return time+(tick-previous)/doc.ppq*tempo/1e6;}
  function tickAt(seconds){let time=0,previous=0,tempo=doc.tempos[0].tempo;for(const e of doc.tempos){const next=time+(e.tick-previous)/doc.ppq*tempo/1e6;if(next>seconds)return previous+(seconds-time)*1e6/tempo*doc.ppq;time=next;previous=e.tick;tempo=e.tempo;}return previous+(seconds-time)*1e6/tempo*doc.ppq;}
  function voice(pitch,velocity,when,duration,drum=false){const gain=audioContext.createGain();gain.connect(audioContext.destination);const volume=Math.max(0,Math.min(100,Number($('#midi-volume').value)))/100;const level=Math.max(.0001,Math.min(.14,velocity/127*.1)*volume);gain.gain.setValueAtTime(.0001,when);gain.gain.exponentialRampToValueAtTime(level,when+.006);gain.gain.exponentialRampToValueAtTime(.0001,when+Math.max(.02,duration));let source;if(drum){const length=Math.max(1,Math.floor(audioContext.sampleRate*Math.min(.3,duration))),buffer=audioContext.createBuffer(1,length,audioContext.sampleRate),data=buffer.getChannelData(0);for(let i=0;i<length;i++)data[i]=pitch<=36?Math.sin(2*Math.PI*(90-i/length*50)*i/audioContext.sampleRate):(Math.random()*2-1);source=audioContext.createBufferSource();source.buffer=buffer;}else{source=audioContext.createOscillator();source.type='triangle';source.frequency.value=440*2**((pitch-69)/12);}source.connect(gain);voices.add(source);source.onended=()=>{voices.delete(source);source.disconnect();gain.disconnect();};source.start(when);source.stop(when+Math.max(.03,duration)+.02);}
  function stop(reset=true){clearInterval(playTimer);playTimer=null;for(const v of voices){try{v.stop();}catch{}}voices.clear();if(reset)pausedBeat=null;playBeat=null;$('#midi-play').textContent='▶ 试听';draw();}
  async function togglePlay(){if(playTimer){const elapsed=audioContext.currentTime-playStart;pausedBeat=tickAt(secondsAt(playStartBeat*doc.ppq)+elapsed)/doc.ppq;stop(false);$('#midi-play').textContent='▶ 继续';return;}if(!doc)return;audioContext ||=new(window.AudioContext||window.webkitAudioContext)();await audioContext.resume();const start=pausedBeat??Number($('#midi-loop-start').value),end=Number($('#midi-loop-end').value);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start||start<0)throw new Error('试听起止范围无效');playStart=audioContext.currentTime+.05;playStartBeat=start;let scheduledUntil=playStart,lastClick=-1;for(const track of doc.tracks){if(track.muted||doc.tracks.some(t=>t.solo)&&!track.solo)continue;for(const note of track.notes){if(note.tick<start*doc.ppq&&note.tick+note.duration>start*doc.ppq)voice(note.pitch,note.velocity,playStart,Math.min(secondsAt(note.tick+note.duration),secondsAt(end*doc.ppq))-secondsAt(start*doc.ppq),track.role==='drums');}}$('#midi-play').textContent='Ⅱ 暂停';const solo=doc.tracks.some(t=>t.solo),tracks=doc.tracks.filter(t=>!t.muted&&(!solo||t.solo));const loopTime=secondsAt(end*doc.ppq)-secondsAt(start*doc.ppq);if(loopTime<=0)throw new Error('试听范围无效');
    playTimer=setInterval(()=>{const now=audioContext.currentTime,offset=now-playStart;if(offset>loopTime+.1){if($('#midi-loop').checked){stop();pausedBeat=start;togglePlay().catch(e=>message(e.message,true));}else stop();return;}const until=Math.min(playStart+loopTime,now+.18);for(const track of tracks)for(const note of track.notes){const onset=playStart+secondsAt(note.tick)-secondsAt(start*doc.ppq),finish=playStart+secondsAt(note.tick+note.duration)-secondsAt(start*doc.ppq);if(onset>=scheduledUntil&&onset<until&&onset>=playStart&&note.tick/doc.ppq<end)voice(note.pitch,note.velocity,Math.max(now+.002,onset),Math.max(.005,Math.min(finish,playStart+loopTime)-Math.max(now+.002,onset)),track.role==='drums');}if($('#midi-metronome').checked){for(let beat=Math.ceil(start);beat<end;beat++){const onset=playStart+secondsAt(beat*doc.ppq)-secondsAt(start*doc.ppq);if(beat>lastClick&&onset>=scheduledUntil&&onset<until){voice(beat%4?84:96,45,Math.max(now+.002,onset),.035);lastClick=beat;}}}scheduledUntil=until;playBeat=tickAt(secondsAt(start*doc.ppq)+Math.max(0,offset))/doc.ppq;draw();},40);
  }
  $('#midi-play').onclick=()=>togglePlay().catch(e=>message(e.message,true));$('#midi-stop').onclick=()=>stop();
  const observer=new MutationObserver(()=>{if(!$('#midi').classList.contains('active'))stop();else draw();});observer.observe($('#midi'),{attributes:true,attributeFilter:['class']});new ResizeObserver(draw).observe(canvas);document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('beforeunload',()=>{if(doc&&dirty>saved)backup();stop();});
  root.querySelectorAll('[data-midi-view]').forEach(b=>b.onclick=()=>{$('.midi-layout').dataset.view=b.dataset.midiView;draw();});

  $('#midi-save').onclick=()=>save().then(()=>loadDocuments()).catch(e=>message(e.message,true));$('#midi-reload').onclick=()=>restore();
  $('#midi-archive').onclick=async()=>{
    const context=actionContext();try{
      if(!doc)return;
      if(!await uiConfirm('归档后从文档列表收起，可以恢复。已保存 MIDI、歌曲和生成输入继续保留。','归档当前 MIDI','归档'))return;
      await saveContext(context);await postContext(context,`/documents/${doc.id}/archive`);
      await restore(context.project);message('文档已归档，可在“查看归档文档”中恢复。');
    }catch(e){message(e.message,true);}
  };
  $('#midi-open-archive').onclick=async()=>{
    const context=actionContext(),dialog=document.createElement('dialog');dialog.className='midi-archive-dialog';
    dialog.innerHTML='<form method="dialog"><h3>归档的 MIDI 文档</h3><div class="midi-archive-list"></div><div class="toolbar"><button class="ghost" type="button" data-archive-prev>上一页</button><span class="meta" data-archive-page></span><button class="ghost" type="button" data-archive-next>下一页</button><button class="ghost">关闭</button></div><p class="meta" role="status" data-archive-error></p></form>';
    let page=0;
    const show=async next=>{try{
      guard(context);const result=await api(`${prefix}/documents?project_id=${encodeURIComponent(context.project)}&archived=1&limit=20&offset=${next*20}`);guard(context);
      page=next;const pages=Math.max(1,Math.ceil(result.total/20));
      dialog.querySelector('.midi-archive-list').innerHTML=result.documents.map(item=>`<div class="midi-archive-entry"><span>${escapeHtml(item.title)} · v${item.version}</span><button type="button" class="ghost" data-restore-document="${item.id}">恢复并打开</button></div>`).join('')||'<p class="meta">没有归档的文档。</p>';
      dialog.querySelector('[data-archive-page]').textContent=`${page+1}/${pages} 页 · ${result.total} 份`;
      dialog.querySelector('[data-archive-prev]').disabled=page===0;dialog.querySelector('[data-archive-next]').disabled=page+1>=pages;
      dialog.querySelectorAll('[data-restore-document]').forEach(button=>button.onclick=async()=>{try{
        await saveContext(context);const result=await postContext(context,`/documents/${button.dataset.restoreDocument}/restore`);
        install(result.document);dialog.close();await loadDocuments();message('归档文档已恢复，继续编辑。');
      }catch(e){dialog.querySelector('[data-archive-error]').textContent=e.message;}});
    }catch(e){dialog.querySelector('[data-archive-error]').textContent=e.message;}};
    dialog.querySelector('[data-archive-prev]').onclick=()=>show(Math.max(0,page-1));dialog.querySelector('[data-archive-next]').onclick=()=>show(page+1);
    document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();await show(0);
  };
  $('#midi-clear-cache').onclick=async()=>{try{
    const info=await api(`${prefix}/cache`);
    if(!await uiConfirm(`${info.description}\n可清理 ${info.files} 个缓存文件，约 ${(info.bytes/1024/1024).toFixed(1)} MiB。`,'清理 MIDI 识别缓存','清理缓存'))return;
    const result=await post('/cache/clear',{confirmed:true});message(`已清理 ${result.removed} 个缓存文件${result.skipped?`，${result.skipped} 个正在使用，暂时保留`:''}；编辑文档和作品均保留。`);
  }catch(e){message(e.message,true);}};
  $('#midi-save-copy').onclick=async()=>{const context=actionContext();try{if(!doc)return;install(await copyContext(context,()=>({...clone(doc),title:doc.title.slice(0,190)+' · 副本'})));await loadDocuments();}catch(e){if(context.revision===scopeRevision)message(e.message,true);}};
  $('#midi-new').onclick=async()=>{const context=actionContext();try{await saveContext(context);install(await postContext(context,'/documents'));await loadDocuments();}catch(e){if(context.revision===scopeRevision)message(e.message,true);}};
  $('#midi-documents').onchange=async event=>{const context=actionContext(),id=event.target.value;try{await saveContext(context);install(await postContext(context,`/documents/${id}/select`));}catch(e){if(context.revision===scopeRevision)message(e.message,true);}};
  $('#midi-publish').onclick=async()=>{try{const context=actionContext();await saveContext(context);await postContext(context,`/documents/${doc.id}/publish`,{version:doc.version});message('此版多轨 MIDI 已保存到资产库'+(scope?'和当前项目':''));await window.refreshWorkbenchProject?.();}catch(e){message(e.message,true);}};
  root.addEventListener('click',async event=>{const a=event.target.closest('a[download]');if(!a||!a.href.includes(prefix)||dirty<=saved)return;event.preventDefault();const context=actionContext(),url=new URL(a.href);try{await saveContext(context);url.searchParams.set('version',doc.version);a.href=url.href;a.click();}catch(e){if(context.revision===scopeRevision)message(e.message,true);}});
  $('#midi-import').onclick=()=>$('#midi-upload').click();$('#midi-upload').onchange=async event=>{const file=event.target.files[0],context=actionContext();if(!file)return;try{await saveContext(context);const uploaded=await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});guard(context);install(await postContext(context,'/import',{source_path:uploaded.path,title:file.name}));message('已导入。请选择各轨道用途，再点击“确认轨道用途”。原 MIDI 保留。');await loadDocuments();}catch(e){if(context.revision===scopeRevision)message(e.message,true);}finally{event.target.value='';}};
  $('#midi-audio').onclick=()=>$('#midi-audio-upload').click();$('#midi-audio-upload').onchange=async event=>{const file=event.target.files[0],context=actionContext();if(!file)return;try{const uploaded=await api(`/api/uploads?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});guard(context);await stageAudio(context,{source_path:uploaded.path,title:file.name});}catch(e){if(context.revision===scopeRevision)message(e.message,true);}finally{event.target.value='';}};
  async function stageAudio(context,source){const result=await postContext(context,'/source',source);setAudio(result,result.url);doc.settings.audio_source=result;changed();}
  let objectUrl='';function setAudio(source,url){if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=url.startsWith('blob:')?url:'';audioSource=source;$('#midi-audio-card').classList.remove('hidden');$('#midi-source-name').textContent=source.title;$('#midi-source-player').src=url;$('#midi-clip-start').value=0;$('#midi-clip-end').value='';$('#midi-extract-progress').textContent='先试听原曲，再确认整首或提取范围。';$('#midi-extract-tracks').innerHTML='';}
  $('#midi-source-player').onloadedmetadata=()=>{const duration=$('#midi-source-player').duration;$('#midi-source-name').textContent=`${audioSource?.title||'原始歌曲'}${Number.isFinite(duration)?` · ${duration.toFixed(1)} 秒`:''}`;};
  for(const [id,key] of [['midi-clip-start','clip_start'],['midi-clip-end','clip_end'],['midi-extract-bpm','extract_bpm']])$('#'+id).onchange=event=>{if(doc){doc.settings[key]=event.target.value;changed();}};
  async function pollExtract(id,token){
    if(token!==scopeRevision)return;
    extractJob=id;$('#midi-extract').disabled=true;$('#midi-cancel-extract').classList.remove('hidden');
    try{while(token===scopeRevision){
      const job=await api(`/api/jobs/${id}`);if(token!==scopeRevision)return;
      const stage={queued:'排队等待',midi_decode:'解码音乐',midi_tempo:'估计 BPM',midi_notes:'识别旋律与鼓组',midi_chords:'识别和弦',midi_export:'保存三轨 MIDI',cancelling:'正在取消'}[job.stage]||job.stage;
      $('#midi-extract-progress').textContent=`${stage}${job.total?` · ${job.completed||0}/${job.total}`:''} · 已用 ${Math.round((Date.now()/1000-job.created_at))} 秒`;
      if(TERMINAL.has(job.status)){
        const resultId=job.result?.midi_document_id;
        const untouched=doc?.id===job.result?.editor_document_id&&doc?.version===job.result?.editor_document_version&&dirty===saved;
        if(resultId&&untouched){const context=actionContext();install(await postContext(context,`/documents/${resultId}/select`));await loadDocuments();}
        $('#midi-extract-progress').textContent=job.status==='complete'?(job.result?.partial?'部分完成：成功轨已保留，失败轨见说明':'提取完成，可试听、编辑和下载'):`${stageLabel(job.status)}：${job.error||'成功阶段已保留，可继续编辑'}`;
        if(resultId&&!untouched){
          $('#midi-extract-progress').append(document.createTextNode('；当前编辑保留，结果已单独保存。'));
          const button=document.createElement('button');button.type='button';button.className='ghost';button.textContent='打开提取结果';
          button.onclick=async()=>{const context=actionContext();try{await saveContext(context);install(await postContext(context,`/documents/${resultId}/select`));await loadDocuments();button.remove();}catch(e){if(token===scopeRevision)message(e.message,true);}};
          $('#midi-extract-progress').append(button);
        }
        break;
      }
      await new Promise(r=>setTimeout(r,1000));
    }}catch(e){if(token===scopeRevision)$('#midi-extract-progress').textContent=e.message;}
    finally{if(token===scopeRevision){$('#midi-extract').disabled=false;$('#midi-cancel-extract').classList.add('hidden');extractJob='';}}
  }
  $('#midi-extract').onclick=async()=>{const token=scopeRevision,context=actionContext(),source=clone(audioSource);try{if(!source)throw new Error('先上传音乐或从资产库选择');await saveContext(context);const request={...source,project_id:context.project,editor_document_id:doc.id,editor_document_version:doc.version,clip_start:Number($('#midi-clip-start').value)||0,clip_end:$('#midi-clip-end').value?Number($('#midi-clip-end').value):null,bpm:$('#midi-extract-bpm').value?Number($('#midi-extract-bpm').value):null};$('#midi-extract').disabled=true;const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'midi_extract',request,result_panel:'midi',source:'webui',client_request_id:crypto.randomUUID()})});try{localStorage.setItem(`yue2:midi-extract:${context.project}`,job.id);}catch{}await pollExtract(job.id,token);}catch(e){if(token===scopeRevision){$('#midi-extract').disabled=false;$('#midi-extract-progress').textContent=e.message;}}};
  $('#midi-cancel-extract').onclick=async()=>{if(extractJob)await api(`/api/jobs/${extractJob}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(e=>message(e.message,true));};
  function renderExtractTracks(){if(!doc?.extraction)return;$('#midi-audio-card').classList.remove('hidden');const states=doc.extraction.tracks||{};$('#midi-extract-tracks').innerHTML=['melody','chord','drums'].map(role=>{const state=states[role]||{},count=doc.tracks.filter(t=>t.role===role).reduce((s,t)=>s+t.notes.length,0),failed=state.status==='failed',pending=state.status==='pending';return `<div class="midi-track-result"><h4>${labels[role]} MIDI</h4><p class="meta">${failed?'识别失败':pending?'尚未识别':count?`${count} 个音符${state.status==='edited'?' · 已手工编辑':''}`:'没有识别到音符（空轨）'}</p>${failed?`<p class="meta">${escapeHtml(state.error||'')}</p>`:''}<div class="toolbar">${!failed&&!pending?`<a class="ghost" href="${prefix}/documents/${doc.id}/download?version=${doc.version}&role=${role}" download="${role}.mid">下载</a>`:''}<button class="ghost" type="button" data-midi-edit-role="${role}">编辑 / 试听</button></div></div>`;}).join('');root.querySelectorAll('[data-midi-edit-role]').forEach(b=>b.onclick=()=>{chooseTrack(doc.tracks.find(t=>t.role===b.dataset.midiEditRole)?.id||trackId);$('.midi-layout').dataset.view='edit';canvas.focus();});}
  $('#midi-correct-bpm').onclick=async()=>{try{if(!await uiConfirm('按原始识别时间重新定位，会替换当前手工编辑的识别轨道。需要保留编辑时请先另存副本；原音频仍保留。','校正识别 BPM','重新定位'))return;const context=actionContext();await saveContext(context);const value=Number($('#midi-bpm').value);install(await postContext(context,`/documents/${doc.id}/correct-bpm`,{version:doc.version,bpm:value}));message('已按原始秒级识别校正 BPM，没有重新调用 GPU。');}catch(e){message(e.message,true);}};
  root.querySelectorAll('[data-midi-gen]').forEach(input=>input.addEventListener('input',()=>{if(!doc)return;doc.generation ||= {};doc.generation[input.dataset.midiGen]=input.type==='number'?Number(input.value):input.value;changed();}));
  $('#midi-generation-form').onsubmit=async event=>{
    event.preventDefault();if(generationBusy)return;
    const token=scopeRevision,button=$('#midi-generate'),projectScope=scope;
    generationBusy=true;readiness();
    try{
      const context=actionContext();await saveContext(context);const current=clone(doc);
      const check=await post(`/documents/${current.id}/check`,{allow_empty_chords:$('#midi-empty-chords').checked});
      if(!check.ready)throw new Error(check.errors.join('；'));
      if(check.warnings.length){const ok=await studioDialog({title:'确认生成副本',message:check.warnings.join('\n'),confirmLabel:'确认生成',cancelLabel:'返回编辑'});if(!ok)return;}
      if(token!==scopeRevision)throw new Error('项目已切换，请在当前项目重新确认');
      const snapshot=await post(`/documents/${current.id}/snapshot`,{version:current.version,allow_empty_chords:$('#midi-empty-chords').checked,acknowledge_projection:true});
      if(token!==scopeRevision)throw new Error('项目已切换，固定 MIDI 仍保留；返回原项目后可生成。');
      const request={...current.generation,source_mode:'midi',style_mode:'custom',source:snapshot.source,project_id:projectScope,midi_document_id:snapshot.document_id,midi_document_version:snapshot.document_version,midi_snapshot_id:snapshot.snapshot_id};
      const job=await submit('mulacover_remix',request,$('#midi-gen-result'),button,projectScope);
      if(token!==scopeRevision)return;generationJob=job.id;
      try{localStorage.setItem(`yue2:midi-generation:${scope}`,job.id);}catch{}
      renderJob(job,$('#midi-gen-result'));message(`已用 MIDI v${snapshot.document_version} 生成，后续编辑不改变本次作品。`);
    }catch(e){if(token===scopeRevision)message(e.message,true);}
    finally{if(token===scopeRevision){generationBusy=false;readiness();}}
  };
  async function restoreJobs(token){
    let jobs=[];
    try{const response=await api(`/api/jobs?limit=100&project_id=${encodeURIComponent(scope||'__global__')}`);if(token!==scopeRevision)return;jobs=response.jobs.filter(job=>String(job.project_id||'')===scope);}catch{}
    for(const type of ['extract','generation']){
      const candidates=jobs.filter(job=>type==='extract'?job.kind==='midi_extract':job.kind==='mulacover_remix'&&resultPanel(job)==='midi');
      const latest=candidates.find(job=>!TERMINAL.has(job.status))||candidates[0];
      let id=latest?.id||'';try{id ||=localStorage.getItem(`yue2:midi-${type}:${scope}`)||'';}catch{}
      if(!id)continue;
      try{
        const job=latest||await api(`/api/jobs/${id}`);if(token!==scopeRevision)return;
        if(type==='extract'&&!TERMINAL.has(job.status)){void pollExtract(id,token);}
        else if(type==='generation'){
          generationJob=id;
          if(!TERMINAL.has(job.status)){
            generationBusy=true;bindButton($('#midi-generate'),job);readiness();
            void waitForJob(id,$('#midi-gen-result')).catch(error=>{if(token===scopeRevision)message(error.message,true);}).finally(()=>{if(token===scopeRevision){generationBusy=false;readiness();}});
          }
        }
      }catch{}
    }
  }
  window.midiOpenAsset=async(assetId)=>{const context=actionContext();const asset=await api(`/api/workbench/assets/${assetId}`);await saveContext(context);if(asset.metadata?.midi_document_id){const version=Number(asset.metadata.document_version),value=await api(`${prefix}/documents/${asset.metadata.midi_document_id}${version?`?version=${version}`:''}`),latest=await api(`${prefix}/documents/${value.id}`);if(value.project_id===scope&&value.version===latest.version&&!latest.archived_at)install(await postContext(context,`/documents/${value.id}/select`));else install(await postContext(context,'/documents',{document:{...value,title:value.title.slice(0,182)+' · 固定版本副本'}}));}else install(await postContext(context,'/import',{asset_id:assetId,revision_id:asset.current_revision_id}));$('.tab[data-tab="midi"]').click();await loadDocuments();};
  window.midiOpenAudio=async(assetId)=>{
    const context=actionContext(),asset=await api(`/api/workbench/assets/${assetId}`);
    guard(context);await stageAudio(context,{source_path:{$asset:assetId,revision_id:asset.current_revision_id},title:asset.title});
    $('.tab[data-tab="midi"]').click();
  };
  $('#midi-choose-asset').onclick=async()=>{const result=await api('/api/workbench/assets?limit=100');const items=result.assets.filter(a=>['song','work','vocal','instrumental','reference_voice','midi'].includes(a.kind));const dialog=document.createElement('dialog');dialog.innerHTML=`<form method="dialog"><h3>选择音频或 MIDI</h3><p class="meta">音频进入提取，MIDI 直接进入编辑器。此处显示最近 100 项，可到资产库搜索更多。</p><div class="asset-use-actions">${items.map(a=>`<button type="button" class="ghost" data-midi-asset="${a.id}">${escapeHtml(a.title)} · ${a.kind==='midi'?'MIDI':'音频'}</button>`).join('')||'<p>尚无可用素材，请上传音乐或导入 MIDI。</p>'}</div><button class="ghost">关闭</button></form>`;document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.querySelectorAll('[data-midi-asset]').forEach(b=>b.onclick=async()=>{try{const asset=items.find(a=>a.id===b.dataset.midiAsset);if(asset.kind==='midi')await window.midiOpenAsset(asset.id);else await window.midiOpenAudio(asset.id);dialog.close();}catch(e){message(e.message,true);}});dialog.showModal();};
  window.midiSwitchProject=async next=>{await restore(String(next||''));};
  window.midiOpenExtractionResult=async jobId=>{
    const context=actionContext();try{
      await saveContext(context);const job=await api(`/api/jobs/${jobId}`);guard(context);
      if(!job.result?.midi_document_id)throw new Error('这个任务还没有已保存的 MIDI 文档');
      const id=job.result.midi_document_id,version=job.result.document_version;
      const value=await api(`${prefix}/documents/${id}${version?`?version=${Number(version)}`:''}`),latest=await api(`${prefix}/documents/${id}`);guard(context);
      if(value.project_id===context.project&&value.version===latest.version&&!latest.archived_at)install(await postContext(context,`/documents/${id}/select`));
      else install(await postContext(context,'/documents',{document:{...value,title:value.title.slice(0,180)+' · 任务版本副本'}}));
      $('.tab[data-tab="midi"]').click();await loadDocuments();message('已打开任务保存的 MIDI；可编辑、试听和下载可用轨道。');
    }catch(e){if(context.revision===scopeRevision)await uiAlert(e.message);}
  };
  window.midiOpenJobCondition=async(jobId)=>{
    const context=actionContext();try{await saveContext(context);const job=await api(`/api/jobs/${jobId}`),sources={};guard(context);
      for(const [role,key] of [['melody','melody_midi'],['chord','chord_midi'],['drums','drum_midi']]){const rel=relativeAudio(job,job.result?.[key]);if(rel)sources[role]={$job_file:{job_id:jobId,relative:rel}};}
      install(await postContext(context,'/import-group',{sources,title:`编曲条件 · ${jobId}`}));$('.tab[data-tab="midi"]').click();await loadDocuments();
    }catch(e){message(e.message,true);await uiAlert(e.message);}
  };
  window.midiOpenJobAudio=async(jobId,relative)=>{
    const context=actionContext();await stageAudio(context,{source_path:{$job_file:{job_id:jobId,relative}},title:`作品 · ${jobId}`});$('.tab[data-tab="midi"]').click();
  };
  window.midiOpenRemix=async()=>{
    const context=actionContext();try{const sourceMode=$('#remix-source-mode').value;
      if(sourceMode==='audio'){const input=$('#remix-file'),ref=localInputReference(input)||await inputSourceValue(input),url=input.dataset.localPreview||(input.files[0]?URL.createObjectURL(input.files[0]):'');guard(context);if(ref)await stageAudio(context,{source_path:ref,title:input.dataset.localName||input.files[0]?.name||'重新编曲参考音乐'});$('.tab[data-tab="midi"]').click();if(!ref)$('#midi-audio-upload').click();}
      else{const sources={};for(const [role,id] of [['melody','remix-melody-midi'],['chord','remix-chord-midi'],['drums','remix-drum-midi']]){const input=$('#'+id),value=localInputReference(input)||(input.files.length?await inputSourceValue(input):null);if(value)sources[role]=value;}await saveContext(context);install(await postContext(context,'/import-group',{sources,title:'重新编曲 MIDI'}));$('.tab[data-tab="midi"]').click();await loadDocuments();}
    }catch(e){await uiAlert(e.message);}
  };
  window.midiEditorState=()=>clone(doc); // Used by integration verification, no private data.
  restore();checkModels();setInterval(checkModels,15000);
})();
