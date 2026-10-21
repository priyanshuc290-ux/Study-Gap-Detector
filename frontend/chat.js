/*
 * Ask AI Agent: full-screen chat with saved conversations.
 * The AI itself is the existing study agent (backend chat.py). This file is the screen around it: sidebar of previous
 * conversations, message history, live streaming of steps and text, attachments, copy, rename, delete.
 * Conversations and messages live in the server database and belong to the logged-in student; nothing private is
 * kept in localStorage.
 */
(() => {
  'use strict';
  const { $, $$, el, fmtSize } = SG;

  const CONFIG = { MAX_ATTACHMENT_BYTES: 10 * 1024 * 1024, CHAT_IDLE_TIMEOUT_MS: 60000 };
  const PROMPTS = ['What should I study next?', 'Make me a 3-day revision plan', 'Explain a concept', 'Help me understand a difficult topic'];
  const S = { convs: [], activeId: null, title: 'New chat', busy: false, docName: null, loadingList: false, listError: '', query: '', unanswered: null };
  let attachment = null;   // { file, kind, url }
  let chatAbort = null;    // AbortController of the answer being streamed
  let stick = true;        // is the reader at the bottom? (then new text scrolls into view; otherwise we leave them alone)
  let entered = false;

  /* ---------- Small safe Markdown renderer (headings, lists, bold, inline code, code blocks). Builds DOM nodes, never innerHTML. ---------- */
  function renderInline(text, parent) {
    const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.append(document.createTextNode(text.slice(last, m.index)));
      const t = m[0];
      parent.append(t.startsWith('**') ? el('strong', '', t.slice(2, -2)) : el('code', '', t.slice(1, -1)));
      last = m.index + t.length;
    }
    if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
  }

  function renderMarkdown(text, root) {
    let list = null, para = [], code = null;
    const flushPara = () => {
      if (!para.length) return;
      const p = el('p');
      renderInline(para.join(' '), p);
      root.append(p);
      para = [];
    };
    const flushCode = () => { const pre = el('pre'); pre.append(el('code', '', code.join('\n'))); root.append(pre); code = null; };
    text.replace(/\r/g, '').split('\n').forEach((line) => {
      if (/^\s*```/.test(line)) {
        if (code) flushCode(); else { flushPara(); list = null; code = []; }
        return;
      }
      if (code) { code.push(line); return; }
      const h = line.match(/^#{1,4}\s+(.*)/);
      const li = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)/);
      if (h) {
        flushPara(); list = null;
        const hd = el('h5');
        renderInline(h[1], hd);
        root.append(hd);
      } else if (li) {
        flushPara();
        const ordered = /^\s*\d/.test(line);
        if (!list || list.ordered !== ordered) { list = el(ordered ? 'ol' : 'ul'); list.ordered = ordered; root.append(list); }
        const item = el('li');
        renderInline(li[1], item);
        list.append(item);
      } else if (!line.trim()) {
        flushPara(); list = null;
      } else {
        list = null;
        para.push(line.trim());
      }
    });
    if (code) flushCode();
    flushPara();
  }

  /* ---------- Scrolling ---------- */
  const scroller = () => $('#chatScroll');
  function nearBottom() { const s = scroller(); return s.scrollHeight - s.scrollTop - s.clientHeight < 90; }
  function toBottom(force) {
    const s = scroller();
    if (force || stick) { s.scrollTop = s.scrollHeight; stick = true; }
    $('#jumpBtn').hidden = stick;
  }
  function onScroll() { stick = nearBottom(); $('#jumpBtn').hidden = stick || !$('#chatLog').children.length; }

  /* ---------- Sidebar: conversation list ---------- */
  function groupLabel(iso) {
    const d = new Date(iso), now = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(now) - day(d)) / 86400000);
    if (diff <= 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff < 8) return 'Previous 7 days';
    return 'Older';
  }

  function renderList() {
    const box = $('#convList');
    box.replaceChildren();
    if (S.loadingList) { box.append(el('div', 'list-note', 'Loading chats…')); return; }
    if (S.listError) {
      const b = el('button', 'btn btn-sm btn-secondary', 'Retry');
      b.type = 'button';
      b.addEventListener('click', loadList);
      box.append(el('div', 'list-note error', S.listError), b);
      return;
    }
    if (!S.convs.length) {
      box.append(el('div', 'list-note', S.query ? 'No chats match your search.' : 'No chats yet. Your conversations will appear here.'));
      return;
    }
    let lastGroup = null;
    S.convs.forEach((c) => {
      const g = groupLabel(c.updated_at);
      if (g !== lastGroup) { box.append(el('h2', 'conv-group', g)); lastGroup = g; }
      box.append(convItem(c));
    });
  }

  function convItem(c) {
    const row = el('div', 'conv-item' + (c.id === S.activeId ? ' active' : ''));
    row.dataset.id = c.id;
    const link = el('a', 'conv-link', c.title);
    link.href = '#/chat/' + c.id;
    link.title = c.title;
    if (c.id === S.activeId) link.setAttribute('aria-current', 'page');
    link.addEventListener('click', () => closeDrawer());
    const actions = el('div', 'conv-actions');
    const ren = iconBtn('Rename chat: ' + c.title, 'M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4');
    const del = iconBtn('Delete chat: ' + c.title, 'M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13');
    ren.addEventListener('click', (e) => { e.preventDefault(); startRename(row, c); });
    del.addEventListener('click', (e) => { e.preventDefault(); deleteConversation(c); });
    actions.append(ren, del);
    row.append(link, actions);
    return row;
  }

  function iconBtn(label, path) {
    const b = el('button', 'icon-btn sm');
    b.type = 'button';
    b.setAttribute('aria-label', label);
    b.title = label.split(':')[0];
    b.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
    return b;
  }

  function startRename(row, c) {
    const input = el('input', 'conv-rename');
    input.type = 'text';
    input.value = c.title;
    input.maxLength = 80;
    input.setAttribute('aria-label', 'Chat name');
    row.replaceChildren(input);
    input.focus();
    input.select();
    let finished = false;
    const finish = async (save) => {
      if (finished) return;
      finished = true;
      const title = input.value.trim();
      if (save && title && title !== c.title) {
        try {
          const d = await SG.api('PATCH', '/api/conversations/' + c.id, { title });
          c.title = d.conversation.title;
          if (c.id === S.activeId) setTitle(c.title);
          SG.toast('Chat renamed.');
        } catch (err) { SG.toast(err.message, 'error'); }
      } else if (save && !title) SG.toast('A chat name cannot be empty.', 'error');
      renderList();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
  }

  async function deleteConversation(c) {
    const ok = await SG.confirmDialog({ title: 'Delete this chat?', message: `"${c.title}" and all its messages will be permanently deleted. This cannot be undone.`, confirmLabel: 'Delete chat', danger: true });
    if (!ok) return;
    try {
      await SG.api('DELETE', '/api/conversations/' + c.id);
    } catch (err) { if (err.status !== 404) { SG.toast(err.message, 'error'); return; } }
    S.convs = S.convs.filter((x) => x.id !== c.id);
    SG.toast('Chat deleted.');
    if (c.id === S.activeId) SG.go('/chat'); else renderList();
  }

  async function loadList() {
    S.loadingList = true; S.listError = '';
    renderList();
    try {
      const q = S.query ? '?q=' + encodeURIComponent(S.query) : '';
      S.convs = (await SG.api('GET', '/api/conversations' + q)).conversations;
    } catch (err) { S.listError = err.message; }
    S.loadingList = false;
    renderList();
  }

  function touchConversation(conv) { // move to the top of the list with its fresh title / time
    const i = S.convs.findIndex((c) => c.id === conv.id);
    if (i >= 0) S.convs.splice(i, 1);
    S.convs.unshift(conv);
    renderList();
  }

  /* ---------- Drawer (mobile) ---------- */
  function openDrawer() {
    $('#chatShell').classList.add('side-open');
    $('#sideBackdrop').hidden = false;
    $('#sideOpen').setAttribute('aria-expanded', 'true');
    $('#newChatBtn').focus();
  }
  function closeDrawer() {
    if (!$('#chatShell').classList.contains('side-open')) return;
    $('#chatShell').classList.remove('side-open');
    $('#sideBackdrop').hidden = true;
    $('#sideOpen').setAttribute('aria-expanded', 'false');
  }

  /* ---------- Header / welcome ---------- */
  function setTitle(t) { S.title = t; $('#chatTitle').textContent = t; document.title = t + ' · Study Gap Detector'; }
  function setEmpty(empty) { $('#chatEmpty').hidden = !empty; }
  function renderDocChip() {
    const box = $('#docChip');
    box.replaceChildren();
    box.hidden = !S.docName;
    if (!S.docName) return;
    const clear = el('button', 'remove-btn', 'Stop using');
    clear.type = 'button';
    clear.addEventListener('click', async () => {
      if (!S.activeId) return;
      try { await SG.api('DELETE', `/api/conversations/${S.activeId}/document`); S.docName = null; renderDocChip(); } catch (err) { SG.toast(err.message, 'error'); }
    });
    box.append(el('span', '', `Using document: ${S.docName}`), clear);
  }

  /* ---------- Messages ---------- */
  function copyText(text, btn) {
    const done = () => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1800); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => SG.toast('Could not copy. Select the text and copy it manually.', 'error'));
    else {
      const ta = el('textarea'); ta.value = text; ta.setAttribute('readonly', ''); ta.className = 'offscreen';
      document.body.append(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { SG.toast('Could not copy. Select the text and copy it manually.', 'error'); }
      ta.remove();
    }
  }

  function fileCard(att) {
    const card = el('div', 'msg-file');
    if (att.kind === 'image' && att.url) {
      const img = el('img', 'thumb');
      img.src = att.url;
      img.alt = 'Preview of ' + att.file.name;
      card.append(img);
    } else card.append(el('div', 'pdf-icon', att.kind === 'image' ? 'IMG' : 'PDF'));
    const info = el('div');
    info.append(el('div', 'fname', att.file.name), el('div', 'fmeta', att.file.size !== undefined ? `${att.kind === 'pdf' ? 'PDF' : 'Image'} · ${fmtSize(att.file.size)}` : (att.kind === 'pdf' ? 'PDF' : 'Image')));
    card.append(info);
    return card;
  }

  function addUser(text, att) {
    setEmpty(false);
    const div = el('div', 'msg user');
    if (text) div.append(el('p', '', text));
    if (att) div.append(fileCard(att));
    $('#chatLog').append(div);
    return div;
  }

  function actionRow(actions) {
    const row = el('div', 'agent-actions');
    actions.forEach((act) => {
      const b = el('button', 'btn btn-secondary btn-sm', act.label);
      b.type = 'button';
      b.addEventListener('click', () => SG.quiz.ensureLoaded().then(() => SG.quiz.runAgentAction(act.action))); // only a click runs an action, never the model
      row.append(b);
    });
    return row;
  }

  function stepsBlock(steps) {
    const box = el('div', 'steps');
    box.append(el('strong', '', 'Agent steps'));
    steps.forEach((st) => { const r = el('div', 'step done'); r.append(el('span', 'dot', '✓'), document.createTextNode(' ' + st.label)); box.append(r); });
    return box;
  }

  function addCopy(div, getText) {
    const bar = el('div', 'msg-tools');
    const b = el('button', 'tool-btn', 'Copy');
    b.type = 'button';
    b.setAttribute('aria-label', 'Copy this answer');
    b.addEventListener('click', () => copyText(getText(), b));
    bar.append(b);
    div.append(bar);
  }

  function addAssistantSaved(m) { // an assistant message loaded from the database
    const meta = m.meta || {};
    const div = el('div', 'msg bot' + (meta.partial ? ' partial' : ''));
    if (meta.steps && meta.steps.length) div.append(stepsBlock(meta.steps));
    const md = el('div', 'md');
    renderMarkdown(m.content, md);
    div.append(md);
    if (meta.actions && meta.actions.length) div.append(actionRow(meta.actions));
    if (meta.partial) div.append(el('small', '', 'This answer is incomplete (it was stopped or interrupted).'));
    else if (meta.model) div.append(el('small', '', `AI answer · ${meta.model}` + (meta.notes && meta.notes.length ? ' · ' + meta.notes.join(' ') : '')));
    addCopy(div, () => m.content);
    $('#chatLog').append(div);
  }

  function addError(message, retry) {
    const div = el('div', 'msg bot error');
    div.setAttribute('role', 'alert');
    div.append(el('p', '', message));
    if (retry) {
      const b = el('button', 'retry', 'Retry');
      b.type = 'button';
      b.addEventListener('click', () => { div.remove(); retry(); });
      div.append(b);
    }
    $('#chatLog').append(div);
    toBottom();
    return div;
  }

  function renderMessages(messages) {
    $('#chatLog').replaceChildren();
    S.unanswered = null;
    messages.forEach((m) => {
      if (m.role === 'user') {
        const att = m.meta && m.meta.attachment ? { file: { name: m.meta.attachment.name }, kind: /\.pdf$/i.test(m.meta.attachment.name) ? 'pdf' : 'image', url: null } : null;
        addUser(/^\[attached .*\]$/.test(m.content) ? '' : m.content, att);
      } else addAssistantSaved(m);
    });
    setEmpty(!messages.length);
    const last = messages[messages.length - 1];
    if (last && last.role === 'user') { // the answer never arrived (error, closed tab): offer to try again
      S.unanswered = last.content;
      addError('This message did not get an answer.', () => deliver({ text: last.content.startsWith('[attached ') ? '' : last.content, attachment: null }, true));
    }
  }

  /* ---------- Opening / starting conversations ---------- */
  async function openConversation(id) {
    abortStream();
    S.activeId = id;
    renderList();
    $('#chatLog').replaceChildren();
    setEmpty(false);
    $('#chatLoading').hidden = false;
    try {
      const d = await SG.api('GET', '/api/conversations/' + id);
      if (S.activeId !== id) return;
      setTitle(d.conversation.title);
      S.docName = d.conversation.doc_name;
      renderDocChip();
      renderMessages(d.messages);
      stick = true;
      requestAnimationFrame(() => toBottom(true));
    } catch (err) {
      if (S.activeId !== id) return;
      if (err.status === 404) { SG.toast('That conversation was not found. It may have been deleted.', 'error'); S.activeId = null; SG.go('/chat'); return; }
      addError(err.message, () => openConversation(id));
    } finally {
      $('#chatLoading').hidden = true;
    }
  }

  function newChatView() {
    abortStream();
    S.activeId = null;
    S.docName = null;
    renderDocChip();
    setTitle('New chat');
    $('#chatLog').replaceChildren();
    setEmpty(true);
    renderList();
    $('#chatInput').focus();
  }

  async function ensureConversation() {
    if (S.activeId) return S.activeId;
    const d = await SG.api('POST', '/api/conversations', {});
    S.activeId = d.conversation.id;
    S.convs.unshift(d.conversation);
    renderList();
    SG.router.silent('/chat/' + S.activeId); // put the chat in the address bar without reloading the screen
    return S.activeId;
  }

  /* ---------- Attachments ---------- */
  const ALLOWED_MIME = { 'application/pdf': 'pdf', 'image/jpeg': 'image', 'image/png': 'image' };
  function fileKind(file) {
    if (file.type) return ALLOWED_MIME[file.type] || null;
    const name = file.name.toLowerCase();
    if (name.endsWith('.pdf')) return 'pdf';
    return /\.(jpe?g|png)$/.test(name) ? 'image' : null;
  }
  function showAttachError(msg) { $('#attachError').textContent = msg; $('#attachError').hidden = !msg; }
  function renderTray() {
    const tray = $('#attachTray');
    tray.replaceChildren();
    tray.hidden = !attachment;
    updateSend();
    if (!attachment) return;
    const remove = el('button', 'remove-btn', 'Remove');
    remove.type = 'button';
    remove.setAttribute('aria-label', 'Remove attachment ' + attachment.file.name);
    remove.addEventListener('click', removeAttachment);
    tray.append(fileCard(attachment), remove);
  }
  function removeAttachment() {
    if (attachment && attachment.url) URL.revokeObjectURL(attachment.url);
    attachment = null;
    renderTray();
  }
  function setAttachment(file) {
    const kind = fileKind(file);
    if (!kind) return showAttachError('Unsupported file type. Please attach a PDF, JPG or PNG.');
    if (file.size === 0) return showAttachError('This file is empty. Please choose another file.');
    if (file.size > CONFIG.MAX_ATTACHMENT_BYTES) return showAttachError(`This file is ${fmtSize(file.size)}. The limit is ${fmtSize(CONFIG.MAX_ATTACHMENT_BYTES)}.`);
    showAttachError('');
    removeAttachment();
    attachment = { file, kind, url: kind === 'image' ? URL.createObjectURL(file) : null };
    renderTray();
  }
  function readAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
      reader.onerror = () => reject(new Error('Your browser could not read the file.'));
      reader.readAsDataURL(file);
    });
  }

  /* ---------- Sending ---------- */
  function setBusy(busy) {
    S.busy = busy;
    $('#sendBtn').hidden = busy;      // while an answer is streaming the student can Stop it instead
    $('#stopBtn').hidden = !busy;
    $('#attachBtn').disabled = busy;
    $('#chatForm').classList.toggle('busy', busy);
  }
  function abortStream() { if (chatAbort) chatAbort.abort(); }

  function createStreamView() {
    setEmpty(false);
    const div = el('div', 'msg bot streaming');
    div.setAttribute('aria-busy', 'true'); // screen readers wait for the finished answer instead of reading every token
    const steps = el('div', 'steps');
    steps.hidden = true;
    steps.append(el('strong', '', 'Agent steps'));
    const answer = el('div', 'md');
    const status = el('div', 'stream-status', 'Thinking…');
    div.append(steps, answer, status);
    $('#chatLog').append(div);
    return { div, steps, answer, status };
  }

  async function deliver(payload, retry) {
    setBusy(true);
    const att = payload.attachment;
    const view = createStreamView();
    if (att) view.status.textContent = 'Reading your file and thinking…';
    toBottom(true);
    const ctrl = new AbortController();
    chatAbort = ctrl;
    let text = '', raf = 0, finished = false, final = null, actions = [];
    const stepRows = new Map();
    const schedule = (fn) => (window.requestAnimationFrame ? window.requestAnimationFrame(fn) : setTimeout(fn, 16));

    // Re-render the Markdown from the full text so far (cheap for answers of this size), at most once per frame.
    const render = () => {
      raf = 0;
      view.answer.replaceChildren();
      renderMarkdown(text, view.answer);
      if (!finished && text) {
        let host = view.answer.lastElementChild;
        if (host && (host.tagName === 'UL' || host.tagName === 'OL')) host = host.lastElementChild;
        if (!host || host.tagName === 'PRE') host = view.answer;
        host.append(el('span', 'caret', '▍'));
      }
      toBottom();
    };
    const queue = () => { if (!raf) raf = schedule(render); };

    const onEvent = (ev) => {
      switch (ev.type) {
        case 'status':
          view.status.hidden = ev.state === 'writing';
          if (ev.state === 'thinking') view.status.textContent = stepRows.size ? 'Thinking about the results…' : 'Thinking…';
          break;
        case 'step_start': {
          view.steps.hidden = false;
          const row = el('div', 'step running');
          row.append(el('span', 'dot', '●'), document.createTextNode(' ' + ev.label));
          view.steps.append(row);
          stepRows.set(ev.id, row);
          toBottom();
          break;
        }
        case 'step_done': {
          const row = stepRows.get(ev.id) || view.steps.appendChild(el('div', 'step'));
          view.steps.hidden = false;
          row.className = 'step done';
          row.replaceChildren(el('span', 'dot', '✓'), document.createTextNode(' ' + ev.label));
          break;
        }
        case 'token': text += ev.text; queue(); break;
        case 'reset': text = ''; queue(); break; // the text so far was only a lead-in to a tool call
        case 'actions': actions = Array.isArray(ev.actions) ? ev.actions : []; break;
        case 'done': final = ev; break;
        case 'error': { const e = new Error(ev.message || 'The AI could not finish the answer.'); e.fromServer = true; throw e; }
        default: break; // 'ping' and unknown events are ignored
      }
    };
    const finishView = () => {
      finished = true;
      view.status.remove();
      view.div.removeAttribute('aria-busy');
      view.div.classList.remove('streaming');
      if (raf) raf = 0;
      view.answer.replaceChildren();
      renderMarkdown(text, view.answer);
      toBottom();
    };

    try {
      const id = await ensureConversation();
      const body = { message: payload.text, use_results: $('#useResults').checked };
      if (retry) body.retry = true;
      if (att) body.file = { name: att.file.name, data: await readAsBase64(att.file) };
      await StudyGapReport.postStream(`/api/conversations/${id}/messages`, body, { baseUrl: SG.API_BASE, signal: ctrl.signal, idleMs: CONFIG.CHAT_IDLE_TIMEOUT_MS, onEvent });
      if (!final) throw new Error('The connection closed before the answer was complete.');
      text = final.reply;
      finishView();
      if (final.conversation) { touchConversation(final.conversation); if (S.activeId === final.conversation.id) setTitle(final.conversation.title); }
      if (final.document) { S.docName = final.document.name; renderDocChip(); }
      const acts = final.actions && final.actions.length ? final.actions : actions;
      if (acts.length) view.div.append(actionRow(acts));
      view.div.append(el('small', '', `AI answer · ${final.model}` + (final.notes && final.notes.length ? ' · ' + final.notes.join(' ') : '')));
      addCopy(view.div, () => final.reply);
      toBottom();
    } catch (err) {
      if (err.status === 401) { view.div.remove(); SG.auth.expired(); }
      else if (err.name === 'AbortError') { // the student pressed Stop (or opened another chat): keep what has arrived
        finishView();
        if (text) { view.div.classList.add('partial'); view.div.append(el('small', '', 'Stopped by you. This answer is incomplete.')); }
        else if (!view.steps.hidden) view.div.append(el('small', '', 'Stopped by you.'));
        else { view.div.remove(); addError('Stopped. Nothing was answered.', () => deliver(payload, true)); }
      } else {
        finishView();
        if (text) { view.div.classList.add('partial'); view.div.append(el('small', '', 'This answer is incomplete.')); }
        else if (view.steps.hidden) view.div.remove();
        addError(err.message || 'Something went wrong. Please try again.', () => deliver(payload, true));
      }
    } finally {
      chatAbort = null;
      setBusy(false);
      if (S.activeId) $('#chatInput').focus();
    }
  }

  function handleSend(rawText) {
    const text = rawText.trim();
    if ((!text && !attachment) || S.busy) return;
    const att = attachment;
    attachment = null; // the object URL now belongs to the sent message
    renderTray();
    showAttachError('');
    addUser(text, att);
    deliver({ text, attachment: att });
  }

  function updateSend() {
    const empty = !$('#chatInput').value.trim() && !attachment;
    $('#sendBtn').disabled = empty;
  }
  function autosize() {
    const t = $('#chatInput');
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight, 200) + 'px';
  }

  /* ---------- Init ---------- */
  function init() {
    PROMPTS.forEach((p) => {
      const b = el('button', 'chip', p);
      b.type = 'button';
      b.addEventListener('click', () => handleSend(p));
      $('#chips').append(b);
    });
    const input = $('#chatInput');
    $('#chatForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const value = input.value;
      if (!value.trim() && !attachment) return;
      input.value = '';
      autosize(); updateSend();
      handleSend(value);
    });
    input.addEventListener('keydown', (e) => { // Enter sends, Shift+Enter makes a new line (and Enter inside an IME composition is left alone)
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); $('#chatForm').requestSubmit ? $('#chatForm').requestSubmit() : $('#chatForm').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true })); }
    });
    input.addEventListener('input', () => { autosize(); updateSend(); });
    $('#newChatBtn').addEventListener('click', () => { closeDrawer(); if (SG.router.current().path === '/chat') newChatView(); else SG.go('/chat'); });
    $('#stopBtn').addEventListener('click', abortStream);
    $('#attachBtn').addEventListener('click', () => $('#fileInput').click());
    $('#fileInput').addEventListener('change', (e) => {
      const file = e.target.files[0];
      e.target.value = '';
      if (file) setAttachment(file);
    });
    $('#sideOpen').addEventListener('click', openDrawer);
    $('#sideClose').addEventListener('click', () => { closeDrawer(); $('#sideOpen').focus(); });
    $('#sideBackdrop').addEventListener('click', () => { closeDrawer(); $('#sideOpen').focus(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#chatShell').classList.contains('side-open')) { closeDrawer(); $('#sideOpen').focus(); } });
    $('#sideLogout').addEventListener('click', () => SG.auth.logout());
    let t = null;
    $('#convSearch').addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { S.query = e.target.value.trim(); loadList(); }, 250); });
    scroller().addEventListener('scroll', onScroll);
    $('#jumpBtn').addEventListener('click', () => { toBottom(true); });
    SG.auth.onChange(() => { // logging out or switching account must never leave another person's chats on screen
      S.convs = []; S.activeId = null; S.query = ''; S.docName = null; entered = false;
      abortStream();
      $('#chatLog').replaceChildren(); $('#convList').replaceChildren(); $('#convSearch').value = '';
      removeAttachment();
    });

    SG.views.chat = {
      async enter(params) {
        SG.showView('chat', { focus: false });
        $('#sideUser').textContent = SG.auth.user ? SG.auth.user.name : '';
        $('#sideUser').title = SG.auth.user ? SG.auth.user.email : '';
        const id = params[0] && /^[0-9a-f]{32}$/.test(params[0]) ? params[0] : null;
        if (!entered) { entered = true; await SG.quiz.ensureLoaded(); await loadList(); }
        if (id) { if (id !== S.activeId) await openConversation(id); else renderList(); }
        else newChatView();
        updateSend();
      },
      leave() { abortStream(); closeDrawer(); }
    };
  }
  init();
})();
