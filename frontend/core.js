/*
 * Shared plumbing for every page: DOM helpers, the API client, login state, toasts, the confirm dialog and the hash router.
 * Everything is exposed on window.SG. No API keys live in the browser: the backend holds them. The login itself is an
 * HttpOnly cookie that JavaScript cannot read.
 */
(() => {
  'use strict';

  // Backend URL. On localhost with another port (VS Code Live Server 5500) or when the page is opened as a file, call the backend on :8000.
  // On a real domain (served by the backend or by a reverse proxy over HTTPS) use the same origin, so no URL is hard-coded.
  const LOCAL_DEV = location.protocol === 'file:' || (['localhost', '127.0.0.1'].includes(location.hostname) && location.port !== '8000');
  const API_BASE = LOCAL_DEV ? 'http://localhost:8000' : '';
  const HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'StudyGap' };

  /* ---------- DOM helpers ---------- */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function fmtSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function fmtDate(iso) {
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function fmtDateTime(iso) {
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  }

  /* ---------- API client ---------- */
  async function api(method, path, body, opts = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs || 30000);
    try {
      const res = await fetch(API_BASE + path, {
        method, headers: HEADERS, credentials: 'include', signal: ctrl.signal,
        body: body === undefined || body === null ? undefined : JSON.stringify(body)
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const err = new Error((data && data.error && data.error.message) || `The server returned an error (${res.status}).`);
        err.status = res.status;
        err.code = data && data.error && data.error.code;
        err.fields = data && data.error && data.error.fields;
        if (res.status === 401 && !opts.quiet) auth.expired();
        throw err;
      }
      return data;
    } catch (err) {
      if (err.name === 'AbortError') throw new Error('The request timed out. Please retry.');
      if (err.name === 'TypeError') {
        const hint = location.protocol === 'file:' ? ' You opened the page as a file; open http://localhost:8000 instead.' : '';
        const e = new Error('Could not reach the server. Start it with "python backend/server.py" (see backend/README.md).' + hint);
        e.network = true;
        throw e;
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  /* ---------- Toasts ---------- */
  function toast(message, kind) {
    const t = el('div', 'toast' + (kind ? ' ' + kind : ''), message);
    $('#toasts').append(t);
    setTimeout(() => t.remove(), kind === 'error' ? 8000 : 4500);
  }

  /* ---------- Confirm dialog (custom, so it works the same everywhere and can be tested) ---------- */
  function confirmDialog({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false }) {
    return new Promise((resolve) => {
      const previous = document.activeElement;
      const overlay = el('div', 'modal-overlay');
      const box = el('div', 'modal');
      box.setAttribute('role', 'alertdialog');
      box.setAttribute('aria-modal', 'true');
      const h = el('h2', '', title);
      h.id = 'modalTitle';
      const p = el('p', '', message);
      p.id = 'modalMsg';
      box.setAttribute('aria-labelledby', 'modalTitle');
      box.setAttribute('aria-describedby', 'modalMsg');
      const row = el('div', 'actions');
      const cancel = el('button', 'btn btn-secondary', cancelLabel);
      const ok = el('button', 'btn' + (danger ? ' btn-danger' : ''), confirmLabel);
      cancel.type = ok.type = 'button';
      row.append(cancel, ok);
      box.append(h, p, row);
      overlay.append(box);
      $('#modalRoot').append(overlay);
      const done = (v) => { document.removeEventListener('keydown', onKey, true); overlay.remove(); if (previous && previous.focus) previous.focus(); resolve(v); };
      const onKey = (e) => {
        if (e.key === 'Escape') { e.preventDefault(); done(false); }
        if (e.key === 'Tab') { // keep focus inside the dialog
          const items = [cancel, ok];
          const i = items.indexOf(document.activeElement);
          e.preventDefault();
          items[(i + (e.shiftKey ? items.length - 1 : 1)) % items.length].focus();
        }
      };
      document.addEventListener('keydown', onKey, true);
      cancel.addEventListener('click', () => done(false));
      ok.addEventListener('click', () => done(true));
      overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) done(false); });
      cancel.focus(); // the safe choice has focus first
    });
  }

  /* ---------- Form helpers ---------- */
  function setFieldError(id, message) {
    const input = document.getElementById(id);
    const err = document.getElementById(id + '-err');
    if (err) { err.textContent = message || ''; err.hidden = !message; }
    if (input) {
      if (message) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
      const ids = new Set((input.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
      if (message) ids.add(id + '-err'); else ids.delete(id + '-err');
      if (ids.size) input.setAttribute('aria-describedby', [...ids].join(' ')); else input.removeAttribute('aria-describedby');
    }
  }
  function clearErrors(form) {
    $$('.field-error', form).forEach((p) => { p.hidden = true; p.textContent = ''; });
    $$('[aria-invalid]', form).forEach((i) => i.removeAttribute('aria-invalid'));
  }
  function setStatus(box, message, kind) {
    box.hidden = !message;
    box.textContent = message || '';
    box.className = 'form-status' + (kind ? ' ' + kind : '');
  }
  function setBusy(btn, busy, busyLabel) {
    if (busy) { btn.dataset.label = btn.textContent; btn.textContent = busyLabel; btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    else { if (btn.dataset.label) btn.textContent = btn.dataset.label; btn.disabled = false; btn.removeAttribute('aria-busy'); }
  }

  /* ---------- Login state ---------- */
  const auth = {
    user: null,
    listeners: [],
    onChange(fn) { auth.listeners.push(fn); },
    set(user) { auth.user = user; auth.listeners.forEach((fn) => fn(user)); },
    async refresh() {
      try { auth.set((await api('GET', '/api/auth/me', null, { quiet: true })).user); auth.offline = false; }
      catch (err) { auth.offline = !!err.network; auth.set(null); if (err.network) toast(err.message, 'error'); }
    },
    expiredShown: false,
    expired() { // the server says our session is gone (expired, or logged out elsewhere)
      if (!auth.user || auth.expiredShown) return;
      auth.expiredShown = true;
      auth.set(null);
      toast('Your session has expired. Please log in again.', 'error');
      router.go('/login?next=' + encodeURIComponent(router.current().path));
      setTimeout(() => { auth.expiredShown = false; }, 1000);
    },
    async logout() {
      try { await api('POST', '/api/auth/logout', {}, { quiet: true }); } catch (err) { /* the cookie is cleared server-side when reachable; the UI logs out regardless */ }
      auth.set(null);
      router.go('/');
      toast('You have been logged out.');
    }
  };

  /* ---------- Views and routing ---------- */
  const views = {};            // name -> { enter(params, query), leave() }
  let currentView = null, silentHash = null;
  const ROUTES = {
    '': { view: 'home', title: 'Home' },
    dashboard: { view: 'dashboard', auth: true, title: 'Dashboard' },
    'study-gap': { view: 'study-gap', title: 'Study Gap Checker' },
    chat: { view: 'chat', auth: true, layout: 'chat', title: 'Ask AI Agent' },
    about: { view: 'about', title: 'About' },
    contact: { view: 'contact', title: 'Contact' },
    reviews: { view: 'reviews', title: 'Ratings and reviews' },
    login: { view: 'login', guestOnly: true, title: 'Log in' },
    signup: { view: 'signup', guestOnly: true, title: 'Sign up' },
    admin: { view: 'admin', auth: 'admin', title: 'Admin' }
  };

  function showView(name, opts = {}) {
    $$('.view').forEach((v) => { v.hidden = v.id !== 'view-' + name; });
    currentView = name;
    if (!opts.keepScroll) window.scrollTo(0, 0);
    if (opts.focus !== false) {
      const h = $(`#view-${name} h1`);
      if (h) { h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); }
    }
  }

  const router = {
    current() {
      const raw = location.hash.replace(/^#\/?/, '');
      const [pathPart, queryPart] = raw.split('?');
      const segs = pathPart.split('/').filter(Boolean);
      return { name: segs[0] || '', params: segs.slice(1), path: '/' + pathPart.replace(/^\/+/, ''), query: new URLSearchParams(queryPart || '') };
    },
    go(path, replace) {
      const hash = '#' + path;
      if (location.hash === hash) { router.render(); return; }
      if (replace) location.replace(hash); else location.hash = hash;
    },
    silent(path) { // change the URL without re-rendering (used for sub-steps inside one page)
      if (location.hash !== '#' + path) { silentHash = '#' + path; location.hash = path; }
    },
    async render() {
      await readyPromise;
      const r = router.current();
      const route = ROUTES[r.name] || ROUTES[''];
      if (route.auth && !auth.user) {
        toast(auth.offline ? 'Could not reach the server, so you cannot log in right now.' : 'Please log in to continue.');
        return router.go('/login?next=' + encodeURIComponent(r.path), true);
      }
      if (route.auth === 'admin' && !(auth.user && auth.user.is_admin)) { toast('That page is only for administrators.', 'error'); return router.go('/dashboard', true); }
      if (route.guestOnly && auth.user) return router.go('/dashboard', true);
      const prev = currentView && views[currentView];
      document.body.dataset.layout = route.layout || 'site';
      document.title = route.title + ' · Study Gap Detector';
      $$('#siteNav a[data-link]').forEach((a) => { if (a.dataset.link === r.name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
      $('#siteNav').classList.remove('open');
      $('#navToggle').setAttribute('aria-expanded', 'false');
      if (prev && prev.leave && route.view !== currentView) prev.leave();
      const mod = views[route.view];
      if (mod && mod.enter) await mod.enter(r.params, r.query);
      else showView(route.view);
    }
  };
  window.addEventListener('hashchange', () => {
    if (silentHash && location.hash === silentHash) { silentHash = null; return; }
    silentHash = null;
    router.render();
  });

  let readyResolve;
  const readyPromise = new Promise((r) => { readyResolve = r; });

  /* ---------- Header: login / account area ---------- */
  function renderNavAuth() {
    const box = $('#navAuth');
    box.replaceChildren();
    $('#navDashboard').hidden = !auth.user;
    if (auth.user) {
      const name = el('span', 'nav-user truncate', auth.user.name);
      name.title = auth.user.email;
      const out = el('button', 'btn btn-sm btn-secondary', 'Log out');
      out.type = 'button';
      out.addEventListener('click', () => auth.logout());
      if (auth.user.is_admin) { const a = el('a', '', 'Admin'); a.href = '#/admin'; a.dataset.link = 'admin'; box.append(a); }
      box.append(name, out);
    } else {
      const login = el('a', 'btn btn-sm btn-secondary', 'Log in');
      login.href = '#/login';
      const signup = el('a', 'btn btn-sm', 'Sign up');
      signup.href = '#/signup';
      box.append(login, signup);
    }
    const start = $('#ctaStart');
    if (start) { start.textContent = auth.user ? 'Go to dashboard' : 'Get started'; start.setAttribute('href', auth.user ? '#/dashboard' : '#/signup'); }
  }
  auth.onChange(renderNavAuth);

  /* ---------- Boot ---------- */
  function start() {
    $('#navToggle').addEventListener('click', () => {
      const open = $('#siteNav').classList.toggle('open');
      $('#navToggle').setAttribute('aria-expanded', String(open));
      $('#navToggle').setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && $('#siteNav').classList.contains('open')) { $('#siteNav').classList.remove('open'); $('#navToggle').setAttribute('aria-expanded', 'false'); $('#navToggle').focus(); }
    });
    const NAV = { dashboard: () => (auth.user ? '/dashboard' : '/'), chat: () => '/chat', home: () => '/' };
    document.addEventListener('click', (e) => {
      const n = e.target.closest && e.target.closest('[data-nav]');
      if (!n) return;
      const name = n.dataset.nav;
      if (NAV[name]) router.go(NAV[name]());
    });
    document.addEventListener('click', (e) => { // password show / hide buttons
      const t = e.target.closest && e.target.closest('.pw-toggle');
      if (!t) return;
      const input = document.getElementById(t.dataset.for);
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      t.textContent = show ? 'Hide' : 'Show';
      t.setAttribute('aria-pressed', String(show));
      t.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
    renderNavAuth();
    auth.refresh().then(() => { readyResolve(); router.render(); });
  }

  window.SG = { $, $$, el, fmtSize, fmtDate, fmtDateTime, api, auth, toast, confirmDialog, setFieldError, clearErrors, setStatus, setBusy, router, views, showView, start, API_BASE, ready: readyPromise };
  window.SG.go = (p) => router.go(p);
})();
