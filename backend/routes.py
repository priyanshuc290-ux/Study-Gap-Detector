"""New API routes: accounts, conversations (saved chat), quiz attempts, reviews, contact, admin.

The existing Study Gap report route and the AI/LLM code (analyzer.py, chat.py, streaming.py) are NOT changed.
This module only adds: who is logged in, what they own, and where it is stored (SQLite, see db.py).

Security rules applied here:
  * every private route needs a valid session cookie; every query filters on the logged-in user's id
    (someone else's conversation id answers 404, exactly like an id that does not exist)
  * state-changing requests must send the header  X-Requested-With: StudyGap  (a cross-site form cannot) and, if the
    browser sends an Origin header, it must be an allowed origin
  * all SQL is parameterised; all input is validated and size-capped
"""
import json
import os
import re
import time
import uuid
from collections import defaultdict, deque
from urllib.parse import parse_qs, urlsplit

import auth
import chat
import db
from analyzer import AnalyzerError, _load
from auth import ApiError, iso, now

CSRF_HEADER, CSRF_VALUE = "X-Requested-With", "StudyGap"
BIG_BODY = 15 * 1024 * 1024  # chat messages may carry a base64 file
MAX_ATTEMPTS_PER_USER = 1000
MAX_CONVERSATIONS_PER_USER = 500
ATTEMPT_TYPES = ("diagnostic", "weak", "mixed", "reattempt")


class Stream:
    """Returned by a handler that wants to stream NDJSON events (see server.py _stream)."""
    def __init__(self, events):
        self.events = events


class Ctx:
    def __init__(self, user, body, params, query, ip):
        self.user, self.body, self.params, self.query, self.ip = user, body, params, query, ip
        self.set_cookie = None  # ("value", max_age) or ("", 0) to clear


# ---------------------------------------------------------------- rate limiting (in memory, per server process)
_hits = defaultdict(deque)


def rate_ok(key, limit, window=60):
    t = time.time()
    q = _hits[key]
    while q and t - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(t)
    return True


def _limit(key, limit, window=60, msg="Too many requests. Please wait a moment and try again."):
    if not rate_ok(key, limit, window):
        raise ApiError("rate_limited", msg, 429)


# ---------------------------------------------------------------- tiny router
ROUTES = []


def route(method, pattern, auth_required=True, max_body=64 * 1024):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn, auth_required, max_body))
        return fn
    return deco


def match(method, path):
    """Returns (route_tuple, params) or None. A path that exists for another method gives ('405', None)."""
    other = False
    for r in ROUTES:
        m = r[1].match(path)
        if m:
            if r[0] == method:
                return r, m.groupdict()
            other = True
    return ("405", None) if other else None


def csrf_ok(headers, allowed_origins):
    if headers.get(CSRF_HEADER) != CSRF_VALUE:
        return False
    origin = headers.get("Origin")
    if origin and origin not in allowed_origins:
        host = headers.get("Host", "")
        if origin not in (f"http://{host}", f"https://{host}"):
            return False
    return True


def current_user(headers):
    cookie = headers.get("Cookie") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == auth.COOKIE:
            return auth.user_for_token(v), v
    return None, None


def is_admin(user):
    return bool(user) and user["email"].lower() in auth.admin_emails()


def _s(v, lo, hi, field, label):
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
        msg = f"{label} must be {lo}-{hi} characters." if lo else f"{label} must be at most {hi} characters."
        raise ApiError("validation_error", msg, 400, {field: msg})
    return v.strip()


def _body_dict(ctx):
    if not isinstance(ctx.body, dict):
        raise ApiError("invalid_request", "Request body must be a JSON object.")
    return ctx.body


# ================================================================ auth
@route("POST", "/api/auth/signup", auth_required=False)
def signup(ctx):
    _limit(("signup", ctx.ip), 5, 600, "Too many sign-up attempts. Please wait a few minutes.")
    uid = auth.signup(_body_dict(ctx))
    ctx.set_cookie = (auth.create_session(uid), auth.SESSION_DAYS * 86400)
    return 201, {"user": _user_by_id(uid)}


