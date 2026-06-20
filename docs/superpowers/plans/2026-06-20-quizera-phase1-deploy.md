# Quizera Phase 1 (Secure / De-messaged / Render Deploy) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Quizera Flask app deployable on Render — secrets in env vars, messaging removed, Firestore quota protected, rate limiting added, contact-us images fixed — without breaking the auth/email flows.

**Architecture:** Stay on Flask + Jinja (server-rendered). Remove Flask-SocketIO (only the chat used it) so the app runs under plain `gunicorn`. Wrap the Firestore client in `config.py` with a transparent counting proxy that tracks reads/writes per Pacific day and exposes a "mode" (normal / read-only / maintenance); the app enforces that mode via a `before_request` hook and shows friendly pages. Flask-Limiter throttles abusive clients per IP.

**Tech Stack:** Python 3.12, Flask 2.3.3, firebase-admin / google-cloud-firestore, Flask-Mail, Flask-Limiter, gunicorn, pytest (dev/test only).

## Global Constraints

- Firebase stays on the **Spark (free) plan** — no billing account; charges are impossible.
- Daily Firestore free caps (hard backstop): **50,000 reads / 20,000 writes / 20,000 deletes**, reset midnight **US/Pacific**.
- **No secret values in code or committed files.** `SECRET_KEY`, `MAIL_USERNAME`, `MAIL_PASSWORD`, and Firebase credentials come only from environment variables (Firebase via `FIREBASE_CREDENTIALS` JSON string; local dev may fall back to `firebase-key.json`).
- Auth/email flow MUST keep working: signup → confirm-email → login → logout → forgot-password → reset-password.
- Quota thresholds are configurable; defaults: read-only at **80% of writes (16,000)**, maintenance at **90% of reads (45,000)**. Thresholds stay conservative because in-app counts are approximate.
- Source-of-truth repo after Phase 1: `somarjez/quizera-app.git`. Deploy target: **Render** only.
- `db` is imported app-wide as `from config import db`; wrap it in `config.py` so the 389 existing call sites are unchanged.

---

### Task 1: Add test scaffolding and pin tooling

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `runtime.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `pytest` setup so later tasks can write unit tests; `runtime.txt` pinning Python for Render.

- [ ] **Step 1: Create the dev requirements file**

`requirements-dev.txt`:
```
-r requirements.txt
pytest==8.2.0
freezegun==1.5.1
```

- [ ] **Step 2: Pin the Python runtime for Render**

`runtime.txt`:
```
python-3.12.6
```

- [ ] **Step 3: Create the tests package and a shared conftest**

`tests/__init__.py`: (empty file)

`tests/conftest.py`:
```python
import os
import sys

# Make the project root importable so `import quota_guard` etc. work under pytest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 4: Install dev deps and verify pytest runs**

Run: `pip install -r requirements-dev.txt && pytest -q`
Expected: pytest runs and reports `no tests ran` (exit code 5) — confirms collection works.

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt runtime.txt tests/__init__.py tests/conftest.py
git commit -m "chore: add pytest scaffolding and pin python runtime"
```

---

### Task 2: Firestore usage counter + quota mode (pure logic, TDD)

This task builds the *logic* with no Firestore dependency: a per-Pacific-day counter that persists to JSON and resolves the current mode. The proxy that wires it to Firestore is Task 3.

**Files:**
- Create: `quota_guard.py`
- Test: `tests/test_quota_guard.py`

**Interfaces:**
- Produces:
  - `class QuotaConfig(read_limit=50000, write_limit=20000, read_only_write_ratio=0.8, maintenance_read_ratio=0.9, state_path="quota_state.json")`
  - `class UsageCounter(config: QuotaConfig)` with:
    - `add_reads(n: int) -> None`
    - `add_writes(n: int) -> None`
    - `snapshot() -> dict` → `{"date": "YYYY-MM-DD", "reads": int, "writes": int}`
    - `mode() -> str` → one of `"normal"`, `"read_only"`, `"maintenance"`
  - `def pacific_date_str(now=None) -> str` (YYYY-MM-DD in US/Pacific)

- [ ] **Step 1: Write the failing tests**

`tests/test_quota_guard.py`:
```python
import json
from datetime import datetime, timezone, timedelta
from freezegun import freeze_time
from quota_guard import QuotaConfig, UsageCounter, pacific_date_str


