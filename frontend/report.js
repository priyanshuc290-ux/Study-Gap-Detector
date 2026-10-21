/*
 * Study Gap report module: talks to the backend and renders the report.
 * It never sees API keys; the backend holds them. It also never computes scores:
 * everything numeric comes from the quiz engine in app.js.
 */
(() => {
  'use strict';

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  // Every request carries the session cookie (credentials) and this header, which the backend requires on changes so that
  // another website cannot send requests on the student's behalf.
  const HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'StudyGap' };

  // Generic JSON POST to the backend with timeout and friendly errors. Shared by the report and the chat.
  async function post(path, payload, { baseUrl, timeoutMs }) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(`${baseUrl}${path}`, {
        method: 'POST', headers: HEADERS, credentials: 'include', body: JSON.stringify(payload), signal: ctrl.signal
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const err = new Error((data && data.error && data.error.message) || `The server returned an error (${res.status}).`);
        err.status = res.status;
        err.resources = data && data.resources;
        throw err;
      }
      return data;
    } catch (err) {
      if (err.name === 'AbortError') throw new Error('The request timed out. Please retry.');
      if (err.name === 'TypeError') { // network failure (fetch rejects with TypeError)
        const hint = location.protocol === 'file:' ? ' You opened the page as a file; open http://localhost:8000 instead.' : '';
        throw new Error('Could not reach the backend. Start it with "python backend/server.py" (see backend/README.md).' + hint);
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  // Streaming POST (NDJSON: one JSON event per line). Calls onEvent(event) as each line arrives.
  //   signal      - an AbortSignal; aborting it (the Stop button) throws an AbortError so the caller can tell "stopped" apart
  //   idleMs      - give up if NOTHING arrives for this long (the backend sends small "ping" events while the model thinks)
  // Throws errors with a friendly message. err.status = HTTP status (e.g. 404 = backend too old to stream).
  async function postStream(path, payload, { baseUrl, signal, idleMs, onEvent }) {
    const inner = new AbortController();
    let timedOut = false, timer = null;
    const bump = () => { clearTimeout(timer); timer = setTimeout(() => { timedOut = true; inner.abort(); }, idleMs); };
    const onAbort = () => inner.abort();
    if (signal) { if (signal.aborted) inner.abort(); else signal.addEventListener('abort', onAbort); }
    try {
      bump();
      const res = await fetch(`${baseUrl}${path}`, {
        method: 'POST', headers: HEADERS, credentials: 'include', body: JSON.stringify(payload), signal: inner.signal
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        const err = new Error((data && data.error && data.error.message) || `The server returned an error (${res.status}).`);
        err.status = res.status;
        err.code = data && data.error && data.error.code;
        throw err;
      }
      const handleLine = (line) => {
        if (!line.trim()) return;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { return; } // ignore a damaged line rather than break the whole answer
        onEvent(ev);
      };
      if (!res.body || !res.body.getReader) { // very old browser: no streaming reader, read everything then replay
        (await res.text()).split('\n').forEach(handleLine);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bump();
        buffer += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buffer.indexOf('\n')) >= 0) {
          handleLine(buffer.slice(0, nl));
          buffer = buffer.slice(nl + 1);
        }
      }
      buffer += decoder.decode();
      handleLine(buffer);
    } catch (err) {
      if (err.name === 'AbortError') {
        if (timedOut) throw new Error('The AI stopped responding. Please retry.');
        throw err; // the student pressed Stop
      }
      if (err.name === 'TypeError') { // network failure (fetch rejects with TypeError), or the connection dropped mid-stream
        const hint = location.protocol === 'file:' ? ' You opened the page as a file; open http://localhost:8000 instead.' : '';
        throw new Error('Could not reach the backend, or the connection closed. Start it with "python backend/server.py" (see backend/README.md).' + hint);
      }
      throw err;
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener('abort', onAbort);
    }
  }

  async function request(payload, opts) {
    const data = await post('/api/study-gap-report', payload, opts);
    if (!data || !data.report || !data.mode) throw new Error('The server sent an unexpected response.');
    return data;
  }

  function block(title, items) {
    if (!items || !items.length) return null;
    const box = el('div', 'rep-block');
    box.append(el('h4', '', title));
    const ul = el('ul');
    items.forEach((i) => ul.append(el('li', '', i)));
    box.append(ul);
    return box;
  }

  function renderReport(box, data) {
    box.innerHTML = '';
    const r = data.report;
    box.append(el('p', 'notice' + (data.mode === 'ai' ? ' ok' : ''), data.mode === 'ai'
      ? `AI-generated interpretation (model: ${data.model}). Scores and topic results come from the quiz engine, not the AI. AI text can still be wrong, so check it against your notes.`
      : 'Demo report: rule-based text, no AI model was used. It restates your results and cannot explain why you got things wrong. See backend/README.md to enable real AI analysis.'));

    const overall = el('div', 'rep-block');
    overall.append(el('h4', '', 'Overall assessment'), el('p', '', r.overall.summary));
    box.append(overall);
    [block('Key learning gaps', r.overall.key_gaps), block('What you appear to understand well', r.overall.strengths)].forEach((b) => b && box.append(b));

    box.append(el('h4', 'rep-h', 'Topic-wise analysis'));
    r.topics.forEach((t) => {
      const card = el('div', 'topic-card ' + t.priority.toLowerCase());
      const head = el('div', 'bar-top');
      head.append(el('strong', '', t.topic));
      const tags = el('span');
      tags.append(el('span', 'tag prio', 'Priority: ' + t.priority), el('span', 'tag', t.confidence === 'observed' ? 'Observed' : 'Suspected'));
      head.append(tags);
      card.append(head);
      [['Observed performance', t.observed], ['Gap', t.gap], ['Evidence', t.evidence], ['Why it matters', t.why_it_matters]].forEach(([k, v]) => {
        const p = el('p', 'kv');
        p.append(el('strong', '', k + ': '), document.createTextNode(v));
        card.append(p);
      });
      box.append(card);
    });

    const tips = r.tips;
    box.append(el('h4', 'rep-h', 'Personalized improvement tips'));
    [block('Revise first', tips.revise_first), block('Understand before moving ahead', tips.understand_before_moving_on),
     block('Mistakes to review', tips.mistakes_to_review), block('Practical exercises', tips.exercises)].forEach((b) => b && box.append(b));
  }

  function renderResources(box, resources) {
    box.innerHTML = '';
    if (!resources || !resources.length) {
      box.append(el('p', 'muted', 'No topics need extra resources from this attempt.'));
      return;
    }
    box.append(el('p', 'muted small', 'Resources come from the project\'s curated list. Links marked "not yet verified" have not been checked by the project owner. Topics are listed in suggested study order.'));
    resources.forEach((entry) => {
      const sec = el('div', 'rep-block');
      const head = el('h4', '', entry.topic);
      head.append(el('span', 'tag', entry.classification === 'weak' ? 'Needs improvement' : 'More assessment needed'));
      sec.append(head);
      if (entry.items.length) {
        const ol = el('ol', 'res-list');
        entry.items.forEach((it) => {
          const li = el('li');
          const title = el('strong', '', it.title);
          if (it.url && /^https:\/\//.test(it.url)) {
            const a = el('a', '', it.title);
            a.href = it.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
            li.append(a);
          } else {
            li.append(title);
          }
          li.append(el('span', 'tag', it.type), el('span', 'tag', it.verified_on ? 'Link checked ' + it.verified_on : 'Link not yet verified'));
          li.append(el('div', 'muted small', it.why));
          ol.append(li);
        });
        sec.append(ol);
      } else {
        sec.append(el('p', 'muted', entry.note || 'No verified resource is available for this topic yet.'));
      }
      const s = el('p', 'muted small');
      s.append(document.createTextNode('Search suggestion: '));
      const a = el('a', '', entry.search_suggestion);
      a.href = 'https://www.google.com/search?q=' + encodeURIComponent(entry.search_suggestion);
      a.target = '_blank'; a.rel = 'noopener noreferrer';
      s.append(a);
      sec.append(s);
      box.append(sec);
    });
  }

  function renderPlan(box, data) {
    box.innerHTML = '';
    const r = data.report;
    const ol = el('ol', 'plan');
    r.topics.filter((t) => t.priority !== 'Maintain').forEach((t) => {
      ol.append(el('li', '', `${t.topic} (${t.priority} priority): ${t.gap}`));
    });
    if (ol.children.length) {
      box.append(el('h4', '', 'Topics in priority order'), ol);
    }
    const sched = el('div', 'rep-block');
    sched.append(el('h4', '', 'Suggested revision schedule'));
    const ul = el('ul');
    r.tips.revision_schedule.forEach((s) => ul.append(el('li', '', `${s.when}: ${s.focus}`)));
    sched.append(ul, el('p', '', 'Next assessment: ' + r.tips.next_assessment));
    box.append(sched);
  }

  window.StudyGapReport = { post, postStream, request, renderReport, renderResources, renderPlan };
})();
