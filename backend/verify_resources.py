"""Checks every URL in data/resources.json and stamps verified_on with today's date when the link responds.
Run on a machine with internet access:  python backend/verify_resources.py
This only proves the link opens. Still check that the content suits the topic before trusting it."""
import json
import urllib.request
from datetime import date
from pathlib import Path

path = Path(__file__).resolve().parent.parent / "data" / "resources.json"
db = json.loads(path.read_text(encoding="utf-8"))
for r in db["resources"]:
    try:
        req = urllib.request.Request(r["url"], headers={"User-Agent": "Mozilla/5.0 (link check)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = 200 <= resp.status < 400
    except Exception as e:
        ok = False
        print(f"FAILED  {r['url']}  ({e})")
    r["verified_on"] = date.today().isoformat() if ok else None
    if ok:
        print(f"ok      {r['url']}")
path.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")
