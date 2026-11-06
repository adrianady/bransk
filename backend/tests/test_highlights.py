"""Tests for PDF highlights: CRUD, ownership validation, and QA pickup."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "highlights.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def _study(client, title="Highlight study"):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201
    return res.json()


def test_highlight_crud_roundtrip(client):
    study = _study(client)
    created = client.post(f"/api/studies/{study['id']}/highlights", json={
        "quote": "Sleep consolidates declarative memory.",
        "note": "Relevant pentru capitolul 2",
        "page_number": 4,
    })
    assert created.status_code == 201
    hl = created.json()
    assert hl["page_number"] == 4
    assert hl["quote"].startswith("Sleep consolidates")

    listed = client.get(f"/api/studies/{study['id']}/highlights").json()
    assert [h["id"] for h in listed] == [hl["id"]]

    updated = client.put(f"/highlights/{hl['id']}".replace("/highlights", "/api/highlights"),
                         json={"note": "Notiță revizuită"})
    assert updated.status_code == 200
    assert updated.json()["note"] == "Notiță revizuită"

    deleted = client.delete(f"/api/highlights/{hl['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/studies/{study['id']}/highlights").json() == []


def test_highlight_rejects_foreign_document(client):
    study = _study(client)
    other = _study(client, "Other study")
    from app.db import get_db
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO documents (entry_id, chunk_index, content) VALUES (?, ?, ?)",
            [other["id"], 0, "unrelated"],
        )
        conn.commit()
        foreign_doc = cur.lastrowid
    finally:
        conn.close()

    bad = client.post(f"/api/studies/{study['id']}/highlights", json={
        "quote": "q", "document_id": foreign_doc,
    })
    assert bad.status_code == 400


def test_qa_prompt_includes_highlights(client, monkeypatch):
    study = _study(client)
    client.post(f"/api/studies/{study['id']}/highlights", json={
        "quote": "N=124 participants", "note": "eșantion", "page_number": 3,
    })
    from app.routers import study_qa as sq
    captured = {}

    async def fake_llm(messages, **kw):
        captured["content"] = messages[0]["content"]
        return ("", "", "ok")

    monkeypatch.setattr(sq, "llm_complete", fake_llm)
    # The mock provider branch never reaches the LLM; force the real path.
    monkeypatch.setattr(sq, "LLM_PROVIDER", "deepseek")
    res = client.post(f"/api/studies/{study['id']}/qa", json={"question": "câți participanti?"})
    assert res.status_code == 200
    assert "N=124 participants" in captured["content"]
    assert "pagina 3" in captured["content"]
