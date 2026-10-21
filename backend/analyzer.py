"""Study Gap Analyzer.

Pure logic with no HTTP code, so it can be called from server.py today and from a
Fetch.ai uAgent handler later:  analyze(payload) -> dict  (raises AnalyzerError).

Design rules:
  * Scores and topic classifications are computed by the frontend quiz engine and only
    CHECKED here. The LLM never decides or changes a number.
  * The LLM only writes interpretation. Its JSON is validated; observed performance text
    and resource links are attached by this code, never taken from the LLM.
  * API keys come from environment variables only.
"""
import http.client
import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRIORITIES = ("High", "Medium", "Maintain")
CONFIDENCE = ("observed", "suspected")
CLASSES = ("strong", "weak", "insufficient")
ATTEMPT_TYPES = ("diagnostic", "weak", "mixed", "reattempt")
DIFFICULTIES = ("easy", "medium", "hard")


class AnalyzerError(Exception):
    def __init__(self, code, message, status=400, resources=None):
        super().__init__(message)
        self.code, self.message, self.status, self.resources = code, message, status, resources


# ---------------------------------------------------------------- config
def llm_config():
    return {
        "key": os.environ.get("LLM_API_KEY", "").strip(),
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.asi1.ai/v1").rstrip("/"),
        "model": os.environ.get("LLM_MODEL", "asi1-mini"),
        "timeout": float(os.environ.get("LLM_TIMEOUT", "40")),
        "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "4000")),  # reasoning models spend tokens thinking; too low cuts the JSON off
    }


def ai_configured():
    return bool(llm_config()["key"])


def _load(name):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)  # read on every call so edits to data files apply without a restart


# ---------------------------------------------------------------- validation
def _int(v, lo, hi, name):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise AnalyzerError("invalid_request", f"'{name}' must be an integer from {lo} to {hi}.")
    return v


def _num(v):
    return int(v) if isinstance(v, str) and v.isdigit() else v


def _str(v, maxlen, name, allow_none=False):
    if v is None and allow_none:
        return None
    if not isinstance(v, str) or len(v) > maxlen:
        raise AnalyzerError("invalid_request", f"'{name}' must be text of at most {maxlen} characters.")
    return v.strip()


def _pct(correct, total):
    return int(correct * 100 / total + 0.5) if total else 0  # round half up, same as JavaScript Math.round


