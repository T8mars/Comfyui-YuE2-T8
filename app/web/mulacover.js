(() => {
  const form = $('#remix-form');
  if (!form) return;
  const mode = $('#remix-source-mode'), audioInput = $('#remix-file');
  const styleMode = $('#remix-style-mode');
  const melodyInput = $('#remix-melody-midi'), chordInput = $('#remix-chord-midi'), drumInput = $('#remix-drum-midi');
  const button = $('#remix-button'), status = $('#remix-model-status');
  const preview = $('#remix-preview'), player = preview.querySelector('audio');
  let previewUrl = '', modelsReady = false;

  function draftKey() { return `remix-draft:${String(window.workbenchProjectId?.() || '__global__')}`; }
  function saveDraft() {
    const values = {};
    for (const field of form.elements) if (field.name && field.type !== 'file') values[field.name] = field.value;
    savedValue(draftKey(), JSON.stringify(values));
  }
  function restoreDraft() {
    let values = {};
    try { values = JSON.parse(savedValue(draftKey()) || '{}'); } catch {}
    for (const field of form.elements) if (field.name && field.type !== 'file' && values[field.name] !== undefined) field.value = values[field.name];
    if (values.style_mode === undefined && ['topic','genre','instrument','mood'].some(key=>values[key])) styleMode.value = 'custom';
  }
  function switchProjectDraft() {
    for (const input of [audioInput, melodyInput, chordInput, drumInput]) clearLocalInputReference(input);
    form.reset();
    restoreDraft();
    updateStyleMode();
    updateSourceMode();
    updatePreview();
  }
  function sourceReady() {
    return mode.value === 'audio' ? inputHasSource(audioInput) : inputHasSource(melodyInput) && inputHasSource(chordInput);
  }
  function updateStyleMode() {
    const custom = styleMode.value === 'custom';
    for (const label of form.querySelectorAll('[data-remix-style]')) {
      label.classList.toggle('hidden', !custom);
      label.querySelector('input').disabled = !custom;
    }
    $('#remix-style-hint').textContent = custom
      ? '填写至少一项主题、流派、乐器或情绪；这些条件会影响新版本的编曲。'
      : '不额外指定流派、乐器或情绪；参考歌曲提供旋律、和弦与鼓点条件，生成结果仍可能改变原编曲和音色。';
  }
  function updateButtonState() {
    if (!button.dataset.jobId) button.disabled = !modelsReady || !sourceReady();
  }
  function updateSourceMode() {
    const audio = mode.value === 'audio';
    $('#remix-audio-source').classList.toggle('hidden', !audio);
    $('#remix-midi-source').classList.toggle('hidden', audio);
    audioInput.disabled = !audio;
    for (const input of [melodyInput, chordInput, drumInput]) input.disabled = audio;
    melodyInput.required = chordInput.required = !audio;
    saveDraft(); updateButtonState();
  }
  function updatePreview() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = audioInput.files[0], local = localInputReference(audioInput), present = Boolean(file || local);
    preview.classList.toggle('hidden', !present);
    $('#remix-drop-zone').classList.toggle('hidden', present);
    if (file) { previewUrl = URL.createObjectURL(file); player.src = previewUrl; }
    else if (local && audioInput.dataset.localPreview) { player.src = audioInput.dataset.localPreview; player.load(); previewUrl = ''; }
    else { player.removeAttribute('src'); player.load(); previewUrl = ''; }
    updateButtonState();
  }
  async function refreshModelState() {
    try {
      const health = await api('/api/health'), state = health.ready?.mulacover_models;
      modelsReady = Boolean(health.ready?.capabilities?.mulacover);
      if (modelsReady) status.textContent = 'MuLaCover 模型已就绪；生成过程会依次载入转谱、曲风、编曲和解码组件。';
      else {
        const missing = Object.entries(state?.components || {}).filter(([, value]) => !value.ready).map(([name]) => name);
        status.textContent = `MuLaCover 模型未完整安装${missing.length ? `：${missing.join('、')}` : ''}。请在“模型与设置”中确认模型目录。`;
      }
    } catch (error) { status.textContent = `模型状态读取失败：${error.message}`; modelsReady = false; }
    updateButtonState();
  }

  restoreDraft(); updateStyleMode(); updateSourceMode(); updatePreview(); refreshModelState();
  window.mulacoverSaveDraft = saveDraft;
  window.mulacoverRestoreDraft = switchProjectDraft;
  mode.onchange = updateSourceMode;
  styleMode.onchange = () => { updateStyleMode(); saveDraft(); };
  audioInput.onchange = () => { clearLocalInputReference(audioInput); updatePreview(); };
  audioInput.addEventListener('local-source-change', updatePreview);
  for (const input of [melodyInput, chordInput, drumInput]) {
    input.onchange = () => { clearLocalInputReference(input); updateButtonState(); };
    input.addEventListener('local-source-change', updateButtonState);
  }
  $('#remix-replace').onclick = () => audioInput.click();
  form.addEventListener('input', event => { if (event.target.type !== 'file') saveDraft(); });
  window.addEventListener('beforeunload', () => { if (previewUrl) URL.revokeObjectURL(previewUrl); });

  form.onsubmit = async event => {
    event.preventDefault();
    const projectScope = String(window.workbenchProjectId?.() || '');
    try {
      if (!modelsReady) throw new Error('MuLaCover 模型尚未安装完整');
      if (!sourceReady()) throw new Error(mode.value === 'audio' ? '请先选择参考歌曲' : '请同时选择旋律 MIDI 和和弦 MIDI');
      const data = Object.fromEntries(new FormData(form).entries());
      for (const key of ['duration_seconds','semitone_shift','octave_shift','topk']) data[key] = Number(data[key]);
      for (const key of ['cfg_scale','temperature']) data[key] = Number(data[key]);
      data.seed = safeSeed(data.seed); data.decode_seed = safeSeed(data.decode_seed);
      if (mode.value === 'audio') {
        data.source_path = await inputSourceValue(audioInput);
      } else {
        const [melody, chord, drum] = await Promise.all([
          inputSourceValue(melodyInput), inputSourceValue(chordInput), inputSourceValue(drumInput)
        ]);
        data.melody_midi = melody; data.chord_midi = chord;
        if (drum) data.drum_midi = drum;
      }
      saveDraft();
      await submit('mulacover_remix', data, $('#remix-result'), button, projectScope);
    } catch (error) { restoreButton(button); renderScopedFailure($('#remix-result'), error, projectScope); }
  };
  setInterval(refreshModelState, 15000);
})();
