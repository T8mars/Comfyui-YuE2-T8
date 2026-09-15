(() => {
  const form = $('#remix-form');
  if (!form) return;
  const mode = $('#remix-source-mode'), audioInput = $('#remix-file');
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
  }
  function sourceReady() {
    return mode.value === 'audio' ? Boolean(audioInput.files[0]) : Boolean(melodyInput.files[0] && chordInput.files[0]);
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
    const file = audioInput.files[0];
    preview.classList.toggle('hidden', !file);
    $('#remix-drop-zone').classList.toggle('hidden', Boolean(file));
    if (file) { previewUrl = URL.createObjectURL(file); player.src = previewUrl; }
    else { player.removeAttribute('src'); player.load(); previewUrl = ''; }
    updateButtonState();
  }
  async function upload(file) {
    if (!file) return null;
    return api(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file
    });
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

  restoreDraft(); updateSourceMode(); updatePreview(); refreshModelState();
  mode.onchange = updateSourceMode;
  audioInput.onchange = updatePreview;
  for (const input of [melodyInput, chordInput, drumInput]) input.onchange = updateButtonState;
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
        const uploaded = await upload(audioInput.files[0]); data.source_path = uploaded.path;
      } else {
        const [melody, chord, drum] = await Promise.all([
          upload(melodyInput.files[0]), upload(chordInput.files[0]), upload(drumInput.files[0])
        ]);
        data.melody_midi = melody.path; data.chord_midi = chord.path;
        if (drum) data.drum_midi = drum.path;
      }
      saveDraft();
      await submit('mulacover_remix', data, $('#remix-result'), button, projectScope);
    } catch (error) { restoreButton(button); renderScopedFailure($('#remix-result'), error, projectScope); }
  };
  setInterval(refreshModelState, 15000);
})();

