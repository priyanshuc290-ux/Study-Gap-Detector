# Backend: Study Gap Analyzer

Python standard library only. No `pip install` needed (Python 3.9+).

## Run
```
python backend/server.py
```
Open **http://localhost:8000**. The backend also serves the frontend, so there are no CORS problems.
(VS Code Live Server on port 5500 also works: it is in the default `ALLOWED_ORIGINS`.
Opening `index.html` by double-click still runs the quiz, but the AI report cannot reach the backend.)

The console says whether it started in **REAL AI** or **DEMO** mode.

## Modes
| Mode | When | What you get |
|------|------|--------------|
| Demo | `LLM_API_KEY` is empty | Rule-based report built from the real results. Labelled "Demo report". No AI model is used. |
| Real AI | `LLM_API_KEY` is set | An LLM writes the interpretation. Output is validated; invalid output is retried once, then shown as an error with Retry. |

## Turn on real AI
1. Copy `backend/.env.example` to `backend/.env` (it is git-ignored).
2. Set `LLM_API_KEY` to your key. Never put it in `frontend/`.
3. Check `LLM_BASE_URL` and `LLM_MODEL`. Defaults point at ASI:One's OpenAI-compatible endpoint
   (`https://api.asi1.ai/v1`, `asi1-mini`). **These are assumptions I could not verify without a key**;
   confirm them in your provider dashboard. Any OpenAI-compatible `/chat/completions` provider works.
4. Restart the server.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_API_KEY` | empty | Secret key. Empty = demo mode |
| `LLM_BASE_URL` | `https://api.asi1.ai/v1` | OpenAI-compatible API base URL |
| `LLM_MODEL` | `asi1-mini` | Model name |
| `LLM_TIMEOUT` | `40` | Seconds before the AI call times out |
| `PORT` | `8000` | Server port |
| `ALLOWED_ORIGINS` | localhost 5500 / 8000 | Browser origins allowed to call the API |
| `RATE_LIMIT_PER_MIN` | `10` | Report requests per minute per client |
| `CHAT_RATE_LIMIT_PER_MIN` | `20` | Chat requests per minute per client (streaming and non-streaming share it) |

## API
`GET /api/health` returns `{"status":"ok","ai_configured":bool}`.

`POST /api/study-gap-report` (JSON) takes the quiz engine's results: subject, unit, year, semester, branch,
`rules` (weak_below, min_answered), `attempt` (score, accuracy, per-topic counts + classification, missed questions)
and up to 5 `previous` attempt summaries. No name, email or other identity is sent.
It returns `{mode, model, report, resources, generated_on}`. Errors return `{error:{code,message}}`
(and `resources` when only the AI step failed).

What the server checks before calling any AI: subject/unit/topics exist in `data/syllabus.json`; score, answered and total
are consistent; accuracy matches score/total; each topic's classification matches the stated rules; sizes are capped.
The AI never supplies numbers or links: observed performance text is generated from the data, and resources come only from
`data/resources.json`.

## Resources
Edit `data/resources.json` to add or change resources. No code changes needed.
Every link shipped is **unverified** (`verified_on: null`) and the UI says so. No video lectures are included yet.
On a machine with internet access run `python backend/verify_resources.py` to check links and stamp `verified_on`.
Optional fields `units` and `years` limit where a resource is shown. A topic with no resource shows an honest note and a search suggestion.

## Fetch.ai / uAgents / Agentverse / ACP / ASI:One
**Not implemented.** `analyzer.py` has no HTTP code; `analyzer.analyze(payload)` takes a dict and returns a dict,
so a uAgent message handler can call it later. Wiring that up is still to do.

## Streaming (`POST /api/chat/stream`)
The response is **NDJSON**: `Content-Type: application/x-ndjson`, one JSON object per line, flushed as each event happens.
(No Content-Length; the connection closes when the stream ends. If the request is invalid you get a normal HTTP error with a
JSON body instead of a stream.) Events, each with a `type`:

| `type` | Fields | Meaning |
|--------|--------|---------|
| `status` | `state`: `thinking` or `writing` | What the agent is doing now |
| `ping` | none | Sent about once a second while the model only thinks; ignore it |
| `step_start` | `id`, `tool`, `label` | A tool call started (for example "Checking your quiz results…") |
| `step_done` | `id`, `tool`, `label` | The same tool call finished (label says what was found, or that nothing was) |
| `token` | `text` | The next piece of the answer |
| `reset` | none | Text so far was only a lead-in before a tool call: drop it |
| `actions` | `actions` | Buttons the student may click (sent once, after the text) |
| `done` | `reply`, `model`, `notes`, `steps`, `actions`, `document?` | Final result; `reply` is the authoritative full text |
| `error` | `code`, `message` | The AI step failed after the stream started (for example the provider dropped) |

How it works: the backend calls the provider with `stream: true` (OpenAI-compatible SSE) and assembles streamed `tool_calls`
(names and JSON arguments arrive in fragments) in `streaming.py`. The tool loop is still bounded (4 rounds, then a final
answer without tools) and tools are still deterministic. Fallbacks: if the provider rejects `stream: true` (HTTP 400/404/405/415/422/501)
before sending anything, that round is done without streaming and a note says so; if it rejects tools, the request is retried
once as a plain chat (still streamed) and a note says so. A provider failure *after* text was sent is reported as an `error`
event (the browser keeps the partial text and offers Retry); nothing is silently re-run. When the browser disconnects
(Stop button, closed tab) the next write fails, the backend closes the provider connection, and generation stops.
Reasoning text some models stream (`delta.reasoning`) is deliberately not forwarded.

