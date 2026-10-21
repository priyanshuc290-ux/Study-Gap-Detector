"""Take screenshots of the app to compare with design-reference/*.png. Run from the project root:
    python browser_tests/design_shots.py
Saves PNG files in browser_tests/screens/ and prints the size/position of the navbar and hero.
Uses a throw-away database and no AI key. Needs: pip install playwright, then: playwright install chromium"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "screens"; OUT.mkdir(exist_ok=True)
tmp = tempfile.mkdtemp()
env = {**os.environ, "LLM_API_KEY": "", "PORT": "8000", "DATABASE_PATH": os.path.join(tmp, "shots.db")}
srv = subprocess.Popen([sys.executable, str(ROOT / "backend" / "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try: urllib.request.urlopen("http://localhost:8000/api/health", timeout=1); break
    except Exception: time.sleep(.2)
PAGES = ["", "study-gap", "about", "reviews", "contact", "login", "signup", "dashboard"]
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        for label, kw in (("desktop", dict(viewport={"width": 1527, "height": 823})),
                          ("mobile", dict(viewport={"width": 375, "height": 740}, is_mobile=True, has_touch=True)),
                          ("dark", dict(viewport={"width": 1527, "height": 823}))):
            pg = b.new_context(**kw).new_page()
            if label == "dark":  # the app starts light; the saved choice below is what the toggle button stores
                pg.add_init_script("try{localStorage.setItem('sg-theme','dark')}catch(e){}")
            for r in PAGES:
                pg.goto(f"http://localhost:8000/#/{r}"); time.sleep(.7)
                pg.screenshot(path=str(OUT / f"{label}-{r or 'home'}.png"), full_page=True)
            if label == "desktop":
                pg.goto("http://localhost:8000/#/"); time.sleep(.7)
                print(json.dumps(pg.evaluate("""()=>{const r=s=>{const e=document.querySelector(s);const b=e.getBoundingClientRect();return [Math.round(b.left),Math.round(b.top),Math.round(b.width),Math.round(b.height)]};
                  return {navbar_pill:r('.header-in'),hero_band:r('.hero'),hero_h1:r('.hero h1'),primary_button:r('#ctaStart')}}""")))
        b.close()
    print("screenshots saved in", OUT)
finally:
    srv.terminate()
