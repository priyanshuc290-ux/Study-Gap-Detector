"""Minimal backend (Python standard library only, no pip install needed).

Run:  python backend/server.py      then open http://localhost:8000
Endpoints:
  GET  /api/health              -> {"status": "ok", "ai_configured": bool}
  POST /api/study-gap-report    -> structured study gap report (see analyzer.py)
  POST /api/chat                -> Ask AI Agent reply, optional PDF/image (see chat.py)
  POST /api/chat/stream         -> same agent, but streamed live as NDJSON events (one JSON object per line)
  /api/auth/*, /api/conversations/*, /api/attempts/*, /api/reviews/*, /api/contact, /api/admin/*   -> see routes.py
The two legacy chat routes now need a logged-in session (cookie); the Study Gap report route is unchanged and public.
Also serves ../frontend so one command runs the whole app without CORS issues.
Secrets are read from environment variables or backend/.env. They are never sent to the browser.
"""
import json
import os
import socket
import time
from http.cookies import SimpleCookie
from collections import defaultdict, deque
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent / "frontend"


def load_env_file():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file()
import analyzer  # noqa: E402  (after .env is loaded)
import auth  # noqa: E402
import chat  # noqa: E402
import db  # noqa: E402
import routes  # noqa: E402
import importlib.util