## Tests
From the project root: `python -m unittest discover -s backend/tests -v`
They use a fake LLM and a local mock provider. No real AI API has been called by this project's tests.
(The streaming tests also start the real handler on a free local port and read it over HTTP, to prove events arrive one by one.)

**Optional browser-flow tests** (`e2e/`, needs Node 18+): `cd e2e && npm install && npm test`. They start the real backend against
`e2e/mock_provider.py` (a fake OpenAI-compatible provider that only accepts the key `test-key`) and drive the real frontend in jsdom:
streaming steps and text, Stop, Retry, provider-without-streaming fallback, PDF attachment, old-backend fallback, and the whole
Mode 1 flow (quiz, results, report in demo mode, weak-topic practice, dashboard). jsdom is not a real browser: it does not prove
layout or looks.

## Ask AI Agent (a study agent with tools)
Two endpoints with the same request body and the same agent: **`POST /api/chat/stream`** (used by the browser: live events)
and **`POST /api/chat`** (one JSON answer at the end; the browser falls back to it if it gets a 404 from the stream route).
Both share one rate limit (`CHAT_RATE_LIMIT_PER_MIN`).

`POST /api/chat` (JSON): `message`, optional `history` (last 10 turns), optional `document` ({name, text} from an earlier PDF),
optional `file` ({name, data: base64}, PDF/JPG/PNG up to 10 MB) and optional `study_context` (a compact summary of the
student's saved quiz results, sent only when the "use my results" toggle is on).
Returns `{reply, model, notes, steps, actions, document?}`. Needs `LLM_API_KEY`; without it you get a clear error, never a canned answer.

**Agent loop.** The model may call these tools, up to 4 rounds. `chat.py` runs each one deterministically and returns the result:
| Tool | What it does |
|------|--------------|
| `get_study_results` | Returns the saved quiz results the browser sent (topics outside the syllabus are dropped) |
| `get_syllabus_topics` | Reads `data/syllabus.json` |
| `find_resources` | Returns only entries from `data/resources.json`, with their verified flag |
| `suggest_action` | Prepares a button (weak-topic practice, mixed practice, diagnostic). The browser runs it only when the student clicks it. Weak-topic practice is refused if there are no weak topics. |

`steps` lists what the agent did and is shown in the chat. The model must support tool calling (OpenAI-style `tools`).
If the provider rejects tools, the request is retried once as a plain chat and a note says so. Vision (image) calls are made without tools.

| Attachment | How it works | What you must set up |
|------------|--------------|----------------------|
| PDF | Text is extracted on the server and given to the model as data. Scanned PDFs have no text and are rejected with a clear message. | `pip install pypdf` (see `requirements.txt`) |
| JPG / PNG | Sent to a vision model, which reads and describes it (this is the OCR). | Set `LLM_VISION_MODEL` to a vision-capable model on your provider |

Files are checked by their real content (not their name), processed in memory and never saved to disk.
The chat is a separate mode from the Study Gap report and shares only the LLM settings.

Limits to know about: the model can be wrong; text inside a PDF is passed as study material and the model is told to ignore
instructions inside it, which reduces but does not eliminate prompt-injection risk; attached files and quiz results are sent to your AI provider.
Replies are formatted with a small Markdown renderer (headings, lists, bold, code); tables are not rendered.

---

## Web app upgrade: accounts, saved chats, reviews, contact (added later)
Still **Python standard library only** (no new `pip install`; `pypdf` stays optional for PDFs). New files: `db.py` (SQLite), `auth.py`
(accounts, password hashing, sessions), `routes.py` (new API). `analyzer.py`, `chat.py`, `streaming.py` and the Study Gap report route are unchanged.

**Run:** `python backend/server.py` then open http://localhost:8000 (open it this way, not by double-clicking `index.html`).

**New settings** (all optional, see `.env.example`): `DATABASE_PATH`, `ADMIN_EMAILS` (comma-separated emails that may approve reviews and read
contact messages; they must also sign up), `COOKIE_SECURE=1` (when served over HTTPS), `SESSION_DAYS`.

**Database:** one SQLite file, created automatically at `instance/studygap.db` (git-ignored). Back it up by copying that file while the server is stopped.
It holds users (password hashes only), sessions (token hashes only), chats, quiz attempts, reviews and contact messages.

**Login rules:** `Ask AI Agent` and the Dashboard need a login. The quiz (Study Gap Checker) works without one (results stay in that browser);
logged-in students' results are stored in their account. The Study Gap *report* route stays public and rate-limited.
Reviews are shown only after an admin approves them. Contact messages are stored in the database (no email is sent).
Password reset by email is **not** implemented: it needs an email service (SMTP) configured first.

**Tests:** `python -m unittest discover -s backend/tests -v` (106 tests, fake AI).
Real-browser checks (needs `pip install playwright` and Chromium): `python browser_tests/browser_check.py` (92 checks, fake AI, throw-away database).

## Going online
See `DEPLOYMENT.md` in the project root (HTTPS with a reverse proxy, `COOKIE_SECURE`, `ALLOWED_ORIGINS`, `ADMIN_EMAILS`, `TRUST_PROXY`, backups).
`python backend/backup_db.py` makes a safe copy of the database while the server runs. `GET /api/health` also reports
`vision_configured` and `pdf_support`, so you can see whether image and PDF attachments are set up.