@route("POST", "/api/auth/login", auth_required=False)
def login(ctx):
    body = _body_dict(ctx)
    email = str(body.get("email", "")).strip().lower()[:254]
    _limit(("login-ip", ctx.ip), 20, 300, "Too many login attempts. Please wait a few minutes.")
    q = _hits[("login-fail", email)]
    t = time.time()
    while q and t - q[0] > 900:
        q.popleft()
    if len(q) >= 8:
        raise ApiError("rate_limited", "Too many failed logins for this account. Try again in 15 minutes.", 429)
    try:
        uid = auth.login(body)
    except ApiError as e:
        if e.code == "invalid_credentials" and e.status == 401:
            q.append(t)
        raise
    q.clear()
    ctx.set_cookie = (auth.create_session(uid), auth.SESSION_DAYS * 86400)
    return {"user": _user_by_id(uid)}


@route("POST", "/api/auth/logout", auth_required=False)
def logout(ctx):
    ctx.set_cookie = ("", 0)
    return {"ok": True}


@route("GET", "/api/auth/me", auth_required=False)
def me(ctx):
    if not ctx.user:
        return {"user": None}
    return {"user": auth.public_user(ctx.user)}


def _user_by_id(uid):
    with db.connect() as c:
        return auth.public_user(c.execute("SELECT id, name, email FROM users WHERE id = ?", (uid,)).fetchone())


# ================================================================ quiz attempts (Mode 1 history)
def _attempt_clean(a):
    """Light shape check. The browser computes scores (as before); the server stores the user's own record, size-capped."""
    if not isinstance(a, dict):
        raise ApiError("invalid_request", "Each attempt must be an object.")
    aid = a.get("id")
    if not isinstance(aid, str) or not re.fullmatch(r"[\w.\-]{1,64}", aid):
        raise ApiError("invalid_request", "Attempt id is missing or invalid.")
    if not isinstance(a.get("ts"), str) or len(a["ts"]) > 40 or not isinstance(a.get("key"), str) or len(a["key"]) > 300:
        raise ApiError("invalid_request", "Attempt timestamp or key is invalid.")
    if a.get("type") not in ATTEMPT_TYPES or not isinstance(a.get("setup"), dict) or not isinstance(a.get("topics"), dict):
        raise ApiError("invalid_request", "Attempt type, setup or topics are invalid.")
    for k in ("total", "answered", "score", "accuracy"):
        if isinstance(a.get(k), bool) or not isinstance(a.get(k), int) or not 0 <= a[k] <= 1000:
            raise ApiError("invalid_request", f"Attempt field '{k}' is invalid.")
    for k in ("strong", "weak", "insufficient"):
        if not isinstance(a.get(k), list) or len(a[k]) > 100 or not all(isinstance(x, str) and len(x) <= 120 for x in a[k]):
            raise ApiError("invalid_request", f"Attempt field '{k}' is invalid.")
    blob = json.dumps(a, ensure_ascii=False)
    if len(blob) > 300_000:
        raise ApiError("invalid_request", "Attempt record is too large.", 413)
    return aid, a["ts"], blob


@route("GET", "/api/attempts")
def attempts_list(ctx):
    with db.connect() as c:
        rows = c.execute("SELECT data FROM attempts WHERE user_id = ? ORDER BY ts, id", (ctx.user["id"],)).fetchall()
    return {"attempts": [json.loads(r["data"]) for r in rows]}


@route("POST", "/api/attempts", max_body=400 * 1024)
def attempts_add(ctx):
    aid, ts, blob = _attempt_clean(_body_dict(ctx))
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM attempts WHERE user_id = ?", (ctx.user["id"],)).fetchone()[0]
        if n >= MAX_ATTEMPTS_PER_USER:
            raise ApiError("limit_reached", "You have reached the limit of saved attempts. Clear some to save more.", 409)
        c.execute("INSERT OR REPLACE INTO attempts (id, user_id, ts, data) VALUES (?, ?, ?, ?)", (aid, ctx.user["id"], ts, blob))
    return 201, {"ok": True}


