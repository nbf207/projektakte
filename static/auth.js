/* Shared request boundary: Access redirects are never parsed as application JSON. */
(function (root) {
  class LoginRequired extends Error {
    constructor() { super('Anmeldung oder Sicherheitsnachweis erneuern.'); this.name = 'LoginRequired'; }
  }
  function providerURL(value) {
    try {
      const host = new URL(value).hostname;
      return host === 'accounts.google.com' || host === 'cloudflareaccess.com' || host.endsWith('.cloudflareaccess.com');
    } catch { return false; }
  }
  function needsLogin(response) {
    return response.type === 'opaqueredirect' || [301, 302, 303, 307, 308, 401, 403].includes(response.status)
      || providerURL(response.url) || providerURL(response.headers?.get('Location'));
  }
  function refreshURL(value) {
    const base = root.location?.origin || 'http://localhost';
    let url = new URL(value || '/', base);
    if (url.origin !== base) url = new URL('/', base);
    url.searchParams.set('auth_refresh', '1');
    return url.href;
  }
  async function requestJSON(url, options = {}) {
    const response = await fetch(url, {...options, credentials: 'same-origin', redirect: 'manual', cache: 'no-store'});
    if (needsLogin(response)) throw new LoginRequired();
    if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
      throw new Error('Antwort nicht bestätigt. Eingaben bleiben erhalten.');
    }
    return {ok: response.ok, status: response.status, data: await response.json()};
  }
  function loginNotice(target, returnURL, text) {
    target.textContent = text || 'Anmeldung oder Verbindung unterbrochen. Deine Eingaben bleiben erhalten. ';
    const link = document.createElement('a');
    link.href = refreshURL(returnURL); link.textContent = 'Anmeldung erneuern und fortsetzen';
    target.append(' ', link);
  }
  const api = {LoginRequired, providerURL, needsLogin, refreshURL, requestJSON, loginNotice};
  root.ProjektakteNet = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
