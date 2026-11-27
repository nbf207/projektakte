// Instant App-Shell with volatile Browser-RAM Stale-While-Revalidate cache.
// Keeps Topbar, Sidebar, CSS & JS permanently in memory; swaps only .content with smooth View Transitions.
(() => {
  if (!window.fetch || !window.DOMParser || !history.pushState) return;
  if (location.pathname.startsWith('/demo/') || location.pathname.startsWith('/login')) return;

  const MAX_ENTRIES = 24;
  const TTL_MS = 180_000;      // 3 min in Browser RAM (never written to disk)
  const REVALIDATE_MS = 15_000; // background refresh if cached entry is > 15s old
  const SKIP_PATH = /\/(abmelden|logout|login|anmelden|demo|admin|media|export|teilen|loeschen|endgueltig|druck|pdf|backup)(\/|$)/i;
  const BRIDGED_SCRIPTS = /(^|\/)(capture|voice)\.js(\?|$)/;

  const cache = new Map();
  const inflight = new Map();
  let navSeq = 0;
  let activeTransition = null;

  const normUrl = href => {
    try {
      const u = new URL(href, location.href);
      u.hash = '';
      return u;
    } catch { return null; }
  };

  const canHandleUrl = u => {
    if (!u || u.origin !== location.origin) return false;
    if (SKIP_PATH.test(u.pathname)) return false;
    return true;
  };

  const cacheSet = (key, entry) => {
    if (cache.has(key)) cache.delete(key);
    cache.set(key, entry);
    if (cache.size > MAX_ENTRIES) {
      const oldest = cache.keys().next().value;
      if (oldest) cache.delete(oldest);
    }
  };

  const parseHtml = (html, url) => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const content = doc.querySelector('.app-window .content');
    if (!content) return null;
    // Check if incoming content requires an un-bridged page script (e.g. settings, review, entry-editor).
    const scripts = [...content.querySelectorAll('script[src]')];
    const hasUnbridged = scripts.some(s => !BRIDGED_SCRIPTS.test(s.getAttribute('src') || ''));
    const hasInlineScript = [...content.querySelectorAll('script:not([src])')].some(s => !s.type || s.type === 'text/javascript');
    if (hasUnbridged || hasInlineScript) return { fallback: true };
    const stylesheets = [...doc.querySelectorAll('link[rel="stylesheet"][href]')].map(l => l.getAttribute('href'));
    return {
      url,
      title: doc.title,
      bodyClass: doc.body.className,
      locationHtml: doc.querySelector('.window-location')?.innerHTML || '',
      sidebarHtml: doc.querySelector('.app-window .sidebar')?.innerHTML || '',
      contentHtml: content.innerHTML,
      stylesheets,
      scripts: scripts.map(s => s.getAttribute('src')),
      ts: Date.now(),
    };
  };

  const ensureAssets = entry => {
    for (const href of entry.stylesheets) {
      if (!href) continue;
      const abs = new URL(href, location.href).href;
      if (![...document.querySelectorAll('link[rel="stylesheet"]')].some(l => l.href === abs)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = href;
        document.head.appendChild(link);
      }
    }
    for (const src of entry.scripts) {
      if (!src) continue;
      const abs = new URL(src, location.href).href;
      if (![...document.querySelectorAll('script[src]')].some(s => s.src === abs)) {
        const sc = document.createElement('script');
        sc.src = src;
        sc.defer = true;
        document.head.appendChild(sc);
      }
    }
  };

  const fetchEntry = async (urlStr, { background = false } = {}) => {
    if (inflight.has(urlStr)) return inflight.get(urlStr);
    const p = (async () => {
      try {
        const res = await fetch(urlStr, {
          credentials: 'same-origin',
          headers: { 'X-Instant-Shell': '1' },
          priority: background ? 'low' : 'high',
        });
        if (!res.ok) return { fallback: true };
        const finalUrl = normUrl(res.url);
        if (!canHandleUrl(finalUrl)) return { fallback: true };
        const ct = (res.headers.get('content-type') || '').toLowerCase();
        if (!ct.includes('text/html')) return { fallback: true };
        const html = await res.text();
        const parsed = parseHtml(html, finalUrl.href);
        if (!parsed || parsed.fallback) return { fallback: true };
        cacheSet(urlStr, parsed);
        if (finalUrl.href !== urlStr) cacheSet(finalUrl.href, parsed);
        return parsed;
      } catch {
        return { fallback: true };
      } finally {
        inflight.delete(urlStr);
      }
    })();
    inflight.set(urlStr, p);
    return p;
  };

  const isUserEditing = () => {
    const el = document.activeElement;
    if (!el) return false;
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable) return true;
    return !!document.querySelector('dialog[open]');
  };

  const applyEntry = (entry, { push = false, scrollTop = true, animate = true } = {}) => {
    ensureAssets(entry);
    const commit = () => {
      document.title = entry.title;
      document.body.className = entry.bodyClass;
      const loc = document.querySelector('.window-location');
      if (loc && entry.locationHtml) loc.innerHTML = entry.locationHtml;
      const sb = document.querySelector('.app-window .sidebar');
      if (sb && entry.sidebarHtml) sb.innerHTML = entry.sidebarHtml;
      const content = document.querySelector('.app-window .content');
      if (content) {
        content.innerHTML = entry.contentHtml;
        if (window.htmx) window.htmx.process(content);
      }
      if (push) history.pushState({ lg: true, url: entry.url }, '', entry.url);
      if (scrollTop) window.scrollTo({ top: 0, behavior: 'instant' });
      document.dispatchEvent(new CustomEvent('lg:navigate', { detail: { url: entry.url } }));
    };
    const reduced = matchMedia('(prefers-reduced-motion:reduce)').matches;
    let committed = false;
    const runCommit = () => {
      if (committed) return;
      committed = true;
      commit();
    };
    if (animate && !reduced && document.visibilityState === 'visible' && typeof document.startViewTransition === 'function') {
      if (activeTransition) {
        try { activeTransition.skipTransition(); } catch {}
        activeTransition = null;
      }
      try {
        const vt = document.startViewTransition(runCommit);
        activeTransition = vt;
        const clear = () => { if (activeTransition === vt) activeTransition = null; };
        vt.ready.catch(() => {});
        vt.updateCallbackDone.catch(() => { runCommit(); });
        vt.finished.catch(() => {}).finally(() => { clear(); runCommit(); });
      } catch {
        activeTransition = null;
        runCommit();
      }
    } else {
      runCommit();
    }
  };

  const navigate = async (urlStr, { push = true, scrollTop = true } = {}) => {
    const seq = ++navSeq;
    const cached = cache.get(urlStr);
    const now = Date.now();
    if (cached && now - cached.ts < TTL_MS) {
      // 0ms instant swap from Browser RAM!
      applyEntry(cached, { push, scrollTop, animate: true });
      // Stale-While-Revalidate silently in background if older than REVALIDATE_MS.
      if (now - cached.ts > REVALIDATE_MS) {
        fetchEntry(urlStr, { background: true }).then(fresh => {
          if (seq !== navSeq || !fresh || fresh.fallback) return;
          if (fresh.contentHtml !== cached.contentHtml && !isUserEditing()) {
            applyEntry(fresh, { push: false, scrollTop: false, animate: false });
          }
        });
      }
      return;
    }
    const fresh = await fetchEntry(urlStr, { background: false });
    if (seq !== navSeq) return;
    if (!fresh || fresh.fallback) {
      location.assign(urlStr);
      return;
    }
    applyEntry(fresh, { push, scrollTop, animate: true });
  };

  // Seed current page into Browser RAM on initial load.
  const seedCurrent = () => {
    const u = normUrl(location.href);
    if (!canHandleUrl(u)) return;
    const content = document.querySelector('.app-window .content');
    if (!content) return;
    const scripts = [...content.querySelectorAll('script[src]')];
    const hasUnbridged = scripts.some(s => !BRIDGED_SCRIPTS.test(s.getAttribute('src') || ''));
    if (hasUnbridged) return;
    cacheSet(u.href, {
      url: u.href,
      title: document.title,
      bodyClass: document.body.className,
      locationHtml: document.querySelector('.window-location')?.innerHTML || '',
      sidebarHtml: document.querySelector('.app-window .sidebar')?.innerHTML || '',
      contentHtml: content.innerHTML,
      stylesheets: [...document.querySelectorAll('link[rel="stylesheet"][href]')].map(l => l.getAttribute('href')),
      scripts: scripts.map(s => s.getAttribute('src')),
      ts: Date.now(),
    });
    history.replaceState({ lg: true, url: u.href }, '', location.href);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', seedCurrent);
  else seedCurrent();

  // Intercept safe same-origin link clicks for instant RAM swaps.
  document.addEventListener('click', e => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = e.target?.closest?.('a[href]');
    if (!link || link.target || link.hasAttribute('download') || link.hasAttribute('hx-get') || link.hasAttribute('hx-post')) return;
    const rawHref = link.getAttribute('href') || '';
    if (rawHref.startsWith('#')) return;
    const u = normUrl(link.href);
    if (!canHandleUrl(u)) return;
    if (u.href === normUrl(location.href)?.href) return;
    e.preventDefault();
    navigate(u.href, { push: true, scrollTop: true });
  });

  // Support browser Back/Forward smoothly from RAM.
  window.addEventListener('popstate', () => {
    const u = normUrl(location.href);
    if (!canHandleUrl(u)) { location.reload(); return; }
    navigate(u.href, { push: false, scrollTop: false });
  });

  // Gentle hover / touchstart pre-warm straight into Browser RAM (max 1 in-flight).
  let hoverTimer = 0;
  const warmLink = link => {
    if (navigator.connection?.saveData || inflight.size >= 1) return;
    if (!link || link.target || link.hasAttribute('download')) return;
    const u = normUrl(link.href);
    if (!canHandleUrl(u) || u.href === normUrl(location.href)?.href) return;
    const existing = cache.get(u.href);
    if (existing && Date.now() - existing.ts < TTL_MS) return;
    fetchEntry(u.href, { background: true });
  };
  document.addEventListener('pointerover', e => {
    if (e.pointerType !== 'mouse') return;
    const link = e.target?.closest?.('a[href]');
    if (!link) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => warmLink(link), 75);
  }, { passive: true });
  document.addEventListener('touchstart', e => {
    const link = e.target?.closest?.('a[href]');
    if (link) warmLink(link);
  }, { passive: true });

  // Clear RAM cache immediately on any form submission or mutating HTMX request.
  document.addEventListener('submit', () => cache.clear(), true);
  document.addEventListener('htmx:afterRequest', e => {
    const verb = (e.detail?.requestConfig?.verb || 'get').toLowerCase();
    if (verb !== 'get') cache.clear();
  });
})();