def validate_payload(p, syllabus):
    if not isinstance(p, dict):
        raise AnalyzerError("invalid_request", "Request body must be a JSON object.")
    subject = _str(p.get("subject"), 80, "subject")
    unit = p.get("unit")
    unit = int(unit) if isinstance(unit, str) and unit.isdigit() else unit
    unit = _int(unit, 1, 20, "unit")
    unit_def = syllabus["subjects"].get(subject, {}).get("units", {}).get(str(unit))
    if not unit_def:
        raise AnalyzerError("out_of_syllabus", f"No syllabus is available for {subject}, unit {unit}.")
    topics_allowed = unit_def["topics"]

    clean = {
        "subject": subject, "unit": unit, "unit_title": unit_def["title"],
        "year": _int(_num(p.get("year")), 1, 4, "year"),
        "semester": _int(_num(p.get("semester")), 1, 2, "semester"),
        "branch": _str(p.get("branch"), 80, "branch"),
    }
    rules = p.get("rules") or {}
    weak_below = rules.get("weak_below")
    min_answered = _int(rules.get("min_answered"), 1, 20, "rules.min_answered")
    if not isinstance(weak_below, (int, float)) or isinstance(weak_below, bool) or not 0 < weak_below <= 1:
        raise AnalyzerError("invalid_request", "'rules.weak_below' must be a number between 0 and 1.")
    clean["rules"] = {"weak_below": float(weak_below), "min_answered": min_answered}

    a = p.get("attempt")
    if not isinstance(a, dict):
        raise AnalyzerError("invalid_request", "'attempt' is required.")
    total, answered, score = _int(a.get("total"), 1, 200, "attempt.total"), _int(a.get("answered"), 0, 200, "attempt.answered"), _int(a.get("score"), 0, 200, "attempt.score")
    if not score <= answered <= total:
        raise AnalyzerError("inconsistent_data", "Score, answered and total do not fit together.")
    if _int(a.get("accuracy"), 0, 100, "attempt.accuracy") != _pct(score, total):
        raise AnalyzerError("inconsistent_data", "Accuracy does not match score / total.")
    if a.get("type") not in ATTEMPT_TYPES:
        raise AnalyzerError("invalid_request", "Unknown attempt type.")
    topics = a.get("topics")
    if not isinstance(topics, dict) or not topics:
        raise AnalyzerError("invalid_request", "'attempt.topics' is required.")
    clean_topics, sums = {}, [0, 0, 0]
    for name, t in topics.items():
        if name not in topics_allowed:
            raise AnalyzerError("out_of_syllabus", f"Topic '{str(name)[:60]}' is not in this unit's syllabus.")
        if not isinstance(t, dict):
            raise AnalyzerError("invalid_request", f"Topic '{name}' is malformed.")
        c, an, tt = _int(t.get("correct"), 0, 200, "correct"), _int(t.get("answered"), 0, 200, "answered"), _int(t.get("total"), 1, 200, "total")
        if not c <= an <= tt:
            raise AnalyzerError("inconsistent_data", f"Counts for '{name}' do not fit together.")
        expected = "insufficient" if an < min_answered else ("weak" if c / an < weak_below else "strong")
        if t.get("classification") != expected:
            raise AnalyzerError("inconsistent_data", f"Classification for '{name}' does not match the stated rules.")
        clean_topics[name] = {"correct": c, "answered": an, "total": tt, "classification": expected}
        sums[0] += c; sums[1] += an; sums[2] += tt
    if sums != [score, answered, total]:
        raise AnalyzerError("inconsistent_data", "Topic totals do not add up to the attempt totals.")

    misses = []
    for m in (a.get("misses") or [])[:20]:
        if not isinstance(m, dict) or m.get("topic") not in topics_allowed:
            raise AnalyzerError("out_of_syllabus", "A missed question refers to a topic outside the syllabus.")
        if m.get("difficulty") not in DIFFICULTIES:
            raise AnalyzerError("invalid_request", "Unknown difficulty on a missed question.")
        misses.append({
            "topic": m["topic"], "difficulty": m["difficulty"],
            "question": _str(m.get("question"), 300, "misses.question"),
            "chosen": _str(m.get("chosen"), 200, "misses.chosen", allow_none=True),
            "correct_answer": _str(m.get("correct_answer"), 200, "misses.correct_answer"),
            "explanation": _str(m.get("explanation") or "", 300, "misses.explanation"),
        })
    clean["attempt"] = {"number": _int(a.get("number"), 1, 1000, "attempt.number"), "type": a["type"], "total": total,
                        "answered": answered, "score": score, "accuracy": _pct(score, total), "topics": clean_topics, "misses": misses}

    previous = []
    for pr in (p.get("previous") or [])[-5:]:
        if not isinstance(pr, dict):
            raise AnalyzerError("invalid_request", "A previous attempt is malformed.")
        ptopics = {k: _int(v, 0, 100, "previous.topics") for k, v in (pr.get("topics") or {}).items() if k in topics_allowed}
        previous.append({"number": _int(pr.get("number"), 1, 1000, "previous.number"), "accuracy": _int(pr.get("accuracy"), 0, 100, "previous.accuracy"), "topics": ptopics})
    clean["previous"] = previous
    return clean


# ---------------------------------------------------------------- resources
def recommend_resources(clean, db=None):
    """Deterministic: only entries from data/resources.json are ever returned. Nothing is invented."""
    db = db or _load("resources.json")
    out = []
    needing = [(n, t) for n, t in clean["attempt"]["topics"].items() if t["classification"] in ("weak", "insufficient")]
    needing.sort(key=lambda kv: (kv[1]["classification"] != "weak", kv[1]["correct"] / kv[1]["answered"] if kv[1]["answered"] else 0))
    for name, t in needing:
        items = [r for r in db["resources"]
                 if r["subject"] == clean["subject"] and r["topic"] == name
                 and clean["unit"] in r.get("units", [clean["unit"]]) and clean["year"] in r.get("years", [clean["year"]])]
        items.sort(key=lambda r: r.get("order", 99))
        entry = {
            "topic": name, "classification": t["classification"],
            "items": [{"order": i + 1, "title": r["title"], "type": r["type"], "why": r["why"], "url": r.get("url"),
                       "level": r.get("level"), "verified_on": r.get("verified_on")} for i, r in enumerate(items)],
            "search_suggestion": db.get("search_suggestions", {}).get(name) or f"{name} {clean['subject']} tutorial for beginners",
        }
        if not items:
            entry["note"] = "No verified resource is available for this topic yet. Use the search suggestion or ask your instructor."
        out.append(entry)
    return out


