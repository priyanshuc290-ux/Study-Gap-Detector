/*
 * End-to-end browser-flow tests (optional, needs Node 18+ and `npm install` in this folder).
 *   cd e2e && npm install && npm test
 * It starts the REAL backend (backend/server.py) twice:
 *   phase A: no API key  -> demo mode, used to re-check Mode 1 (quiz, results, dashboard, report)
 *   phase B: key "test-key" pointing at e2e/mock_provider.py (a fake OpenAI-compatible provider)
 * and drives the REAL frontend files inside jsdom. No real AI provider and no real key are ever used.
 */
const { JSDOM } = require('jsdom');
const { spawn } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PY = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const APP = 'http://127.0.0.1:8000/';
const MOCK = 'http://127.0.0.1:9911';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : '  -> ' + detail}`);
}

function start(args, env) {
  const p = spawn(PY, args, { cwd: ROOT, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  p.output = '';
  p.stdout.on('data', (d) => { p.output += d; });
  p.stderr.on('data', (d) => { p.output += d; });
  return p;
}
async function stop(p) { p.kill(); await sleep(300); }
async function waitUp(url) {
  for (let i = 0; i < 60; i++) { try { await fetch(url); return; } catch (e) { await sleep(200); } }
  throw new Error('server did not start: ' + url);
}
async function waitFor(fn, ms = 8000, what = 'condition') {
  const t0 = Date.now();
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() - t0 > ms) throw new Error('timed out waiting for ' + what);
    await sleep(20);
  }
}
const mock = async (p = '/_state', opts) => (await fetch(MOCK + p, opts)).json();

async function openPage({ storage, fetchHook } = {}) {
  const dom = await JSDOM.fromURL(APP, {
    runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true,
    beforeParse(window) {
      const realFetch = globalThis.fetch;
      window.fetchLog = [];
      window.fetch = (u, o) => {
        const url = new URL(u, APP).toString();
        window.fetchLog.push(new URL(url).pathname);
        if (fetchHook) { const r = fetchHook(url, o); if (r) return r; }
        return realFetch(url, o);
      };
      window.AbortController = globalThis.AbortController; // Node's fetch needs Node's AbortSignal
      window.TextDecoder = globalThis.TextDecoder;
      window.confirm = () => true;
      window.scrollTo = () => {};
      if (storage) Object.entries(storage).forEach(([k, v]) => window.localStorage.setItem(k, v));
    }
  });
  await waitFor(() => dom.window.document.readyState === 'complete', 10000, 'page load');
  return dom;
}
const $ = (w, sel) => w.document.querySelector(sel);
const $$ = (w, sel) => [...w.document.querySelectorAll(sel)];
const click = (w, sel) => { const e = typeof sel === 'string' ? $(w, sel) : sel; e.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true })); };
const submit = (w, sel) => $(w, sel).dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
const visible = (w, sel) => !$(w, sel).hidden;

/* ---------------- phase A: demo backend, Mode 1 regression ---------------- */
async function phaseA() {
  console.log('\n== Phase A: backend without key (demo mode) - Mode 1 and chat error path ==');
  const server = start(['backend/server.py'], { LLM_API_KEY: '', LLM_BASE_URL: MOCK + '/v1', PORT: '8000' });
  await waitUp(APP + 'api/health');
  let attemptsJson = null;
  try {
    const dom = await openPage();
    const w = dom.window;
    check('dashboard shows the two modes', visible(w, '#view-dashboard') && $$(w, '.mode-card').length === 2);
    click(w, '[data-nav="gap-setup"]');
    check('setup: sample bank available for Data Structures', !$(w, '#gapContinue').disabled);
    submit(w, '#gapForm');
    check('quiz setup screen', visible(w, '#view-quiz-setup'));
    submit(w, '#quizForm');
    check('quiz screen with questions', visible(w, '#view-quiz') && /Question 1 of/.test($(w, '#quizCount').textContent));
    const total = Number($(w, '#quizCount').textContent.match(/of (\d+)/)[1]);
    for (let i = 0; i < total; i++) {
      const radios = $$(w, '#quizOptions input');
      const pick = radios[0];
      pick.checked = true;
      pick.dispatchEvent(new w.Event('change', { bubbles: true }));
      if (i < total - 1) click(w, '#nextBtn');
    }
    check('submit button on the last question', visible(w, '#submitBtn'));
    click(w, '#submitBtn');
    check('results screen', visible(w, '#view-results'));
    const stored = JSON.parse(w.localStorage.getItem('asga.attempts.v1'));
    const a = stored[stored.length - 1];
    check('attempt saved in localStorage', stored.length === 1 && a.total === total);
    check('score shown matches the saved attempt', $(w, '#resScore').textContent === `${a.score} / ${a.total}`, $(w, '#resScore').textContent);
    const okClass = Object.entries(a.topics).every(([t, v]) => {
      const expect = v.answered < 2 ? 'insufficient' : (v.correct / v.answered < 0.6 ? 'weak' : 'strong');
      const got = a.weak.includes(t) ? 'weak' : a.strong.includes(t) ? 'strong' : a.insufficient.includes(t) ? 'insufficient' : '?';
      return got === expect;
    });
    check('topic classes follow the deterministic rules', okClass);
    check('topic bars rendered', $$(w, '#topicBars .bar-row').length === Object.keys(a.topics).length);
    await waitFor(() => $(w, '#reportBox').children.length > 0, 8000, 'report');
    check('study-gap report rendered (demo mode, honest label)', /demo/i.test($(w, '#reportMode').textContent), $(w, '#reportMode').textContent);
    check('resources and plan sections filled', $(w, '#resourcesBox').children.length > 0 && $(w, '#planBox').children.length > 0);
    check('review of incorrect answers lists explanations', a.score === a.total || /Explanation:/.test($(w, '#reviewList').textContent));
    if (a.weak.length) {
      click(w, '#weakBtn');
      check('weak-topic practice starts', visible(w, '#view-quiz') && /Weak/i.test($(w, '#quizType').textContent), $(w, '#quizType').textContent);
    } else {
      click(w, '#mixedBtn');
      check('mixed practice starts', visible(w, '#view-quiz'));
    }
    click(w, '[data-nav="dashboard"]');
    check('dashboard shows the saved attempt', /Data Structures/.test($(w, '#progressBox').textContent));

    // chat without a key: a normal HTTP error before any streaming, shown with Retry
    click(w, '[data-nav="chat"]');
    $(w, '#chatInput').value = 'hello';
    submit(w, '#chatForm');
    await waitFor(() => $(w, '.msg.error'), 5000, 'error message');
    check('chat without key: honest error + Retry, Send is back', /AI key/.test($(w, '.msg.error').textContent) && !!$(w, '.msg.error .retry') && visible(w, '#sendBtn') && !visible(w, '#stopBtn'));
    attemptsJson = w.localStorage.getItem('asga.attempts.v1');
    dom.window.close();
  } finally { await stop(server); }
  return attemptsJson;
}

/* ---------------- phase B: streaming chat against the mock provider ---------------- */
function pdfBytes(text) {
  const objs = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>'];
  const stream = `BT /F1 12 Tf 20 100 Td (${text}) Tj ET`;
  objs.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  let out = '%PDF-1.4\n'; const offs = [];
  objs.forEach((o, i) => { offs.push(out.length); out += `${i + 1} 0 obj\n${o}\nendobj\n`; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offs.map((o) => String(o).padStart(10, '0') + ' 00000 n \n').join('');
  return out + `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
}

