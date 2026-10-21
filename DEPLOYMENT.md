# Putting the app online (HTTPS)

Written for a beginner. Read it once fully before you start. You need: a small Linux server (a VPS), a domain name pointing at it
(an "A record" to the server's IP address), and SSH access. Below, `studygap.example.com` is a placeholder: use your own domain.

## How the pieces fit
```
Browser --HTTPS--> Caddy (reverse proxy, gets the HTTPS certificate)  --http--> python backend/server.py (127.0.0.1:8000)
```
* The Python server only listens on `127.0.0.1`, so nobody can reach it directly. Only the proxy can. That is intended; do not change it.
* The same server also serves the website files, so you deploy one thing and the browser uses one address (no CORS trouble).
* A *reverse proxy* is a program in front of your app that handles HTTPS. Caddy is the easiest: it gets and renews the certificate by itself.

## 1. Settings in `backend/.env` on the server
Create the file on the server (never upload your laptop's file by email or chat, never commit it):

| Setting | Value | Why |
|---|---|---|
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | your own values | unchanged |
| `COOKIE_SECURE` | `1` | marks the login cookie "Secure": the browser sends it only over HTTPS |
| `ALLOWED_ORIGINS` | `https://studygap.example.com` | the exact address people type: `https://`, no slash at the end, no path. Remove the localhost entries on the live server. It is used for the CSRF check (blocks forged requests) and CORS |
| `ADMIN_EMAILS` | `you@example.com` | the account(s) allowed to approve reviews and read contact messages. **You must also sign up with that exact email**, then the Admin link appears |
| `TRUST_PROXY` | `1` | tells the app to use the visitor's real address from the proxy for rate limits. **Without this, all visitors look identical (127.0.0.1) and share one limit: a few login attempts by one person would block everyone.** Only set it when the proxy below really is in front |
| `PORT` | `8000` | leave as is; the proxy points to it |

If `ALLOWED_ORIGINS` is wrong you will see "Request blocked: it did not come from this app" when logging in or chatting.

## 2. Run the app all the time (systemd)
Create `/etc/systemd/system/studygap.service` (change the user and folder to yours):
```
[Unit]
Description=AI Study Gap Detector
After=network.target

[Service]
User=studygap
WorkingDirectory=/home/studygap/ai-study-agent
ExecStart=/usr/bin/python3 backend/server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
Then: `sudo systemctl daemon-reload && sudo systemctl enable --now studygap` and look at the log with `journalctl -u studygap -f`.
(`enable --now` = start now and start again after a reboot. `Restart=on-failure` = start again if it crashes.)
For PDF reading: `pip install pypdf` for the same Python (`/usr/bin/python3 -m pip install pypdf`).

## 3. The reverse proxy: Caddy (recommended)
Install Caddy (see caddyserver.com/docs/install), then put this in `/etc/caddy/Caddyfile`:
```
studygap.example.com {
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Real-IP {remote_host}
        flush_interval -1
    }
}
```
* `header_up X-Real-IP {remote_host}` sends the visitor's real address (this is what `TRUST_PROXY=1` reads; Caddy **overwrites** any value a visitor tries to send).
* `flush_interval -1` sends each streamed chat piece immediately instead of buffering it. Without it the chat text can arrive in one lump at the end.
* Then `sudo systemctl reload caddy`. Open ports 80 and 443 in the server's firewall (80 is needed to get the certificate).

**If you use nginx instead:** inside `location / { ... }` use `proxy_pass http://127.0.0.1:8000; proxy_set_header X-Real-IP $remote_addr;
proxy_buffering off; proxy_read_timeout 120s; client_max_body_size 15m;`. The last one matters: nginx's default 1 MB limit would
reject PDF/image attachments (the app allows 10 MB files).

## 4. Backups
All data (accounts, chats, results, reviews, contact messages) is one file: `instance/studygap.db`. If it is lost, it is gone.
`python backend/backup_db.py` makes a safe snapshot **while the server runs** into `backups/` and keeps the newest 14.
Run it every night with cron (`crontab -e`):
```
15 3 * * * cd /home/studygap/ai-study-agent && /usr/bin/python3 backend/backup_db.py
```
Backups on the same disk do not protect you from losing the server. Regularly copy the `backups/` folder somewhere else too
(your own computer with `scp`, or a storage service). Backups contain password hashes and students' data: keep them private.
**Test a restore once:** stop the app, copy a backup over `instance/studygap.db`, start the app, log in.

## 5. After going live: quick checks
1. `https://your-domain/api/health` shows `"status":"ok"` and `ai_configured: true` (it also shows `vision_configured` and `pdf_support`).
2. Sign up, log out, log in. In the browser's developer tools (F12, Application, Cookies) the session cookie shows **Secure** and **HttpOnly**.
3. Open the chat and send a message: text should appear piece by piece, not all at once.
4. Attach a small PDF (and an image, if `LLM_VISION_MODEL` is set).
5. Sign up with your `ADMIN_EMAILS` address and check the Admin link appears.
6. `http://your-domain` (without s) should jump to `https://`. Caddy does this by default.

## What this guide does NOT do
Password reset by email (needs SMTP, not set up), server hardening (firewall, SSH keys, updates), uptime monitoring, and a
privacy policy. Questions and chat messages go to your AI provider, so tell your users that. If you reach many users, plan for
more than one server process: this is a small standard-library server, fine for a project and early users, not for heavy traffic.
