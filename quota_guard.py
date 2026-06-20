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