@route("POST", "/api/attempts/import", max_body=4 * 1024 * 1024)
def attempts_import(ctx):
    items = _body_dict(ctx).get("attempts")
    if not isinstance(items, list) or len(items) > 200:
        raise ApiError("invalid_request", "'attempts' must be a list of at most 200 attempts.")
    cleaned = [_attempt_clean(a) for a in items]
    added = 0
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM attempts WHERE user_id = ?", (ctx.user["id"],)).fetchone()[0]
        for aid, ts, blob in cleaned:
            if n + added >= MAX_ATTEMPTS_PER_USER:
                break
            cur = c.execute("INSERT OR IGNORE INTO attempts (id, user_id, ts, data) VALUES (?, ?, ?, ?)", (aid, ctx.user["id"], ts, blob))
            added += cur.rowcount
    return {"imported": added}


@route("PATCH", r"/api/attempts/(?P<aid>[\w.\-]{1,64})", max_body=400 * 1024)
def attempts_report(ctx):
    report = _body_dict(ctx).get("report")
    if not isinstance(report, dict):
        raise ApiError("invalid_request", "'report' must be an object.")
    with db.connect() as c:
        row = c.execute("SELECT data FROM attempts WHERE user_id = ? AND id = ?", (ctx.user["id"], ctx.params["aid"])).fetchone()
        if not row:
            raise ApiError("not_found", "Attempt not found.", 404)
        data = json.loads(row["data"])
        data["report"] = report
        blob = json.dumps(data, ensure_ascii=False)
        if len(blob) > 300_000:
            raise ApiError("invalid_request", "Attempt record is too large.", 413)
        c.execute("UPDATE attempts SET data = ? WHERE user_id = ? AND id = ?", (blob, ctx.user["id"], ctx.params["aid"]))
    return {"ok": True}


@route("DELETE", "/api/attempts")
def attempts_clear(ctx):
    with db.connect() as c:
        c.execute("DELETE FROM attempts WHERE user_id = ?", (ctx.user["id"],))
    return {"ok": True}


def study_context(user_id):
    """The compact summary of a user's saved quiz results that the agent's get_study_results tool reads.
    Built on the server from the user's own stored attempts (same shape the browser used to send)."""
    with db.connect() as c:
        rows = c.execute("SELECT data FROM attempts WHERE user_id = ? ORDER BY ts, id", (user_id,)).fetchall()
    groups = {}
    for r in rows:
        a = json.loads(r["data"])
        groups.setdefault(a.get("key"), []).append(a)
    ordered = sorted(groups.values(), key=lambda g: g[-1]["ts"], reverse=True)[:3]
    out = []
    for g in ordered:
        last, s = g[-1], g[-1].get("setup", {})
        try:
            unit, year, sem = int(s.get("unit")), int(s.get("year")), int(s.get("semester"))
        except (TypeError, ValueError):
            continue
        out.append({"subject": s.get("subject"), "unit": unit, "year": year, "semester": sem, "attempts": len(g),
                    "accuracy_trend": [a.get("accuracy") for a in g[-8:]], "strong": last.get("strong", []), "weak": last.get("weak", []),
                    "insufficient": last.get("insufficient", []),
                    "topics": {t: {"correct": v.get("correct"), "answered": v.get("answered")} for t, v in last.get("topics", {}).items() if isinstance(v, dict)}})
    return out


# ================================================================ conversations
def _conv_row(c, ctx):
    row = c.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (ctx.params["cid"], ctx.user["id"])).fetchone()
    if not row:
        raise ApiError("not_found", "Conversation not found.", 404)
    return row


