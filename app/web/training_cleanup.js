/* Training cache disposal is separate from asset/model and task cleanup. */
(() => {
  const panel = document.createElement('section');
  panel.className = 'result-card'; panel.id = 'training-cleanup-manager';
  panel.innerHTML = `<div class="canvas-head"><div><small>磁盘空间管理</small><h3>清理训练记录与快照</h3></div><button id="training-cleanup-toggle" class="ghost" type="button" aria-expanded="false" aria-controls="training-cleanup-content">管理 / 清理</button></div><p class="meta">删除不用的训练记录、预处理缓存和检查点。原始音频、导出文件及已保存的模型会保留。</p><div id="training-cleanup-content" class="hidden"><div class="toolbar"><label>查看<select id="training-cleanup-kind"><option value="runs">训练记录与缓存</option><option value="snapshots">素材快照</option></select></label><button id="training-cleanup-refresh" class="ghost" type="button">刷新</button><button id="training-cleanup-clear" class="ghost" type="button">取消选择</button><button id="training-cleanup-selected" class="danger" type="button" disabled>清理所选</button></div><p class="meta">每页 10 项。暂停的训练需明确放弃继续训练；文件被占用时可再次选择“待重试”记录清理。保留的模型可在资产库中找到。</p><label class="check"><input id="training-cleanup-select-page" type="checkbox">选择本页</label><div id="training-cleanup-list" aria-live="polite"></div><nav class="asset-pagination" aria-label="训练清理分页"><button id="training-cleanup-prev" class="ghost compact" type="button">上一页</button><span id="training-cleanup-pages" class="meta"></span><button id="training-cleanup-next" class="ghost compact" type="button">下一页</button></nav></div><p id="training-cleanup-status" class="meta" role="status"></p>`;
  $('#training-progress-card').after(panel);
  const selected = new Set();
  let offset = 0, total = 0, items = [], revision = 0, busy = false;
  const size = bytes => `${(Number(bytes || 0) / 1048576).toFixed(1)} MB`;
  function controls() {
    $('#training-cleanup-selected').disabled = busy || !selected.size;
    $('#training-cleanup-selected').textContent = `清理所选${selected.size ? `（${selected.size} 项）` : ''}`;
    for (const id of ['kind', 'refresh', 'select-page', 'clear']) $('#training-cleanup-' + id).disabled = busy;
    $('#training-cleanup-prev').disabled = busy || offset === 0;
    $('#training-cleanup-next').disabled = busy || offset + 10 >= total;
    $$('[data-select-training-cleanup]').forEach(input => input.disabled = busy);
  }
  async function load() {
    const current = ++revision, kind = $('#training-cleanup-kind').value;
    $('#training-cleanup-list').textContent = '正在读取记录与缓存大小…';
    try {
      const result = await api(`/api/workbench/training-cleanup?kind=${kind}&limit=10&offset=${offset}`);
      if (current !== revision) return;
      total = result.total;
      if (offset && offset >= total) { offset = Math.max(0, Math.floor((total - 1) / 10) * 10); return load(); }
      items = result.items;
      $('#training-cleanup-pages').textContent = `第 ${Math.floor(offset / 10) + 1} / ${Math.max(1, Math.ceil(total / 10))} 页 · ${total} 项`;
      $('#training-cleanup-list').innerHTML = items.map(item => `<article class="training-cleanup-row"><label class="check"><input type="checkbox" data-select-training-cleanup="${escapeHtml(item.id)}" ${selected.has(item.id) ? 'checked' : ''}><b>${escapeHtml(item.title)}</b></label><p class="meta">${kind === 'runs' ? `${item.cleanup_pending ? '待重试清理' : escapeHtml(stageLabel(item.state))} · ${Number(item.steps || 0)} 步 · ${Number(item.checkpoints || 0)} 个检查点 · 缓存 ${size(item.bytes)}` : `关联 ${Number(item.run_count)} 项训练${item.run_count ? '（需要先清理关联记录）' : '（无关联训练）'}`} · ${new Date(Number(item.created_at) * 1000).toLocaleString('zh-CN', {hour12:false})}</p>${item.path ? `<details><summary>查看缓存文件夹位置</summary><p class="training-model-path">${escapeHtml(item.path)}</p></details>` : ''}${item.path_error ? `<p class="error">${escapeHtml(item.path_error)}</p>` : ''}</article>`).join('') || '<p class="meta">没有需要清理的记录。</p>';
      $$('[data-select-training-cleanup]').forEach(input => input.onchange = () => { if (input.checked && selected.size < 500) selected.add(input.dataset.selectTrainingCleanup); else if (input.checked) input.checked = false; else selected.delete(input.dataset.selectTrainingCleanup); updatePageSelection(); controls(); });
      updatePageSelection(); controls();
    } catch (error) { if (current === revision) { $('#training-cleanup-list').textContent = error.message; controls(); } }
  }
  function updatePageSelection() {
    const checkbox = $('#training-cleanup-select-page'), count = items.filter(item => selected.has(item.id)).length;
    checkbox.checked = Boolean(items.length && count === items.length); checkbox.indeterminate = count > 0 && count < items.length;
  }
  $('#training-cleanup-toggle').onclick = () => {
    const content = $('#training-cleanup-content'), open = content.classList.contains('hidden');
    content.classList.toggle('hidden', !open); $('#training-cleanup-toggle').setAttribute('aria-expanded', String(open));
    if (open && !busy) load();
  };
  $('#training-cleanup-kind').onchange = () => { selected.clear(); offset = 0; load(); };
  $('#training-cleanup-refresh').onclick = load;
  $('#training-cleanup-clear').onclick = () => { selected.clear(); load(); };
  $('#training-cleanup-prev').onclick = () => { offset = Math.max(0, offset - 10); load(); };
  $('#training-cleanup-next').onclick = () => { offset += 10; load(); };
  $('#training-cleanup-select-page').onchange = event => { items.forEach(item => event.target.checked ? (selected.size < 500 && selected.add(item.id)) : selected.delete(item.id)); load(); };
  $('#training-cleanup-selected').onclick = async () => {
    if (busy || !selected.size) return;
    busy = true; controls();
    const kind = $('#training-cleanup-kind').value;
    const data = {run_ids: kind === 'runs' ? [...selected] : [], snapshot_ids: kind === 'snapshots' ? [...selected] : [], include_unused_snapshots:true};
    const dialog = document.createElement('dialog');
    dialog.id = 'training-cleanup-dialog'; dialog.setAttribute('aria-labelledby', 'training-cleanup-dialog-title');
    dialog.innerHTML = `<form><h3 id="training-cleanup-dialog-title">确认清理训练记录与快照</h3><p>删除后不能恢复训练进度。原始音频、已保存模型、任务的歌曲结果和导出文件会保留；已删除快照将不再保护素材的固定版本。</p>${kind === 'runs' ? '<label class="check"><input name="discard_paused" type="checkbox">放弃所选暂停训练的继续训练能力</label>' : ''}<p data-preview role="status">正在检查引用与可释放空间…</p><div class="toolbar"><button class="ghost" type="button" data-cancel>取消</button><button class="danger" type="submit" data-confirm disabled>确认清理</button></div></form>`;
    document.body.append(dialog); dialog.showModal();
    let previewVersion = 0, approved = null, executing = false;
    const confirm = dialog.querySelector('[data-confirm]'), message = dialog.querySelector('[data-preview]');
    async function preview() {
      const version = ++previewVersion; approved = null; confirm.disabled = true;
      data.discard_paused = Boolean(dialog.querySelector('[name="discard_paused"]')?.checked);
      message.textContent = '正在检查引用与可释放空间…';
      try {
        const result = await api('/api/workbench/training-cleanup/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
        if (version !== previewVersion || !dialog.open) return;
        approved = {...data, run_ids:result.run_ids, snapshot_ids:result.snapshot_ids, include_unused_snapshots:false};
        message.textContent = `可清理 ${result.run_ids.length} 项训练、${result.snapshot_ids.length} 个快照，约释放 ${size(result.bytes)}。\n原始音频和已保存模型保留。`;
        if (result.titles.length) message.textContent += '\n将删除训练：' + result.titles.join('、');
        if (result.snapshot_titles.length) message.textContent += '\n将删除快照：' + result.snapshot_titles.join('、');
        const kept = [...result.skipped, ...result.kept_snapshots];
        if (kept.length) message.textContent += '\n保留：\n' + kept.map(item => `${item.title}：${item.reason}`).join('\n');
        confirm.disabled = !result.run_ids.length && !result.snapshot_ids.length;
      } catch (error) { if (version === previewVersion) message.textContent = `无法安全清理：${error.message}`; }
    }
    dialog.querySelector('[name="discard_paused"]')?.addEventListener('change', preview);
    dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
    dialog.oncancel = event => { if (executing) event.preventDefault(); };
    dialog.onclose = () => { previewVersion++; dialog.remove(); busy = false; controls(); };
    dialog.querySelector('form').onsubmit = async event => {
      event.preventDefault(); if (executing || confirm.disabled || !approved) return;
      executing = true; dialog.querySelectorAll('button,input').forEach(control => control.disabled = true);
      message.textContent = '正在清理缓存，请等待完成…';
      try {
        const result = await api('/api/workbench/training-cleanup/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...approved, confirmed:true})});
        [...result.deleted_runs, ...result.deleted_snapshots].forEach(id => selected.delete(id));
        $('#training-cleanup-status').textContent = `已清理 ${result.deleted_runs.length} 项训练、${result.deleted_snapshots.length} 个快照，释放 ${size(result.released_bytes)}；原始音频与已保存模型保留。` + (result.pending_runs.length ? ` ${result.pending_runs.length} 项待重试清理。` : '') + (result.skipped.length ? ` ${result.skipped.length} 项受保护，已保留。` : '') + (result.errors.length ? ' ' + result.errors.map(item => item.reason).join('；') : '');
        dialog.close();
        await Promise.all([load(), window.refreshWorkbenchTrainingCleanup?.([...result.deleted_runs, ...result.pending_runs])]);
      } catch (error) {
        executing = false; message.textContent = error.message;
        dialog.querySelectorAll('button,input').forEach(control => control.disabled = false);
        await preview();
      }
    };
    await preview();
  };
})();
