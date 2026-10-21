"""Accounts, password hashing and sessions (standard library only).

* Passwords: scrypt (stdlib hashlib) with a random 16-byte salt; PBKDF2-SHA256 is the fallback if scrypt is missing.
  Format "algo$params$salt$hash". Plaintext passwords are never stored or logged.
* Sessions: a random 256-bit token goes in an HttpOnly cookie. Only its SHA-256 is stored, so a leaked database
  cannot be used to log in. Sessions expire after SESSION_DAYS and are deleted on logout.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import db

COOKIE = "sg_session"
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "7"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD, MAX_PASSWORD = 8, 128


class ApiError(Exception):
    def __init__(self, code, message, status=400, fields=None):
        super().__init__(message)
        self.code, self.message, self.status, self.fields = code, message, status, fields


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- passwords
def _b64(b):
    return base64.b64encode(b).decode("ascii")


def hash_password(password):
    salt = os.urandom(16)
    if hasattr(hashlib, "scrypt"):
        n, r, p = 2 ** 14, 8, 1
        h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)
        return f"scrypt${n}:{r}:{p}${_b64(salt)}${_b64(h)}"
    iters = 600_000
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, 32)
    return f"pbkdf2${iters}${_b64(salt)}${_b64(h)}"


def verify_password(password, stored):
    try:
        algo, params, salt, expected = stored.split("$")
        salt, expected = base64.b64decode(salt), base64.b64decode(expected)
        if algo == "scrypt":
            n, r, p = (int(x) for x in params.split(":"))
            got = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=64 * 1024 * 1024)
        elif algo == "pbkdf2":
            got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(params), len(expected))
        else:
            return False
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = None


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("not-a-real-password")
    return _DUMMY_HASH


# ---------------------------------------------------------------- validation
def clean_name(v):
    if not isinstance(v, str) or not 1 <= len(v.strip()) <= 80:
        raise ApiError("validation_error", "Enter your name (up to 80 characters).", fields={"name": "Enter your name (up to 80 characters)."})
    return " ".join(v.split())


def clean_email(v):
    v = v.strip().lower() if isinstance(v, str) else ""
    if len(v) > 254 or not EMAIL_RE.match(v):
        raise ApiError("validation_error", "Enter a valid email address.", fields={"email": "Enter a valid email address."})
    return v


def check_password_strength(pw):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(pw) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Include at least one letter and one number."
    return None


# ---------------------------------------------------------------- accounts
def signup(body):
    fields = {}
    try:
        name = clean_name(body.get("name"))
    except ApiError as e:
        fields.update(e.fields); name = None
    try:
        email = clean_email(body.get("email"))
    except ApiError as e:
        fields.update(e.fields); email = None
    pw, pw2 = body.get("password"), body.get("password_confirm")
    problem = check_password_strength(pw)
    if problem:
        fields["password"] = problem
    elif pw != pw2:
        fields["password_confirm"] = "Passwords do not match."
    if fields:
        raise ApiError("validation_error", "Check the highlighted fields.", 400, fields)
    with db.connect() as c:
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ApiError("email_taken", "An account with this email may already exist. Try logging in instead.", 409, {"email": "This email can't be used for a new account."})
        cur = c.execute("INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)", (name, email, hash_password(pw), iso(now())))
        return cur.lastrowid


def login(body):
    email = body.get("email")
    pw = body.get("password")
    email = email.strip().lower() if isinstance(email, str) else ""
    if not email or not isinstance(pw, str) or not pw or len(pw) > 1024:
        raise ApiError("invalid_credentials", "Enter your email and password.", 400)
    with db.connect() as c:
        row = c.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    ok = verify_password(pw, row["password_hash"] if row else _dummy_hash())  # same work whether or not the email exists
    if not row or not ok:
        raise ApiError("invalid_credentials", "Incorrect email or password.", 401)
    return row["id"]


def admin_emails():
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def public_user(row):
    return {"id": row["id"], "name": row["name"], "email": row["email"], "is_admin": row["email"].lower() in admin_emails()}


# ---------------------------------------------------------------- sessions
def _hash_token(t):
    return hashlib.sha256(t.encode("ascii")).hexdigest()


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    with db.connect() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (iso(now()),))  # tidy up expired sessions
        c.execute("INSERT INTO sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (_hash_token(token), user_id, iso(now()), iso(now() + timedelta(days=SESSION_DAYS))))
    return token


def user_for_token(token):
    """Returns the user row for a valid, unexpired session token, otherwise None."""
    if not token or len(token) > 200 or not token.isascii():
        return None
    with db.connect() as c:
        row = c.execute("SELECT u.id, u.name, u.email, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
                        (_hash_token(token),)).fetchone()
        if not row:
            return None
        if row["expires_at"] < iso(now()):
            c.execute("DELETE FROM sessions WHERE id = ?", (_hash_token(token),))
            return None
        return row


def destroy_session(token):
    if token:
        with db.connect() as c:
            c.execute("DELETE FROM sessions WHERE id = ?", (_hash_token(token),))
