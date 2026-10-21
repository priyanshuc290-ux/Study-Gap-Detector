# CodeyHub redesign of AI Study Gap Detector

## Short summary (Hinglish)
Poori app ka UI ab CodeyHub ke design system jaisa hai: wahi navbar pill (1224x78 px), wahi colours, wahi font (Plus Jakarta Sans),
wahi cards aur buttons. Backend, database, login, chat, quiz, reports kuch nahi badla. Sirf 3 files badli (`frontend/styles.css`,
`frontend/index.html`, `backend/server.py` mein 1 line) aur font files + reference screenshots add hui.
**Aapki `backend/.env` aur `instance/studygap.db` is zip mein nahi hain.** Apne purane project folder mein inhe rehne dena
(nayi zip ko purane folder ke upar extract karoge to woh safe rahengi). `.env` kabhi kisi ko mat bhejna.

## Update 3: light / dark button
* New **moon / sun button** in the navbar (and in the chat sidebar, next to Log out). Light mode = the white CodeyHub design; dark mode = the earlier dark design.
* The app always **starts in light mode** (it no longer follows the phone/PC setting). The choice is saved in this browser (localStorage key `sg-theme`), so it stays after a reload, with no flash of the wrong colours.
* How it works: `frontend/theme.js` (new, loaded in `<head>`) sets `data-theme="light|dark"` on `<html>`; `styles.css` has one set of tokens for light (`:root`) and one for dark (`:root[data-theme="dark"]`). To change the dark colours, edit that block only.
* The blue band and the white cards on it keep the same strong blue in both modes.
* Files changed in this update: `frontend/theme.js` (new), `frontend/index.html` (script tag + 2 buttons), `frontend/styles.css`, `browser_tests/liveserver_check.py` (8 new checks), `browser_tests/design_shots.py`.
* Tests: unit 110 OK; browser_check 92/92 (2 runs); liveserver_check 22/22 (includes: button switches to dark, dark remembered after reload, switches back, no sideways scroll and button on screen at 375 px in dark mode).
* Not checked: dark mode on every page at every size (I looked at Home and the Study Gap form in dark), and Firefox/Safari.

## Update 2 (after your feedback: "background not white, headings blue, paragraphs black, buttons blue")
* The app now starts in white light mode like CodeyHub, even if your phone/PC is set to dark (see Update 3: dark mode is back as a button). This was the most likely reason you did not see a white background.
* The light-blue hero band is now white too (a thin border line separates it). Cards, chat, notices and empty states are white. The only coloured areas are the blue band and the dark code window, as in your screenshots.
* Paragraph text is now near black (`#374151`, one token: `--muted` in `styles.css`). Headings stay `#111827`.
* Blue accent words in headings: hero ("Fix"), each page title (e.g. "Study Gap **Checker**", "Your **results**"), dashboard name, "study plan". Small labels above titles are blue. Buttons are blue (the white "Check study gap" button is the secondary style from your screenshot).
* **Real bug fixed in `frontend/quiz.js`** (found because a browser check failed about 1 run in 3): the AI/demo report could be saved to the server *before* the attempt itself finished saving, so the server answered "not found" and the report was lost. The report save now waits for the attempt save. `browser_check.py` also now waits up to 10 s instead of a fixed 0.6 s.
* `frontend/pages.js`: the dashboard greeting now puts the first name in blue (set with textContent, never innerHTML).
* Changed files now: `styles.css`, `index.html`, `quiz.js`, `pages.js`, `server.py` (1 line), `browser_tests/browser_check.py`, plus the new items listed below.
* Final test results: unit 110 OK; browser_check 92/92 in 5 consecutive runs; liveserver_check 15/15. The e2e suite is unchanged (12/15, same 3 failures as your original zip).
* The "dark mode" decision (item 1 below) and the dark-mode screenshots in older notes no longer apply.

## What changed (and nothing else)
| File | Change |
|---|---|
| `frontend/styles.css` | Rewritten. All colours/sizes are tokens in `:root`, measured from your 3 CodeyHub screenshots. Same class names as before, so JavaScript is untouched |
| `frontend/index.html` | Shared navbar markup (floating pill), new Home page sections (hero, blue band, how-it-works), small blue "eyebrow" labels above page titles, favicon colours |
| `backend/server.py` | One line: `.woff2` added to the MIME table so the self-hosted font is served correctly |
| `frontend/fonts/` (new) | Plus Jakarta Sans 400/500/600/700/800 (latin) as .woff2 + OFL licence. Self-hosted because the server's security policy only allows its own files (no Google Fonts, no external requests) |
| `design-reference/` (new) | Copies of your 3 CodeyHub screenshots, the design source of truth |
| `browser_tests/design_shots.py` (new) | Takes desktop / phone / dark screenshots of the app into `browser_tests/screens/` and prints navbar/hero sizes, to compare with `design-reference/` |

Not touched: every `.js` file, all Python routes/auth/AI code, the database, API formats, `.env` handling, tests.