# ---------------------------------------------------------------- demo (rule-based, no AI)
def _observed(t):
    if not t["answered"]:
        return "No questions answered."
    return f"{t['correct']} of {t['answered']} answered correctly ({_pct(t['correct'], t['answered'])}%)."


def demo_report(clean, syllabus):
    a, topics = clean["attempt"], clean["attempt"]["topics"]
    sdef = syllabus["subjects"][clean["subject"]]["units"][str(clean["unit"])]["topics"]
    weak = [n for n, t in topics.items() if t["classification"] == "weak"]
    strong = [n for n, t in topics.items() if t["classification"] == "strong"]
    more = [n for n, t in topics.items() if t["classification"] == "insufficient"]
    band = "a strong result" if a["accuracy"] >= 80 else "a mixed result" if a["accuracy"] >= 50 else "a result with clear room to improve"
    summary = f"You answered {a['score']} of {a['total']} questions correctly ({a['accuracy']}%), which is {band}."
    if clean["previous"]:
        diff = a["accuracy"] - clean["previous"][-1]["accuracy"]
        summary += f" That is {'+' if diff >= 0 else ''}{diff} points compared with your previous attempt."
    cards = []
    for n, t in topics.items():
        cls = t["classification"]
        cards.append({
            "topic": n, "observed": _observed(t),
            "gap": {"weak": "Below the weak-topic threshold. The demo report cannot say which concept caused this.",
                    "insufficient": "Not enough answered questions to judge this topic yet.",
                    "strong": "No gap observed in this attempt."}[cls],
            "evidence": "; ".join(f"missed: {m['question']}" for m in a["misses"] if m["topic"] == n)[:400] or "No missed questions recorded.",
            "why_it_matters": sdef[n]["why"],
            "priority": {"weak": "High", "insufficient": "Medium", "strong": "Maintain"}[cls],
            "confidence": "observed" if t["answered"] >= 3 else "suspected",
        })
    cards.sort(key=lambda c: PRIORITIES.index(c["priority"]))
    mistakes = [f"{m['topic']}: you chose \"{m['chosen'] or 'no answer'}\" for \"{m['question']}\". Correct: \"{m['correct_answer']}\". {m['explanation']}".strip() for m in a["misses"][:6]]
    return {
        "overall": {"summary": summary,
                    "key_gaps": [f"{n}: {_observed(topics[n])}" for n in weak] + [f"{n}: more questions needed before judging" for n in more],
                    "strengths": [f"{n}: {_observed(topics[n])}" for n in strong]},
        "topics": cards,
        "tips": {
            "revise_first": weak or more[:2] or ["No weak topics found. Keep practising with mixed questions."],
            "understand_before_moving_on": [f"{n}: {sdef[n]['outcome']}" for n in weak[:3]],
            "mistakes_to_review": mistakes,
            "exercises": [f"Write three short questions on {n} and answer them without notes." for n in weak[:3]] + ["Re-answer the questions you missed after revising."],
            "revision_schedule": [{"when": "Day 1", "focus": ", ".join(weak[:2]) or "Light review of all topics"},
                                  {"when": "Day 2-3", "focus": "Work through the resources and the missed questions"},
                                  {"when": "Day 4", "focus": "Attempt the weak-topic practice quiz"}],
            "next_assessment": "Attempt weak-topic practice in about 3 to 4 days, then take a mixed quiz to confirm progress.",
        },
    }


