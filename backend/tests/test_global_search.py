"""Tests for the global search endpoint behind the command palette."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "gsearch.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def test_global_search_finds_every_layer(client):
    entry = client.post("/api/entries", json={
        "title": "Hippocampus și consolidarea",
        "content": "Somnul lent consolidează amintirile declarative.",
    }).json()
    study = client.post("/api/entries", json={
        "title": "Hippocampus replay study", "study_type": "study",
        "authors": "Ioan Smith", "entry_date": "2021-01-01",
    }).json()
    client.post(f"/api/studies/{study['id']}/notes", json={
        "content": "Notiță despre hippocampus, pagină 4",
    })
    client.post(f"/api/studies/{study['id']}/highlights", json={
        "quote": "The hippocampus replays during sleep, N=124", "page_number": 3,
    })
    from app.db import get_db
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO documents (entry_id, chunk_index, page_number, content) VALUES (?, ?, ?, ?)",
            [study["id"], 0, 7, "The hippocampus replays during slow-wave sleep."],
        )
        conn.commit()
    finally:
        conn.close()

    res = client.get("/api/search/global", params={"q": "hippocampus"})
    assert res.status_code == 200
    data = res.json()
    assert any(e["id"] == entry["id"] for e in data["entries"])
    assert any(s["id"] == study["id"] for s in data["studies"])
    assert data["notes"] and data["notes"][0]["entry_title"] == "Hippocampus replay study"
    assert data["highlights"] and "N=124" in data["highlights"][0]["quote"]
    assert data["chunks"] and data["chunks"][0]["page_number"] == 7
    assert data["total"] >= 4


def test_global_search_empty_query_is_empty(client):
    res = client.get("/api/search/global", params={"q": "  "})
    assert res.status_code == 200
    assert res.json()["total"] == 0