def _conv_public(r):
    return {"id": r["id"], "title": r["title"], "doc_name": r["doc_name"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@route("GET", "/api/conversations")
def conv_list(ctx):
    q = (ctx.query.get("q") or [""])[0].strip()[:100]
    try:
        limit = max(1, min(200, int((ctx.query.get("limit") or ["100"])[0])))
    except ValueError:
        limit = 100
    # chats with no messages yet (for example the first message was rejected) are not listed
    sql, args = "SELECT * FROM conversations c WHERE c.user_id = ? AND EXISTS (SELECT 1 FROM messages x WHERE x.conversation_id = c.id)", [ctx.user["id"]]
    if q:
        sql += (" AND (c.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'))")
        args += [_like(q), _like(q)]
    sql += " ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?"
    with db.connect() as c:
        rows = c.execute(sql, args + [limit]).fetchall()
    return {"conversations": [_conv_public(r) for r in rows]}


@route("POST", "/api/conversations")
def conv_create(ctx):
    title = "New chat"
    body = ctx.body if isinstance(ctx.body, dict) else {}
    if isinstance(body.get("title"), str) and body["title"].strip():
        title = body["title"].strip()[:80]
    cid, ts = uuid.uuid4().hex, iso(now())
    with db.connect() as c:
        if c.execute("SELECT COUNT(*) FROM conversations WHERE user_id = ?", (ctx.user["id"],)).fetchone()[0] >= MAX_CONVERSATIONS_PER_USER:
            raise ApiError("limit_reached", "You have reached the limit of saved conversations. Delete some to start new ones.", 409)
        c.execute("INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", (cid, ctx.user["id"], title, ts, ts))
        row = c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return 201, {"conversation": _conv_public(row)}


@route("GET", r"/api/conversations/(?P<cid>[0-9a-f]{32})")
def conv_get(ctx):
    with db.connect() as c:
        conv = _conv_row(c, ctx)
        rows = c.execute("SELECT id, role, content, meta, created_at FROM messages WHERE conversation_id = ? ORDER BY id", (conv["id"],)).fetchall()
    return {"conversation": _conv_public(conv),
            "messages": [{"id": r["id"], "role": r["role"], "content": r["content"], "meta": json.loads(r["meta"]) if r["meta"] else {}, "created_at": r["created_at"]} for r in rows]}


@route("PATCH", r"/api/conversations/(?P<cid>[0-9a-f]{32})")
def conv_rename(ctx):
    title = _s(_body_dict(ctx).get("title"), 1, 80, "title", "Title")
    with db.connect() as c:
        conv = _conv_row(c, ctx)
        c.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv["id"]))  # renaming does not reorder the list
        conv = c.execute("SELECT * FROM conversations WHERE id = ?", (conv["id"],)).fetchone()
    return {"conversation": _conv_public(conv)}


@route("DELETE", r"/api/conversations/(?P<cid>[0-9a-f]{32})")
def conv_delete(ctx):
    with db.connect() as c:
        conv = _conv_row(c, ctx)
        c.execute("DELETE FROM conversations WHERE id = ?", (conv["id"],))  # messages go with it (ON DELETE CASCADE)
    return {"ok": True}


@route("DELETE", r"/api/conversations/(?P<cid>[0-9a-f]{32})/document")
def conv_doc_clear(ctx):
    """Stops using the PDF text that was attached earlier in this conversation."""
    with db.connect() as c:
        conv = _conv_row(c, ctx)
        c.execute("UPDATE conversations SET doc_name = NULL, doc_text = NULL WHERE id = ?", (conv["id"],))
    return {"ok": True}


@route("POST", r"/api/conversations/(?P<cid>[0-9a-f]{32})/messages", max_body=BIG_BODY)
def conv_message(ctx):
    """Sends a message to the existing Ask AI Agent (chat.chat_stream, unchanged) and saves both sides.
    Order matters: nothing is written until the agent accepted the request (a missing key, a bad file or a rejected
    message is a plain error and leaves the conversation as it was). The user's message is then saved, and the assistant's
    reply is saved when the stream finishes (or the partial text if the student pressed Stop or the provider failed)."""
    body = _body_dict(ctx)
    _limit(("chat", ctx.user["id"]), int(os.environ.get("CHAT_RATE_LIMIT_PER_MIN", "20")), 60,
           "Too many messages. Please wait a minute and retry.")
    with db.connect() as c:
        conv = _conv_row(c, ctx)
        rows = c.execute("SELECT id, role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 12", (conv["id"],)).fetchall()
    stored = list(reversed(rows))
    retry = bool(body.get("retry")) and stored and stored[-1]["role"] == "user"  # the last user message never got an answer
    prior = stored[:-1] if retry else stored
    payload = {"message": body.get("message", ""), "history": [{"role": r["role"], "content": r["content"]} for r in prior[-chat.MAX_HISTORY:]]}
    if conv["doc_text"]:
        payload["document"] = {"name": conv["doc_name"] or "document", "text": conv["doc_text"]}
    if body.get("file") is not None:
        payload["file"] = body["file"]
    if body.get("use_results", True):
        ctxt = study_context(ctx.user["id"])
        if ctxt:
            payload["study_context"] = ctxt
    events = chat.chat_stream(payload)  # validates now: AnalyzerError here becomes a normal HTTP error and nothing is saved
    text = str(payload["message"]).strip()
    attach = payload.get("file") if isinstance(payload.get("file"), dict) else None
    user_content = text or (f"[attached {str(attach.get('name', 'file'))[:100]}]" if attach else "")
    meta = {"attachment": {"name": str(attach.get("name", "file"))[:100]}} if attach else {}
    ts = iso(now())
    with db.connect() as c:
        if not retry:
            c.execute("INSERT INTO messages (conversation_id, role, content, meta, created_at) VALUES (?, 'user', ?, ?, ?)",
                      (conv["id"], user_content, json.dumps(meta) if meta else None, ts))
        if conv["title"] == "New chat" and not prior:
            c.execute("UPDATE conversations SET title = ? WHERE id = ?", ((user_content.replace("\n", " ")[:60] or "New chat"), conv["id"]))
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, conv["id"]))
    return Stream(_persisting(events, conv["id"]))


