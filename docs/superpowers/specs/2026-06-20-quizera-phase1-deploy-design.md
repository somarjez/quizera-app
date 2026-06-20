# Quizera — Phase 1: Secure, De-messaged, Render-Deployable

**Date:** 2026-06-20
**Status:** Approved (pending written-spec review)
**Stack decision:** Stay on Flask + Jinja (server-rendered). A React rewrite is explicitly deferred to a future, separately-planned project.

## Goal

Get the existing Quizera Flask app deployed to **Render** as a public, free-tier app that:

- Cannot incur charges (Firebase stays on the Spark/free plan).
- Protects its Firestore free-tier quota from abuse and degrades gracefully when near the daily cap.
- Has no leaked secrets.
- Has the messaging/chat feature removed.
- Keeps **login, signup, email confirmation, logout, forgot-password, and reset-password working** — this is the primary acceptance criterion.

Restructuring `app.py` (~6,050 lines, 389 Firestore calls) into modules/blueprints is **out of scope** for Phase 1 and deferred to Phase 2.

## Context / current state

- **App:** Flask 2.3.3, server-rendered Jinja templates (~40+), session + Flask-Login auth, Flask-Mail for email, Flask-SocketIO + eventlet (used *only* by the chat feature).
- **Database:** Firestore via the `firebase-admin` server SDK. Free (Spark) plan — hard daily caps of 50K reads / 20K writes / 20K deletes; no billing account, so charges are impossible. The SDK has **no API to query current quota usage**, so the app must count usage itself.
- **Repos:** Local remote is `somarjez/Flask-Quizera.git`; deploy target is `somarjez/quizera-app.git` (public, non-empty). Source of truth going forward = `quizera-app`.
- **Known security problem:** `config.py` (tracked in git, public repos) hardcodes a Gmail App Password and a weak `SECRET_KEY` fallback. The password is committed in history and must be **revoked**, not just removed.

## Security pre-work (must happen first, outside the codebase)

1. Revoke the leaked Gmail App Password at https://myaccount.google.com/apppasswords and generate a new one.
2. Treat the new password and `SECRET_KEY` as secrets supplied only via environment variables.
   Note: removing the secret from current code does **not** remove it from `Flask-Quizera` git history — revocation is the real fix.

## Phase 1 work items

### 1. Remove the messaging feature

- Remove chat routes and handlers from `app.py`: `/chat`, `/api/chat/*`, and all `@socketio.on(...)` handlers (≈ lines 4430–4920), the `flask_socketio` import, and the `socketio = SocketIO(app)` init.
- Replace the `socketio.run(...)` entrypoint with a plain Flask entrypoint suitable for `gunicorn` (`app:app`).
- Delete `templates/chat.html`; remove chat links from `base.html` / navbar.
- Remove `Flask-SocketIO`, `python-socketio`, `eventlet` from `requirements.txt`.
- Verify notifications still work — they are Firestore-document based, not socket based, so they are unaffected.

### 2. Secrets → environment variables

- Read `SECRET_KEY`, `MAIL_USERNAME`, `MAIL_PASSWORD` from env with **no hardcoded fallbacks** (fail loudly if missing in production).
- **Firebase credentials:** load from a `FIREBASE_CREDENTIALS` env var containing the service-account JSON as a string (parsed via `credentials.Certificate(json.loads(...))`). Fall back to the local `firebase-key.json` file for local dev only.
- Add `.env.example` documenting every required variable. Confirm `config.py` no longer contains any secret values before pushing.

### 3. Rate limiting (anti-abuse — focus: single bot/user spamming)

- Add **Flask-Limiter** with in-memory storage (fits Render's single free instance).
- Global default per-IP cap (e.g. `200/hour`), with tighter per-route caps on abusable/expensive endpoints: `/login`, `/signup`, `/forgot-password`, `/resend-confirmation`, quiz submission.
- Over-limit responses render a friendly **429 page**, not a stack trace.

### 4. Firestore quota protection (graceful degradation)

- Implement a transparent **counting proxy** wrapping the Firestore `db` client (Approach A from brainstorming). It mimics the client's interface (`.collection()`, document refs, `.get()`, `.stream()`, `.set()`, `.update()`, `.add()`, `.delete()`), so the **389 existing call sites are unchanged**.
- The proxy counts reads (a `.stream()`/`.get()` returning N docs = N reads) and writes per **Pacific-timezone day**.
- Two configurable thresholds:
  - At ~80% of the daily **write** budget → **read-only mode**: writes are blocked with a friendly "please try again later" message; reads continue.
  - At ~90% of the daily **read** budget → **maintenance page** for everyone until the midnight-PT reset.
- Persist counts to a small local JSON file keyed by the PT date so a mid-day restart doesn't lose the tally.
- **Known limitation:** Render's free tier sleeps on inactivity and has an ephemeral filesystem, so counters reset on cold starts. This is a *soft* early-warning system; Firestore's real daily cap remains the hard backstop. Acceptable for a free-tier hobby deployment. Thresholds stay conservative because in-app counts are approximate.

### 5. Fix contact-us (and audit) images

- Root cause: `static/images/` does not exist on disk or in git; `templates/contactus.html` references five missing team photos (`static/images/team/{jezreel,shaila,judeelyn,shaine,ella}.jpg`).
- Add `onerror` fallbacks (placeholder / initials avatar) to the team `<img>` tags so missing images never render as broken icons.
- Audit other templates for references to the missing `images/` folder and apply the same fallback pattern where needed.
- **User action required:** drop the five real team photos into `static/images/team/` with the exact filenames above. (Cannot be generated.)

### 6. Render deployment

- Add `gunicorn` to `requirements.txt`.
- Add a start command `gunicorn app:app` (via `render.yaml` and/or `Procfile`).
- Pin the Python version (`runtime.txt`).
- Add a `render.yaml` describing the web service.
- Document, in the README/`.env.example`, setting all env vars (including `FIREBASE_CREDENTIALS`) in the Render dashboard.

### 7. Verify auth + email (acceptance gate)

Smoke-test the full flow locally, then again on Render:

signup → email confirmation link → login → logout → forgot-password → reset-password link → login with new password.

All must pass before Phase 1 is considered done.

### 8. Repo cutover

- Point the local repo's `origin` at `somarjez/quizera-app.git` and push the cleaned code.
- Confirm `config.py` (secret-free), `firebase-key.json`, `venv/`, and uploads are not pushed (verify `.gitignore` covers them; `firebase-key.json` and `venv/` already are).
- Add a README note that the old leaked Gmail password must remain revoked.

## Out of scope (future phases)

- **Phase 2:** Split `app.py` into blueprints/service modules.
- **Future:** Possible React SPA frontend (would re-enable a Vercel frontend + Flask API on Render).

## Open items requiring the user

- Revoke + reissue the Gmail App Password.
- Provide the five team photos for `static/images/team/`.
- Provide the Firebase service-account JSON to set as `FIREBASE_CREDENTIALS` on Render.
