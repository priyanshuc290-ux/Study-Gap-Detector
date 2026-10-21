"""Real-browser checks (optional). Needs:  pip install playwright  and a Chromium for it.
Run from the project root:   python browser_tests/browser_check.py
Starts the REAL backend on port 8000 with a throw-away database, pointed at e2e/mock_provider.py (a fake AI that accepts only
the key "test-key"). No real AI and no real key are used. Screenshots go to browser_tests/screens/.
Set CHROMIUM_PATH to use a specific browser binary."""
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
APP = "http://localhost:8000/"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok or detail == "" else "  -> " + str(detail)), flush=True)


def until(page, js, timeout=15, what="condition"):
    """Polls a JS expression with page.evaluate. (Playwright's wait_for_function needs unsafe-eval, which the app's CSP forbids.)"""
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate("() => !!(" + js + ")"):
            return
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for " + what + ": " + js[:80])


def wait_up(url):
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server did not start: " + url)


def main():
    tmp = tempfile.TemporaryDirectory()
    py = sys.executable
    mock = subprocess.Popen([py, str(ROOT / "e2e" / "mock_provider.py")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = {**os.environ, "LLM_API_KEY": "test-key", "LLM_BASE_URL": "http://127.0.0.1:9911/v1", "LLM_MODEL": "mock-model", "PORT": "8000",
           "DATABASE_PATH": os.path.join(tmp.name, "b.db"), "ADMIN_EMAILS": "admin@example.com", "CHAT_RATE_LIMIT_PER_MIN": "1000"}
    server = subprocess.Popen([py, str(ROOT / "backend" / "server.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        wait_up(APP + "api/health")
        run()
    finally:
        server.terminate(); mock.terminate()
        try:
            log = server.communicate(timeout=5)[0]
            check("server log never contains a password, the API key or message text", "Passw0rd" not in log and "test-key" not in log and "SCENARIO" not in log)
        except Exception:
            pass
    # second run: NO AI key (demo mode), to prove Mode 1 reports and the chat error path
    env2 = {**env, "LLM_API_KEY": "", "DATABASE_PATH": os.path.join(tmp.name, "demo.db")}
    server2 = subprocess.Popen([py, str(ROOT / "backend" / "server.py")], env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        wait_up(APP + "api/health")
        demo_phase()
    finally:
        server2.terminate()
        tmp.cleanup()
    bad = results.count(False)
    print(f"\n{len(results) - bad}/{len(results)} checks passed")
    sys.exit(1 if bad else 0)


def signup(page, name, email, pw="Passw0rd1"):
    page.goto(APP + "#/signup")
    page.fill("#sName", name); page.fill("#sEmail", email); page.fill("#sPassword", pw); page.fill("#sConfirm", pw)
    page.click("#signupBtn")


def demo_phase():
    print("\n== Demo mode (no AI key) ==")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
        signup(page, "Demo User", "demo@example.com")
        page.wait_for_url("**#/dashboard")
        page.goto(APP + "#/study-gap"); page.click("#gapContinue"); page.click("#quizForm button[type=submit]")
        total = int(page.inner_text("#quizCount").split(" of ")[1])
        for i in range(total):
            page.locator("#quizOptions label").first.click()
            if i < total - 1:
                page.click("#nextBtn")
        page.click("#submitBtn")
        page.wait_for_selector("#view-results:visible")
        until(page, "document.querySelector('#reportBox').children.length > 0", timeout=15, what="demo report")
        check("demo mode: the report is honestly labelled Demo", "Demo" in page.inner_text("#reportMode"))
        stored = False
        for _ in range(20):  # the page saves the report right after showing it; wait up to 10 s instead of a fixed guess
            stored = page.evaluate("fetch('/api/attempts',{credentials:'include'}).then(r=>r.json()).then(d=>!!d.attempts[0].report)")
            if stored:
                break
            page.wait_for_timeout(500)
        check("the report is stored with the attempt on the server", stored)
        page.goto(APP + "#/chat"); page.wait_for_selector("#chatInput:visible")
        page.fill("#chatInput", "hello"); page.keyboard.press("Enter")
        page.wait_for_selector(".msg.error")
        check("chat without a key: honest error with Retry, controls restored", "AI key" in page.inner_text(".msg.error") and page.locator(".msg.error .retry").count() == 1 and page.locator("#sendBtn").is_visible())
        page.reload(); page.wait_for_selector("#chatInput:visible")
        check("a chat whose first message was rejected leaves no empty entry in the sidebar", page.locator(".conv-item").count() == 0)
        # guest (no login) Mode 1 still works and uses browser storage
        g = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
        g.goto(APP + "#/study-gap"); g.wait_for_selector("#view-gap-setup:visible")
        check("guest sees the sign-in suggestion on Study Gap", g.locator("#guestNotice:visible").count() == 1)
        g.click("#gapContinue"); g.click("#quizForm button[type=submit]")
        for i in range(int(g.inner_text("#quizCount").split(" of ")[1])):
            g.locator("#quizOptions label").first.click()
            if g.locator("#nextBtn:visible").count():
                g.click("#nextBtn")
        g.click("#submitBtn")
        g.wait_for_selector("#view-results:visible")
        check("guest quiz works and is kept in this browser only", g.evaluate("JSON.parse(localStorage.getItem('asga.attempts.v1')).length") == 1)
        browser.close()


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        errors, api_log = [], []
        page.on("response", lambda r: api_log.append((r.request.method, r.url.split("8000")[-1], r.status)) if "/api/attempts/" in r.url else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not any(c in m.text for c in ("401", "404", "403", "409", "502")) else None)

        # ---- 1. public pages and protected routes
        page.goto(APP)
        check("home renders with Get started, Check study gap and Ask AI", page.locator("#view-home:visible").count() == 1 and page.locator(".cta-row a").count() == 3)
        page.screenshot(path=str(SHOTS / "home.png"), full_page=True)
        for route, title in (("about", "About"), ("contact", "Contact"), ("reviews", "Ratings"), ("login", "Log in"), ("signup", "Create")):
            page.goto(APP + "#/" + route)
            check(f"#/{route} renders", page.locator("main h1:visible").first.inner_text().startswith(title))
        page.goto(APP + "#/reviews"); page.wait_for_selector("#reviewGuest:visible")
        check("reviews page: no reviews and no rating are invented", "No reviews yet" in page.inner_text("#reviewSummary") and "Nothing here yet" in page.inner_text("#reviewList2"))
        for route in ("chat", "dashboard", "admin"):
            page.goto(APP + "#/" + route)
            page.wait_for_url("**#/login?next=*")
            check(f"guest opening #/{route} is sent to login", f"next=%2F{route}" in page.url)

        # ---- 2. sign up validation and success
        page.goto(APP + "#/signup")
        page.click("#signupBtn")
        check("empty sign-up shows field errors", page.locator("#sName-err:visible").count() == 1 and page.locator("#sEmail-err:visible").count() == 1 and "signup" in page.url)
        page.fill("#sName", "Asha Kumar"); page.fill("#sEmail", "asha@example.com"); page.fill("#sPassword", "short"); page.fill("#sConfirm", "other")
        page.click("#signupBtn")
        check("weak password explained", "8 characters" in page.inner_text("#sPassword-err"))
        page.fill("#sPassword", "Passw0rd1"); page.fill("#sConfirm", "Passw0rd2"); page.click("#signupBtn")
        check("password mismatch explained", "do not match" in page.inner_text("#sConfirm-err"))
        signup(page, "Asha Kumar", "asha@example.com")
        page.wait_for_url("**#/dashboard")
        check("sign up logs in and greets by name", "Welcome back, Asha" in page.inner_text("#dashGreeting"))
        until(page, "document.querySelector('#convBox').textContent.includes('No conversations yet')", what="conversation panel")
        check("new account shows honest empty states", "No quiz results yet" in page.inner_text("#latestBox") and "No conversations yet" in page.inner_text("#convBox") and "No quizzes yet" in page.inner_text("#recentBox"))
        page.screenshot(path=str(SHOTS / "dashboard-empty.png"), full_page=True)
        ck = {c["name"]: c for c in ctx.cookies()}["sg_session"]
        check("session cookie is HttpOnly + SameSite=Lax", ck["httpOnly"] and ck["sameSite"] == "Lax")
        check("JavaScript cannot read the session cookie", page.evaluate("document.cookie") == "")
        page.reload(); page.wait_for_selector("#dashGreeting:visible")
        check("login survives reload", "Welcome back, Asha" in page.inner_text("#dashGreeting"))
        page.goto(APP + "#/login"); page.wait_for_url("**#/dashboard")
        check("logged-in users skip the login page", True)

        # ---- 3. Study Gap Checker (Mode 1), saved to the account
        page.click("a.quick-card[href='#/study-gap']")
        page.wait_for_selector("#view-gap-setup:visible")
        check("study gap setup: sample bank available", page.locator("#gapContinue").is_enabled())
        page.screenshot(path=str(SHOTS / "gap-setup.png"))
        page.click("#gapContinue")
        page.click("#quizForm button[type=submit]")
        page.wait_for_selector("#view-quiz:visible")
        page.screenshot(path=str(SHOTS / "quiz.png"))
        total = int(page.inner_text("#quizCount").split(" of ")[1])
        for i in range(total):
            page.locator("#quizOptions label").first.click()
            if i < total - 1:
                page.click("#nextBtn")
        page.click("#submitBtn")
        page.wait_for_selector("#view-results:visible")
        check("results: score, topic bars, lists", "/" in page.inner_text("#resScore") and page.locator("#topicBars .bar-row").count() > 0)
        until(page, "document.querySelector('#reportBox').children.length > 0 || document.querySelector('#reportStatus .report-error')", timeout=20)
        check("report resolved (Demo/AI report, or an honest error with Retry)", page.inner_text("#reportBox") != "" or page.locator("#reportStatus .report-error").count() == 1)
        page.screenshot(path=str(SHOTS / "results.png"), full_page=True)
        page.wait_for_timeout(500)
        n = page.evaluate("fetch('/api/attempts',{credentials:'include'}).then(r=>r.json()).then(d=>d.attempts.length)")
        check("the attempt is stored on the server for this account", n == 1, n)
        has_report = page.evaluate("fetch('/api/attempts',{credentials:'include'}).then(r=>r.json()).then(d=>!!d.attempts[0].report)")
        if page.locator("#reportBox").inner_text() != "":  # here the fake AI cannot write a valid report, so usually only the error path runs
            check("the generated report is stored with the attempt", has_report, api_log)
        page.goto(APP + "#/dashboard"); page.wait_for_selector("#latestBox .mini-stats")
        check("dashboard shows the real latest result and recent quiz", "Data Structures" in page.inner_text("#latestBox") and page.locator("#recentBox tbody tr").count() == 1)
        check("progress by unit built from the saved attempt", page.locator("#progressBox .unit-card").count() == 1)
        page.screenshot(path=str(SHOTS / "dashboard.png"), full_page=True)
        weak = page.locator("#latestBox button:has-text('Practise weak topics')").count()
        if weak:
            page.click("#latestBox button:has-text('Practise weak topics')")
            page.wait_for_selector("#view-quiz:visible")
            check("practise weak topics starts a weak-topic quiz", "Weak" in page.inner_text("#quizType"))
            page.goto(APP + "#/dashboard")

        # ---- 4. full-screen chat with saved conversations
        page.goto(APP + "#/chat")
        page.wait_for_selector("#chatInput:visible")
        box = page.locator(".chat-shell").bounding_box()
        vp = page.viewport_size
        check("chat fills the viewport; site header and footer are hidden", box["width"] >= vp["width"] - 1 and box["height"] >= vp["height"] - 1 and not page.locator("#siteHeader").is_visible())
        check("sidebar: New chat, search, empty state", page.locator("#newChatBtn").is_visible() and page.locator("#convSearch").is_visible() and "No chats yet" in page.inner_text("#convList"))
        page.screenshot(path=str(SHOTS / "chat-welcome.png"))
        page.fill("#chatInput", "SCENARIO:plain what is a stack?")
        page.keyboard.press("Enter")
        page.wait_for_selector(".msg.bot .md:has-text('last-in')", timeout=15000)
        until(page, "!document.querySelector('.msg.streaming')")
        check("Enter sends; the answer comes from the backend agent", page.locator(".msg.user").count() == 1 and page.locator(".msg.bot").count() == 1)
        check("conversation is listed and its id is in the URL", page.locator(".conv-item").count() == 1 and "#/chat/" in page.url)
        page.fill("#chatInput", "line one"); page.keyboard.press("Shift+Enter"); page.keyboard.type("line two")
        check("Shift+Enter makes a new line and does not send", page.input_value("#chatInput") == "line one\nline two" and page.locator(".msg.user").count() == 1)
        page.fill("#chatInput", "")
        page.screenshot(path=str(SHOTS / "chat.png"))
        conv_url = page.url
        page.reload(); page.wait_for_selector(".msg.user")
        check("after reload both messages return in order", page.locator("#chatLog .msg").count() == 2 and "msg user" in page.locator("#chatLog .msg").nth(0).get_attribute("class"))
        check("title was taken from the first message", page.inner_text("#chatTitle").startswith("SCENARIO:plain what is a stack"))
        check("answer has a Copy action", page.locator(".msg.bot .tool-btn").count() == 1)
        page.click("#newChatBtn")
        page.wait_for_selector("#chatEmpty:visible")
        check("New chat shows the welcome state", page.locator("#chatLog .msg").count() == 0 and page.inner_text("#chatTitle") == "New chat")
        page.fill("#chatInput", "SCENARIO:plain explain queues"); page.click("#sendBtn")
        until(page, "document.querySelectorAll('.msg.bot').length === 1 && !document.querySelector('.msg.streaming')", timeout=15)
        check("two conversations, newest first", page.locator(".conv-item").count() == 2 and "explain queues" in page.locator(".conv-link").first.inner_text())
        page.fill("#convSearch", "queues")
        until(page, "document.querySelectorAll('.conv-item').length === 1")
        check("search filters the list", "queues" in page.locator(".conv-link").first.inner_text())
        page.fill("#convSearch", "zzzz"); page.wait_for_selector("text=No chats match")
        page.fill("#convSearch", ""); until(page, "document.querySelectorAll('.conv-item').length === 2")
        page.locator(".conv-link", has_text="what is a stack").click()
        page.wait_for_selector(".msg.user:has-text('what is a stack')")
        check("clicking an old conversation reopens its messages", page.locator("#chatLog .msg").count() == 2 and "#/chat/" in page.url)
        page.locator(".conv-item.active").hover()
        page.locator(".conv-item.active button[aria-label^='Rename']").click()
        page.fill(".conv-rename", "Stacks revision"); page.keyboard.press("Enter")
        page.wait_for_selector(".conv-link:has-text('Stacks revision')")
        check("rename works and the header follows", page.inner_text("#chatTitle") == "Stacks revision")
        page.reload(); page.wait_for_selector("#chatTitle:has-text('Stacks revision')")
        check("renamed title survives reload", True)
        page.locator(".conv-item.active").hover()
        page.locator(".conv-item.active button[aria-label^='Delete']").click()
        page.wait_for_selector(".modal")
        check("delete asks for confirmation", "cannot be undone" in page.inner_text(".modal"))
        page.click(".modal button:has-text('Cancel')")
        check("Cancel keeps the conversation", page.locator(".conv-item").count() == 2 and page.locator(".modal").count() == 0)
        page.locator(".conv-item.active").hover()
        page.locator(".conv-item.active button[aria-label^='Delete']").click()
        page.click(".modal button:has-text('Delete chat')")
        until(page, "document.querySelectorAll('.conv-item').length === 1")
        check("confirmed delete removes it and opens a new chat", page.url.endswith("#/chat") and page.locator("#chatEmpty:visible").count() == 1)
        gone = page.evaluate("fetch('/api/conversations/%s',{credentials:'include'}).then(r=>r.status)" % conv_url.split("/")[-1])
        check("the deleted conversation is gone on the server", gone == 404, gone)

        page.fill("#chatInput", "SCENARIO:slow talk for a while"); page.click("#sendBtn")
        until(page, "document.querySelector('.msg.streaming .md') && document.querySelector('.msg.streaming .md').textContent.length > 15", timeout=10)
        check("Stop replaces Send while streaming", page.locator("#stopBtn").is_visible() and not page.locator("#sendBtn").is_visible())
        page.click("#stopBtn"); page.wait_for_selector("#sendBtn:visible")
        check("Stop keeps the partial answer, labelled incomplete", page.locator(".msg.partial").count() == 1)
        time.sleep(1.2)
        page.reload(); page.wait_for_selector(".msg.partial")
        check("the partial answer was saved and is still labelled after reload", "incomplete" in page.locator(".msg.partial small").inner_text())

        page.click("#newChatBtn")
        page.fill("#chatInput", "SCENARIO:tools What should I study next?"); page.click("#sendBtn")
        page.wait_for_selector(".msg.bot .agent-actions button", timeout=20000)
        until(page, "!document.querySelector('.msg.streaming')")
        check("agent steps and the prepared action button are shown", page.locator(".msg.bot .step.done").count() == 2)
        check("agent used the account's REAL saved results", "Checked your quiz results" in page.inner_text(".msg.bot .steps"))
        page.screenshot(path=str(SHOTS / "chat-answer.png"))
        page.reload(); page.wait_for_selector(".msg.bot .agent-actions button")
        check("steps and button return when the conversation is reopened", page.locator(".msg.bot .step.done").count() == 2)
        page.click(".msg.bot .agent-actions button")
        page.wait_for_selector("#view-quiz:visible")
        check("only the click starts weak-topic practice", "Weak" in page.inner_text("#quizType"))

        # ---- 5. mobile
        mctx = browser.new_context(viewport={"width": 390, "height": 780}, storage_state=ctx.storage_state())
        m = mctx.new_page()
        m.goto(APP + "#/chat"); m.wait_for_selector("#chatInput:visible")
        check("mobile chat: sidebar hidden, menu button shown", m.locator("#chatSide").bounding_box()["x"] < 0 and m.locator("#sideOpen").is_visible())
        m.screenshot(path=str(SHOTS / "chat-mobile.png"))
        m.click("#sideOpen"); m.wait_for_timeout(350)
        check("mobile chat: menu opens the conversation drawer", m.locator("#chatSide").bounding_box()["x"] >= 0 and m.locator(".conv-item").count() >= 1)
        m.screenshot(path=str(SHOTS / "chat-mobile-drawer.png"))
        m.keyboard.press("Escape"); m.wait_for_timeout(350)
        check("mobile chat: Escape closes the drawer", m.locator("#chatSide").bounding_box()["x"] < 0)
        check("mobile chat: no horizontal scroll", m.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        for route in ("", "dashboard", "study-gap", "about", "contact", "reviews"):
            m.goto(APP + "#/" + route); m.wait_for_timeout(500)
            check(f"mobile #/{route or 'home'}: no horizontal scroll", m.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), m.evaluate("document.documentElement.scrollWidth"))
        m.goto(APP + "#/"); m.wait_for_selector("#view-home:visible")
        m.screenshot(path=str(SHOTS / "home-mobile.png"), full_page=True)
        m.click("#navToggle")
        check("mobile: hamburger opens the navigation", m.locator("#siteNav").is_visible())
        m.screenshot(path=str(SHOTS / "home-mobile-nav.png"))
        mctx.close()
        tctx = browser.new_context(viewport={"width": 820, "height": 1000}, storage_state=ctx.storage_state())
        t = tctx.new_page()
        for route in ("", "dashboard", "chat", "reviews"):
            t.goto(APP + "#/" + route); t.wait_for_timeout(500)
            check(f"tablet #/{route or 'home'}: no horizontal scroll", t.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        t.goto(APP + "#/dashboard"); t.wait_for_timeout(500); t.screenshot(path=str(SHOTS / "dashboard-tablet.png"))
        tctx.close()

        # ---- 6. isolation
        other = browser.new_context(viewport={"width": 1280, "height": 800})
        op = other.new_page()
        signup(op, "Ravi Shah", "ravi@example.com")
        op.wait_for_url("**#/dashboard")
        op.goto(APP + "#/chat"); op.wait_for_selector("#chatInput:visible")
        check("user B sees none of user A's conversations", op.locator(".conv-item").count() == 0)
        a_ids = page.evaluate("fetch('/api/conversations',{credentials:'include'}).then(r=>r.json()).then(d=>d.conversations.map(c=>c.id))")
        op.goto(APP + "#/chat/" + a_ids[0])
        op.wait_for_selector(".toast.error")
        check("user A's conversation link is refused for user B", "not found" in op.inner_text(".toast.error").lower() and op.url.endswith("#/chat"))
        check("user B has none of user A's quiz attempts", op.evaluate("fetch('/api/attempts',{credentials:'include'}).then(r=>r.json()).then(d=>d.attempts.length)") == 0)

        # ---- 7. logout / login errors
        page.goto(APP + "#/dashboard"); page.wait_for_selector("#dashGreeting:visible")
        page.click("#navAuth button:has-text('Log out')")
        page.wait_for_selector("#view-home:visible")
        check("logout returns home and shows Log in / Sign up", page.locator("#navAuth a:has-text('Log in')").count() == 1)
        page.goto(APP + "#/chat"); page.wait_for_url("**#/login?next=*")
        check("the chat is protected again after logout", True)
        check("API refuses the old session after logout", page.evaluate("fetch('/api/conversations',{credentials:'include'}).then(r=>r.status)") == 401)
        page.fill("#lEmail", "asha@example.com"); page.fill("#lPassword", "WrongPass9"); page.click("#loginBtn")
        page.wait_for_selector("#loginStatus:visible")
        check("wrong password: clear error, stays on login", "Incorrect email or password" in page.inner_text("#loginStatus") and "login" in page.url)
        page.screenshot(path=str(SHOTS / "login-error.png"))
        page.fill("#lPassword", "Passw0rd1"); page.click("#loginBtn")
        page.wait_for_url("**#/chat")
        check("correct login returns to the wanted page (/chat)", True)
        ctx.clear_cookies()
        page.reload(); page.wait_for_url("**#/login?next=*")
        check("a missing or invalid session sends the user to login", True)

        # ---- 8. contact
        page.goto(APP + "#/contact")
        page.click("#contactBtn")
        check("contact validates before sending", page.locator("#cName-err:visible").count() == 1 and page.locator("#cMessage-err:visible").count() == 1 and page.locator("#contactStatus:visible").count() == 0)
        page.fill("#cName", "Meera"); page.fill("#cEmail", "meera@example.com"); page.fill("#cSubject", "Question"); page.fill("#cMessage", "Can I use this for Unit 3 too?")
        page.click("#contactBtn")
        page.wait_for_selector("#contactStatus.success")
        check("contact success is shown only after the server stored the message", "saved" in page.inner_text("#contactStatus"))
        page.screenshot(path=str(SHOTS / "contact.png"))

        # ---- 9. reviews + admin
        op.goto(APP + "#/reviews"); op.wait_for_selector("#reviewForm:visible")
        op.click("#reviewBtn")
        check("review needs a rating", op.locator("#rRating-err:visible").count() == 1)
        op.locator("label[for=star4]").click()
        op.fill("#rText", "Clear explanations and a useful plan.")
        op.click("#reviewBtn")
        op.wait_for_selector("#reviewStatus:has-text('waiting for approval')")
        check("a submitted review is pending, not public", "Nothing here yet" in op.inner_text("#reviewList2"))
        asig = browser.new_context(viewport={"width": 1280, "height": 800})
        ap = asig.new_page()
        signup(ap, "Site Admin", "admin@example.com")
        ap.wait_for_url("**#/dashboard")
        check("admin account sees an Admin link", ap.locator("#navAuth a:has-text('Admin')").count() == 1)
        ap.goto(APP + "#/admin"); ap.wait_for_selector("#adminReviews .review")
        check("admin sees the pending review and the contact message", "Clear explanations" in ap.inner_text("#adminReviews") and "Unit 3" in ap.inner_text("#adminContact"))
        ap.screenshot(path=str(SHOTS / "admin.png"), full_page=True)
        ap.click("#adminReviews button:has-text('Approve')")
        ap.wait_for_selector("#adminReviews :text('No reviews in this view')")
        op.reload(); op.wait_for_selector(".review")
        check("approved review is public with first name + last initial only", "Ravi S." in op.inner_text("#reviewList2") and "ravi@example.com" not in op.inner_text("#reviewList2"))
        check("the average comes from the real approved review", "4.0 out of 5, from 1 approved review" in op.inner_text("#reviewSummary"))
        op.screenshot(path=str(SHOTS / "reviews.png"), full_page=True)
        un = other.new_page()
        un.goto(APP + "#/admin"); un.wait_for_selector("#view-dashboard:visible")
        check("a normal user opening #/admin is turned away", True)
        check("no unexpected JavaScript errors on any page", not errors, errors[:3])
        browser.close()


if __name__ == "__main__":
    main()
