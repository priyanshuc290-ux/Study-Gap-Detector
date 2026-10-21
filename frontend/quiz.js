/*
 * Study Gap Checker (Mode 1): setup, quiz, results, report, practice. The quiz engine and its rules are unchanged from the
 * original app: every score and every strong / weak / not-enough-data label is computed here from the student's real answers.
 * New: where attempts are kept. Logged-in students: in their account (server database). Guests: this browser only (localStorage).
 */
(() => {
  'use strict';
  const { $, $$, el } = SG;


  /* ---------- Config ---------- */
  /* ---------- Config ---------- */
  const CONFIG = {
    MIN_ANSWERED_PER_TOPIC: 2,        // fewer answered questions than this = "not enough data" for a topic
    WEAK_BELOW: 0.6,                  // topic is weak if correct/answered is below this
    STORAGE_KEY: 'asga.attempts.v1',  // guests: attempts are saved in this browser only (localStorage)
    REPORT_TIMEOUT_MS: 45000,
    STABLE_BAND: 5,                   // accuracy change (points) smaller than this counts as "Stable"
    TOPIC_CHANGE_BAND: 0.2            // topic accuracy change needed to call a topic improved or declined
  };

  /* ---------- Sample data (clearly demo only) ---------- */
  /* ---------- Sample data (clearly demo only) ---------- */
  const SUBJECTS = {
    'Computer Science (CSE)': ['Data Structures', 'Operating Systems', 'DBMS', 'Computer Networks'],
    'Information Technology (IT)': ['Data Structures', 'Web Technologies', 'Software Engineering', 'Computer Networks'],
    'Electronics (ECE)': ['Signals and Systems', 'Analog Circuits', 'Digital Electronics', 'Electromagnetics'],
    'Electrical (EEE)': ['Circuit Theory', 'Electrical Machines', 'Power Systems', 'Control Systems'],
    'Mechanical': ['Thermodynamics', 'Fluid Mechanics', 'Strength of Materials', 'Machine Design'],
    'Civil': ['Surveying', 'Structural Analysis', 'Concrete Technology', 'Geotechnical Engineering']
  };

  // Every question carries metadata: subject, unit, topic, difficulty, explanation and the learning
  // outcome of its topic. The first option is the correct one; options are shuffled when a quiz
  // starts. Sample units are NOT a real syllabus. Topics must match data/syllabus.json.
  const OUTCOMES = {
    'Complexity': 'Compare algorithms using Big-O notation and common growth rates',
    'Arrays': 'Explain contiguous storage and the cost of indexing and insertion',
    'Linked Lists': 'Describe node-based storage and compare list operations with array operations',
    'Stacks': 'Apply LIFO behaviour and stack operations to problems such as call management',
    'Queues': 'Apply FIFO behaviour, queue operations and the circular queue idea',
    'Trees': 'Define binary trees and BSTs and describe traversals and basic properties',
    'Sorting': 'Compare common sorting algorithms by complexity and stability',
    'Searching': 'Apply linear and binary search and state their requirements and costs'
  };
  const DS = (unit, topic, difficulty, q, opts, explanation) => ({ subject: 'Data Structures', unit, topic, difficulty, q, opts, explanation, outcome: OUTCOMES[topic] });
  const BANK = [
    DS(1, 'Complexity', 'easy', 'Which notation describes an upper bound on growth rate?', ['Big-O', 'Big-Omega', 'Pointer notation', 'Postfix notation'], 'Big-O gives an upper bound on how fast running time grows, which is why it is used to compare algorithms.'),
    DS(1, 'Complexity', 'easy', 'What is the time complexity of a single loop running from 1 to n?', ['O(n)', 'O(1)', 'O(log n)', 'O(n²)'], 'A loop that runs once per element does work proportional to n, so the cost is O(n).'),
    DS(1, 'Complexity', 'medium', 'Which of these grows fastest as n becomes large?', ['O(n²)', 'O(n)', 'O(log n)', 'O(1)'], 'For large n, n squared grows faster than n, log n or a constant.'),
    DS(1, 'Arrays', 'easy', 'What is the time complexity of accessing an array element by index?', ['O(1)', 'O(n)', 'O(log n)', 'O(n²)'], 'The address of element i is computed directly from the base address, so indexing takes constant time.'),
    DS(1, 'Arrays', 'medium', 'Which statement about arrays is true?', ['Elements are stored in contiguous memory', 'Size can always grow without copying', 'Elements must have different types', 'Insertion in the middle is O(1)'], 'An array reserves one block of memory, so its elements sit next to each other.'),
    DS(1, 'Arrays', 'easy', 'In C and Java, the index of the first array element is:', ['0', '1', '-1', 'Depends on the array size'], 'C and Java count array positions from 0, so the first element is at index 0.'),
    DS(1, 'Linked Lists', 'easy', 'Each node of a singly linked list stores:', ['Data and a pointer to the next node', 'Only data', 'Data and pointers to previous and next nodes', 'Only an index'], 'A singly linked node holds its data and one pointer to the next node.'),
    DS(1, 'Linked Lists', 'medium', 'Time to insert at the head of a singly linked list:', ['O(1)', 'O(n)', 'O(log n)', 'O(n log n)'], 'Inserting at the head only changes the head pointer and one next pointer, with no traversal.'),
    DS(1, 'Linked Lists', 'medium', 'Which operation is slower in a linked list than in an array?', ['Accessing the k-th element', 'Inserting at the head', 'Deleting the head node', 'Checking whether the list is empty'], 'To reach the k-th node you must follow k pointers from the head, unlike direct array indexing.'),
    DS(1, 'Stacks', 'easy', 'Which principle does a stack follow?', ['LIFO', 'FIFO', 'Random access', 'Priority order'], 'The last element pushed is the first one popped, which is LIFO.'),
    DS(1, 'Stacks', 'easy', 'Which operation removes the top element of a stack?', ['pop', 'push', 'peek', 'enqueue'], 'pop removes and returns the top element; push adds one and peek only reads it.'),
    DS(1, 'Stacks', 'medium', 'Which application commonly uses a stack?', ['Function call management', 'Round-robin scheduling', 'Print spooling in arrival order', 'Breadth-first search'], 'Function calls are kept on a call stack: the most recent call finishes first.'),
    DS(2, 'Queues', 'easy', 'Which principle does a queue follow?', ['FIFO', 'LIFO', 'Random access', 'Sorted order'], 'The first element enqueued is the first one dequeued, which is FIFO.'),
    DS(2, 'Queues', 'easy', 'Adding an element to a queue is called:', ['enqueue', 'pop', 'push', 'peek'], 'enqueue adds at the rear. push and pop belong to stacks.'),
    DS(2, 'Queues', 'medium', 'What problem does a circular queue solve compared to a simple array queue?', ['Wasted space after dequeues', 'Slower enqueue', 'Loss of FIFO order', 'Inability to store integers'], 'A circular queue reuses freed front slots by wrapping around, so space is not wasted.'),
    DS(2, 'Trees', 'easy', 'Maximum number of children of a binary tree node:', ['2', '1', '3', 'Unlimited'], 'In a binary tree every node has at most two children, a left and a right.'),
    DS(2, 'Trees', 'medium', 'Inorder traversal of a binary search tree visits keys in:', ['Ascending order', 'Descending order', 'Level order', 'Random order'], 'Inorder visits the left subtree, the node, then the right subtree, which gives sorted order in a BST.'),
    DS(2, 'Trees', 'medium', 'A tree with n nodes has how many edges?', ['n − 1', 'n', 'n + 1', '2n'], 'Every node except the root has exactly one parent edge, so n nodes have n − 1 edges.'),
    DS(2, 'Sorting', 'easy', 'Worst-case time complexity of bubble sort:', ['O(n²)', 'O(n)', 'O(log n)', 'O(1)'], 'Bubble sort compares adjacent pairs in nested passes, so its worst case is quadratic.'),
    DS(2, 'Sorting', 'medium', 'Which algorithm runs in O(n log n) time on average?', ['Merge sort', 'Bubble sort', 'Insertion sort', 'Selection sort'], 'Merge sort halves the list repeatedly and merges, giving O(n log n).'),
    DS(2, 'Sorting', 'medium', 'Which sorting algorithm is stable in its standard form?', ['Merge sort', 'Quick sort', 'Heap sort', 'Selection sort'], 'Merge sort keeps equal elements in their original order when merging, so it is stable.'),
    DS(2, 'Searching', 'easy', 'Binary search requires the array to be:', ['Sorted', 'Of even length', 'Made of prime numbers', 'Stored as a linked list'], 'Binary search discards half the range by comparing with the middle element, which only works on sorted data.'),
    DS(2, 'Searching', 'easy', 'Time complexity of binary search:', ['O(log n)', 'O(n)', 'O(n log n)', 'O(1)'], 'Halving the search range at each step takes about log n steps.'),
    DS(2, 'Searching', 'easy', 'Worst-case time complexity of linear search:', ['O(n)', 'O(1)', 'O(log n)', 'O(n²)'], 'In the worst case linear search checks every element, so it is O(n).')
].map((q, id) => ({ ...q, id }));
  const TYPE_LABEL = {
    diagnostic: 'Diagnostic Quiz',
    weak: 'Weak Topics Practice',
    mixed: 'Mixed Practice',
    reattempt: 'Reattempt'
  };

  /* ---------- Helpers ---------- */
  function shuffle(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function fillSelect(sel, values, labelFn) {
    sel.innerHTML = '';
    values.forEach((v) => {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = labelFn ? labelFn(v) : v;
      sel.append(o);
    });
  }

  /* ---------- Where attempts are kept ---------- */
  const state = { setup: null, quiz: null, attempts: [], loaded: false };
  let lastView = 'gap-setup';

  function loadLocal() {
    try {
      const v = JSON.parse(localStorage.getItem(CONFIG.STORAGE_KEY) || '[]');
      return Array.isArray(v) ? v : [];
    } catch (err) {
      return [];
    }
  }
  function saveLocal(list) {
    try { localStorage.setItem(CONFIG.STORAGE_KEY, JSON.stringify(list)); return true; } catch (err) { return false; }
  }
  const inAccount = () => !!SG.auth.user;
  const pendingAdds = {}; // attempt id -> the save in progress
  const Store = {
    async load() {
      if (inAccount()) state.attempts = (await SG.api('GET', '/api/attempts')).attempts || [];
      else state.attempts = loadLocal();
      state.loaded = true;
    },
    // Each returns true when the attempt is safely stored, otherwise an error message.
    async add(a) {
      if (!inAccount()) return saveLocal(state.attempts) || 'This browser would not let the app save your attempts, so the history will be lost when you reload.';
      // Remember this save: a report that is ready before it finishes must wait (otherwise the server says "not found" and the report is lost).
      pendingAdds[a.id] = (async () => { try { await SG.api('POST', '/api/attempts', a); return true; } catch (err) { return err.message; } })();
      return pendingAdds[a.id];
    },
    async saveReport(a) {
      if (!inAccount()) { saveLocal(state.attempts); return true; }
      if (pendingAdds[a.id]) await pendingAdds[a.id];
      try { await SG.api('PATCH', '/api/attempts/' + encodeURIComponent(a.id), { report: a.report }); return true; } catch (err) { return err.message; }
    },
    async clear() {
      if (inAccount()) await SG.api('DELETE', '/api/attempts');
      else saveLocal([]);
      state.attempts = [];
    }
  };
  SG.auth.onChange(() => { state.loaded = false; state.attempts = []; state.setup = null; state.quiz = null; lastView = 'gap-setup'; });

  async function ensureLoaded() {
    if (state.loaded) return true;
    try { await Store.load(); return true; } catch (err) { SG.toast(err.message, 'error'); state.attempts = []; return false; }
  }

  const selectionKey = (s) => [s.year, s.semester, s.branch, s.subject, s.unit].join('|');
  const attemptsFor = (key) => state.attempts.filter((a) => a.key === key).sort((a, b) => a.ts.localeCompare(b.ts));
  const latestAttempt = () => {
    const list = attemptsFor(selectionKey(state.setup));
    return list[list.length - 1];
  };
  const attemptLabel = (a) => (a.type === 'reattempt' ? `Reattempt (${TYPE_LABEL[a.basedOn] || 'quiz'})` : TYPE_LABEL[a.type]);
  const unitQuestions = (s) => BANK.filter((q) => q.subject === s.subject && q.unit === Number(s.unit));

  /* ---------- Navigation inside the Study Gap pages ---------- */
  function show(view) {
    lastView = view;
    if (SG.router.current().name === 'study-gap') { SG.showView(view); SG.router.silent('/study-gap'); } else SG.go('/study-gap');
  }

  /* ---------- Study gap setup ---------- */
  function currentSelection() {
    return { year: $('#year').value, semester: $('#semester').value, branch: $('#branch').value, subject: $('#subject').value, unit: $('#unit').value };
  }

  function updateAvailability() {
    const count = unitQuestions(currentSelection()).length;
    const note = $('#availability');
    if (count) {
      note.className = 'notice ok full';
      note.textContent = `Sample questions available for this subject and unit: ${count} (demo data, not a real syllabus).`;
    } else {
      note.className = 'notice full';
      note.textContent = 'No sample questions for this subject and unit yet. The demo bank only covers Data Structures, Units 1 and 2 (under Computer Science or Information Technology).';
    }
    $('#gapContinue').disabled = !count;
  }

  function updateSizeNote() {
    const size = Number(document.querySelector('input[name="size"]:checked').value);
    const count = unitQuestions(state.setup).length;
    $('#sizeNote').textContent = size > count
      ? `The demo bank has ${count} questions for this unit, so a quiz of ${size} will contain ${count}.`
      : `Demo bank: ${count} questions available for this unit.`;
  }

  function renderQuizSetup() {
    const s = state.setup;
    $('#setupSummary').textContent = `Year ${s.year}, Semester ${s.semester} · ${s.branch} · ${s.subject} · Unit ${s.unit}`;
    const prior = attemptsFor(selectionKey(s));
    $('#priorAttempts').hidden = !prior.length;
    if (prior.length) {
      $('#priorText').textContent = `You have ${prior.length} saved attempt(s) for this unit. Weak-topic practice, mixed questions and reattempts are on the results screen.`;
      renderHistory($('#setupHistory'), prior);
    }
    updateSizeNote();
    show('quiz-setup');
  }

  function initSetup() {
    fillSelect($('#year'), [1, 2, 3, 4], (v) => 'Year ' + v);
    fillSelect($('#semester'), [1, 2], (v) => 'Semester ' + v);
    fillSelect($('#branch'), Object.keys(SUBJECTS));
    fillSelect($('#unit'), [1, 2, 3, 4, 5], (v) => 'Unit ' + v);
    const updateSubjects = () => {
      fillSelect($('#subject'), SUBJECTS[$('#branch').value]);
      updateAvailability();
    };
    $('#branch').addEventListener('change', updateSubjects);
    $('#subject').addEventListener('change', updateAvailability);
    $('#unit').addEventListener('change', updateAvailability);
    updateSubjects();

    $('#gapForm').addEventListener('submit', (e) => {
      e.preventDefault();
      if (!unitQuestions(currentSelection()).length) return;
      state.setup = currentSelection();
      renderQuizSetup();
    });

    // The first quiz for a unit is always a Diagnostic Quiz; other types are offered from the results screen.
    $('#quizForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const size = Number(document.querySelector('input[name="size"]:checked').value);
      startQuiz('diagnostic', size);
    });
    $$('input[name="size"]').forEach((r) => r.addEventListener('change', updateSizeNote));

    $('#viewLastBtn').addEventListener('click', () => {
      const last = latestAttempt();
      if (last) { renderResults(last); show('results'); }
    });
    $('#clearHistoryBtn').addEventListener('click', async () => {
      const where = inAccount() ? 'your account' : 'this browser';
      if (!(await SG.confirmDialog({ title: 'Clear saved attempts?', message: `This deletes all your saved quiz attempts from ${where}. It cannot be undone.`, confirmLabel: 'Delete attempts', danger: true }))) return;
      try { await Store.clear(); } catch (err) { SG.toast(err.message, 'error'); return; }
      renderQuizSetup();
    });
  }

  /* ---------- Quiz ---------- */
  const seenCounts = (key) => {
    const c = {};
    attemptsFor(key).forEach((a) => a.qids.forEach((id) => { c[id] = (c[id] || 0) + 1; }));
    return c;
  };

  // Picks up to `size` questions, rotating through topics so every topic is represented.
  // Questions the student has seen least often come first, so repeats only happen when needed.
  function balancedPick(pool, size, opts = {}) {
    const seen = opts.seen || {};
    const groups = {};
    shuffle(pool).sort((a, b) => (seen[a.id] || 0) - (seen[b.id] || 0)).forEach((q) => { (groups[q.topic] = groups[q.topic] || []).push(q); });
    const topics = opts.topicOrder ? opts.topicOrder.filter((t) => groups[t]) : Object.keys(groups);
    const out = [];
    while (out.length < size && topics.some((t) => groups[t].length)) {
      topics.forEach((t) => {
        if (out.length < size && groups[t].length) out.push(groups[t].shift());
      });
    }
    return out;
  }

  function startQuiz(type, size, opts = {}) {
    const pool = unitQuestions(state.setup);
    if (!pool.length) return;
    const notes = [];
    const seen = seenCounts(selectionKey(state.setup));
    let list;
    if (type === 'reattempt') {
      list = opts.qids.map((id) => BANK[id]);
      notes.push('Same questions as your previous attempt, with the answer options reshuffled.');
    } else if (type === 'weak') {
      list = balancedPick(pool.filter((q) => opts.weakTopics.includes(q.topic)), size, { seen, topicOrder: opts.weakTopics });
      notes.push(`This practice quiz only uses your weak topics, most important first: ${opts.weakTopics.join(', ')}.`);
    } else if (type === 'mixed') {
      list = shuffle(balancedPick(pool, size, { seen }));
    } else {
      const order = [...new Set(pool.map((q) => q.topic))];
      list = balancedPick(pool, size, { seen }).sort((a, b) => order.indexOf(a.topic) - order.indexOf(b.topic));
    }
    if ((type === 'diagnostic' || type === 'mixed') && list.length < size) {
      notes.push(`The demo bank has only ${list.length} questions for this unit, so this quiz has ${list.length} instead of ${size}.`);
    }
    state.quiz = {
      type, size, notes, index: 0, basedOn: opts.basedOn || null,
      items: list.map((q) => ({
        id: q.id, topic: q.topic, q: q.q, answer: null,
        options: shuffle(q.opts.map((t, i) => ({ t, ok: i === 0 })))
      }))
    };
    renderQuiz();
    show('quiz');
  }

  function updateProgress() {
    const { items, index } = state.quiz;
    const answered = items.filter((i) => i.answer !== null).length;
    const pct = Math.round(((index + 1) / items.length) * 100);
    $('#progressFill').style.width = pct + '%';
    $('#progress').setAttribute('aria-valuenow', pct);
    $('#quizAnswered').textContent = `${answered} of ${items.length} answered`;
  }

  function renderQuiz() {
    const { items, index, notes, type, basedOn } = state.quiz;
    const item = items[index];
    const last = index === items.length - 1;
    $('#quizCount').textContent = `Question ${index + 1} of ${items.length}`;
    $('#quizType').textContent = attemptLabel({ type, basedOn });
    $('#quizNote').hidden = !notes.length;
    $('#quizNote').textContent = notes.join(' ');
    $('#quizQuestion').textContent = item.q;
    const box = $('#quizOptions');
    box.innerHTML = '';
    item.options.forEach((opt, i) => {
      const label = el('label', 'choice');
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'option';
      input.checked = item.answer === i;
      input.addEventListener('change', () => { item.answer = i; updateProgress(); });
      label.append(input, el('span', '', opt.t));
      box.append(label);
    });
    $('#prevBtn').disabled = index === 0;
    $('#nextBtn').hidden = last;
    $('#submitBtn').hidden = !last;
    updateProgress();
  }

  function move(step) {
    state.quiz.index += step;
    renderQuiz();
  }

  // Builds the attempt record from the student's actual answers.
  function buildAttempt() {
    const q = state.quiz;
    const topics = {};
    let score = 0, answered = 0;
    q.items.forEach((it) => {
      const t = (topics[it.topic] = topics[it.topic] || { correct: 0, answered: 0, total: 0 });
      t.total++;
      if (it.answer !== null) {
        t.answered++; answered++;
        if (it.options[it.answer].ok) { t.correct++; score++; }
      }
    });
    const strong = [], weak = [], insufficient = [];
    Object.entries(topics).forEach(([topic, t]) => {
      if (t.answered < CONFIG.MIN_ANSWERED_PER_TOPIC) insufficient.push(topic);
      else (t.correct / t.answered < CONFIG.WEAK_BELOW ? weak : strong).push(topic);
    });
    const total = q.items.length;
    return {
      id: Date.now() + '-' + Math.random().toString(36).slice(2, 7),
      ts: new Date().toISOString(),
      key: selectionKey(state.setup),
      setup: { ...state.setup },
      type: q.type, basedOn: q.basedOn, size: q.size,
      qids: q.items.map((i) => i.id),
      answers: q.items.map((i) => ({ id: i.id, chosen: i.answer === null ? null : i.options[i.answer].t, correct: i.answer !== null && i.options[i.answer].ok })),
      total, answered, score,
      accuracy: total ? Math.round((score / total) * 100) : 0,
      topics, strong, weak, insufficient
    };
  }

  let submitting = false;
  async function submitQuiz() {
    if (submitting) return;
    const unanswered = state.quiz.items.filter((i) => i.answer === null).length;
    if (unanswered && !(await SG.confirmDialog({ title: 'Submit with unanswered questions?', message: `${unanswered} question(s) are unanswered and will count as incorrect.`, confirmLabel: 'Submit anyway', cancelLabel: 'Keep answering' }))) return;
    submitting = true;
    const attempt = buildAttempt();
    state.attempts.push(attempt);
    renderResults(attempt, { auto: true });
    show('results');
    const saved = await Store.add(attempt);  // the results are already on screen; saving happens alongside
    submitting = false;
    showStorageNote(saved);
  }

  function showStorageNote(saved) {
    const note = $('#storageNote');
    note.hidden = saved === true;
    if (saved !== true) note.textContent = inAccount() ? `This attempt could not be saved to your account (${saved}). It is shown here, but it may be missing later.` : saved;
  }

  function renderHistory(container, attempts, currentId) {
    container.innerHTML = '';
    const table = el('table', 'history');
    const headRow = el('tr');
    ['#', 'Type', 'Questions', 'Score', 'Accuracy', 'Weak topics', 'Date'].forEach((h) => headRow.append(el('th', '', h)));
    const thead = el('thead');
    thead.append(headRow);
    table.append(thead);
    const body = el('tbody');
    attempts.forEach((a, i) => {
      const row = el('tr', a.id === currentId ? 'current' : '');
      const weakText = a.weak.length ? a.weak.join(', ') : (a.strong.length + a.weak.length === 0 ? 'Not enough data' : 'None');
      [i + 1, attemptLabel(a), a.total, `${a.score}/${a.total}`, a.accuracy + '%', weakText, new Date(a.ts).toLocaleString()]
        .forEach((v) => row.append(el('td', '', String(v))));
      body.append(row);
    });
    table.append(body);
    container.append(table);
  }

  const pct = (n) => Math.round(n * 100);
  const trendLabel = (diff) => (diff >= CONFIG.STABLE_BAND ? ['Improved', 'up'] : diff <= -CONFIG.STABLE_BAND ? ['Declined', 'down'] : ['Stable', 'flat']);
  const sign = (n) => (n >= 0 ? '+' : '') + n;
  let currentResultId = null, reportRun = 0;

  function fillTopicList(list, names, topics, emptyText, fmt) {
    list.innerHTML = '';
    if (!names.length) { list.append(el('li', 'none', emptyText)); return; }
    names.forEach((n) => list.append(el('li', '', fmt(n, topics[n]))));
  }

  function sameQuestions(a, b) {
    return JSON.stringify([...a.qids].sort((x, y) => x - y)) === JSON.stringify([...b.qids].sort((x, y) => x - y));
  }

  // Weak topics ordered by importance: gaps backed by more evidence first, then lowest accuracy.
  function prioritizedWeakTopics(a) {
    const all = attemptsFor(a.key);
    const stats = (t) => all.reduce((s, x) => { const v = x.topics[t]; if (v) { s.c += v.correct; s.n += v.answered; } return s; }, { c: 0, n: 0 });
    return [...a.weak].sort((x, y) => {
      const sx = stats(x), sy = stats(y);
      if ((sx.n >= 3) !== (sy.n >= 3)) return sx.n >= 3 ? -1 : 1;
      return sx.c / sx.n - sy.c / sy.n;
    });
  }

  function nextAction(a) {
    if (a.weak.length) return `Attempt weak-topic practice on ${a.weak.join(', ')}.`;
    if (a.insufficient.length) return `Take more diagnostic questions to judge: ${a.insufficient.join(', ')}.`;
    return 'No weak topics found. Try mixed questions, or reattempt the quiz to confirm.';
  }

  function renderResults(a, opts = {}) {
    currentResultId = a.id;
    const all = attemptsFor(a.key);
    const idx = all.findIndex((x) => x.id === a.id);
    const unanswered = a.total - a.answered;
    $('#resultsContext').textContent = `${a.setup.subject} · Unit ${a.setup.unit} · ${attemptLabel(a)} · Attempt ${idx + 1} of ${all.length} for this unit`;
    $('#resScore').textContent = `${a.score} / ${a.total}`;
    $('#resAcc').textContent = a.accuracy + '%';
    $('#resMeta').textContent = `${a.answered} answered` + (unanswered ? `, ${unanswered} unanswered (counted as incorrect)` : '');

    const delta = $('#resDelta');
    if (idx > 0) {
      const prev = all[idx - 1];
      const diff = a.accuracy - prev.accuracy;
      const [label, cls] = trendLabel(diff);
      delta.textContent = `${label}: ${sign(diff)} points vs attempt ${idx}` + (sameQuestions(a, prev) ? '' : ' (different question set, so not a like-for-like comparison)');
      delta.className = cls;
    } else {
      delta.textContent = 'First attempt for this unit';
      delta.className = 'muted';
    }

    const changes = [];
    Object.entries(a.topics).forEach(([topic, t]) => {
      if (t.answered < CONFIG.MIN_ANSWERED_PER_TOPIC) return;
      const before = all.slice(0, idx).reverse().map((x) => x.topics[topic]).find((v) => v && v.answered >= CONFIG.MIN_ANSWERED_PER_TOPIC);
      if (!before) return;
      const d = t.correct / t.answered - before.correct / before.answered;
      if (Math.abs(d) >= CONFIG.TOPIC_CHANGE_BAND) changes.push(`${topic} ${d > 0 ? 'improved' : 'declined'} (${pct(before.correct / before.answered)}% → ${pct(t.correct / t.answered)}%)`);
    });
    $('#topicChanges').hidden = !changes.length;
    $('#topicChanges').textContent = changes.length ? 'Topic changes vs earlier attempts: ' + changes.join('; ') + '.' : '';

    const bars = $('#topicBars');
    bars.innerHTML = '';
    Object.entries(a.topics).forEach(([topic, t]) => {
      const status = a.weak.includes(topic) ? 'weak' : a.strong.includes(topic) ? 'strong' : 'na';
      const tagText = { weak: 'Needs improvement', strong: 'Strong', na: 'More assessment needed' }[status];
      const row = el('div', 'bar-row ' + status);
      const top = el('div', 'bar-top');
      const right = el('span', 'muted', `${t.correct}/${t.answered} correct`);
      right.append(el('span', 'tag', tagText));
      top.append(el('strong', '', topic), right);
      const bar = el('div', 'bar');
      const fill = el('i');
      fill.style.width = (t.answered ? pct(t.correct / t.answered) : 0) + '%';
      bar.append(fill);
      row.append(top, bar);
      bars.append(row);
    });

    const ratio = (n, t) => `${n} (${t.correct}/${t.answered} correct)`;
    fillTopicList($('#strongList'), a.strong, a.topics, 'None identified in this attempt', ratio);
    fillTopicList($('#weakList'), a.weak, a.topics, 'None identified in this attempt', ratio);
    fillTopicList($('#moreList'), a.insufficient, a.topics, 'None', (n, t) => `${n} (${t.answered} answered, need ${CONFIG.MIN_ANSWERED_PER_TOPIC})`);

    const classified = a.strong.length + a.weak.length;
    let message = '';
    if (!classified) {
      message = `Not enough data to identify strong or weak topics. Each topic needs at least ${CONFIG.MIN_ANSWERED_PER_TOPIC} answered questions. Try more diagnostic questions and answer more of them.`;
    } else if (a.insufficient.length) {
      message = `Not enough data for: ${a.insufficient.join(', ')} (fewer than ${CONFIG.MIN_ANSWERED_PER_TOPIC} answered questions), so they are not classified.`;
    } else if (!a.weak.length) {
      message = 'No weak topics were identified in this attempt. You can still try mixed questions or reattempt the quiz.';
    }
    $('#dataMessage').hidden = !message;
    $('#dataMessage').textContent = message;

    $('#weakBtn').disabled = !a.weak.length;
    $('#moreDiagBtn').hidden = !a.insufficient.length;
    $('#weakHint').textContent = a.weak.length ? '' : (a.insufficient.length
      ? 'There is not enough evidence yet to choose weak topics. Take more diagnostic questions first.'
      : 'Attempt Weak Topics is available once an attempt identifies at least one weak topic.');
    renderHistory($('#resultsHistory'), all, a.id);
    renderReview(a);
    loadReportUI(a, !!opts.auto);
  }

  function renderReview(a) {
    const box = $('#reviewBox'), list = $('#reviewList');
    list.innerHTML = '';
    box.hidden = !a.answers;
    if (!a.answers) return;
    const wrong = a.answers.filter((x) => !x.correct);
    if (!wrong.length) { list.append(el('p', 'muted', 'No incorrect answers in this attempt.')); return; }
    wrong.forEach((x) => {
      const q = BANK[x.id];
      const item = el('div', 'review-item');
      item.append(el('strong', '', q.q));
      [`Your answer: ${x.chosen || 'Not answered'}`, `Correct answer: ${q.opts[0]}`, `Explanation: ${q.explanation}`,
        `${q.topic} · ${q.difficulty} · Outcome: ${q.outcome}`].forEach((t) => item.append(el('div', 'muted small', t)));
      list.append(item);
    });
  }

  /* ---------- AI study gap report (see report.js and backend/) ---------- */
  // Only deterministic results and what is needed to ground the explanation are sent. No name or contact details.
  function buildReportPayload(a) {
    const all = attemptsFor(a.key);
    const idx = all.findIndex((x) => x.id === a.id);
    const topics = {};
    Object.entries(a.topics).forEach(([t, v]) => {
      topics[t] = { correct: v.correct, answered: v.answered, total: v.total, classification: a.weak.includes(t) ? 'weak' : a.strong.includes(t) ? 'strong' : 'insufficient' };
    });
    const misses = (a.answers || []).filter((x) => !x.correct).slice(0, 20).map((x) => {
      const q = BANK[x.id];
      return { topic: q.topic, difficulty: q.difficulty, question: q.q, chosen: x.chosen, correct_answer: q.opts[0], explanation: q.explanation };
    });
    const start = Math.max(0, idx - 5);
    return {
      subject: a.setup.subject, unit: Number(a.setup.unit), year: Number(a.setup.year), semester: Number(a.setup.semester), branch: a.setup.branch,
      rules: { weak_below: CONFIG.WEAK_BELOW, min_answered: CONFIG.MIN_ANSWERED_PER_TOPIC },
      attempt: { number: idx + 1, type: a.type, total: a.total, answered: a.answered, score: a.score, accuracy: a.accuracy, topics, misses },
      previous: all.slice(start, idx).map((p, i) => ({
        number: start + i + 1, accuracy: p.accuracy,
        topics: Object.fromEntries(Object.entries(p.topics).filter(([, v]) => v.answered).map(([k, v]) => [k, pct(v.correct / v.answered)]))
      }))
    };
  }

  function resetReportUI() {
    $('#reportBox').innerHTML = '';
    $('#reportStatus').innerHTML = '';
    $('#reportMode').textContent = '';
    $('#resourcesBox').innerHTML = '<p class="muted">Resources appear with the report.</p>';
    $('#planBox').innerHTML = '<p class="muted">Your plan appears with the report.</p>';
  }

  function showReport(a) {
    const d = a.report;
    $('#reportStatus').innerHTML = '';
    $('#reportMode').textContent = d.mode === 'ai' ? 'AI' : 'Demo';
    StudyGapReport.renderReport($('#reportBox'), d);
    StudyGapReport.renderResources($('#resourcesBox'), d.resources);
    StudyGapReport.renderPlan($('#planBox'), d);
  }

  function reportStatus(kind, msg, a) {
    const box = $('#reportStatus');
    box.innerHTML = '';
    if (kind === 'loading') {
      box.append(el('p', 'muted', 'Generating your report…'));
      return;
    }
    const retry = el('button', 'btn' + (kind === 'error' ? ' ghost' : ''), kind === 'error' ? 'Retry' : 'Generate study gap report');
    retry.type = 'button';
    retry.addEventListener('click', () => runReport(a));
    if (kind === 'error') box.append(el('div', 'report-error', msg));
    else box.append(el('p', 'muted', 'No report has been generated for this attempt yet.'));
    box.append(retry);
  }

  function loadReportUI(a, auto) {
    resetReportUI();
    if (a.report) showReport(a);
    else if (auto) runReport(a);
    else reportStatus('idle', '', a);
  }

  async function runReport(a) {
    const run = ++reportRun;
    reportStatus('loading');
    try {
      const data = await StudyGapReport.request(buildReportPayload(a), { baseUrl: SG.API_BASE, timeoutMs: CONFIG.REPORT_TIMEOUT_MS });
      a.report = data;
      Store.saveReport(a);
      if (run === reportRun && currentResultId === a.id) showReport(a);
    } catch (err) {
      if (run !== reportRun || currentResultId !== a.id) return;
      reportStatus('error', err.message, a);
      if (err.resources) StudyGapReport.renderResources($('#resourcesBox'), err.resources);
    }
  }

  function initResultsButtons() {
    $('#weakBtn').addEventListener('click', () => {
      const a = latestAttempt();
      if (a && a.weak.length) startQuiz('weak', a.size, { weakTopics: prioritizedWeakTopics(a) });
    });
    $('#mixedBtn').addEventListener('click', () => {
      const a = latestAttempt();
      if (a) startQuiz('mixed', a.size);
    });
    $('#reattemptBtn').addEventListener('click', () => {
      const a = latestAttempt();
      if (a) startQuiz('reattempt', a.size, { qids: a.qids, basedOn: a.type === 'reattempt' ? a.basedOn : a.type });
    });
    $('#moreDiagBtn').addEventListener('click', () => {
      const a = latestAttempt();
      if (a) startQuiz('diagnostic', a.size);
    });
    $('#resourcesBtn').addEventListener('click', () => {
      const target = $('#resourcesSection');
      if (target.scrollIntoView) target.scrollIntoView({ behavior: 'smooth' });
    });
  }


  /* ---------- Study progress dashboard (stored attempts only) ---------- */
  function unitCard(g) {
    const latest = g[g.length - 1];
    const s = latest.setup;
    const card = el('div', 'unit-card');
    card.append(el('h3', '', `${s.subject} · Unit ${s.unit}`), el('div', 'muted small', `Year ${s.year}, Semester ${s.semester} · ${g.length} attempt(s)`));
    const trend = el('div', 'trend');
    g.slice(-8).forEach((a, i, arr) => {
      const bar = el('div');
      bar.style.height = Math.max(a.accuracy, 3) + '%';
      bar.title = `Attempt ${g.length - arr.length + i + 1}: ${a.accuracy}%`;
      bar.append(el('span', '', a.accuracy + '%'));
      trend.append(bar);
    });
    card.append(trend);
    const summary = el('p', '');
    if (g.length > 1) {
      const diff = latest.accuracy - g[g.length - 2].accuracy;
      const [label, cls] = trendLabel(diff);
      summary.append(document.createTextNode(`Latest accuracy ${latest.accuracy}%: `), el('span', cls, `${label} (${sign(diff)} points vs previous attempt)`));
    } else {
      summary.textContent = `Latest accuracy ${latest.accuracy}% (first attempt, no trend yet).`;
    }
    card.append(summary);
    if (g.length > 1) {
      const earlier = g.slice(0, -1);
      const persistent = latest.weak.filter((t) => earlier.some((a) => a.weak.includes(t)));
      const improved = latest.strong.filter((t) => earlier.some((a) => a.weak.includes(t)));
      card.append(el('p', 'small', 'Persistent weak topics: ' + (persistent.join(', ') || 'none')), el('p', 'small', 'Topics that have improved: ' + (improved.join(', ') || 'none yet')));
    } else {
      card.append(el('p', 'muted small', 'Persistent and improved topics need at least 2 attempts.'));
    }
    card.append(el('div', 'next-action', 'Recommended next action: ' + nextAction(latest)));
    const open = el('button', 'btn ghost', 'Open latest results');
    open.type = 'button';
    open.style.marginTop = '.6rem';
    open.addEventListener('click', () => openResults(latest));
    card.append(open);
    return card;
  }

  function renderProgress(box) {
    box.innerHTML = '';
    if (!state.attempts.length) {
      const empty = el('div', 'empty');
      empty.append(el('strong', '', 'No quiz attempts yet'), el('p', '', 'Take a Diagnostic Quiz in Check Your Study Gap. Your attempts, accuracy trend and weak topics will appear here.'));
      box.append(empty);
      return;
    }
    const groups = {};
    state.attempts.forEach((a) => { (groups[a.key] = groups[a.key] || []).push(a); });
    Object.values(groups)
      .map((g) => g.sort((x, y) => x.ts.localeCompare(y.ts)))
      .sort((g1, g2) => g2[g2.length - 1].ts.localeCompare(g1[g1.length - 1].ts))
      .slice(0, 3)
      .forEach((g) => box.append(unitCard(g)));
  }

  function openResults(a) { state.setup = a.setup; renderResults(a); show('results'); }

  // Runs only when the student clicks a button the agent prepared in chat. Applies to the most recent unit they practised.
  function runAgentAction(action) {
    const latest = [...state.attempts].sort((a, b) => a.ts.localeCompare(b.ts)).pop();
    if (!latest) return;
    state.setup = latest.setup;
    if (action === 'weak_practice' && latest.weak.length) startQuiz('weak', latest.size, { weakTopics: prioritizedWeakTopics(latest) });
    else if (action === 'mixed_practice') startQuiz('mixed', latest.size);
    else if (action === 'diagnostic') startQuiz('diagnostic', latest.size);
  }

  const sortedAttempts = () => [...state.attempts].sort((a, b) => a.ts.localeCompare(b.ts));

  /* ---------- Init ---------- */
  function init() {
    initSetup();
    initResultsButtons();
    $('#prevBtn').addEventListener('click', () => move(-1));
    $('#nextBtn').addEventListener('click', () => move(1));
    $('#submitBtn').addEventListener('click', submitQuiz);
    $('#quizOptions').setAttribute('role', 'radiogroup');
    $('#quizOptions').setAttribute('aria-labelledby', 'quizQuestion');
    document.addEventListener('click', (e) => { // "Back" to the first Study Gap step
      const b = e.target.closest && e.target.closest('[data-nav="gap-setup"]');
      if (b) show('gap-setup');
    });
    SG.views['study-gap'] = {
      async enter() {
        $('#guestNotice').hidden = inAccount();
        await ensureLoaded();
        if (lastView === 'results' && (!state.setup || !latestAttempt())) lastView = 'gap-setup';
        if (lastView === 'quiz-setup' && !state.setup) lastView = 'gap-setup';
        if (lastView === 'quiz' && !state.quiz) lastView = 'gap-setup';
        if (lastView === 'quiz-setup') renderQuizSetup();
        SG.showView(lastView);
      }
    };
  }

  SG.quiz = {
    ensureLoaded, attempts: sortedAttempts, attemptLabel, openResults, runAgentAction, renderProgress, trendLabel, sign, nextAction,
    inAccount, hasLocal: () => loadLocal().length > 0, localCount: () => loadLocal().length,
    async importLocal() {
      const local = loadLocal();
      const res = await SG.api('POST', '/api/attempts/import', { attempts: local.slice(-200) });
      saveLocal([]);
      state.loaded = false;
      await ensureLoaded();
      return res.imported;
    }
  };
  init();
})();