# ---------------------------------------------------------------- LLM path
def build_messages(clean, syllabus):
    sdef = syllabus["subjects"][clean["subject"]]["units"][str(clean["unit"])]["topics"]
    system = (
        "You are a careful study-gap analyst for B.Tech students. Use ONLY the JSON data supplied. "
        "Never invent or change scores, counts, answers or topics. Do not assume anything about the student beyond the data. "
        "Separate what was observed from what you suspect: use confidence 'observed' only when the evidence is in the missed questions, otherwise 'suspected'. "
        "If evidence is thin, say so. Reply with ONE JSON object and nothing else, in exactly this shape: "
        '{"overall":{"summary":str,"key_gaps":[str],"strengths":[str]},'
        '"topics":[{"topic":str,"gap":str,"evidence":str,"why_it_matters":str,"priority":"High|Medium|Maintain","confidence":"observed|suspected"}],'
        '"tips":{"revise_first":[str],"understand_before_moving_on":[str],"mistakes_to_review":[str],"exercises":[str],'
        '"revision_schedule":[{"when":str,"focus":str}],"next_assessment":str}}. '
        "Include one entry in topics for every topic in the data. Do not output URLs or resource names."
    )
    user = json.dumps({"syllabus_unit": {"title": clean["unit_title"], "topics": sdef}, "student_data": {k: clean[k] for k in ("subject", "unit", "year", "semester", "branch", "rules", "attempt", "previous")}})
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _llm_request(messages, cfg, tools=None, stream=False):
    payload = {"model": cfg["model"], "messages": messages, "temperature": 0.2, "max_tokens": cfg["max_tokens"]}
    if tools:
        payload["tools"], payload["tool_choice"] = tools, "auto"
    if stream:
        payload["stream"] = True
    return urllib.request.Request(f"{cfg['base_url']}/chat/completions", data=json.dumps(payload).encode(), method="POST",
                                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['key']}",
                                           "Accept": "text/event-stream" if stream else "application/json",
                                           "User-Agent": "StudyGapAnalyzer/1.0"})  # providers behind Cloudflare reject urllib's default agent (error 1010)


def _http_error(e, cfg):
    try:  # show what the provider said (key redacted) so the cause is visible
        detail = e.read().decode("utf-8", "replace")[:300].replace(cfg["key"], "***").strip()
    except Exception:
        detail = ""
    print(f"LLM provider returned HTTP {e.code}: {detail}")
    err = AnalyzerError("llm_http_error", f"The AI provider returned HTTP {e.code}. Check LLM_API_KEY, LLM_MODEL and LLM_BASE_URL." + (f" Provider said: {detail}" if detail else ""), 502)
    err.upstream_status = e.code  # lets the streaming path tell "streaming not accepted" from "bad key"
    return err


def call_llm_message(messages, cfg, tools=None):
    """Calls an OpenAI-compatible /chat/completions endpoint and returns the full assistant message (dict)."""
    req = _llm_request(messages, cfg, tools)
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]
    except urllib.error.HTTPError as e:
        raise _http_error(e, cfg)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AnalyzerError("llm_unavailable", "The AI provider could not be reached or timed out.", 504)
    except (KeyError, IndexError, ValueError, TypeError):
        raise AnalyzerError("invalid_ai_response", "The AI provider returned an unexpected response format.", 502)


def call_llm_stream(messages, cfg, tools=None, opener=None):
    """Streaming version of call_llm_message: a generator that yields parsed chunk dicts from an OpenAI-compatible
    SSE response (stream: true). If the provider ignores `stream` and answers with plain JSON, that answer is yielded
    as one chunk. Closing the generator closes the connection, which stops the provider from generating more.
    `opener` is swappable in tests (defaults to urllib.request.urlopen)."""
    import streaming  # local import: streaming.py imports AnalyzerError from this module
    opener = opener or urllib.request.urlopen
    req = _llm_request(messages, cfg, tools, stream=True)
    try:
        with opener(req, timeout=cfg["timeout"]) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower() if getattr(resp, "headers", None) else "text/event-stream"
            if "event-stream" not in ctype:  # provider ignored stream:true
                data = json.loads(resp.read().decode("utf-8"))
                yield streaming.message_to_chunk(data["choices"][0]["message"], data["choices"][0].get("finish_reason") or "stop")
                return
            for data in streaming.iter_sse_data(resp):
                yield streaming.parse_event(data)
    except urllib.error.HTTPError as e:
        raise _http_error(e, cfg)
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise AnalyzerError("llm_unavailable", "The AI provider could not be reached or timed out.", 504)
    except (KeyError, IndexError, ValueError, TypeError):
        raise AnalyzerError("invalid_ai_response", "The AI provider returned an unexpected response format.", 502)


def call_llm(messages, cfg):
    """Text-only convenience wrapper used by the study gap report."""
    return call_llm_message(messages, cfg).get("content") or ""


def _list(v, name, maxitems=8, maxlen=400):
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise AnalyzerError("invalid_ai_response", f"AI field '{name}' must be a list of text.", 502)
    return [x.strip()[:maxlen] for x in v[:maxitems] if x.strip()]


