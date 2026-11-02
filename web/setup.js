"use strict";
(() => {
  const el = id => document.getElementById(id);
  const dialog = el('setup-dialog');
  let running = false;
  let refreshTimer;

  function render(state) {
    running = state.state === 'running';
    el('setup-status').textContent = state.message + (state.error ? ' ' + state.error : '');
    el('setup-data').textContent = 'Local data folder: ' + state.data_directory;
    el('setup-download').disabled = running;
    el('setup-profile').disabled = running;
    el('setup-speakers').disabled = running;
    el('setup-extras').querySelectorAll('input').forEach(input => input.disabled = running);
    el('setup-use').classList.toggle('hidden', state.state !== 'complete');
    clearTimeout(refreshTimer);
    if (running) refreshTimer = setTimeout(refresh, 1200);
  }

  async function refresh() {
    try { render(await api('/setup')); }
    catch (error) { el('setup-status').textContent = error.message; }
  }

  el('setup-open').onclick = () => { dialog.showModal(); refresh(); };
  el('setup-close').onclick = () => dialog.close();
  el('setup-use').onclick = () => window.location.reload();
  el('setup-profile').onchange = () => {
    el('setup-speakers').checked = el('setup-profile').value !== 'lightweight';
  };
  el('setup-download').onclick = async () => {
    if (running) return;
    el('setup-download').disabled = true;
    try {
      render(await api('/setup', {
        profile: el('setup-profile').value,
        speakers: el('setup-speakers').checked,
        models: [...el('setup-extras').querySelectorAll('input:checked')].map(input => input.value)
      }));
    } catch (error) {
      el('setup-status').textContent = error.message;
      el('setup-download').disabled = false;
    }
  };

  (async () => {
    try {
      const models = await api('/models');
      for (const model of [...models.transcription, ...models.translation].filter(model => model.id !== 'whisper')) {
        const label = document.createElement('label');
        label.className = 'setup-option';
        const input = document.createElement('input');
        input.type = 'checkbox'; input.value = model.id;
        label.append(input, document.createTextNode(' ' + model.name + (model.installed ? ' (installed)' : '')));
        el('setup-extras').append(label);
      }
      await refresh();
      if (!models.transcription.some(model => model.installed)) dialog.showModal();
    } catch (error) { el('setup-status').textContent = error.message; }
  })();
})();