def _persisting(events, cid):
    parts, finished = [], False

    def save(content, meta):
        ts = iso(now())
        with db.connect() as c:
            c.execute("INSERT INTO messages (conversation_id, role, content, meta, created_at) VALUES (?, 'assistant', ?, ?, ?)", (cid, content, json.dumps(meta), ts))
            c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, cid))
            return c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()

    try:
        for ev in events:
            kind = ev.get("type")
            if kind == "token":
                parts.append(ev["text"])
            elif kind == "reset":
                parts.clear()
            elif kind == "done":
                meta = {"model": ev.get("model"), "notes": ev.get("notes") or [], "steps": ev.get("steps") or [], "actions": ev.get("actions") or []}
                doc = ev.get("document")
                with db.connect() as c:
                    if doc and isinstance(doc, dict):
                        c.execute("UPDATE conversations SET doc_name = ?, doc_text = ? WHERE id = ?", (str(doc.get("name", ""))[:200], str(doc.get("text", "")), cid))
                row = save(ev["reply"], meta)
                finished = True
                ev = {**ev, "conversation": _conv_public(row)}
            yield ev
    finally:
        close = getattr(events, "close", None)
        if close:
            close()  # stops the provider connection promptly when the browser went away
        if not finished and parts and "".join(parts).strip():
            try:
                save("".join(parts), {"partial": True})  # keep what the student saw
            except Exception:
                pass


# ================================================================ reviews
def _display_name(name):
    bits = name.split()
    return bits[0] if len(bits) == 1 else f"{bits[0]} {bits[-1][0]}."


def _review_public(r, with_status=False):
    out = {"id": r["id"], "name": _display_name(r["name"]), "rating": r["rating"], "text": r["text"], "created_at": r["updated_at"]}
    if with_status:
        out["status"] = r["status"]
    return out


@route("GET", "/api/reviews", auth_required=False)
def reviews_list(ctx):
    with db.connect() as c:
        rows = c.execute("SELECT r.*, u.name FROM reviews r JOIN users u ON u.id = r.user_id WHERE r.status = 'approved' ORDER BY r.updated_at DESC LIMIT 100").fetchall()
        agg = c.execute("SELECT COUNT(*) n, AVG(rating) a FROM reviews WHERE status = 'approved'").fetchone()
        mine = None
        if ctx.user:
            m = c.execute("SELECT r.*, u.name FROM reviews r JOIN users u ON u.id = r.user_id WHERE r.user_id = ?", (ctx.user["id"],)).fetchone()
            mine = _review_public(m, True) if m else None
    return {"summary": {"count": agg["n"], "average": round(agg["a"], 2) if agg["n"] else None},
            "reviews": [_review_public(r) for r in rows], "mine": mine}