def _text(v, name, maxlen=600):
    if not isinstance(v, str) or not v.strip():
        raise AnalyzerError("invalid_ai_response", f"AI field '{name}' is missing.", 502)
    return v.strip()[:maxlen]


def parse_report(text, clean, syllabus):
    """Validate the model output. Observed performance is filled in from real data, not from the model."""
    try:
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        snippet = " ".join(text.split())[:200]
        print(f"AI reply was not valid JSON. Start of reply: {snippet!r}")
        hint = f" Start of the reply: {snippet}" if snippet else " The reply was empty (the model may have run out of tokens)."
        raise AnalyzerError("invalid_ai_response", "The AI response was not valid JSON." + hint, 502)
    if not isinstance(data, dict) or not all(isinstance(data.get(k), dict) for k in ("overall", "tips")) or not isinstance(data.get("topics"), list):
        raise AnalyzerError("invalid_ai_response", "The AI response did not match the required structure.", 502)
    topics = clean["attempt"]["topics"]
    sdef = syllabus["subjects"][clean["subject"]]["units"][str(clean["unit"])]["topics"]
    o, t = data["overall"], data["tips"]
    cards, seen = [], set()
    for c in data["topics"]:
        if not isinstance(c, dict) or c.get("topic") not in topics or c["topic"] in seen:
            continue  # ignore topics the model made up or repeated
        if c.get("priority") not in PRIORITIES or c.get("confidence") not in CONFIDENCE:
            raise AnalyzerError("invalid_ai_response", "The AI gave an invalid priority or confidence value.", 502)
        seen.add(c["topic"])
        cards.append({"topic": c["topic"], "observed": _observed(topics[c["topic"]]), "gap": _text(c.get("gap"), "gap"),
                      "evidence": _text(c.get("evidence"), "evidence", 400),
                      "why_it_matters": (c.get("why_it_matters") or sdef[c["topic"]]["why"])[:300],
                      "priority": c["priority"], "confidence": c["confidence"]})
    if not cards:
        raise AnalyzerError("invalid_ai_response", "The AI response contained no usable topic analysis.", 502)
    cards.sort(key=lambda c: PRIORITIES.index(c["priority"]))
    sched = t.get("revision_schedule")
    if not isinstance(sched, list) or not all(isinstance(s, dict) and isinstance(s.get("when"), str) and isinstance(s.get("focus"), str) for s in sched):
        raise AnalyzerError("invalid_ai_response", "The AI revision schedule is malformed.", 502)
    return {
        "overall": {"summary": _text(o.get("summary"), "summary", 800), "key_gaps": _list(o.get("key_gaps"), "key_gaps"), "strengths": _list(o.get("strengths"), "strengths")},
        "topics": cards,
        "tips": {"revise_first": _list(t.get("revise_first"), "revise_first"), "understand_before_moving_on": _list(t.get("understand_before_moving_on"), "understand_before_moving_on"),
                 "mistakes_to_review": _list(t.get("mistakes_to_review"), "mistakes_to_review"), "exercises": _list(t.get("exercises"), "exercises"),
                 "revision_schedule": [{"when": s["when"][:60], "focus": s["focus"][:300]} for s in sched[:7]],
                 "next_assessment": _text(t.get("next_assessment"), "next_assessment", 300)},
    }


# ---------------------------------------------------------------- entry point
def analyze(payload, llm=call_llm):
    """Main entry. Returns {mode, model, report, resources, generated_on}. `llm` can be swapped in tests."""
    syllabus = _load("syllabus.json")
    clean = validate_payload(payload, syllabus)
    resources = recommend_resources(clean)
    cfg = llm_config()
    result = {"resources": resources, "generated_on": date.today().isoformat()}
    if not cfg["key"]:
        return {**result, "mode": "demo", "model": None, "report": demo_report(clean, syllabus)}
    messages, last = build_messages(clean, syllabus), None
    for _ in range(2):  # one retry if the model returns something invalid
        try:
            return {**result, "mode": "ai", "model": cfg["model"], "report": parse_report(llm(messages, cfg), clean, syllabus)}
        except AnalyzerError as e:
            last = e
            if e.code in ("llm_http_error", "llm_unavailable"):
                break
    last.resources = resources
    raise last
