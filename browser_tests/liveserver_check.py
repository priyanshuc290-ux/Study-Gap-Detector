"""Extra real-browser checks (optional, same requirements as browser_check.py).
Run from the project root:   python browser_tests/liveserver_check.py

1. Live Server case: the FRONTEND is served on port 5500 and the BACKEND on port 8000 (two different ports).
   Proves sign-up, the login cookie and a page reload still work across the two ports.
2. Phone width: every page at 375 px wide must not scroll sideways.
3. Dark mode: screenshots of every page with the browser set to dark mode (browser_tests/screens/dark-*.png) for you to look at.
Uses a throw-away database and no AI key. Nothing here can tell you how it looks on a real phone or in Firefox/Safari."""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).resolve().parent / "screens"
SHOTS.mkdir(exist_ok=True)
FRONT, BACK = "http://localhost:5500/", "http://localhost:8000/"
PAGES = ["#/", "#/about", "#/study-gap", "#/reviews", "#/contact", "#/login", "#/signup", "#/dashboard", "#/chat"]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok or detail == "" else "  -> " + str(detail)), flush=True)


def wait_up(url):
    end = time.time() + 15
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("did not start: " + url)


def settle(page):
    time.sleep(0.6)  # the hash router renders a page right after navigation


def main():
    tmp = tempfile.TemporaryDirectory()
    env = {**os.environ, "LLM_API_KEY": "", "PORT": "8000", "DATABASE_PATH": os.path.join(tmp.name, "live.db")}
    back = subprocess.Popen([sys.executable, str(ROOT / "backend" / "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    front = subprocess.Popen([sys.executable, "-m", "http.server", "5500", "--bind", "127.0.0.1", "--directory", str(ROOT / "frontend")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_up(BACK + "api/health"); wait_up(FRONT)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(FRONT + "#/signup")
            page.fill("#sName", "Live Server"); page.fill("#sEmail", "live@example.com"); page.fill("#sPassword", "Passw0rd1"); page.fill("#sConfirm", "Passw0rd1")
            page.click("#signupBtn")
            page.wait_for_url("**#/dashboard", timeout=10000)
            check("frontend on :5500 talks to the backend on :8000", page.evaluate("SG.API_BASE") == "http://localhost:8000")
            check("sign-up from port 5500 works and lands on the dashboard", page.url.endswith("#/dashboard"))
            page.goto(FRONT + "#/about"); page.reload(); page.goto(FRONT + "#/dashboard"); settle(page)
            check("login cookie survives a reload across ports (dashboard still opens)", page.url.endswith("#/dashboard"), page.url)
            me = page.evaluate("fetch('http://localhost:8000/api/auth/me', {credentials:'include'}).then(r => r.json())")
            check("the backend recognises the 5500 page as logged in", bool(me.get("user")), me)

            # light / dark button
            toggle = page.locator(".site-header [data-theme-toggle]")
            theme = lambda: page.evaluate("document.documentElement.dataset.theme")
            body_bg = lambda: page.evaluate("getComputedStyle(document.body).backgroundColor")
            check("theme: starts in light mode with a white page", theme() == "light" and body_bg() == "rgb(255, 255, 255)", (theme(), body_bg()))
            toggle.click()
            check("theme: the button switches to dark (navy page, pressed state set)", theme() == "dark" and body_bg() == "rgb(15, 22, 38)" and toggle.get_attribute("aria-pressed") == "true", (theme(), body_bg()))
            page.reload(); settle(page)
            check("theme: dark is remembered after a reload", theme() == "dark" and body_bg() == "rgb(15, 22, 38)", (theme(), body_bg()))
            toggle.click()
            check("theme: the button switches back to light", theme() == "light" and body_bg() == "rgb(255, 255, 255)" and toggle.get_attribute("aria-pressed") == "false", (theme(), body_bg()))

            # phone width (emulation only)
            phone = browser.new_context(viewport={"width": 375, "height": 740}, device_scale_factor=2, is_mobile=True, has_touch=True)
            mp = phone.new_page()
            for route in PAGES:
                mp.goto(FRONT + route); settle(mp)
                over = mp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                check(f"phone width 375px: {route} does not scroll sideways", over <= 1, f"{over}px too wide")
            # phone width again, in dark mode, and the button must stay on screen
            md = phone.new_page(); md.add_init_script("try{localStorage.setItem('sg-theme','dark')}catch(e){}")
            for route in ("#/", "#/study-gap", "#/reviews"):
                md.goto(FRONT + route); settle(md)
                box = md.locator(".site-header [data-theme-toggle]").bounding_box()
                over = md.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                check(f"phone dark mode: {route} has no sideways scroll and the theme button is on screen", over <= 1 and box and 0 <= box["x"] and box["x"] + box["width"] <= 375, (over, box))
            # dark mode screenshots (one logged-out context, one logged-in)
            dark = browser.new_context(viewport={"width": 1280, "height": 800})
            dp = dark.new_page(); dp.add_init_script("try{localStorage.setItem('sg-theme','dark')}catch(e){}")
            dp.goto(FRONT + "#/signup")
            dp.fill("#sName", "Dark Mode"); dp.fill("#sEmail", "dark@example.com"); dp.fill("#sPassword", "Passw0rd1"); dp.fill("#sConfirm", "Passw0rd1")
            dp.click("#signupBtn"); dp.wait_for_url("**#/dashboard", timeout=10000)
            for route in PAGES:
                dp.goto(FRONT + route); settle(dp)
                name = "home" if route == "#/" else route.strip("#/").replace("/", "-")
                dp.screenshot(path=str(SHOTS / f"dark-{name}.png"), full_page=True)
            check("dark-mode screenshots saved to browser_tests/screens/ (look at them yourself)", True)
            check("no unexpected JavaScript errors", not errors, errors[:3])
            browser.close()
    finally:
        back.terminate(); front.terminate()
    bad = results.count(False)
    print(f"\n{len(results) - bad}/{len(results)} checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
