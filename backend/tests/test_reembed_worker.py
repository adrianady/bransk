"""Background re-embed worker: it must repair, not rewrite, and never block."""
import json
import os

import pytest

from app import reembed_worker as rw


@pytest.fixture(autouse=True)
def _schema():
    """The shared fixture wipes the DB per test, so rebuild the schema first."""
    from app.db import init_db

    from app.db import get_db

    init_db()
    conn = get_db()
    try:
        # documents.entry_id is a foreign key: give the chunks a real study.
        cur = conn.execute(
            "INSERT INTO entries (title, content, importance) VALUES ('Study', 'x', 3)"
        )
        conn.commit()
        globals()["_ENTRY_ID"] = cur.lastrowid
    finally:
        conn.close()
    yield


def _seed(dim_specs):
    """Insert chunks with the given stored-vector dimensions."""
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute("DELETE FROM documents")
        ids = []
        for i, dim in enumerate(dim_specs):
            vec = json.dumps([0.1] * dim) if dim else None
            cur = conn.execute(
                "INSERT INTO documents (entry_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
                [_ENTRY_ID, i, f"chunk text {i}", vec],
            )
            ids.append(cur.lastrowid)
        conn.commit()
        return ids
    finally:
        conn.close()


def _dims():
    from app.db import get_db

    conn = get_db()
    try:
        rows = conn.execute("SELECT embedding FROM documents").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            v = json.loads(r["embedding"])
        except (TypeError, json.JSONDecodeError):
            v = None
        out.append(len(v) if isinstance(v, list) and v else 0)
    return out


def test_pending_ids_is_exactly_the_mismatched_chunks():
    _seed([1024, 1024, 0, 768])
    pending = rw._pending_ids(1024)
    assert len(pending) == 2, "only the blank and the 768 chunk need a new vector"


def test_repair_writes_only_mismatched_chunks(monkeypatch):
    _seed([1024, 0, 0])

    def fake_embed(texts):
        return [[0.5] * 1024 for _ in texts]

    monkeypatch.setattr(rw, "embed", fake_embed)
    monkeypatch.setattr(rw, "PAUSE_SECONDS", 0)
    written = rw._repair_one_pass(1024)

    assert written == 2
    assert _dims() == [1024, 1024, 1024], "every chunk ends up in the active dimension"
    assert rw.reembed_state()["done"] == 2
    assert rw.reembed_state()["total"] == 2


def test_repair_stops_without_writing_when_backend_is_gone(monkeypatch):
    _seed([0, 0])

    monkeypatch.setattr(rw, "embed", lambda texts: None)
    written = rw._repair_one_pass(1024)

    assert written == 0
    assert _dims() == [0, 0], "no vector is invented when the server is down"
    assert rw.reembed_state()["last_error"] == "embedding backend unavailable"


def test_repair_rejects_vectors_of_the_wrong_dimension(monkeypatch):
    _seed([0])

    # A backend that answers with the wrong shape must not poison the column.
    monkeypatch.setattr(rw, "embed", lambda texts: [[0.5] * 768 for _ in texts])
    assert rw._repair_one_pass(1024) == 0
    assert _dims() == [0]


def test_worker_can_be_disabled_by_environment(monkeypatch):
    monkeypatch.setenv("CHRONOKG_REEMBED_ON_START", "0")
    assert rw.start_background_reembed() is False


def test_nothing_to_do_reports_zero(monkeypatch):
    _seed([1024, 1024])
    monkeypatch.setattr(rw, "PAUSE_SECONDS", 0)
    assert rw._repair_one_pass(1024) == 0
    assert rw.reembed_state()["total"] == 0
