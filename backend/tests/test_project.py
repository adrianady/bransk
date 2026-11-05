"""
Tests for the thesis project centre: project storage, study recommendations,
structural feedback and the outline example.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Isolated database per test so the real project row is never touched."""
    import app.db as db_module

    db_path = tmp_path / "project_test.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def test_project_defaults_and_update(client):
    res = client.get("/api/project")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 1
    assert data["work_type"] == "licenta"
    assert len(data["structure"]) >= 6

    res = client.put("/api/project", json={
        "title": "Efectele mindfulness asupra creierului",
        "work_type": "disertatie",
        "research_q": "Ce modificări structurale produce meditația?",
    })
    assert res.status_code == 200
    saved = res.json()
    assert saved["title"] == "Efectele mindfulness asupra creierului"
    assert saved["work_type"] == "disertatie"

    # Reading it back proves the row was persisted, not just echoed.
    again = client.get("/api/project").json()
    assert again["title"] == saved["title"]
    assert again["research_q"] == saved["research_q"]


def test_project_structure_roundtrip(client):
    structure = [
        {"id": "s1", "title": "Introducere", "target_words": 1000, "description": "", "content": "Text scris."},
        {"id": "s2", "title": "Metodologie", "target_words": 2000, "description": "", "content": ""},
    ]
    res = client.put("/api/project", json={"structure": structure})
    assert res.status_code == 200
    assert len(res.json()["structure"]) == 2
    assert res.json()["structure"][0]["content"] == "Text scris."


def test_recommendations_match_studies(client):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO entries (title, content, tags, study_type, design, quality, entry_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ["Mindfulness and cortical thickness", "Gray matter changes after meditation",
             "study, mindfulness", "peer-reviewed paper", "systematic review", 4, "2024-01-01"],
        )
        conn.execute(
            """INSERT INTO entries (title, content, tags, study_type, quality, entry_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ["Quantum computing survey", "Qubits and gates", "study, physics",
             "peer-reviewed paper", 3, "2020-01-01"],
        )
        conn.commit()
    finally:
        conn.close()

    client.put("/api/project", json={
        "title": "Mindfulness și structura creierului",
        "research_q": "Ce schimbări produce meditația?",
    })
    res = client.get("/api/project/studies?limit=5")
    assert res.status_code == 200
    body = res.json()
    assert body["total_library"] >= 2
    titles = [s["title"] for s in body["studies"]]
    assert "Mindfulness and cortical thickness" in titles
    # The unrelated study must not outrank the on-topic one.
    assert titles[0] == "Mindfulness and cortical thickness"
    assert body["studies"][0]["score"] > 0


def test_feedback_flags_missing_project_parts(client):
    res = client.get("/api/project/feedback")
    assert res.status_code == 200
    items = res.json()["items"]
    messages = " ".join(i["message"] for i in items)
    assert "titlu" in messages.lower()
    assert "întrebarea de cercetare" in messages.lower()

    # With a title and question filled in, those two problems disappear.
    client.put("/api/project", json={
        "title": "Efectele meditației mindfulness asupra structurii creierului la adulți",
        "research_q": "Ce modificări structurale produce meditația mindfulness?",
    })
    items2 = client.get("/api/project/feedback").json()["items"]
    messages2 = " ".join(i["message"] for i in items2).lower()
    assert "nu are încă un titlu" not in messages2
    assert "lipsește întrebarea de cercetare" not in messages2


def test_structure_example_returns_usable_outline(client):
    res = client.post("/api/project/structure/example", json={
        "title": "Efectele mindfulness asupra creierului",
        "work_type": "licenta",
    })
    assert res.status_code == 200
    body = res.json()
    assert len(body["structure"]) >= 6
    for section in body["structure"]:
        assert section["title"]
        assert isinstance(section["target_words"], int)
        assert "content" in section
    total = sum(s["target_words"] for s in body["structure"])
    assert total >= 5000
