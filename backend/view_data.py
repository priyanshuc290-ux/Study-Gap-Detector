"""See what is stored in the database, in the terminal. Nothing to install. It only READS: it cannot change or delete anything.

Run from the project root:
    python backend/view_data.py             # counts of everything + the main tables
    python backend/view_data.py users       # one table: users, reviews, contact, attempts, chats
Password hashes, session tokens and chat message text are never shown."""
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

QUERIES = {  # only safe columns are listed here
    "users": ("SELECT id, name, email, created_at FROM users ORDER BY id", ["id", "name", "email", "created_at"]),
    "reviews": ("SELECT r.id, u.name, r.rating, r.status, r.text FROM reviews r JOIN users u ON u.id = r.user_id ORDER BY r.id",
                ["id", "name", "rating", "status", "text"]),
    "contact": ("SELECT id, name, email, subject, message, created_at FROM contact_messages ORDER BY id",
                ["id", "name", "email", "subject", "message", "created_at"]),
    "attempts": ("SELECT user_id, COUNT(*) AS results, MAX(ts) AS latest FROM attempts GROUP BY user_id ORDER BY user_id",
                 ["user_id", "results", "latest"]),
    "chats": ("SELECT c.id, u.name, c.title, c.updated_at FROM conversations c JOIN users u ON u.id = c.user_id ORDER BY c.updated_at DESC LIMIT 50",
              ["id", "name", "title", "updated_at"]),
}
COUNTS = ["users", "sessions", "conversations", "messages", "attempts", "reviews", "contact_messages"]


def db_file():
    path = os.environ.get("DATABASE_PATH", "").strip()
    env = HERE / ".env"
    if not path and env.exists():  # the server reads backend/.env too, so do the same for this one setting
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_PATH="):
                path = line.split("=", 1)[1].strip().strip('"').strip("'")
    return Path(path) if path else ROOT / "instance" / "studygap.db"


def show(con, name):
    sql, cols = QUERIES[name]
    rows = con.execute(sql).fetchall()
    print(f"\n=== {name} ({len(rows)} rows) ===")
    if not rows:
        print("(empty)")
    for row in rows:
        print("  " + " | ".join(f"{c}: {str(v)[:120]}" for c, v in zip(cols, row)))


def main():
    path = db_file()
    if not path.exists():
        raise SystemExit(f"Database file not found: {path}\nStart the server once (python backend/server.py) and sign up; it creates the file.")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)  # read-only
    print(f"Database: {path}")
    if len(sys.argv) > 1:
        name = sys.argv[1].lower()
        if name not in QUERIES:
            raise SystemExit("Unknown table. Choose one of: " + ", ".join(QUERIES))
        show(con, name)
    else:
        print("\nHow many rows in each table:")
        for t in COUNTS:
            print(f"  {t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
        for name in ("users", "reviews", "contact"):
            show(con, name)
        print("\nOther tables: python backend/view_data.py attempts   (or chats)")
    con.close()


if __name__ == "__main__":
    main()