"""
Tests for the Study QA + health-check endpoints.

Runs with the mock LLM provider (offline), a fresh test DB per test.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db import get_db, init_db  # noqa: E402


def _make_study(title: str = "Test study about memory") -> int:
    init_db()
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO entries (title, content, entry_date, tags, source, study_type, authors, quality, design, effect_direction, pdf_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [title, "This study found that repetition improves memory. The conclusion is positive.", "2010-01-01", "study,pdf", "http://example.com/study.pdf", "study", "Author One", 4, "RCT", "positive", "study.pdf"],
        )
        eid = cur.lastrowid
        conn.execute(
            "INSERT INTO documents (entry_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
            [eid, 0, "Repetition improves memory retention over time.", "[0.1, 0.2, 0.3]"],
        )
        conn.commit()
        return eid
    finally:
        conn.close()


def test_study_check_returns_issues():
    client = TestClient(app)
    eid = _make_study("Memory repetition study")
    res = client.get(f"/api/studies/{eid}/check")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["entry_id"] == eid
    assert data["is_study"] is True
    assert isinstance(data["issues"], list)
    assert isinstance(data["suggestions"], list)
    assert data["indexed_chunks"] == 1


def test_study_qa_returns_answer():
    client = TestClient(app)
    eid = _make_study("Memory repetition study")
    res = client.post(f"/api/studies/{eid}/qa", json={"question": "Care este efectul principal?"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["entry_id"] == eid
    assert isinstance(data["answer"], str) and len(data["answer"]) > 0
    assert isinstance(data["sources"], list)


def test_study_qa_requires_question():
    client = TestClient(app)
    eid = _make_study("Memory repetition study")
    res = client.post(f"/api/studies/{eid}/qa", json={"question": ""})
    assert res.status_code == 400


def test_study_check_unknown_entry_404():
    client = TestClient(app)
    init_db()
    res = client.get("/api/studies/999999/check")
    assert res.status_code == 404
