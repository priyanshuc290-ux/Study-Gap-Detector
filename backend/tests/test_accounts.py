"""Accounts, sessions, saved chats, attempts, reviews, contact and admin: real HTTP against the real handler,
a temporary SQLite file, and a fake LLM stream (no real AI is called)."""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def text_chunk(t, finish=None):
    return {"choices": [{"delta": {"content": t}, "finish_reason": finish}]}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update({"LLM_API_KEY": "test-key", "LLM_BASE_URL": "http://127.0.0.1:9/v1", "LLM_MODEL": "test-model", "CHAT_RATE_LIMIT_PER_MIN": "1000",
                           "DATABASE_PATH": os.path.join(cls.tmp.name, "t.db"), "ADMIN_EMAILS": "admin@example.com"})
        import analyzer, auth, chat, db, routes, server
        cls.m = type("M", (), dict(analyzer=analyzer, auth=auth, chat=chat, db=db, routes=routes, server=server))
        db.init_db()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.orig_defaults = chat.chat_stream.__defaults__
        cls.set_llm(lambda messages, cfg, tools=None: iter([text_chunk("Hello "), text_chunk("there."), text_chunk("", "stop")]))

    @classmethod
    def set_llm(cls, fn):
        cls.m.chat.chat_stream.__defaults__ = (fn, cls.m.analyzer.call_llm_message)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()
        cls.m.chat.chat_stream.__defaults__ = cls.orig_defaults
        for k in ("LLM_API_KEY", "DATABASE_PATH", "ADMIN_EMAILS"):
            os.environ.pop(k, None)
        cls.tmp.cleanup()

    # -- helpers
    def call(self, method, path, body=None, cookie=None, headers=None, csrf=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        if csrf:
            h["X-Requested-With"] = "StudyGap"
        if cookie:
            h["Cookie"] = cookie
        h.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw
        return r.status, data, r

    def stream(self, path, body, cookie):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json", "X-Requested-With": "StudyGap", "Cookie": cookie})
        r = conn.getresponse()
        raw = r.read().decode()
        conn.close()
        if r.status != 200 or "ndjson" not in (r.getheader("Content-Type") or ""):
            return r.status, json.loads(raw)
        return r.status, [json.loads(l) for l in raw.splitlines() if l.strip()]

    _n = 0

    def user(self, name="Test User", email=None, pw="password1"):
        type(self)._n += 1
        email = email or f"u{self._n}_{id(self)}@example.com"
        self.m.routes._hits.pop(("signup", "127.0.0.1"), None)  # the sign-up limiter is tested on its own below
        s, d, r = self.call("POST", "/api/auth/signup", {"name": name, "email": email, "password": pw, "password_confirm": pw})
        self.assertEqual(s, 201, d)
        cookie = r.getheader("Set-Cookie").split(";")[0]
        return email, cookie


class AuthTests(Base):
    def test_signup_sets_a_secure_cookie_and_never_returns_the_hash(self):
        s, d, r = self.call("POST", "/api/auth/signup", {"name": "Asha K", "email": "Asha@Example.com", "password": "abcdefg1", "password_confirm": "abcdefg1"})
        self.assertEqual(s, 201)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie); self.assertIn("SameSite=Lax", cookie); self.assertIn("Path=/", cookie)
        self.assertEqual(d["user"]["email"], "asha@example.com")
        self.assertNotIn("password", json.dumps(d))

    def test_password_is_hashed_never_plaintext(self):
        self.user(email="hash@example.com", pw="Sup3rSecret!")
        con = sqlite3.connect(os.environ["DATABASE_PATH"])
        stored = con.execute("SELECT password_hash FROM users WHERE email = 'hash@example.com'").fetchone()[0]
        con.close()
        self.assertTrue(stored.startswith(("scrypt$", "pbkdf2$")))
        self.assertNotIn("Sup3rSecret", stored)
        self.assertTrue(self.m.auth.verify_password("Sup3rSecret!", stored))
        self.assertFalse(self.m.auth.verify_password("sup3rsecret!", stored))

    def test_session_token_is_stored_only_as_a_hash(self):
        _, cookie = self.user()
        token = cookie.split("=", 1)[1]
        con = sqlite3.connect(os.environ["DATABASE_PATH"])
        ids = [r[0] for r in con.execute("SELECT id FROM sessions")]
        con.close()
        self.assertNotIn(token, ids)

    def test_signup_is_rate_limited(self):
        self.m.routes._hits.clear()
        codes = []
        for i in range(7):
            codes.append(self.call("POST", "/api/auth/signup", {"name": "R", "email": f"rl{i}@example.com", "password": "abcdefg1", "password_confirm": "abcdefg1"})[0])
        self.assertEqual(codes[:5], [201] * 5)
        self.assertEqual(codes[-1], 429)
        self.m.routes._hits.clear()

    def test_signup_validation(self):
        self.m.routes._hits.clear()
        bad = [({"name": "", "email": "a@b.co", "password": "abcdefg1", "password_confirm": "abcdefg1"}, "name"),
               ({"name": "A", "email": "nope", "password": "abcdefg1", "password_confirm": "abcdefg1"}, "email"),
               ({"name": "A", "email": "a@b.co", "password": "short1", "password_confirm": "short1"}, "password"),
               ({"name": "A", "email": "a@b.co", "password": "onlyletters", "password_confirm": "onlyletters"}, "password"),
               ({"name": "A", "email": "a@b.co", "password": "abcdefg1", "password_confirm": "different1"}, "password_confirm")]
        for body, field in bad:
            s, d, _ = self.call("POST", "/api/auth/signup", body)
            self.assertEqual(s, 400, body)
            self.assertIn(field, d["error"]["fields"])

    def test_duplicate_email_is_refused_case_insensitively(self):
        email, _ = self.user(email="dup@example.com")
        s, d, r = self.call("POST", "/api/auth/signup", {"name": "X", "email": "DUP@example.com", "password": "abcdefg1", "password_confirm": "abcdefg1"})
        self.assertEqual(s, 409)
        self.assertIsNone(r.getheader("Set-Cookie"))

    def test_login_logout_and_me(self):
        self.user(email="l@example.com", pw="abcdefg1")
        s, d, r = self.call("POST", "/api/auth/login", {"email": "L@example.com", "password": "abcdefg1"})
        self.assertEqual(s, 200)
        cookie = r.getheader("Set-Cookie").split(";")[0]
        self.assertEqual(self.call("GET", "/api/auth/me", cookie=cookie)[1]["user"]["email"], "l@example.com")
        s, _, r = self.call("POST", "/api/auth/logout", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"))
        self.assertIsNone(self.call("GET", "/api/auth/me", cookie=cookie)[1]["user"])  # the old cookie is dead server-side
        self.assertEqual(self.call("GET", "/api/conversations", cookie=cookie)[0], 401)

    def test_wrong_password_and_unknown_email_give_the_same_error(self):
        self.user(email="w@example.com")
        a = self.call("POST", "/api/auth/login", {"email": "w@example.com", "password": "wrongpass1"})
        b = self.call("POST", "/api/auth/login", {"email": "nobody@example.com", "password": "wrongpass1"})
        self.assertEqual((a[0], a[1]["error"]["message"]), (b[0], b[1]["error"]["message"]))
        self.assertEqual(a[0], 401)

    def test_repeated_failed_logins_are_throttled(self):
        self.user(email="t@example.com")
        codes = [self.call("POST", "/api/auth/login", {"email": "t@example.com", "password": "bad"})[0] for _ in range(10)]
        self.assertEqual(codes[0], 401)
        self.assertEqual(codes[-1], 429)

    def test_invalid_and_expired_sessions_are_rejected(self):
        self.assertEqual(self.call("GET", "/api/conversations", cookie="sg_session=garbage")[0], 401)
        self.assertEqual(self.call("GET", "/api/attempts")[0], 401)
        _, cookie = self.user()
        token = cookie.split("=", 1)[1]
        con = sqlite3.connect(os.environ["DATABASE_PATH"])
        con.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (self.m.auth._hash_token(token),))
        con.commit(); con.close()
        s, d, r = self.call("GET", "/api/conversations", cookie=cookie)
        self.assertEqual(s, 401)
        self.assertEqual(d["error"]["code"], "unauthenticated")

    def test_csrf_header_and_origin_are_required_for_changes(self):
        _, cookie = self.user()
        self.assertEqual(self.call("POST", "/api/conversations", {}, cookie=cookie, csrf=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/conversations", {}, cookie=cookie, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/conversations", {}, cookie=cookie, headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 201)


class ConversationTests(Base):
    def test_create_send_save_reopen_in_order(self):
        _, ck = self.user()
        s, d, _ = self.call("POST", "/api/conversations", {}, cookie=ck)
        cid = d["conversation"]["id"]
        s, ev = self.stream(f"/api/conversations/{cid}/messages", {"message": "What is a stack?"}, ck)
        self.assertEqual(s, 200)
        done = ev[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["reply"], "Hello there.")
        self.assertEqual(done["conversation"]["title"], "What is a stack?")
        self.stream(f"/api/conversations/{cid}/messages", {"message": "And a queue?"}, ck)
        s, d, _ = self.call("GET", f"/api/conversations/{cid}", cookie=ck)
        self.assertEqual([(m["role"], m["content"]) for m in d["messages"]],
                         [("user", "What is a stack?"), ("assistant", "Hello there."), ("user", "And a queue?"), ("assistant", "Hello there.")])
        self.assertEqual(d["messages"][1]["meta"]["steps"], [])

    def test_history_comes_from_the_database_not_from_the_browser(self):
        _, ck = self.user()
        cid = self.call("POST", "/api/conversations", {}, cookie=ck)[1]["conversation"]["id"]
        self.stream(f"/api/conversations/{cid}/messages", {"message": "first"}, ck)
        seen = {}

        def llm(messages, cfg, tools=None):
            seen["roles"] = [m["role"] for m in messages]
            return iter([text_chunk("ok"), text_chunk("", "stop")])
        self.set_llm(llm)
        try:
            self.stream(f"/api/conversations/{cid}/messages", {"message": "second", "history": [{"role": "user", "content": "FORGED"}]}, ck)
        finally:
            self.set_llm(lambda m, c, t=None: iter([text_chunk("Hello "), text_chunk("there."), text_chunk("", "stop")]))
        self.assertEqual(seen["roles"], ["system", "user", "assistant", "user"])

    def test_users_cannot_see_or_change_each_others_conversations(self):
        _, a = self.user(); _, b = self.user()
        cid = self.call("POST", "/api/conversations", {}, cookie=a)[1]["conversation"]["id"]
        self.stream(f"/api/conversations/{cid}/messages", {"message": "private note"}, a)
        self.assertEqual(self.call("GET", f"/api/conversations/{cid}", cookie=b)[0], 404)
        self.assertEqual(self.call("PATCH", f"/api/conversations/{cid}", {"title": "hacked"}, cookie=b)[0], 404)
        self.assertEqual(self.call("DELETE", f"/api/conversations/{cid}", cookie=b)[0], 404)
        self.assertEqual(self.stream(f"/api/conversations/{cid}/messages", {"message": "x"}, b)[0], 404)
        self.assertEqual(self.call("GET", "/api/conversations", cookie=b)[1]["conversations"], [])
        self.assertEqual(self.call("GET", "/api/conversations?q=private", cookie=b)[1]["conversations"], [])
        s, d, _ = self.call("GET", f"/api/conversations/{cid}", cookie=a)  # still intact for the owner
        self.assertEqual(s, 200); self.assertEqual(len(d["messages"]), 2)

    def test_rename_search_and_delete(self):
        _, ck = self.user()
        cid = self.call("POST", "/api/conversations", {}, cookie=ck)[1]["conversation"]["id"]
        self.stream(f"/api/conversations/{cid}/messages", {"message": "binary trees please"}, ck)
        s, d, _ = self.call("PATCH", f"/api/conversations/{cid}", {"title": "Trees"}, cookie=ck)
        self.assertEqual(d["conversation"]["title"], "Trees")
        self.assertEqual(self.call("PATCH", f"/api/conversations/{cid}", {"title": "  "}, cookie=ck)[0], 400)
        self.assertEqual(len(self.call("GET", "/api/conversations?q=binary", cookie=ck)[1]["conversations"]), 1)  # finds message text
        self.assertEqual(len(self.call("GET", "/api/conversations?q=100%25", cookie=ck)[1]["conversations"]), 0)  # % is not a wildcard
        self.assertEqual(self.call("DELETE", f"/api/conversations/{cid}", cookie=ck)[0], 200)
        self.assertEqual(self.call("GET", f"/api/conversations/{cid}", cookie=ck)[0], 404)
        con = sqlite3.connect(os.environ["DATABASE_PATH"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (cid,)).fetchone()[0], 0)  # messages deleted too
        con.close()

    def test_a_rejected_request_saves_nothing(self):
        _, ck = self.user()
        cid = self.call("POST", "/api/conversations", {}, cookie=ck)[1]["conversation"]["id"]
        s, d = self.stream(f"/api/conversations/{cid}/messages", {"message": ""}, ck)
        self.assertEqual(s, 400)
        self.assertEqual(self.call("GET", f"/api/conversations/{cid}", cookie=ck)[1]["messages"], [])

    def test_provider_failure_keeps_the_user_message_and_partial_text_and_retry_does_not_duplicate(self):
        _, ck = self.user()
        cid = self.call("POST", "/api/conversations", {}, cookie=ck)[1]["conversation"]["id"]

        def bad(messages, cfg, tools=None):
            raise self.m.analyzer.AnalyzerError("llm_unavailable", "The AI provider could not be reached or timed out.", 504)
            yield
        self.set_llm(bad)
        s, ev = self.stream(f"/api/conversations/{cid}/messages", {"message": "hello?"}, ck)
        self.assertEqual(ev[-1]["type"], "error")
        d = self.call("GET", f"/api/conversations/{cid}", cookie=ck)[1]
        self.assertEqual([m["role"] for m in d["messages"]], ["user"])  # no assistant message was invented
        self.set_llm(lambda m, c, t=None: iter([text_chunk("recovered"), text_chunk("", "stop")]))
        s, ev = self.stream(f"/api/conversations/{cid}/messages", {"message": "hello?", "retry": True}, ck)
        self.assertEqual(ev[-1]["reply"], "recovered")
        d = self.call("GET", f"/api/conversations/{cid}", cookie=ck)[1]
        self.assertEqual([(m["role"], m["content"]) for m in d["messages"]], [("user", "hello?"), ("assistant", "recovered")])
        self.set_llm(lambda m, c, t=None: iter([text_chunk("Hello "), text_chunk("there."), text_chunk("", "stop")]))

    def test_partial_answer_is_saved_when_the_browser_disconnects(self):
        _, ck = self.user()
        cid = self.call("POST", "/api/conversations", {}, cookie=ck)[1]["conversation"]["id"]
        import time

        def slow(messages, cfg, tools=None):
            for i in range(8):
                yield text_chunk(f"w{i} ")
                time.sleep(0.4)
        self.set_llm(slow)
        import socket
        body = json.dumps({"message": "talk"})
        sock = socket.create_connection(("127.0.0.1", self.port))
        sock.sendall((f"POST /api/conversations/{cid}/messages HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                      f"X-Requested-With: StudyGap\r\nCookie: {ck}\r\nContent-Length: {len(body)}\r\n\r\n{body}").encode())
        buf = b""
        while b'"token"' not in buf:
            buf += sock.recv(4096)
        sock.close()  # like pressing Stop: the browser goes away mid-answer
        for _ in range(60):
            d = self.call("GET", f"/api/conversations/{cid}", cookie=ck)[1]
            if len(d["messages"]) == 2:
                break
            time.sleep(0.1)
        self.set_llm(lambda m, c, t=None: iter([text_chunk("Hello "), text_chunk("there."), text_chunk("", "stop")]))
        self.assertTrue(d["messages"][1]["content"].startswith("w0 "))
        self.assertNotIn("w7", d["messages"][1]["content"])  # generation stopped early
        self.assertTrue(d["messages"][1]["meta"]["partial"])

    def test_study_results_come_from_the_users_own_saved_attempts(self):
        _, a = self.user(); _, b = self.user()
        att = {"id": "a1", "ts": "2026-01-01T00:00:00Z", "key": "2|1|CSE|Data Structures|1", "type": "diagnostic",
               "setup": {"year": "2", "semester": "1", "branch": "CSE", "subject": "Data Structures", "unit": "1"},
               "total": 10, "answered": 10, "score": 4, "accuracy": 40,
               "topics": {"Arrays": {"correct": 1, "answered": 3, "total": 3}}, "strong": [], "weak": ["Arrays"], "insufficient": []}
        self.assertEqual(self.call("POST", "/api/attempts", att, cookie=a)[0], 201)
        ctx_a = self.m.routes.study_context(self.call("GET", "/api/auth/me", cookie=a)[1]["user"]["id"])
        ctx_b = self.m.routes.study_context(self.call("GET", "/api/auth/me", cookie=b)[1]["user"]["id"])
        self.assertEqual(ctx_a[0]["weak"], ["Arrays"])
        self.assertEqual(ctx_b, [])


class AttemptTests(Base):
    ATT = {"id": "x1", "ts": "2026-01-01T00:00:00Z", "key": "k", "type": "diagnostic", "setup": {}, "total": 3, "answered": 3, "score": 2, "accuracy": 67,
           "topics": {}, "strong": [], "weak": [], "insufficient": []}

    def test_attempts_are_private_and_validated(self):
        _, a = self.user(); _, b = self.user()
        self.assertEqual(self.call("POST", "/api/attempts", self.ATT, cookie=a)[0], 201)
        self.assertEqual(len(self.call("GET", "/api/attempts", cookie=a)[1]["attempts"]), 1)
        self.assertEqual(self.call("GET", "/api/attempts", cookie=b)[1]["attempts"], [])
        self.assertEqual(self.call("PATCH", "/api/attempts/x1", {"report": {"mode": "demo"}}, cookie=b)[0], 404)
        self.assertEqual(self.call("PATCH", "/api/attempts/x1", {"report": {"mode": "demo"}}, cookie=a)[0], 200)
        self.assertEqual(self.call("GET", "/api/attempts", cookie=a)[1]["attempts"][0]["report"], {"mode": "demo"})
        self.assertEqual(self.call("POST", "/api/attempts", {**self.ATT, "id": "../x"}, cookie=a)[0], 400)
        self.assertEqual(self.call("POST", "/api/attempts", {**self.ATT, "score": "9"}, cookie=a)[0], 400)
        self.call("DELETE", "/api/attempts", cookie=b)  # clearing b's attempts does not touch a's
        self.assertEqual(len(self.call("GET", "/api/attempts", cookie=a)[1]["attempts"]), 1)

    def test_import_skips_existing_ids(self):
        _, a = self.user()
        self.call("POST", "/api/attempts", self.ATT, cookie=a)
        s, d, _ = self.call("POST", "/api/attempts/import", {"attempts": [self.ATT, {**self.ATT, "id": "x2"}]}, cookie=a)
        self.assertEqual(d["imported"], 1)


class ReviewAndContactTests(Base):
    def test_review_flow_with_moderation(self):
        _, ck = self.user(name="Priya Nair")
        self.assertEqual(self.call("PUT", "/api/reviews/mine", {"rating": 5, "text": "Helpful"})[0], 401)  # needs login
        for bad in ({"rating": 0}, {"rating": 6}, {"rating": "5"}, {"rating": True}, {"rating": 3, "text": "x" * 1001}):
            self.assertEqual(self.call("PUT", "/api/reviews/mine", bad, cookie=ck)[0], 400, bad)
        s, d, _ = self.call("PUT", "/api/reviews/mine", {"rating": 4, "text": "Clear explanations"}, cookie=ck)
        self.assertEqual(d["mine"]["status"], "pending")
        self.assertNotIn("Clear explanations", json.dumps(self.call("GET", "/api/reviews")[1]["reviews"]))  # pending reviews are not public
        # a normal user cannot moderate
        self.assertEqual(self.call("GET", "/api/admin/reviews", cookie=ck)[0], 403)
        self.assertEqual(self.call("POST", f"/api/admin/reviews/{d['mine']['id']}", {"status": "approved"}, cookie=ck)[0], 403)
        _, admin = self.user(name="Admin", email="admin@example.com")
        pending = self.call("GET", "/api/admin/reviews?status=pending", cookie=admin)[1]["reviews"]
        self.assertTrue(any(r["id"] == d["mine"]["id"] for r in pending))
        self.assertEqual(self.call("POST", f"/api/admin/reviews/{d['mine']['id']}", {"status": "approved"}, cookie=admin)[0], 200)
        pub = self.call("GET", "/api/reviews")[1]
        mine = [r for r in pub["reviews"] if r["id"] == d["mine"]["id"]][0]
        self.assertEqual(mine["name"], "Priya N.")  # first name + last initial only; no email exposed
        self.assertNotIn("email", mine)
        self.assertGreaterEqual(pub["summary"]["count"], 1)
        # editing sends it back to moderation; one review per user
        s, d2, _ = self.call("PUT", "/api/reviews/mine", {"rating": 2, "text": "Changed"}, cookie=ck)
        self.assertEqual(d2["mine"]["id"], d["mine"]["id"]); self.assertEqual(d2["mine"]["status"], "pending")
        self.assertNotIn("Changed", json.dumps(self.call("GET", "/api/reviews")[1]["reviews"]))

    def test_summary_is_empty_without_approved_reviews(self):
        con = sqlite3.connect(os.environ["DATABASE_PATH"])
        con.execute("DELETE FROM reviews"); con.commit(); con.close()
        d = self.call("GET", "/api/reviews")[1]
        self.assertEqual(d["summary"], {"count": 0, "average": None})
        self.assertEqual(d["reviews"], [])

    def test_contact_validation_storage_and_admin_view(self):
        good = {"name": "Ravi", "email": "ravi@example.com", "subject": "Question", "message": "How do I reset my quiz history?"}
        for field, value in (("name", ""), ("email", "bad"), ("subject", "x"), ("message", "short")):
            s, d, _ = self.call("POST", "/api/contact", {**good, field: value})
            self.assertEqual(s, 400, field)
            self.assertIn(field, d["error"]["fields"])
        self.m.routes._hits.clear()
        con = sqlite3.connect(os.environ["DATABASE_PATH"])
        before = con.execute("SELECT COUNT(*) FROM contact_messages").fetchone()[0]
        self.assertEqual(self.call("POST", "/api/contact", good)[0], 201)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM contact_messages").fetchone()[0], before + 1)  # success means it really was stored
        self.assertEqual(self.call("POST", "/api/contact", {**good, "website": "http://spam"})[0], 201)  # honeypot: dropped quietly
        self.assertEqual(con.execute("SELECT COUNT(*) FROM contact_messages").fetchone()[0], before + 1)
        con.close()
        _, ck = self.user()
        self.assertEqual(self.call("GET", "/api/admin/contact", cookie=ck)[0], 403)
        self.assertEqual(self.call("GET", "/api/admin/contact")[0], 401)

    def test_contact_is_rate_limited(self):
        good = {"name": "Ravi", "email": "ravi@example.com", "subject": "Question", "message": "A message long enough."}
        self.m.routes._hits.clear()
        codes = [self.call("POST", "/api/contact", good)[0] for _ in range(7)]
        self.assertEqual(codes[:5], [201] * 5)
        self.assertEqual(codes[-1], 429)
        self.m.routes._hits.clear()


class StaticAndHeaderTests(Base):
    def test_frontend_is_served_with_security_headers_and_no_traversal(self):
        s, d, r = self.call("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.call("GET", "/../backend/auth.py")[0], 404)
        self.assertEqual(self.call("GET", "/instance/studygap.db")[0], 404)
        self.assertEqual(self.call("GET", "/api/health")[0], 200)

    def test_study_gap_report_route_is_still_public(self):
        s, d, _ = self.call("POST", "/api/study-gap-report", {"bad": "payload"})
        self.assertEqual(s, 400)  # reached the analyzer's own validation (no login needed), not a 401
        self.assertEqual(d["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
