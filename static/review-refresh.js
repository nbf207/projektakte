/* Refresh completed background work without discarding an in-progress form. */
function createReviewRefresh({fetchState, update, canReload, reload, notify, schedule}) {
  async function poll() {
    let state;
    try { state = await fetchState(); }
    catch { schedule(poll, 10000); return; }
    update(state);
    // An API error may temporarily show "review" while a retry is still queued.
    if (state.processing) { schedule(poll, 4000); return; }
    if (canReload()) reload();
    else notify(state);
  }
  return {poll, start: () => schedule(poll, 3000)};
}

function reviewFormSnapshot(forms) {
  return JSON.stringify(forms.flatMap(form => Array.from(form.elements)
    .filter(field => field.name && !['hidden', 'submit', 'button'].includes(field.type))
    .map(field => [field.name, field.value, field.checked])));
}

function startReviewRefresh() {
  const status = document.querySelector('[data-doc-status]');
  if (!status || status.dataset.processing !== '1') return;
  const forms = Array.from(document.querySelectorAll('form'));
  const initial = reviewFormSnapshot(forms);
  let dirty = status.dataset.preserveInput === '1';
  let submitting = false;
  forms.forEach(form => {
    form.addEventListener('input', () => { dirty = true; });
    form.addEventListener('change', () => { dirty = true; });
    form.addEventListener('submit', () => { submitting = true; });
  });
  createReviewRefresh({
    fetchState: async () => {
      const t = window.t || (s => s);
      try {
        const response = await ProjektakteNet.requestJSON(status.dataset.docStatus);
        if (!response.ok) throw new Error(t('Status derzeit nicht verfügbar'));
        return response.data;
      } catch (error) {
        if (error.name === 'LoginRequired' && !document.querySelector('#status-login-refresh')) {
          const note=document.createElement('p');note.id='status-login-refresh';note.className='notice';
          ProjektakteNet.loginNotice(note,location.href);status.after(note);
        }
        throw error;
      }
    },
    update: state => { status.textContent = state.label; },
    canReload: () => !dirty && !submitting && reviewFormSnapshot(forms) === initial,
    reload: () => location.reload(),
    notify: () => {
      const t = window.t || (s => s);
      if (submitting || document.querySelector('#review-refresh-note')) return;
      const notice = document.createElement('div');
      notice.id = 'review-refresh-note'; notice.className = 'notice';
      notice.setAttribute('role', 'status');
      notice.textContent = t('Die Verarbeitung ist fertig. Deine Eingaben bleiben erhalten. ');
      const link = document.createElement('a');
      link.href = location.href; link.textContent = t('Aktuelle Prüfdaten öffnen');
      notice.append(link); (status.closest('.pagehead') || status).after(notice);
    },
    schedule: (callback, delay) => setTimeout(callback, delay)
  }).start();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startReviewRefresh);
  else startReviewRefresh();
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {createReviewRefresh, reviewFormSnapshot};
}