PORT = int(os.environ.get("PORT", "8000"))
# route -> (handler, max request bytes, requests per minute per client)
ROUTES = {
    "/api/study-gap-report": (analyzer.analyze, 64 * 1024, int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))),
    "/api/chat": (chat.chat, 15 * 1024 * 1024, int(os.environ.get("CHAT_RATE_LIMIT_PER_MIN", "20"))),  # files arrive base64-encoded
}
# Streaming routes: same limits as /api/chat and they share its rate-limit bucket. Handler returns an event generator.
STREAM_ROUTES = {
    "/api/chat/stream": (chat.chat_stream, 15 * 1024 * 1024, int(os.environ.get("CHAT_RATE_LIMIT_PER_MIN", "20")), "/api/chat"),
}
ALLOWED_ORIGINS = {o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()}
MIME = {".woff2": "font/woff2", ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")  # set to 1 when served over HTTPS
# Set TRUST_PROXY=1 ONLY when a reverse proxy on this same machine forwards requests and sets X-Real-IP itself.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "").strip().lower() in ("1", "true", "yes")
LEGACY_CHAT_ROUTES = {"/api/chat", "/api/chat/stream"}  # these need a logged-in user
hits = defaultdict(deque)


def real_client_ip(peer, x_real_ip, trust_proxy=None):
    """The address used for rate limits. Behind a local reverse proxy every request comes from 127.0.0.1,
    so (only when TRUST_PROXY is on and the request really came from this machine) use the proxy's X-Real-IP."""
    trust = TRUST_PROXY if trust_proxy is None else trust_proxy
    if trust and peer in ("127.0.0.1", "::1") and x_real_ip:
        candidate = x_real_ip.strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return peer


class Handler(BaseHTTPRequestHandler):
    def _client_ip(self):
        return real_client_ip(self.client_address[0], self.headers.get("X-Real-IP", ""))

    def log_message(self, fmt, *args):  # request bodies (student data) are never logged
        print(f"{self.address_string()} {self.command} {self.path.split('?')[0]}")

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Credentials", "true")  # the session cookie travels with fetch(..., {credentials:'include'})

    def _cookie_header(self, value, max_age):
        parts = [f"{auth.COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={int(max_age)}"]
        if COOKIE_SECURE:
            parts.append("Secure")
        return "; ".join(parts)

    def _json(self, status, obj, cookie=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie is not None:
            self.send_header("Set-Cookie", self._cookie_header(*cookie))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/") and path != "/api/health":
            return self._dispatch("GET", path)
        if path == "/api/health":
            return self._json(200, {"status": "ok", "ai_configured": analyzer.ai_configured(),
                                    "vision_configured": bool(os.environ.get("LLM_VISION_MODEL", "").strip()),
                                    "pdf_support": importlib.util.find_spec("pypdf") is not None})
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (FRONTEND / rel).resolve()
        if FRONTEND not in target.parents or not target.is_file():
            return self._json(404, {"error": {"code": "not_found", "message": "Not found."}})
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        connect = " ".join(sorted(ALLOWED_ORIGINS))
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
                         f"connect-src 'self' {connect}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _allow(self, bucket, limit):
        now, q = time.time(), hits[(self._client_ip(), bucket)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def _read_json(self, max_body):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_body:
            raise analyzer.AnalyzerError("invalid_request", "Request body is missing or too large.", 413 if length > max_body else 400)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise analyzer.AnalyzerError("invalid_request", "Request body is not valid JSON.")

    def _stream(self, events):
        """Sends each event as one JSON line, flushed immediately (NDJSON). The response has no Content-Length: the
        connection (HTTP/1.0) closes when the stream ends. If the browser goes away (Stop button, closed tab) the next
        write fails; we then close the generator, which closes the provider connection so it stops generating."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")  # tell reverse proxies (nginx) not to buffer the stream
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # send small events right away
        except OSError:
            pass

        def send(obj):
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            try:
                for ev in events:
                    send(ev)
            except analyzer.AnalyzerError as e:
                send({"type": "error", "code": e.code, "message": e.message})
            except OSError:
                raise  # the browser went away
            except Exception:  # never leak internals
                send({"type": "error", "code": "server_error", "message": "Unexpected server error."})
        except OSError:
            pass  # includes BrokenPipeError / ConnectionResetError / ConnectionAbortedError (Windows)
        finally:
            close = getattr(events, "close", None)
            if close:
                close()

    def _dispatch(self, method, path):
        """New routes (routes.py): session check, CSRF check, body parsing, JSON / cookie / stream responses."""
        found = routes.match(method, path)
        if found is None:
            return self._json(404, {"error": {"code": "not_found", "message": "Not found."}})
        if found[0] == "405":
            return self._json(405, {"error": {"code": "method_not_allowed", "message": "Method not allowed."}})
        (_, _, fn, auth_required, max_body), params = found
        try:
            if method != "GET" and not routes.csrf_ok(self.headers, ALLOWED_ORIGINS):
                return self._json(403, {"error": {"code": "csrf", "message": "Request blocked: it did not come from this app."}})
            user, token = routes.current_user(self.headers)
            if auth_required and not user:
                clear = (("", 0) if token else None)  # an invalid or expired cookie is cleared
                return self._json(401, {"error": {"code": "unauthenticated", "message": "Please log in to continue."}}, cookie=clear)
            body = None
            if method != "GET":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length > max_body:
                    return self._json(413, {"error": {"code": "too_large", "message": "Request body is too large."}})
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        return self._json(400, {"error": {"code": "invalid_request", "message": "Request body is not valid JSON."}})
            ctx = routes.Ctx(user, body, params, routes.parse_query(self.path), self._client_ip())
            if path == "/api/auth/logout":
                auth.destroy_session(token)
            result = fn(ctx)
            if isinstance(result, routes.Stream):
                return self._stream(result.events)
            status, obj = result if isinstance(result, tuple) else (200, result)
            self._json(status, obj, cookie=ctx.set_cookie)
        except routes.ApiError as e:
            err = {"code": e.code, "message": e.message}
            if e.fields:
                err["fields"] = e.fields
            self._json(e.status, {"error": err})
        except analyzer.AnalyzerError as e:
            self._json(e.status, {"error": {"code": e.code, "message": e.message}})
        except Exception:  # never leak internals (and never log request bodies)
            self._json(500, {"error": {"code": "server_error", "message": "Unexpected server error."}})

    def do_PUT(self):
        self._dispatch("PUT", self.path.split("?")[0])

    def do_PATCH(self):
        self._dispatch("PATCH", self.path.split("?")[0])

    def do_DELETE(self):
        self._dispatch("DELETE", self.path.split("?")[0])

    def do_POST(self):
        path = self.path.split("?")[0]
        route = ROUTES.get(path)
        stream_route = STREAM_ROUTES.get(path)
        if not route and not stream_route:
            return self._dispatch("POST", path)
        if path in LEGACY_CHAT_ROUTES:
            user, token = routes.current_user(self.headers)
            if not user:
                return self._json(401, {"error": {"code": "unauthenticated", "message": "Please log in to use Ask AI Agent."}}, cookie=(("", 0) if token else None))
            if not routes.csrf_ok(self.headers, ALLOWED_ORIGINS):
                return self._json(403, {"error": {"code": "csrf", "message": "Request blocked: it did not come from this app."}})
        handler, max_body, limit = route if route else stream_route[:3]
        if not self._allow(stream_route[3] if stream_route else path, limit):
            return self._json(429, {"error": {"code": "rate_limited", "message": "Too many requests. Please wait a minute and retry."}})
        try:
            payload = self._read_json(max_body)
            result = handler(payload)  # for stream routes this validates now and returns a generator
            if stream_route:
                return self._stream(result)
            self._json(200, result)
        except analyzer.AnalyzerError as e:
            body = {"error": {"code": e.code, "message": e.message}}
            if e.resources is not None:
                body["resources"] = e.resources  # resources are still useful when the AI step fails
            self._json(e.status, body)
        except Exception:  # never leak internals
            self._json(500, {"error": {"code": "server_error", "message": "Unexpected server error."}})


db.init_db()

if __name__ == "__main__":
    mode = "REAL AI (key found in environment)" if analyzer.ai_configured() else "DEMO mode (no LLM_API_KEY set, reports are rule-based)"
    print(f"Study Gap backend on http://localhost:{PORT}  -  {mode}")
    print(f"Database: {db.db_path()}  |  admins: {len(auth.admin_emails())} configured  |  secure cookies: {COOKIE_SECURE}  |  trust proxy: {TRUST_PROXY}")
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()