## Design tokens (all measured from the screenshots by reading pixel values)
| Token | Value |
|---|---|
| Page, cards, navbar background | `#FFFFFF` |
| Hero band | `#F5F8FF` (only where the reference has it: the home hero) |
| Primary blue | `#1F5EFF` (hover `#1B55E6`, estimated) |
| Blue band | `linear-gradient(135deg, #1F5EFF, #1440CD)`, same in light and dark |
| Heading / body / nav link text | `#111827` / `#6B7280` / `#4B5563` |
| Hero heading accent words | blue `#1F5EFF`, green `#16A34A` |
| Borders / divider | `#E8EAED` / `#F5F6F7` |
| Icon tile, badges | `#EDF2FF`, 48 px tile, radius 12 |
| Navbar pill | 1224 x 78 px, 16 px from top, radius 24, 1 px border, soft shadow |
| Cards | radius 20, 1 px border, no shadow (white cards on the blue band: radius 20, padding 32) |
| Buttons | radius 12; large 54 px high, nav button 42 px high |
| Dark code window | `#0F1626` with bar `#171D2D`, red/amber/green dots |
| Font | Plus Jakarta Sans. Hero heading 800 / ~63 px / -0.015em. Section headings 800 / -0.03em. Body 17 px / 400 |
| Content column | 1152 px |

## Check against the screenshots (real measurements, desktop 1527 px wide)
| Item | CodeyHub screenshot | This app |
|---|---|---|
| Navbar pill x, y, width, height | 152, 16, 1224, 78 | 152, 16, 1224, 78 |
| Hero band starts at y | 94 | 94 |
| Hero band, navbar fill, borders, button blue | `#F5F8FF`, `#FFFFFF`, `#E8EAED`, `#1F5EFF` | identical |
| Blue band at both corners | `#1E5CFC` / `#1540CE` | `#1F5EFF` / `#1542D0` |
| Hero heading cap height | 47 px | 47 px (62.4 px font) |
Hero band is 633 px high here vs 649 px there: that is only because the text content is different.

## Decisions where the screenshots give no answer (please review)
1. **Dark mode.** CodeyHub screenshots are light only. The app had a dark mode, so it is kept, built from the dark navy of your code window.
   If you want light-only like CodeyHub: delete the `@media (prefers-color-scheme: dark) { ... }` block at the top of `styles.css`.
2. **Weak-topic orange.** The screenshots have no warning colour. Weak topics keep a readable orange (`#C2410C`), so weak vs strong never depends on green/blue alone.
3. **Hover colours, form field borders, footer, chat sidebar.** Not visible in the screenshots. Hover blue is 10% darker; form borders are darker than `#E8EAED` so they pass the 3:1 contrast rule; footer is plain white with a top border.
4. **Logo.** Keeps your own Study Gap mark. The name uses CodeyHub's two-colour pattern: "Study Gap" dark + "Detector" blue.
5. **Chat page** keeps its own sidebar layout (the workflow needs it) but uses the same colours/type; its background is white.
6. **Navbar links** go behind a menu button below 1020 px wide (this app has more links than CodeyHub).

## Tests actually run (final state)
| Test | Result |
|---|---|
| `python -m unittest discover -s backend/tests -v` | 110 tests, OK |
| `python browser_tests/browser_check.py` | 92/92 (ran 3 times after the final edits; see note) |
| `python browser_tests/liveserver_check.py` | 15/15: frontend on port 5500 + backend on 8000, login across ports, no sideways scroll at 375 px on 9 pages |
| `cd e2e && npm install && npm test` | **12/15. These 3 fail identically on your ORIGINAL zip** (dashboard "two modes", weak-topic practice, dashboard saved attempt), so they are not caused by the redesign. The optional jsdom suite is out of date against the current app; not fixed here |
Note: one browser_check run early on showed 91/92 ("report is stored with the attempt on the server"). It did not fail again in the 3 later runs, but the cause was never found: treat it as a possible timing issue and re-run if you see it.

I looked at screenshots of: Home, Study Gap Checker, Results, Dashboard, Chat, mobile menu, dark Home. I did NOT visually review every page at every size (About, Contact, Reviews, Login, Signup, Admin, Quiz) beyond the automated checks above.

## Not done / yours to check
* Real phone, Firefox and Safari (only Chromium was available), screen reader and keyboard-only use.
* Optional: fix the 3 old e2e checks.
* Still open from before: real questions for more subjects (`frontend/quiz.js` BANK + `data/syllabus.json` + `data/resources.json`), your real About text, password reset (needs SMTP), HTTPS deployment (see `DEPLOYMENT.md`), testing with your real AI key.

## How to run it (beginner)
1. Extract the zip. Put your old `backend/.env` file into the new `backend` folder (it is not in the zip). If you extract on top of your old folder, it is already there.
2. In a terminal opened in the project folder: `python backend/server.py` (starts the app), then open `http://localhost:8000`.
3. To see how it compares with CodeyHub: `python browser_tests/design_shots.py` (needs `pip install playwright` and `playwright install chromium`), then open the PNGs in `browser_tests/screens/` next to `design-reference/`.
