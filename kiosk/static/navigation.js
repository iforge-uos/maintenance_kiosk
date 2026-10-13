/* Per-tab kiosk navigation. Settings/Testing are not destinations for Back. */
(() => {
  'use strict';
  const KEY = 'iforge-navigation-transfer-v1';
  const STATE = 'iforgeNavigation';
  const LIMIT = 80;
  let trail = [];
  let pendingBack = null;

  function localUrl(value) {
    try {
      const url = new URL(value, location.origin);
      if (url.origin !== location.origin) return null;
      // Both dashboard routes represent Home; retain later printer pages.
      if (url.pathname === '/dashboard' || url.pathname === '/') {
        const page = Number(url.searchParams.get('page') || 1);
        return page > 1 && Number.isInteger(page) ? `/dashboard?page=${page}` : '/';
      }
      return url.pathname + url.search;
    } catch { return null; }
  }
  function kioskUrl(value) {
    const url = localUrl(value);
    if (!url) return null;
    const path = url.split('?')[0];
    return path === '/' || path === '/dashboard' || path.startsWith('/printers/') || path.startsWith('/sops/') ? url : null;
  }
  function clean(values) {
    if (!Array.isArray(values)) return [];
    return values.filter(value => typeof value === 'string').map(kioskUrl).filter(Boolean).slice(-LIMIT)
      .filter((value, index, all) => index === 0 || value !== all[index - 1]);
  }
  function takeTransfer() {
    try {
      const value = JSON.parse(sessionStorage.getItem(KEY));
      sessionStorage.removeItem(KEY);
      return value;
    } catch { return null; }
  }
  function remember() {
    try { history.replaceState({...history.state, [STATE]: {trail}}, '', location.href); } catch {}
  }
  function restore() {
    const saved = history.state?.[STATE];
    const transfer = takeTransfer();
    const current = kioskUrl(location.href);
    if (saved && Array.isArray(saved.trail)) {
      // Each browser entry owns its snapshot, including reload and BFCache.
      trail = clean(saved.trail);
    } else {
      trail = [];
      // Consume only a successful departure from this tab and origin. A new
      // tab, external page or direct link uses the server's local fallback.
      if (transfer && transfer.source === localUrl(document.referrer) &&
          Date.now() - transfer.at < 60000 && Date.now() >= transfer.at) {
        trail = clean(transfer.trail);
        if (current && transfer.back?.target === current) trail = clean(transfer.back.trail);
      }
      if (current && trail[trail.length - 1] !== current) trail.push(current);
      trail = trail.slice(-LIMIT);
    }
    pendingBack = null;
    remember();
    updateControls();
  }
  function destination(fallback = '/') {
    const current = kioskUrl(location.href);
    const previous = trail.slice();
    if (current && previous[previous.length - 1] === current) previous.pop();
    while (previous.length && previous[previous.length - 1] === current) previous.pop();
    if (previous.length) return {target: previous[previous.length - 1], trail: previous};
    const target = kioskUrl(fallback) || '/';
    return {target, trail: [target]};
  }
  function back(fallback = '/') {
    const next = destination(fallback);
    if (next.target === kioskUrl(location.href)) return;
    pendingBack = next;
    // A GET avoids resubmitting a maintenance POST. Native beforeunload and
    // the Settings guard still decide whether this departure can complete.
    location.assign(next.target);
  }
  function updateControls() {
    document.querySelectorAll('a[data-back]').forEach(link => {
      if (!link.dataset.backFallback) link.dataset.backFallback = link.getAttribute('href') || '/';
      const target = destination(link.dataset.backFallback).target;
      const disabled = target === kioskUrl(location.href);
      link.href = target;
      if (disabled) {
        link.setAttribute('aria-disabled', 'true');
        link.setAttribute('tabindex', '-1');
      } else {
        link.removeAttribute('aria-disabled');
        link.removeAttribute('tabindex');
      }
    });
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (link.matches('[data-back]')) {
      event.preventDefault();
      back(link.dataset.backFallback || link.getAttribute('href'));
    } else {
      pendingBack = null;
    }
  });
  document.addEventListener('submit', () => { pendingBack = null; });
  window.addEventListener('pagehide', () => {
    // Do not consume history at click time: a user may cancel beforeunload.
    try {
      sessionStorage.setItem(KEY, JSON.stringify({source: localUrl(location.href), trail, back: pendingBack, at: Date.now()}));
    } catch {}
  });
  window.addEventListener('pageshow', restore);
  window.addEventListener('popstate', restore);
  window.KioskNavigation = {back, home: () => { pendingBack = null; location.assign('/'); }};
  restore();
})();
