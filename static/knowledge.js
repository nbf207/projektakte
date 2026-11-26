/* Owner-authenticated chunk upload; originals never enter browser storage. */
(() => {
  document.querySelectorAll('[data-youtube]').forEach(box => {
    if (box.dataset.initialized) return;
    box.dataset.initialized = '1';
    box.querySelector('[data-load-youtube]').addEventListener('click', () => {
      if (!/^[A-Za-z0-9_-]{11}$/.test(box.dataset.youtube)) return;
      const frame = document.createElement('iframe');
      frame.src = 'https://www.youtube-nocookie.com/embed/' + box.dataset.youtube;
      frame.title = 'YouTube-Video'; frame.allowFullscreen = true;
      frame.referrerPolicy = 'strict-origin-when-cross-origin';
      frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation');
      frame.style.cssText = 'width:100%;aspect-ratio:16/9;border:0;border-radius:var(--radius)';
      box.replaceChildren(frame);
    });
  });
  document.querySelectorAll('[data-discard-video]').forEach(button => {
    if (button.dataset.initialized) return; button.dataset.initialized = '1';
    button.addEventListener('click', async () => {
      if (!confirm('Unfertiges Video und gespeichertes Original entfernen?')) return;
      const data = new FormData(); data.set('csrfmiddlewaretoken',document.querySelector('[name=csrfmiddlewaretoken]').value);
      try { const result=await ProjektakteNet.requestJSON(button.dataset.discardVideo,{method:'POST',body:data});
        if (!result.ok) throw new Error(result.data.error); button.closest('article').remove();
      } catch(error) { alert(error.message); }
    });
  });
  document.querySelectorAll('[data-video-upload]').forEach(form => {
    if (form.dataset.initialized) return;
    form.dataset.initialized = '1';
    const input = form.querySelector('input[type=file]'), note = form.querySelector('[role=status]');
    const progress = form.querySelector('progress'), cancel = form.querySelector('[data-video-cancel]');
    const submit = form.querySelector('button:not([type=button])');
    const key = 'knowledge-upload:' + form.action;
    let active = null, stopped = false, busy = false;
    try { active = JSON.parse(sessionStorage.getItem(key)); } catch {}
    if (active) { cancel.hidden = false; note.textContent = 'Unterbrochener Upload: dieselbe Datei auswählen und fortsetzen oder abbrechen.'; }
    function remember() { try { if (active) sessionStorage.setItem(key, JSON.stringify(active)); else sessionStorage.removeItem(key); } catch {} }
    function url(action) { return '/wissen/video/' + active.id + '/' + action + '/'; }
    async function post(target, data) {
      data.set('csrfmiddlewaretoken', form.querySelector('[name=csrfmiddlewaretoken]').value);
      const response = await ProjektakteNet.requestJSON(target, {method:'POST',body:data,signal:AbortSignal.timeout(90000)});
      if (!response.ok) throw new Error(response.data.error || 'Upload nicht bestätigt. Dieselbe Datei erneut auswählen und fortsetzen.');
      return response.data;
    }
    cancel.addEventListener('click', async () => {
      stopped = true;
      if (busy) { note.textContent = 'Abbruch nach dem aktuellen Teilstück …'; return; }
      if (!active) return;
      try { await post(url('abbrechen'), new FormData()); active = null; remember(); cancel.hidden = true; note.textContent = 'Upload abgebrochen.'; }
      catch (error) { note.textContent = error.message; }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy) return;
      const file = input.files[0]; if (!file) return;
      if (file.size > 1024 ** 3) { note.textContent = 'Original maximal 1 GB. Bitte größere Dateien vorab verkleinern.'; return; }
      busy = true; stopped = false; submit.disabled = true; progress.hidden = false;
      try {
        if (active && (active.name !== file.name || active.size !== file.size || active.modified !== file.lastModified)) throw new Error('Bitte dieselbe Datei auswählen oder den bisherigen Upload abbrechen.');
        if (!active) {
          const data = new FormData(); data.set('name',file.name); data.set('size',file.size);
          const created = await post(form.action,data);
          active = {id:created.id,name:file.name,size:file.size,modified:file.lastModified}; remember();
        }
        cancel.hidden = false;
        const response = await ProjektakteNet.requestJSON(url('status'));
        if (!response.ok) throw new Error('Upload nicht mehr vorhanden. Bitte abbrechen und neu beginnen.');
        let offset = response.data.offset;
        if (response.data.state === 'failed') throw new Error(response.data.error);
        while (offset < file.size && !stopped) {
          const data = new FormData(); data.set('offset',offset); data.set('chunk',file.slice(offset,offset+4*1024*1024),'chunk');
          const result = await post(url('chunk'),data); offset = result.offset;
          progress.value = Math.round(offset/file.size*100); note.textContent = 'Hochladen: ' + progress.value + ' %';
        }
        if (stopped) {
          await post(url('abbrechen'),new FormData()); note.textContent = 'Upload abgebrochen.';
        } else {
          note.textContent = 'Gespeichert. Das Video wird im Hintergrund komprimiert. Du kannst diese Seite verlassen und später neu öffnen.';
          input.value = '';
        }
        active = null; remember(); cancel.hidden = true;
      } catch (error) {
        if (error.name === 'LoginRequired') ProjektakteNet.loginNotice(note,location.href,'Upload pausiert. Nach Anmeldung dieselbe Datei auswählen. ');
        else note.textContent = error.message;
      } finally { busy = false; submit.disabled = false; }
    });
  });
})();
