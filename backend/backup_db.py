"""Make a safe copy of the SQLite database. Works while the server is running (uses SQLite's own backup feature).

Usage (from the project root):
    python backend/backup_db.py                 # saves to backups/studygap-YYYYmmdd-HHMMSS.db, keeps the newest 14
    python backend/backup_db.py /some/folder 30 # choose the folder and how many copies to keep

The backup contains password hashes and student data: keep the folder private and never commit it."""
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402  (uses the same DATABASE_PATH rule as the server)


def backup(folder, keep=14):
    src_path = db.db_path()
    if not src_path.exists():
        raise SystemExit(f"No database found at {src_path}. Nothing to back up.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"studygap-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src, dst = sqlite3.connect(str(src_path)), sqlite3.connect(str(target))
    try:
        src.backup(dst)  # a consistent snapshot even if someone is using the app right now
    finally:
        dst.close(); src.close()
    try:
        os.chmod(target, 0o600)  # only the owner can read it
    except OSError:
        pass
    old = sorted(folder.glob("studygap-*.db"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    return target, len(old)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "backups"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 14
    path, removed = backup(out, n)
    print(f"Backup written: {path}" + (f"  (removed {removed} older copies)" if removed else ""))