def make_counter(tmp_path, **kw):
    cfg = QuotaConfig(state_path=str(tmp_path / "state.json"), **kw)
    return UsageCounter(cfg)


def test_counts_accumulate(tmp_path):
    c = make_counter(tmp_path)
    c.add_reads(5)
    c.add_reads(3)
    c.add_writes(2)
    snap = c.snapshot()
    assert snap["reads"] == 8
    assert snap["writes"] == 2


def test_mode_normal_then_read_only_then_maintenance(tmp_path):
    c = make_counter(tmp_path, read_limit=100, write_limit=100,
                     read_only_write_ratio=0.8, maintenance_read_ratio=0.9)
    assert c.mode() == "normal"
    c.add_writes(80)            # hits 80% of writes
    assert c.mode() == "read_only"
    c.add_reads(90)             # hits 90% of reads -> maintenance wins
    assert c.mode() == "maintenance"


def test_counts_reset_on_new_pacific_day(tmp_path):
    c = make_counter(tmp_path)
    with freeze_time("2026-06-20 12:00:00", tz_offset=0):
        c.add_reads(10)
        assert c.snapshot()["reads"] == 10
    with freeze_time("2026-06-21 12:00:00", tz_offset=0):
        # New day -> counter auto-resets on next access.
        assert c.snapshot()["reads"] == 0


def test_state_persists_across_instances(tmp_path):
    c1 = make_counter(tmp_path)
    c1.add_writes(4)
    c2 = make_counter(tmp_path)
    assert c2.snapshot()["writes"] == 4


def test_pacific_date_str_format():
    s = pacific_date_str(datetime(2026, 6, 20, 7, 0, tzinfo=timezone.utc))
    # 07:00 UTC == 00:00 PDT on 2026-06-20
    assert s == "2026-06-20"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_quota_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'quota_guard'`.

- [ ] **Step 3: Implement `quota_guard.py`**

```python
"""Per-Pacific-day Firestore usage counting and quota-mode resolution.

This is a *soft* early-warning system layered on top of Firestore's hard
free-tier daily caps. In-app counts are approximate; thresholds are
deliberately conservative.
"""
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