const idle = (w) => waitFor(() => !$(w, '.msg.streaming') && visible(w, '#sendBtn'), 15000, 'chat to be idle');

async function send(w, text) {
  $(w, '#chatInput').value = text;
  submit(w, '#chatForm');
}

async function phaseB(attemptsJson) {
  console.log('\n== Phase B: streaming chat (real backend + mock OpenAI-compatible provider) ==');
  const provider = start(['e2e/mock_provider.py'], {});
  await waitUp(MOCK + '/_state');
  const server = start(['backend/server.py'], { LLM_API_KEY: 'test-key', LLM_BASE_URL: MOCK + '/v1', LLM_MODEL: 'mock-model', PORT: '8000', CHAT_RATE_LIMIT_PER_MIN: '1000' });
  await waitUp(APP + 'api/health');
  try {
    const storage = { 'asga.attempts.v1': attemptsJson };
    const weakSeeded = JSON.parse(attemptsJson)[0].weak.length > 0;
    check('precondition: the seeded quiz attempt has at least one weak topic', weakSeeded);
    const dom = await openPage({ storage });
    const w = dom.window;
    click(w, '[data-nav="chat"]');

    // ---- B1: tools + live steps + progressive answer
    await mock('/_reset', { method: 'POST' });
    const timeline = [];
    // Exact DOM order (a sampler is too coarse): did the first step row get added before the first answer text?
    let seq = 0, firstStepSeq = null, firstTextSeq = null;
    const observer = new w.MutationObserver((records) => records.forEach((r) => {
      seq++;
      r.addedNodes.forEach((n) => {
        if (firstStepSeq === null && n.nodeType === 1 && n.classList.contains('step')) firstStepSeq = seq;
        const inMd = n.parentElement && n.parentElement.closest && n.parentElement.closest('.md');
        if (firstTextSeq === null && inMd && (n.textContent || '').replace('▍', '').trim()) firstTextSeq = seq;
      });
    }));
    observer.observe($(w, '#chatLog'), { childList: true, subtree: true });
    const t0 = Date.now();
    const sampler = setInterval(() => {
      const md = $(w, '.msg.bot:last-of-type .md');
      timeline.push({
        t: Date.now() - t0,
        thinking: !!$(w, '.msg.streaming .stream-status:not([hidden])'),
        stepsDone: $$(w, '.msg.bot .step.done').length, stepsRunning: $$(w, '.msg.bot .step.running').length,
        len: md ? md.textContent.length : 0, stop: !$(w, '#stopBtn').hidden, send: !$(w, '#sendBtn').hidden
      });
    }, 25);
    await send(w, 'SCENARIO:tools What should I study next?');
    await waitFor(() => !$(w, '.msg.streaming') && $$(w, '.msg.bot small').length > 0, 15000, 'streamed answer');
    clearInterval(sampler);
    const firstStep = timeline.find((s) => s.stepsDone > 0 || s.stepsRunning > 0);
    const firstText = timeline.find((s) => s.len > 0);
    const done = timeline[timeline.length - 1];
    const lens = [...new Set(timeline.map((s) => s.len).filter((l) => l > 0))];
    check('B1 "Thinking…" status is visible before anything else', timeline.some((s) => s.thinking && s.stepsDone === 0 && s.len === 0));
    observer.disconnect();
    check('B1 agent steps appear live, before any answer text', firstStepSeq !== null && firstTextSeq !== null && firstStepSeq < firstTextSeq, JSON.stringify({ firstStepSeq, firstTextSeq }));
    check('B1 steps were visible about 1 s after sending (while the model was still thinking about the next part)', firstStep && firstStep.t > 500, JSON.stringify(firstStep));
    check('B1 the answer grows progressively (several distinct lengths)', lens.length >= 5, `distinct lengths: ${lens.length}`);
    check('B1 first text shows long before the end (not all at once)', firstText && done && (done.t - firstText.t) > 600, `${firstText && firstText.t} -> ${done && done.t}`);
    check('B1 Stop visible while streaming, Send hidden; swapped back after', timeline.some((s) => s.stop && !s.send) && visible(w, '#sendBtn') && !visible(w, '#stopBtn'));
    const bot = $$(w, '.msg.bot').pop();
    check('B1 two steps listed, finished', $$(w, '.step.done', bot).length === 2 || bot.querySelectorAll('.step.done').length === 2);
    check('B1 step labels are the deterministic tool labels', /Checked your quiz results/.test(bot.textContent) && /Prepared a button: Start weak-topic practice/.test(bot.textContent));
    check('B1 final Markdown rendered (bold, bullet list)', bot.querySelectorAll('.md strong').length >= 2 && bot.querySelectorAll('.md li').length === 3);
    check('B1 no streaming caret left behind', bot.querySelectorAll('.caret').length === 0);
    check('B1 meta shows model + not aria-busy', /AI answer · mock-model/.test(bot.textContent) && !bot.hasAttribute('aria-busy'));
    const btn = bot.querySelector('.agent-actions button');
    check('B1 action button prepared, nothing run yet', btn && /Start weak-topic practice/.test(btn.textContent) && visible(w, '#view-chat'));
    const st = await mock();
    check('B1 provider got stream:true with 4 tools, then the tool results', st.requests[0].stream && st.requests[0].tools === 4 && st.requests[1].tool_results === 2, JSON.stringify(st.requests));
    check('B1 get_study_results returned the real quiz data (not invented)', st.requests[1].tool_payloads[0].available === true && st.requests[1].tool_payloads[0].units_most_recent_first[0].subject === 'Data Structures');
    click(w, btn);
    check('B1 clicking the button (and only then) starts weak-topic practice', visible(w, '#view-quiz') && /Weak/i.test($(w, '#quizType').textContent));
    click(w, '[data-nav="chat"]');

    // ---- B2: follow-up keeps history; toggle off sends no results
    $(w, '#useResults').checked = false;
    await send(w, 'SCENARIO:tools and now without my results');
    await waitFor(() => $$(w, '.msg.bot small').length >= 2 && !$(w, '.msg.streaming'), 15000, 'second answer');
    const st2 = await mock();
    const second = st2.requests[st2.requests.length - 1];
    check('B2 history from the first turn was sent along', st2.requests[2].n_messages > st2.requests[0].n_messages, JSON.stringify(st2.requests.map((r) => r.n_messages)));
    check('B2 toggle off: the tool reports no results available', second.tool_payloads[0].available === false);
    $(w, '#useResults').checked = true;

    // ---- B3: Stop
    await mock('/_reset', { method: 'POST' });
    await send(w, 'SCENARIO:slow talk for a while');
    await waitFor(() => { const m = $$(w, '.msg.streaming .md').pop(); return m && m.textContent.length > 15; }, 8000, 'slow text');
    const stopAt = Date.now();
    click(w, '#stopBtn');
    await waitFor(() => visible(w, '#sendBtn') && !visible(w, '#stopBtn'), 2000, 'controls restored');
    check('B3 Stop restores Send within 2 s', Date.now() - stopAt < 2000);
    const partial = $$(w, '.msg.bot').pop();
    check('B3 partial answer kept and labelled', partial.classList.contains('partial') && /word0/.test(partial.textContent) && /Stopped by you/.test(partial.textContent), partial.textContent.slice(0, 120));
    check('B3 no error bubble after Stop', !$(w, '.msg.error'));
    let disconnected = false;
    for (let i = 0; i < 60 && !disconnected; i++) { disconnected = (await mock()).slow_disconnected; if (!disconnected) await sleep(100); }
    check('B3 after Stop the backend closed the provider connection (provider stopped generating)', disconnected);
    const lenAfter = partial.querySelector('.md').textContent.length;
    await sleep(600);
    check('B3 no more text arrives after Stop', partial.querySelector('.md').textContent.length === lenAfter);
    await send(w, 'SCENARIO:plain what is a stack?');
    await waitFor(() => $$(w, '.msg.bot').pop().textContent.includes('last-in'), 8000, 'answer after stop');
    await idle(w);
    check('B3 chat works normally after a Stop', true);

    // ---- B4: provider rejects streaming -> fallback
    await mock('/_reset', { method: 'POST' });
    await send(w, 'SCENARIO:nostream hello');
    await waitFor(() => $$(w, '.msg.bot').pop().textContent.includes('Whole answer'), 10000, 'fallback answer');
    await idle(w);
    const st4 = await mock();
    check('B4 stream was tried first, then the non-streaming call', st4.requests[0].stream === true && st4.requests.some((r) => r.stream === false), JSON.stringify(st4.requests));
    check('B4 the note says honestly that streaming was not available', /did not accept streaming/.test($$(w, '.msg.bot').pop().textContent));

    // ---- B5: error mid-stream keeps partial text, Retry works
    await mock('/_reset', { method: 'POST' });
    await send(w, 'SCENARIO:flaky please answer');
    await waitFor(() => $(w, '.msg.error'), 10000, 'mid-stream error');
    const partialMsg = $$(w, '.msg.partial').pop();
    check('B5 partial text stays visible and is marked incomplete', partialMsg && /This will break/.test(partialMsg.textContent) && /incomplete/i.test(partialMsg.textContent));
    check('B5 error message has Retry and controls are back', !!$(w, '.msg.error .retry') && visible(w, '#sendBtn'));
    click(w, '.msg.error .retry');
    await waitFor(() => $$(w, '.msg.bot').pop().textContent.includes('Second try worked'), 10000, 'retry answer');
    await idle(w);
    check('B5 Retry replaced the broken answer (no error or partial left)', !$(w, '.msg.error') && !$$(w, '.msg.partial').some((m) => /This will break/.test(m.textContent)));

    // ---- B6: PDF attachment still works and is reused for follow-ups
    await mock('/_reset', { method: 'POST' });
    const file = new w.File([pdfBytes('Stacks and queues notes')], 'notes.pdf', { type: 'application/pdf' });
    const input = $(w, '#fileInput');
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new w.Event('change', { bubbles: true }));
    check('B6 PDF shows in the attachment tray', !$(w, '#attachTray').hidden);
    await send(w, 'SCENARIO:plain summarise this');
    await waitFor(() => !$(w, '#docChip').hidden && !$(w, '.msg.streaming'), 15000, 'pdf answer');
    check('B6 backend extracted the PDF text and the chip shows it', /notes\.pdf/.test($(w, '#docChip').textContent));
    await send(w, 'SCENARIO:plain and a follow-up');
    await waitFor(() => !$(w, '.msg.streaming') && $$(w, '.msg.bot').length >= 2, 10000, 'follow-up');
    const st6 = await mock();
    check('B6 the document text reached the model on the first and the follow-up turn', st6.requests.length >= 2 && st6.requests.every((r) => r.system_has_doc));

    // ---- B7: new chat
    click(w, '#newChatBtn');
    check('B7 new chat clears the log', $$(w, '#chatLog .msg').length === 0);

    check('key never appears in the page or in localStorage', !w.document.documentElement.outerHTML.includes('test-key') && !JSON.stringify({ ...w.localStorage }).includes('test-key'));
    check('server log never contains the key or message text', !server.output.includes('test-key') && !/SCENARIO/.test(server.output));
    w.close();

    // ---- B8: older backend without the stream route -> one-shot endpoint
    const old = await openPage({ storage, fetchHook: (url) => (url.endsWith('/api/chat/stream') ? Promise.resolve(new Response('{"error":{"message":"Not found."}}', { status: 404, headers: { 'Content-Type': 'application/json' } })) : null) });
    click(old.window, '[data-nav="chat"]');
    await send(old.window, 'SCENARIO:plain hi from an old backend');
    await waitFor(() => $$(old.window, '.msg.bot').pop() && /last-in/.test($$(old.window, '.msg.bot').pop().textContent), 10000, 'one-shot answer');
    check('B8 404 on the stream route falls back to /api/chat', old.window.fetchLog.includes('/api/chat') && !$(old.window, '.msg.error'), old.window.fetchLog.join(','));
    old.window.close();
  } finally { await stop(server); await stop(provider); }
}

(async () => {
  let failed = false;
  try {
    const attempts = await phaseA();
    await phaseB(attempts);
  } catch (e) {
    failed = true;
    console.log('FAIL  test run crashed: ' + (e && e.stack || e));
  }
  const bad = results.filter((r) => !r.ok);
  console.log(`\n${results.length - bad.length}/${results.length} checks passed` + (bad.length ? `; failed: ${bad.map((b) => b.name).join(' | ')}` : ''));
  process.exit(failed || bad.length ? 1 : 0);
})();
