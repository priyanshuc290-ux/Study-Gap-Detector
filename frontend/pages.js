/*
 * The pages around the quiz and the chat: home, dashboard, login, sign up, contact, reviews, about, admin.
 * Everything shown comes from the server or from the student's own saved results. Nothing is made up:
 * an empty account shows empty states, not sample scores.
 */
(() => {
  'use strict';
  const { $, $$, el, api, toast, setFieldError, clearErrors, setStatus, setBusy } = SG;
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  /* ---------- Static pages ---------- */
  ['home', 'about'].forEach((name) => { SG.views[name] = { enter() { SG.showView(name); } }; });

  /* ---------- Dashboard ---------- */
  function safeNext(q) { // only ever go to one of our own screens after logging in
    const n = q && q.get('next');
    return n && /^\/(dashboard|study-gap|chat|reviews|contact|about)(\/[0-9a-f]{32})?$/.test(n) ? n : '/dashboard';
  }

  function emptyState(title, text, linkText, href) {
    const box = el('div', 'empty');
    box.append(el('strong', '', title), el('p', '', text));
    if (linkText) { const a = el('a', 'btn btn-sm', linkText); a.href = href; box.append(a); }
    return box;
  }

  function latestPanel(box, attempts) {
    box.replaceChildren();
    if (!attempts.length) {
      box.append(emptyState('No quiz results yet', 'Take a diagnostic quiz and your strong and weak topics will show up here.', 'Take a quiz', '#/study-gap'));
      return;
    }
    const a = attempts[attempts.length - 1];
    box.append(el('p', 'muted small', `${a.setup.subject}, unit ${a.setup.unit} · ${SG.quiz.attemptLabel(a)} · ${SG.fmtDate(a.ts)}`));
    const stats = el('div', 'mini-stats');
    const s1 = el('div'), s2 = el('div');
    s1.append(el('strong', '', `${a.score} / ${a.total}`), el('span', 'muted small', 'score'));
    s2.append(el('strong', '', a.accuracy + '%'), el('span', 'muted small', 'accuracy'));
    stats.append(s1, s2);
    box.append(stats);
    const p = el('p', '');
    p.append(el('strong', '', 'Weak topics: '), document.createTextNode(a.weak.length ? a.weak.join(', ') : (a.strong.length ? 'none in this attempt' : 'not enough data yet')));
    box.append(p);
    const row = el('div', 'actions wrap-row');
    const view = el('button', 'btn btn-secondary btn-sm', 'View full results');
    view.type = 'button';
    view.addEventListener('click', () => SG.quiz.openResults(a));
    row.append(view);
    if (a.weak.length) {
      const prac = el('button', 'btn btn-sm', 'Practise weak topics');
      prac.type = 'button';
      prac.addEventListener('click', () => SG.quiz.runAgentAction('weak_practice'));
      row.append(prac);
    }
    box.append(row);
  }

  function recentTable(box, attempts) {
    box.replaceChildren();
    if (!attempts.length) { box.append(emptyState('No quizzes yet', 'Your last quizzes will be listed here.', 'Start your first quiz', '#/study-gap')); return; }
    const table = el('table', 'history');
    const head = el('tr');
    ['Date', 'Subject and unit', 'Type', 'Score', 'Accuracy'].forEach((h) => head.append(el('th', '', h)));
    const thead = el('thead'); thead.append(head); table.append(thead);
    const body = el('tbody');
    attempts.slice(-5).reverse().forEach((a) => {
      const row = el('tr');
      [SG.fmtDate(a.ts), `${a.setup.subject}, unit ${a.setup.unit}`, SG.quiz.attemptLabel(a), `${a.score}/${a.total}`, a.accuracy + '%'].forEach((v) => row.append(el('td', '', v)));
      body.append(row);
    });
    table.append(body);
    box.append(table);
  }

  async function convPanel(box) {
    box.replaceChildren(el('p', 'muted', 'Loading…'));
    try {
      const list = (await api('GET', '/api/conversations?limit=5')).conversations;
      box.replaceChildren();
      if (!list.length) { box.append(emptyState('No conversations yet', 'Ask the study agent anything. Your chats are saved here.', 'Start a chat', '#/chat')); return; }
      const ul = el('ul', 'link-list');
      list.forEach((c) => {
        const li = el('li');
        const a = el('a', '', c.title);
        a.href = '#/chat/' + c.id;
        li.append(a, el('span', 'muted small', SG.fmtDate(c.updated_at)));
        ul.append(li);
      });
      box.append(ul);
    } catch (err) {
      box.replaceChildren(el('p', 'form-status error', err.message));
    }
  }

  function importBanner() {
    const banner = $('#importBanner');
    const n = SG.quiz.localCount();
    banner.hidden = n === 0;
    if (!n) return;
    $('#importText').textContent = `This browser has ${n} quiz attempt${n === 1 ? '' : 's'} saved from before you logged in. Add them to your account?`;
    const btn = $('#importBtn');
    btn.onclick = async () => {
      setBusy(btn, true, 'Importing…');
      try {
        const count = await SG.quiz.importLocal();
        toast(`Imported ${count} attempt${count === 1 ? '' : 's'} to your account.`);
        await renderDashboard();
      } catch (err) { toast(err.message, 'error'); }
      setBusy(btn, false);
    };
  }

  async function renderDashboard() {
    const first = SG.auth.user.name.split(' ')[0];
    const greet = $('#dashGreeting');
    const nameSpan = document.createElement('span');
    nameSpan.className = 'c-blue';
    nameSpan.textContent = first; // textContent, never innerHTML: the name comes from the user
    greet.replaceChildren('Welcome back, ', nameSpan);
    const ok = await SG.quiz.ensureLoaded();
    const attempts = SG.quiz.attempts();
    if (!ok) { $('#latestBox').replaceChildren(el('p', 'form-status error', 'Could not load your quiz results. Reload the page to try again.')); }
    else latestPanel($('#latestBox'), attempts);
    recentTable($('#recentBox'), attempts);
    SG.quiz.renderProgress($('#progressBox'));
    importBanner();
    convPanel($('#convBox'));
  }

  SG.views.dashboard = { async enter() { SG.showView('dashboard'); await renderDashboard(); } };

  /* ---------- Login ---------- */
  function initLogin() {
    const form = $('#loginForm');
    SG.views.login = {
      enter(params, query) {
        SG.showView('login');
        form.reset();
        clearErrors(form);
        setStatus($('#loginStatus'), '');
        $('#loginNote').textContent = query.get('next') ? 'Log in to continue.' : 'Welcome back.';
      }
    };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearErrors(form);
      const email = $('#lEmail').value.trim(), password = $('#lPassword').value;
      let bad = false;
      if (!EMAIL_RE.test(email)) { setFieldError('lEmail', 'Enter a valid email address.'); bad = true; }
      if (!password) { setFieldError('lPassword', 'Enter your password.'); bad = true; }
      if (bad) { (form.querySelector('[aria-invalid]') || {}).focus && form.querySelector('[aria-invalid]').focus(); return; }
      const btn = $('#loginBtn');
      setBusy(btn, true, 'Logging in…');
      setStatus($('#loginStatus'), '');
      try {
        const d = await api('POST', '/api/auth/login', { email, password }, { quiet: true });
        form.reset();
        SG.auth.set(d.user);
        SG.go(safeNext(SG.router.current().query));
      } catch (err) {
        setStatus($('#loginStatus'), err.message, 'error');
      }
      setBusy(btn, false);
    });
  }

  /* ---------- Sign up ---------- */
  function passwordProblem(pw) {
    if (pw.length < 8) return 'Use at least 8 characters.';
    if (pw.length > 128) return 'Use at most 128 characters.';
    if (!/[A-Za-z]/.test(pw) || !/\d/.test(pw)) return 'Include at least one letter and one number.';
    return '';
  }

  function initSignup() {
    const form = $('#signupForm');
    SG.views.signup = {
      enter() { SG.showView('signup'); form.reset(); clearErrors(form); setStatus($('#signupStatus'), ''); }
    };
    const map = { name: 'sName', email: 'sEmail', password: 'sPassword', password_confirm: 'sConfirm' };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearErrors(form);
      const body = { name: $('#sName').value.trim(), email: $('#sEmail').value.trim(), password: $('#sPassword').value, password_confirm: $('#sConfirm').value };
      const errs = {};
      if (!body.name) errs.sName = 'Enter your name.';
      if (!EMAIL_RE.test(body.email)) errs.sEmail = 'Enter a valid email address.';
      const pp = passwordProblem(body.password);
      if (pp) errs.sPassword = pp;
      else if (body.password !== body.password_confirm) errs.sConfirm = 'Passwords do not match.';
      if (Object.keys(errs).length) {
        Object.entries(errs).forEach(([id, m]) => setFieldError(id, m));
        form.querySelector('[aria-invalid]').focus();
        return;
      }
      const btn = $('#signupBtn');
      setBusy(btn, true, 'Creating account…');
      setStatus($('#signupStatus'), '');
      try {
        const d = await api('POST', '/api/auth/signup', body, { quiet: true });
        form.reset();
        SG.auth.set(d.user);
        toast(`Welcome, ${d.user.name.split(' ')[0]}. Your account is ready.`);
        SG.go('/dashboard');
      } catch (err) {
        if (err.fields) Object.entries(err.fields).forEach(([f, m]) => map[f] && setFieldError(map[f], m));
        setStatus($('#signupStatus'), err.message, 'error');
      }
      setBusy(btn, false);
    });
  }

  /* ---------- Contact ---------- */
  function initContact() {
    const form = $('#contactForm');
    const map = { name: 'cName', email: 'cEmail', subject: 'cSubject', message: 'cMessage' };
    SG.views.contact = {
      enter() {
        SG.showView('contact');
        if (SG.auth.user) { if (!$('#cName').value) $('#cName').value = SG.auth.user.name; if (!$('#cEmail').value) $('#cEmail').value = SG.auth.user.email; }
      }
    };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearErrors(form);
      setStatus($('#contactStatus'), '');
      const body = { name: $('#cName').value.trim(), email: $('#cEmail').value.trim(), subject: $('#cSubject').value.trim(), message: $('#cMessage').value.trim(), website: $('#cWebsite').value };
      const errs = {};
      if (!body.name) errs.cName = 'Enter your name.';
      if (!EMAIL_RE.test(body.email)) errs.cEmail = 'Enter a valid email address.';
      if (body.subject.length < 3) errs.cSubject = 'Enter a subject (at least 3 characters).';
      if (body.message.length < 10) errs.cMessage = 'Write a message of at least 10 characters.';
      if (Object.keys(errs).length) {
        Object.entries(errs).forEach(([id, m]) => setFieldError(id, m));
        form.querySelector('[aria-invalid]').focus();
        return;
      }
      const btn = $('#contactBtn');
      setBusy(btn, true, 'Sending…');
      try {
        await api('POST', '/api/contact', body, { quiet: true });
        $('#cSubject').value = ''; $('#cMessage').value = '';
        setStatus($('#contactStatus'), 'Message sent. It has been saved and the administrator can read it now. There is no automatic email reply.', 'success');
        $('#contactStatus').focus && $('#contactStatus').setAttribute('tabindex', '-1');
        $('#contactStatus').focus();
      } catch (err) {
        if (err.fields) Object.entries(err.fields).forEach(([f, m]) => map[f] && setFieldError(map[f], m));
        setStatus($('#contactStatus'), 'Your message was not sent. ' + err.message, 'error');
      }
      setBusy(btn, false);
    });
  }

  /* ---------- Reviews ---------- */
  let rating = 0;
  function starText(n) { return '★'.repeat(n) + '☆'.repeat(5 - n); }
  function starsEl(n) {
    const s = el('span', 'stars-static', starText(n));
    s.setAttribute('role', 'img');
    s.setAttribute('aria-label', `${n} out of 5 stars`);
    return s;
  }
  function paintStars(upTo) { $$('#starInput label').forEach((l, i) => l.classList.toggle('on', i < upTo)); }

  function buildStarInput() {
    const box = $('#starInput');
    for (let i = 1; i <= 5; i++) {
      const id = 'star' + i;
      const input = el('input');
      input.type = 'radio'; input.name = 'rating'; input.id = id; input.value = String(i);
      const label = el('label', '', '★');
      label.htmlFor = id;
      label.title = `${i} star${i === 1 ? '' : 's'}`;
      const sr = el('span', 'sr-only', `${i} star${i === 1 ? '' : 's'}`);
      label.append(sr);
      input.addEventListener('change', () => { rating = i; paintStars(i); setFieldError('rRating', ''); });
      label.addEventListener('mouseenter', () => paintStars(i));
      label.addEventListener('mouseleave', () => paintStars(rating));
      box.append(input, label);
    }
  }
  function setRating(n) { rating = n; paintStars(n); const r = $(`#star${n}`); if (r) r.checked = true; }

  const STATUS_TEXT = { pending: 'Your review is waiting for approval. It is not public yet.', approved: 'Your review is approved and shown on this page.', rejected: 'Your review was not approved, so it is not shown. You can edit it and submit it again.' };

  async function loadReviews() {
    const list = $('#reviewList2');
    list.replaceChildren(el('p', 'muted', 'Loading…'));
    try {
      const d = await api('GET', '/api/reviews', null, { quiet: true });
      const n = d.summary.count;
      $('#reviewSummary').textContent = n ? `${d.summary.average.toFixed(1)} out of 5, from ${n} approved review${n === 1 ? '' : 's'}.` : 'No reviews yet.';
      list.replaceChildren();
      if (!d.reviews.length) list.append(emptyState('Nothing here yet', SG.auth.user ? 'Be the first: write a review using the form.' : 'Log in to be the first to write one.'));
      d.reviews.forEach((r) => {
        const item = el('article', 'review');
        const head = el('div', 'review-head');
        head.append(starsEl(r.rating), el('strong', '', r.name), el('span', 'muted small', SG.fmtDate(r.created_at)));
        item.append(head);
        if (r.text) item.append(el('p', '', r.text));
        list.append(item);
      });
      showMine(d.mine);
    } catch (err) {
      $('#reviewSummary').textContent = '';
      list.replaceChildren(el('p', 'form-status error', err.message));
    }
  }

  function showMine(mine) {
    const form = $('#reviewForm');
    $('#reviewGuest').hidden = !!SG.auth.user;
    form.hidden = !SG.auth.user;
    if (!SG.auth.user) return;
    if (mine) {
      setRating(mine.rating);
      $('#rText').value = mine.text;
      setStatus($('#reviewStatus'), STATUS_TEXT[mine.status], mine.status === 'rejected' ? 'error' : 'info');
    } else { rating = 0; paintStars(0); $$('#starInput input').forEach((i) => { i.checked = false; }); $('#rText').value = ''; setStatus($('#reviewStatus'), ''); }
    $('#reviewBtn').textContent = mine ? 'Update review' : 'Submit review';
    $('#reviewDelete').hidden = !mine;
  }

  function initReviews() {
    buildStarInput();
    const form = $('#reviewForm');
    SG.views.reviews = { async enter() { SG.showView('reviews'); await loadReviews(); } };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearErrors(form);
      if (!rating) { setFieldError('rRating', 'Choose a rating from 1 to 5 stars.'); $('#star1').focus(); return; }
      const btn = $('#reviewBtn');
      setBusy(btn, true, 'Saving…');
      try {
        const d = await api('PUT', '/api/reviews/mine', { rating, text: $('#rText').value.trim() });
        toast('Review saved. It will appear after approval.');
        showMine(d.mine);
        setBusy(btn, false);
        $('#reviewBtn').textContent = 'Update review';
      } catch (err) {
        if (err.fields) { if (err.fields.rating) setFieldError('rRating', err.fields.rating); if (err.fields.text) setFieldError('rText', err.fields.text); }
        setStatus($('#reviewStatus'), 'Your review was not saved. ' + err.message, 'error');
        setBusy(btn, false);
      }
    });
    $('#reviewDelete').addEventListener('click', async () => {
      if (!(await SG.confirmDialog({ title: 'Delete your review?', message: 'Your rating and review will be removed.', confirmLabel: 'Delete review', danger: true }))) return;
      try { await api('DELETE', '/api/reviews/mine'); toast('Review deleted.'); await loadReviews(); } catch (err) { toast(err.message, 'error'); }
    });
  }

  /* ---------- Admin ---------- */
  async function loadAdmin() {
    const status = $('#adminStatus').value;
    const box = $('#adminReviews');
    box.replaceChildren(el('p', 'muted', 'Loading…'));
    try {
      const list = (await api('GET', '/api/admin/reviews?status=' + encodeURIComponent(status))).reviews;
      box.replaceChildren();
      if (!list.length) box.append(el('p', 'muted', 'No reviews in this view.'));
      list.forEach((r) => {
        const item = el('article', 'review');
        const head = el('div', 'review-head');
        head.append(starsEl(r.rating), el('strong', '', `${r.name} (${r.email})`), el('span', 'tag', r.status), el('span', 'muted small', SG.fmtDate(r.created_at)));
        item.append(head);
        if (r.text) item.append(el('p', '', r.text));
        const row = el('div', 'actions');
        [['approved', 'Approve', ''], ['rejected', 'Reject', 'btn-secondary']].forEach(([st, label, cls]) => {
          if (r.status === st) return;
          const b = el('button', 'btn btn-sm ' + cls, label);
          b.type = 'button';
          b.addEventListener('click', async () => {
            try { await api('POST', '/api/admin/reviews/' + r.id, { status: st }); toast(`Review ${st}.`); loadAdmin(); } catch (err) { toast(err.message, 'error'); }
          });
          row.append(b);
        });
        item.append(row);
        box.append(item);
      });
    } catch (err) { box.replaceChildren(el('p', 'form-status error', err.message)); }

    const cbox = $('#adminContact');
    cbox.replaceChildren(el('p', 'muted', 'Loading…'));
    try {
      const msgs = (await api('GET', '/api/admin/contact')).messages;
      cbox.replaceChildren();
      if (!msgs.length) cbox.append(el('p', 'muted', 'No messages yet.'));
      msgs.forEach((m) => {
        const item = el('article', 'review');
        const head = el('div', 'review-head');
        head.append(el('strong', '', m.subject), el('span', 'muted small', `${m.name} <${m.email}> · ${SG.fmtDateTime(m.created_at)}`));
        if (m.handled) head.append(el('span', 'tag', 'handled'));
        item.append(head, el('p', 'preline', m.message));
        const b = el('button', 'btn btn-sm btn-secondary', m.handled ? 'Mark as new' : 'Mark as handled');
        b.type = 'button';
        b.addEventListener('click', async () => { try { await api('POST', '/api/admin/contact/' + m.id, { handled: !m.handled }); loadAdmin(); } catch (err) { toast(err.message, 'error'); } });
        item.append(b);
        cbox.append(item);
      });
    } catch (err) { cbox.replaceChildren(el('p', 'form-status error', err.message)); }
  }

  function initAdmin() {
    SG.views.admin = { async enter() { SG.showView('admin'); await loadAdmin(); } };
    $('#adminStatus').addEventListener('change', loadAdmin);
  }

  initLogin(); initSignup(); initContact(); initReviews(); initAdmin();
})();