@route("PUT", "/api/reviews/mine")
def review_put(ctx):
    body = _body_dict(ctx)
    rating, text = body.get("rating"), body.get("text", "")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ApiError("validation_error", "Choose a rating from 1 to 5 stars.", 400, {"rating": "Choose a rating from 1 to 5 stars."})
    text = _s(text if text is not None else "", 0, 1000, "text", "Review")
    ts = iso(now())
    with db.connect() as c:
        c.execute("""INSERT INTO reviews (user_id, rating, text, status, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET rating = excluded.rating, text = excluded.text, status = 'pending', updated_at = excluded.updated_at""",
                  (ctx.user["id"], rating, text, ts, ts))
        m = c.execute("SELECT r.*, u.name FROM reviews r JOIN users u ON u.id = r.user_id WHERE r.user_id = ?", (ctx.user["id"],)).fetchone()
    return {"mine": _review_public(m, True)}  # an edit goes back to 'pending' so approved text cannot be swapped unseen


@route("DELETE", "/api/reviews/mine")
def review_delete(ctx):
    with db.connect() as c:
        c.execute("DELETE FROM reviews WHERE user_id = ?", (ctx.user["id"],))
    return {"ok": True}


# ================================================================ contact
@route("POST", "/api/contact", auth_required=False)
def contact(ctx):
    body = _body_dict(ctx)
    _limit(("contact", ctx.ip), 5, 600, "Too many messages from this connection. Please try again later.")
    fields = {}
    out = {}
    for field, lo, hi, label in (("name", 1, 80, "Name"), ("subject", 3, 120, "Subject"), ("message", 10, 4000, "Message")):
        try:
            out[field] = _s(body.get(field), lo, hi, field, label)
        except ApiError as e:
            fields.update(e.fields)
    try:
        out["email"] = auth.clean_email(body.get("email"))
    except ApiError as e:
        fields.update(e.fields)
    if fields:
        raise ApiError("validation_error", "Check the highlighted fields.", 400, fields)
    if str(body.get("website", "")).strip():  # hidden honeypot field: only bots fill it in
        return 201, {"ok": True}
    with db.connect() as c:
        c.execute("INSERT INTO contact_messages (user_id, name, email, subject, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (ctx.user["id"] if ctx.user else None, out["name"], out["email"], out["subject"], out["message"], iso(now())))
    return 201, {"ok": True}


# ================================================================ admin (users listed in ADMIN_EMAILS)
def _need_admin(ctx):
    if not is_admin(ctx.user):
        raise ApiError("forbidden", "Administrator access is required.", 403)


@route("GET", "/api/admin/reviews")
def admin_reviews(ctx):
    _need_admin(ctx)
    status = (ctx.query.get("status") or ["pending"])[0]
    sql = "SELECT r.*, u.name, u.email FROM reviews r JOIN users u ON u.id = r.user_id"
    args = []
    if status in ("pending", "approved", "rejected"):
        sql += " WHERE r.status = ?"
        args.append(status)
    with db.connect() as c:
        rows = c.execute(sql + " ORDER BY r.updated_at DESC LIMIT 200", args).fetchall()
    return {"reviews": [{**_review_public(r, True), "name": r["name"], "email": r["email"]} for r in rows]}


@route("POST", r"/api/admin/reviews/(?P<rid>\d{1,9})")
def admin_review_set(ctx):
    _need_admin(ctx)
    status = _body_dict(ctx).get("status")
    if status not in ("approved", "rejected", "pending"):
        raise ApiError("validation_error", "Status must be approved, rejected or pending.")
    with db.connect() as c:
        cur = c.execute("UPDATE reviews SET status = ? WHERE id = ?", (status, int(ctx.params["rid"])))
        if not cur.rowcount:
            raise ApiError("not_found", "Review not found.", 404)
    return {"ok": True}


@route("GET", "/api/admin/contact")
def admin_contact(ctx):
    _need_admin(ctx)
    with db.connect() as c:
        rows = c.execute("SELECT * FROM contact_messages ORDER BY id DESC LIMIT 200").fetchall()
    return {"messages": [dict(r) for r in rows]}


@route("POST", r"/api/admin/contact/(?P<mid>\d{1,9})")
def admin_contact_set(ctx):
    _need_admin(ctx)
    handled = 1 if _body_dict(ctx).get("handled") else 0
    with db.connect() as c:
        cur = c.execute("UPDATE contact_messages SET handled = ? WHERE id = ?", (handled, int(ctx.params["mid"])))
        if not cur.rowcount:
            raise ApiError("not_found", "Message not found.", 404)
    return {"ok": True}


def parse_query(path_with_query):
    return parse_qs(urlsplit(path_with_query).query)