def pacific_date_str(now=None):
    """Return YYYY-MM-DD for the current US/Pacific calendar day.

    Uses a fixed -8h offset (no DST handling) which is good enough for a
    midnight-ish reset; being off by an hour around DST is harmless for a
    soft safeguard.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    pacific = now.astimezone(timezone(timedelta(hours=-8)))
    return pacific.strftime("%Y-%m-%d")


@dataclass
class QuotaConfig:
    read_limit: int = 50000
    write_limit: int = 20000
    read_only_write_ratio: float = 0.8
    maintenance_read_ratio: float = 0.9
    state_path: str = "quota_state.json"


class UsageCounter:
    def __init__(self, config: QuotaConfig):
        self.config = config
        self._lock = threading.Lock()
        self._date = pacific_date_str()
        self._reads = 0
        self._writes = 0
        self._load()

    def _load(self):
        try:
            with open(self.config.state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("date") == self._date:
                self._reads = int(data.get("reads", 0))
                self._writes = int(data.get("writes", 0))
        except (FileNotFoundError, ValueError, OSError):
            pass

    def _save(self):
        tmp = self.config.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"date": self._date, "reads": self._reads,
                           "writes": self._writes}, fh)
            os.replace(tmp, self.config.state_path)
        except OSError:
            pass  # ephemeral fs (e.g. Render) — counts stay in-memory only

    def _roll_day_if_needed(self):
        today = pacific_date_str()
        if today != self._date:
            self._date = today
            self._reads = 0
            self._writes = 0
            self._save()

    def add_reads(self, n):
        if n <= 0:
            return
        with self._lock:
            self._roll_day_if_needed()
            self._reads += n
            self._save()

    def add_writes(self, n):
        if n <= 0:
            return
        with self._lock:
            self._roll_day_if_needed()
            self._writes += n
            self._save()

    def snapshot(self):
        with self._lock:
            self._roll_day_if_needed()
            return {"date": self._date, "reads": self._reads,
                    "writes": self._writes}

    def mode(self):
        with self._lock:
            self._roll_day_if_needed()
            cfg = self.config
            if self._reads >= cfg.read_limit * cfg.maintenance_read_ratio:
                return "maintenance"
            if self._writes >= cfg.write_limit * cfg.read_only_write_ratio:
                return "read_only"
            return "normal"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_quota_guard.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add quota_guard.py tests/test_quota_guard.py
git commit -m "feat: add per-pacific-day Firestore usage counter and quota mode"
```

---

### Task 3: Counting Firestore proxy (TDD with fakes)

**Files:**
- Modify: `quota_guard.py` (append proxy classes + `QuotaExceeded`)
- Test: `tests/test_counting_proxy.py`

**Interfaces:**
- Consumes: `UsageCounter` from Task 2.
- Produces:
  - `class QuotaExceeded(Exception)`
  - `class CountingFirestore(client, counter: UsageCounter)` — wraps a Firestore `Client`. Overrides `collection(path)`, `collection_group(id)`, `document(path)`; delegates everything else via `__getattr__`.
  - Wrapped collection/query/document objects count: each document `.get()` = 1 read; query `.stream()`/`.get()` = len(results) reads; `.set()/.update()/.delete()` = 1 write; `.add()` = 1 write. When `counter.mode()` is `"read_only"` or `"maintenance"`, writes raise `QuotaExceeded`.

- [ ] **Step 1: Write the failing tests (using fake Firestore objects)**

`tests/test_counting_proxy.py`:
```python
import pytest
from quota_guard import (QuotaConfig, UsageCounter, CountingFirestore,
                         QuotaExceeded)


class FakeDoc:
    def __init__(self, store, name):
        self._store = store
        self._name = name
    def get(self):
        return {"name": self._name}
    def set(self, data):
        self._store[self._name] = data
    def update(self, data):
        self._store.setdefault(self._name, {}).update(data)
    def delete(self):
        self._store.pop(self._name, None)


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs
    def where(self, *a, **k):
        return self
    def limit(self, n):
        return self
    def stream(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, store, docs):
        self._store = store
        self._docs = docs
    def document(self, name):
        return FakeDoc(self._store, name)
    def where(self, *a, **k):
        return FakeQuery(self._docs)
    def stream(self):
        return iter(self._docs)
    def add(self, data):
        self._store[str(len(self._store))] = data
        return ("ref", None)


class FakeClient:
    def __init__(self):
        self.store = {}
        self.docs = [object(), object(), object()]
    def collection(self, path):
        return FakeCollection(self.store, self.docs)


def make(tmp_path, **kw):
    counter = UsageCounter(QuotaConfig(state_path=str(tmp_path / "s.json"), **kw))
    return CountingFirestore(FakeClient(), counter), counter


def test_document_get_counts_one_read(tmp_path):
    db, counter = make(tmp_path)
    db.collection("users").document("u1").get()
    assert counter.snapshot()["reads"] == 1


def test_query_stream_counts_each_doc_as_read(tmp_path):
    db, counter = make(tmp_path)
    list(db.collection("users").where("a", "==", 1).stream())
    assert counter.snapshot()["reads"] == 3


def test_set_counts_one_write(tmp_path):
    db, counter = make(tmp_path)
    db.collection("users").document("u1").set({"x": 1})
    assert counter.snapshot()["writes"] == 1


def test_write_blocked_in_read_only_mode(tmp_path):
    db, counter = make(tmp_path, write_limit=10, read_only_write_ratio=0.1)
    counter.add_writes(5)  # 5 >= 10*0.1 -> read_only
    assert counter.mode() == "read_only"
    with pytest.raises(QuotaExceeded):
        db.collection("users").document("u1").set({"x": 1})


def test_reads_still_work_in_read_only_mode(tmp_path):
    db, counter = make(tmp_path, write_limit=10, read_only_write_ratio=0.1)
    counter.add_writes(5)
    assert db.collection("users").document("u1").get() == {"name": "u1"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_counting_proxy.py -v`
Expected: FAIL with `ImportError: cannot import name 'CountingFirestore'`.

- [ ] **Step 3: Append the proxy implementation to `quota_guard.py`**

```python
class QuotaExceeded(Exception):
    """Raised when a write is attempted while in read-only/maintenance mode."""


class _CountingDocument:
    def __init__(self, ref, counter):
        self._ref = ref
        self._counter = counter

    def _guard_write(self):
        if self._counter.mode() != "normal":
            raise QuotaExceeded("Database is in read-only mode (quota guard).")

    def get(self, *a, **k):
        result = self._ref.get(*a, **k)
        self._counter.add_reads(1)
        return result

    def set(self, *a, **k):
        self._guard_write()
        result = self._ref.set(*a, **k)
        self._counter.add_writes(1)
        return result

    def update(self, *a, **k):
        self._guard_write()
        result = self._ref.update(*a, **k)
        self._counter.add_writes(1)
        return result

    def delete(self, *a, **k):
        self._guard_write()
        result = self._ref.delete(*a, **k)
        self._counter.add_writes(1)
        return result

    def collection(self, path):
        return _CountingCollection(self._ref.collection(path), self._counter)

    def __getattr__(self, name):
        return getattr(self._ref, name)


class _CountingQuery:
    def __init__(self, query, counter):
        self._query = query
        self._counter = counter

    def _wrap(self, q):
        return _CountingQuery(q, self._counter)

    def where(self, *a, **k):
        return self._wrap(self._query.where(*a, **k))

    def order_by(self, *a, **k):
        return self._wrap(self._query.order_by(*a, **k))

    def limit(self, *a, **k):
        return self._wrap(self._query.limit(*a, **k))

    def stream(self, *a, **k):
        results = list(self._query.stream(*a, **k))
        self._counter.add_reads(len(results))
        return iter(results)

    def get(self, *a, **k):
        results = list(self._query.get(*a, **k))
        self._counter.add_reads(len(results))
        return results

    def __getattr__(self, name):
        return getattr(self._query, name)


class _CountingCollection:
    def __init__(self, ref, counter):
        self._ref = ref
        self._counter = counter

    def document(self, *a, **k):
        return _CountingDocument(self._ref.document(*a, **k), self._counter)

    def where(self, *a, **k):
        return _CountingQuery(self._ref.where(*a, **k), self._counter)

    def order_by(self, *a, **k):
        return _CountingQuery(self._ref.order_by(*a, **k), self._counter)

    def limit(self, *a, **k):
        return _CountingQuery(self._ref.limit(*a, **k), self._counter)

    def stream(self, *a, **k):
        results = list(self._ref.stream(*a, **k))
        self._counter.add_reads(len(results))
        return iter(results)

    def get(self, *a, **k):
        results = list(self._ref.get(*a, **k))
        self._counter.add_reads(len(results))
        return results

    def add(self, *a, **k):
        if self._counter.mode() != "normal":
            raise QuotaExceeded("Database is in read-only mode (quota guard).")
        result = self._ref.add(*a, **k)
        self._counter.add_writes(1)
        return result

    def __getattr__(self, name):
        return getattr(self._ref, name)


class CountingFirestore:
    def __init__(self, client, counter):
        self._client = client
        self._counter = counter
        self.counter = counter  # exposed for app-level mode checks

    def collection(self, *a, **k):
        return _CountingCollection(self._client.collection(*a, **k),
                                   self._counter)

    def collection_group(self, *a, **k):
        return _CountingQuery(self._client.collection_group(*a, **k),
                              self._counter)

    def document(self, *a, **k):
        return _CountingDocument(self._client.document(*a, **k), self._counter)

    def __getattr__(self, name):
        return getattr(self._client, name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_counting_proxy.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q`
Expected: all tests PASS (Task 2 + Task 3).

- [ ] **Step 6: Commit**

```bash
git add quota_guard.py tests/test_counting_proxy.py
git commit -m "feat: add counting Firestore proxy with read-only write guard"
```

---

### Task 4: Move secrets to env and wrap `db` in config.py

**Files:**
- Modify: `config.py` (whole file)
- Create: `.env.example`
- Test: `tests/test_config_credentials.py`

**Interfaces:**
- Consumes: `CountingFirestore`, `UsageCounter`, `QuotaConfig` from `quota_guard`.
- Produces:
  - `Config` class reading `SECRET_KEY`, `MAIL_USERNAME`, `MAIL_PASSWORD` from env.
  - `def load_firebase_credential()` → returns a `credentials.Certificate` built from `FIREBASE_CREDENTIALS` (JSON string) if set, else from local `firebase-key.json`; raises `RuntimeError` if neither is available.
  - module-level `db` = `CountingFirestore(firestore client, UsageCounter(...))`.

- [ ] **Step 1: Write the failing test for credential selection**

`tests/test_config_credentials.py`:
```python
import json
import builtins
import pytest


def test_load_firebase_credential_prefers_env(monkeypatch):
    captured = {}

    fake_cert_payload = {"type": "service_account", "project_id": "demo"}
    monkeypatch.setenv("FIREBASE_CREDENTIALS", json.dumps(fake_cert_payload))

    import importlib
    import config_credentials  # extracted helper module (see Step 3)
    importlib.reload(config_credentials)

    def fake_certificate(arg):
        captured["arg"] = arg
        return "CERT"

    monkeypatch.setattr(config_credentials.credentials, "Certificate",
                        fake_certificate)
    cred = config_credentials.load_firebase_credential()
    assert cred == "CERT"
    assert captured["arg"] == fake_cert_payload


def test_load_firebase_credential_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    import importlib
    import config_credentials
    importlib.reload(config_credentials)
    monkeypatch.setattr(config_credentials, "LOCAL_KEY_PATH",
                        str(tmp_path / "nope.json"))
    with pytest.raises(RuntimeError):
        config_credentials.load_firebase_credential()
```

> Note: the credential helper is extracted into its own module `config_credentials.py` so it can be unit-tested without importing the full Firebase app init. `config.py` imports from it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config_credentials'`.

- [ ] **Step 3: Create `config_credentials.py`**

```python
"""Firebase credential loading, isolated for testability."""
import json
import os
from firebase_admin import credentials

LOCAL_KEY_PATH = "firebase-key.json"


def load_firebase_credential():
    raw = os.environ.get("FIREBASE_CREDENTIALS")
    if raw:
        return credentials.Certificate(json.loads(raw))
    if os.path.exists(LOCAL_KEY_PATH):
        return credentials.Certificate(LOCAL_KEY_PATH)
    raise RuntimeError(
        "No Firebase credentials. Set FIREBASE_CREDENTIALS (service-account "
        "JSON string) or provide a local firebase-key.json file."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_credentials.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Rewrite `config.py` to use env + wrap db**

```python
import os
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import firestore

from config_credentials import load_firebase_credential
from quota_guard import QuotaConfig, UsageCounter, CountingFirestore

load_dotenv()


class Config:
    SECRET_KEY = os.environ["SECRET_KEY"] if os.environ.get("SECRET_KEY") \
        else os.environ.get("FLASK_SECRET_KEY", "dev-only-insecure-key")

    MAIL_SERVER = "smtp.gmail.com"
    MAIL_PORT = 587
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_USERNAME")
    MAIL_MAX_EMAILS = None
    MAIL_SUPPRESS_SEND = False
    MAIL_ASCII_ATTACHMENTS = False


def init_firebase():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(load_firebase_credential())
    return firestore.client()


_raw_db = init_firebase()
_usage_counter = UsageCounter(QuotaConfig(
    state_path=os.environ.get("QUOTA_STATE_PATH", "quota_state.json")
))
# `db` is a transparent counting proxy: existing call sites are unchanged.
db = CountingFirestore(_raw_db, _usage_counter)
```

> Note on `SECRET_KEY`: the fallback `"dev-only-insecure-key"` is only used when no env var is set (local dev). On Render you MUST set `SECRET_KEY`. (A stricter "raise if missing in production" check is optional and can be added later; keeping a dev fallback avoids breaking local runs.)

- [ ] **Step 6: Create `.env.example`**

```
# Flask
SECRET_KEY=replace-with-a-long-random-string

# Email (Gmail App Password — generate a NEW one; never commit it)
MAIL_USERNAME=youraddress@gmail.com
MAIL_PASSWORD=your-new-gmail-app-password

# Firebase service account: paste the entire service-account JSON on one line.
# Leave unset locally if you keep a firebase-key.json file in the project root.
FIREBASE_CREDENTIALS=

# Optional: where the quota counter persists (defaults to quota_state.json)
QUOTA_STATE_PATH=quota_state.json
```

- [ ] **Step 7: Verify the app still imports with a local key**

Run: `python -c "import config; print(type(config.db).__name__)"`
Expected: prints `CountingFirestore` (requires a valid local `firebase-key.json`).

- [ ] **Step 8: Add quota_state.json to .gitignore and commit**

Add to `.gitignore` (new lines):
```
# Quota guard state
quota_state.json
quota_state.json.tmp
```

```bash
git add config.py config_credentials.py .env.example tests/test_config_credentials.py .gitignore
git commit -m "feat: load secrets from env, load Firebase from FIREBASE_CREDENTIALS, wrap db with counting proxy"
```

---

### Task 5: Remove the messaging feature and drop SocketIO

**Files:**
- Modify: `app.py` (remove chat routes/handlers ≈ lines 4430–4920; imports at 18, 23; init at 37; run block at end)
- Modify: `requirements.txt`
- Delete: `templates/chat.html`
- Modify: `templates/base.html` (remove chat nav links / socket.io script tags, if any)

**Interfaces:**
- Consumes: nothing new.
- Produces: a plain-Flask `app` object runnable via `gunicorn app:app`; no `socketio` symbol remains.

- [ ] **Step 1: Remove SocketIO imports and init**

In `app.py` delete line 18 (`from flask_socketio import SocketIO, emit`), line 23 (`from flask_socketio import SocketIO, emit, join_room, leave_room`), and line 37 (`socketio = SocketIO(app, cors_allowed_origins="*")`).

- [ ] **Step 2: Remove all chat routes and socket handlers**

Delete every route/handler in the chat block (run the grep below to find current line numbers, then remove each function in full):
Run: `grep -nE "@app.route\('/chat|@app.route\('/api/chat|@socketio.on" app.py`
Remove: `/chat`, all `/api/chat/*` routes, and every `@socketio.on(...)` handler function in their entirety.

- [ ] **Step 3: Replace the run block**

At the end of `app.py`, replace the socketio run block with:
```python
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
```

- [ ] **Step 4: Delete the chat template and nav links**

Run: `git rm templates/chat.html`
In `templates/base.html`, remove any `<a href="{{ url_for('chat') }}">` links and any `<script src="...socket.io...">` includes.
Run: `grep -rn "socket.io\|url_for('chat')\|url_for(\"chat\")" templates/`
Expected after edits: no matches.

- [ ] **Step 5: Drop SocketIO deps from requirements.txt**

Remove these three lines from `requirements.txt`:
```
Flask-SocketIO==5.3.6
python-socketio==5.8.0
eventlet==0.33.3
```

- [ ] **Step 6: Verify no SocketIO references remain and app imports**

Run: `grep -rn "socketio\|flask_socketio\|emit(\|join_room\|leave_room" app.py`
Expected: no matches.
Run: `python -c "import app; print('ok')"`
Expected: prints `ok` (no import errors).

- [ ] **Step 7: Smoke-run the app**

Run: `python app.py` (Ctrl+C after it starts)
Expected: server starts cleanly on port 5000 with no SocketIO/eventlet errors.

- [ ] **Step 8: Commit**

```bash
git add app.py requirements.txt templates/base.html
git rm --cached templates/chat.html 2>/dev/null; true
git commit -m "feat: remove messaging/chat feature and SocketIO dependency"
```

---

### Task 6: Enforce quota mode + add rate limiting in the app

**Files:**
- Modify: `app.py` (add imports, limiter, before_request, error handlers near the app setup block ~lines 34–46)
- Create: `templates/maintenance.html`
- Create: `templates/rate_limited.html`
- Modify: `templates/base.html` (add a read-only banner)
- Modify: `requirements.txt` (add Flask-Limiter)

**Interfaces:**
- Consumes: `db.counter.mode()` from the `CountingFirestore` proxy (Task 3/4); `QuotaExceeded` from `quota_guard`.
- Produces: global request gating; friendly 429 and maintenance pages; a read-only banner via template context.

- [ ] **Step 1: Add Flask-Limiter to requirements.txt**

Append to `requirements.txt`:
```
Flask-Limiter==3.5.0
gunicorn==21.2.0
```

- [ ] **Step 2: Wire limiter, mode gating, and error handlers into app.py**

After `app.config.from_object(Config)` (app.py:35) and the `from config import Config, db` import, add:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from quota_guard import QuotaExceeded

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per hour"],
    storage_uri="memory://",
)

# Endpoints that must keep working even in read-only mode (auth flows).
READ_ONLY_ALLOWED_ENDPOINTS = {
    "login", "logout", "signup", "confirm_email", "resend_confirmation",
    "forgot_password", "reset_password", "static",
}


@app.before_request
def enforce_quota_mode():
    mode = db.counter.mode()
    if mode == "maintenance":
        return render_template("maintenance.html"), 503
    if mode == "read_only" and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.endpoint not in READ_ONLY_ALLOWED_ENDPOINTS:
            return render_template("maintenance.html", read_only=True), 503


@app.context_processor
def inject_quota_mode():
    try:
        return {"quota_mode": db.counter.mode()}
    except Exception:
        return {"quota_mode": "normal"}


@app.errorhandler(QuotaExceeded)
def handle_quota_exceeded(_e):
    return render_template("maintenance.html", read_only=True), 503


@app.errorhandler(429)
def handle_rate_limit(_e):
    return render_template("rate_limited.html"), 429
```

- [ ] **Step 3: Add tighter limits to abusable auth/quiz endpoints**

Add a decorator line directly under the relevant `@app.route(...)` decorators:
- `/login` (app.py:449): add `@limiter.limit("10 per minute")`
- `/signup` (app.py:137): add `@limiter.limit("5 per minute")`
- `/forgot-password` (app.py:2922): add `@limiter.limit("5 per minute")`
- `/resend-confirmation` (app.py:368): add `@limiter.limit("5 per minute")`

Example (login):
```python
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    ...
```

- [ ] **Step 4: Create `templates/maintenance.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="container text-center" style="padding:80px 20px;">
  {% if read_only %}
    <h1>We're a bit busy right now</h1>
    <p>Saving is paused for a little while to keep Quizera within its free
       daily limits. You can still browse and read. Please try saving again
       later today.</p>
  {% else %}
    <h1>Quizera is taking a short break</h1>
    <p>We've hit today's free usage limit. The service resets at midnight
       Pacific time. Please check back soon — thanks for your patience!</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Create `templates/rate_limited.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="container text-center" style="padding:80px 20px;">
  <h1>Slow down a moment</h1>
  <p>You're sending requests a little too quickly. Please wait a minute and
     try again.</p>
</div>
{% endblock %}
```

- [ ] **Step 6: Add a read-only banner to base.html**

Inside `templates/base.html`, just after the opening `<body>` (or at the top of the main content block), add:
```html
{% if quota_mode == 'read_only' %}
<div style="background:#fff3cd;color:#664d03;padding:10px;text-align:center;">
  Quizera is in read-only mode right now to stay within free daily limits —
  saving is temporarily paused.
</div>
{% endif %}
```

- [ ] **Step 7: Verify app boots and pages render**

Run: `python -c "import app; print('ok')"`
Expected: `ok`.
Run: `python app.py`, then in a browser/curl hit `/` and a known page.
Expected: pages load; no banner in normal mode.

- [ ] **Step 8: Commit**

```bash
git add app.py requirements.txt templates/maintenance.html templates/rate_limited.html templates/base.html
git commit -m "feat: enforce quota read-only/maintenance modes and add rate limiting"
```

---

### Task 7: Fix contact-us images with graceful fallbacks

**Files:**
- Modify: `templates/contactus.html` (team `<img>` tags, lines ~1057–1166)
- Create: `static/images/team/.gitkeep`
- Modify: other templates if the image audit finds more missing refs

**Interfaces:** none (presentation only).

- [ ] **Step 1: Audit templates for image references to the missing folder**

Run: `grep -rnE "images/|\.jpg|\.png|\.svg" templates/ | grep -iv "data:image" | grep "url_for\|<img"`
Note every referenced path; flag any under `static/images/` (which currently does not exist).

- [ ] **Step 2: Add onerror fallbacks to the five team images**

In `templates/contactus.html`, for each team `<img>` (jezreel, shaila, judeelyn, shaine, ella), add an `onerror` handler that swaps to an inline initials placeholder:
```html
<img
  src="{{ url_for('static', filename='images/team/jezreel.jpg') }}"
  alt="Jezreel Ramos"
  onerror="this.onerror=null;this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22150%22 height=%22150%22%3E%3Crect width=%22150%22 height=%22150%22 fill=%22%23cccccc%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 font-size=%2248%22 fill=%22%23666%22 text-anchor=%22middle%22 dominant-baseline=%22central%22%3EJR%3C/text%3E%3C/svg%3E';"
/>
```
Repeat for each, changing the initials text (`JR`, `SA`, `JC`, `SM`, `ET`) and `alt`.

- [ ] **Step 3: Create the team images directory placeholder**

Create `static/images/team/.gitkeep` (empty) so the folder exists in the repo for the user to drop real photos into.

- [ ] **Step 4: Apply the same fallback to any other missing images found in Step 1**

For each additional missing-image reference found, add an equivalent `onerror` fallback. If none were found, note "no other missing images."

- [ ] **Step 5: Verify in the browser**

Run: `python app.py`, open `/contactus` (confirm the actual route name via `grep -n "def contactus\|/contactus\|aboutus" app.py`).
Expected: team section shows initials placeholders instead of broken-image icons.

- [ ] **Step 6: Commit**

```bash
git add templates/contactus.html static/images/team/.gitkeep
git commit -m "fix: graceful fallbacks for missing contact-us team images"
```

---

### Task 8: Render deployment config

**Files:**
- Create: `render.yaml`
- Create: `Procfile`
- Modify: `README.md` (deployment + env var docs)

**Interfaces:**
- Consumes: `gunicorn` (added in Task 6), `runtime.txt` (Task 1), env vars from Task 4.
- Produces: a Render-deployable service definition.

- [ ] **Step 1: Create `Procfile`**

```
web: gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT
```

- [ ] **Step 2: Create `render.yaml`**

```yaml
services:
  - type: web
    name: quizera
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT
    envVars:
      - key: SECRET_KEY
        generateValue: true
      - key: MAIL_USERNAME
        sync: false
      - key: MAIL_PASSWORD
        sync: false
      - key: FIREBASE_CREDENTIALS
        sync: false
      - key: PYTHON_VERSION
        value: 3.12.6
```

- [ ] **Step 3: Verify gunicorn can load the app**

Run: `pip install gunicorn==21.2.0 && python -c "import app; print('wsgi ok')"`
Expected: `wsgi ok` (confirms `app:app` is importable as a WSGI callable).

- [ ] **Step 4: Document deployment in README.md**

Add a "Deploying to Render" section listing: connect the GitHub repo, Render reads `render.yaml`, then set `MAIL_USERNAME`, `MAIL_PASSWORD`, and `FIREBASE_CREDENTIALS` (paste the full service-account JSON) in the dashboard; `SECRET_KEY` is auto-generated. Note the free-tier sleep/cold-start behavior and that quota counters reset on cold start.

- [ ] **Step 5: Commit**

```bash
git add render.yaml Procfile README.md
git commit -m "chore: add Render deployment config and docs"
```

---

### Task 9: Repo cutover to quizera-app and final auth smoke test

**Files:** none (git + manual verification).

**Interfaces:** none.

- [ ] **Step 1: Confirm no secrets or junk are tracked**

Run: `git ls-files | grep -E "firebase-key.json|\.env$|quota_state.json|venv/"`
Expected: no matches (all ignored). Also confirm the old hardcoded mail password no longer appears in the current `config.py`.

- [ ] **Step 2: Point origin at the deploy repo**

```bash
git remote set-url origin https://github.com/somarjez/quizera-app.git
git remote -v
```
Expected: origin now shows `quizera-app.git`.

- [ ] **Step 3: Push the cleaned code**

```bash
git push -u origin main
```
Expected: push succeeds. (If the remote has unrelated history, coordinate with the user before any force-push — do NOT force-push without confirmation.)

- [ ] **Step 4: Run the full auth/email smoke test locally**

With env vars set (or a local `firebase-key.json`), run `python app.py` and manually verify, in order:
1. `/signup` → submit → confirmation email received.
2. Click confirmation link → account confirmed.
3. `/login` → success.
4. `/logout` → success.
5. `/forgot-password` → reset email received.
6. Reset link → set new password → login with new password.

Expected: every step works. Record any failures as bugs to fix before marking Phase 1 done.

- [ ] **Step 5: Post-deploy verification on Render**

After Render builds, repeat the Step 4 smoke test against the live URL. Confirm `/contactus` shows placeholders, and that hammering `/login` >10×/min returns the friendly rate-limit page.

- [ ] **Step 6: Final commit (if any doc/notes changes)**

```bash
git add -A
git commit -m "docs: phase 1 verification notes" || echo "nothing to commit"
git push
```

---

## Notes for the implementer

- **TDD applies to logic** (Tasks 2–4 have real unit tests). Feature-removal, templates, and deployment config (Tasks 5–9) are verified by running the app and grep checks, since they aren't naturally unit-testable.
- **Do not** reintroduce any hardcoded secret. If the app won't start locally, set env vars or provide a local `firebase-key.json` (gitignored).
- The counting proxy only intercepts the common Firestore surface (`collection`/`document`/`where`/`order_by`/`limit`/`stream`/`get`/`set`/`update`/`delete`/`add`). If the app uses `batch()`, `transaction()`, or `collection_group()` writes anywhere, those pass through uncounted — acceptable for a soft safeguard, but note it.
- Render free tier sleeps after ~15 min idle; the first request after sleep is slow and resets in-memory quota counts. This is expected and documented.
