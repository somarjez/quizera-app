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
