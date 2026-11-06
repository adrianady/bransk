"""
Regression tests for the 2026-11-06 functional audit (D1–D12).

Each test pins one defect the audit confirmed, so the fix stays fixed:
  D1  thoughts must match a tag in any position of entries.tags
  D2  timeline search must be diacritic-insensitive in SQL
  D3  the PDF link must land in `source` (EntryCreate forbids unknown fields)
  D4  PUT /theories with explicit parent_id=null detaches the parent
  D5  the entries list ships a 300-char preview + X-Total-Count, not full text
  D6  study QA embeds only the query, reusing stored chunk vectors
  D7  SR queue and todos carry entry_title/content_preview
  D8  research re-run updates the existing report (covered in test_research)
  D9  POST /entries rejects duplicate titles (server-side dedup)
  D10 deleting a tag strips it from every entry carrying it
  D11 timeline COUNT agrees with LIMIT/OFFSET under a search filter
  D12 study QA keeps its sources visible in mock mode
"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "audit_fixes.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def _mk(client, title, **kw):
    res = client.post("/api/entries", json={"title": title, **kw})
    assert res.status_code == 201
    return res.json()


def _tag_ids(client):
    return {t["name"]: t["id"] for t in client.get("/api/tags").json()}


# ── D1 ────────────────────────────────────────────────────────────────────────

def test_thought_finds_entry_by_second_tag(client):
    entry = _mk(client, "Relativity note", tags="physics, relativity")
    tags = _tag_ids(client)
    thought = client.post(
        "/api/thoughts", json={"name": "relativity", "tag_ids": str(tags["relativity"])}
    ).json()
    rows = client.get(f"/api/thoughts/{thought['id']}/entries").json()
    assert entry["id"] in [r["id"] for r in rows]


# ── D2 + D11 ─────────────────────────────────────────────────────────────────

def test_timeline_search_is_diacritic_insensitive(client):
    _mk(client, "Funcționare internă", content="Școala primară", entry_date="2026-01-01")
    for q in ("functionare", "Funcționare", "scoala", "ȘCOALA"):
        res = client.get("/api/timeline", params={"q": q}).json()
        assert res["total"] == 1, f"q={q!r} lost the entry"


def test_timeline_pagination_agrees_with_total_under_search(client):
    for i in range(5):
        _mk(client, f"Memoria procedurală {i}", entry_date=f"2026-01-0{i + 1}")
    page1 = client.get("/api/timeline", params={"q": "memoria procedurala", "limit": 2}).json()
    assert page1["total"] == 5
    assert len(page1["entries"]) == 2
    page3 = client.get("/api/timeline", params={"q": "memoria procedurala", "limit": 2, "offset": 4}).json()
    assert len(page3["entries"]) == 1


# ── D3 ────────────────────────────────────────────────────────────────────────

def test_entrycreate_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        from app.models import EntryCreate
        EntryCreate(title="x", url="https://example.com/a.pdf")


def test_pdf_url_lands_in_source_for_extraction(client):
    entry = _mk(client, "Paper X", source="https://example.com/paper.pdf")
    from app.studies_store import extract_pdf_url
    detail = client.get(f"/api/entries/{entry['id']}").json()
    assert extract_pdf_url(detail) == "https://example.com/paper.pdf"


# ── D4 ────────────────────────────────────────────────────────────────────────

def test_theory_parent_detaches_on_explicit_null(client):
    parent = client.post("/api/theories", json={"name": "Parent"}).json()
    child = client.post("/api/theories", json={"name": "Child", "parent_id": parent["id"]}).json()
    detached = client.put(f"/api/theories/{child['id']}", json={"parent_id": None}).json()
    assert detached["parent_id"] is None
    # An omitted field must not resurrect or clear anything.
    renamed = client.put(f"/api/theories/{child['id']}", json={"name": "Child renamed"}).json()
    assert renamed["parent_id"] is None


# ── D5 ────────────────────────────────────────────────────────────────────────

def test_entry_list_projects_preview_and_total_header(client):
    _mk(client, "Long entry", content="X" * 5000)
    res = client.get("/api/entries")
    assert res.headers["X-Total-Count"] == "1"
    rows = res.json()
    assert rows[0]["content_preview"] == "X" * 300
    assert rows[0]["content"] == ""
    detail = client.get(f"/api/entries/{rows[0]['id']}").json()
    assert len(detail["content"]) == 5000


# ── D6 ────────────────────────────────────────────────────────────────────────

def test_study_qa_embeds_only_the_query(client, monkeypatch):
    entry = _mk(client, "Vector study", study_type="study")
    from app.db import get_db
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO documents (entry_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
            (entry["id"], 0, "alpha", json.dumps([1.0, 0.0])),
        )
        conn.execute(
            "INSERT INTO documents (entry_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
            (entry["id"], 1, "beta", json.dumps([0.0, 1.0])),
        )
        conn.commit()
    finally:
        conn.close()

    from app.routers import study_qa as sq
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        if len(texts) == 1:
            return [[1.0, 0.0]]
        return None

    monkeypatch.setattr(sq, "embed", fake_embed)
    chunks = sq._study_chunks(entry["id"], "anything", top_k=2)
    assert calls == [["anything"]], "only the query should be embedded"
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["score"] > chunks[1]["score"]


# ── D7 ────────────────────────────────────────────────────────────────────────

def test_review_queue_and_todos_carry_entry_titles(client):
    entry = _mk(client, "Review me please", content="body to preview")
    client.post(f"/api/spaced-repetition/items/{entry['id']}")
    queue = client.get("/api/spaced-repetition/queue").json()
    assert queue and queue[0]["entry_title"] == "Review me please"
    assert "body to preview" in queue[0]["content_preview"]

    client.post(f"/api/entries/{entry['id']}/todo", json={})
    todos = client.get("/api/todos").json()
    assert todos and todos[0]["entry_title"] == "Review me please"


# ── D9 ────────────────────────────────────────────────────────────────────────

def test_create_entry_rejects_duplicate_titles(client):
    first = _mk(client, "Same Paper", content="one")
    dup = client.post("/api/entries", json={"title": "  same  paper "})
    assert dup.status_code == 409
    assert dup.json()["detail"]["existing_id"] == first["id"]
    other = client.post("/api/entries", json={"title": "Different title"})
    assert other.status_code == 201


# ── D10 ───────────────────────────────────────────────────────────────────────

def test_delete_tag_strips_it_from_entries(client):
    entry = _mk(client, "Tagged", tags="physics, relativity")
    tags = _tag_ids(client)
    res = client.delete(f"/api/tags/{tags['physics']}")
    assert res.status_code == 204
    got = client.get(f"/api/entries/{entry['id']}").json()
    assert "physics" not in got["tags"]
    assert "relativity" in got["tags"]


# ── D12 ───────────────────────────────────────────────────────────────────────

def test_study_qa_mock_keeps_sources_visible(client):
    entry = _mk(client, "Mock study", study_type="study")
    from app.db import get_db
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO documents (entry_id, chunk_index, page_number, content) VALUES (?, ?, ?, ?)",
            (entry["id"], 0, 3, "The hippocampus consolidates memory during sleep."),
        )
        conn.commit()
    finally:
        conn.close()
    res = client.post(f"/api/studies/{entry['id']}/qa", json={"question": "ce spune despre memorie?"})
    assert res.status_code == 200
    body = res.json()
    assert body["sources"], "mock mode must still return the retrieved chunks"
    assert body["sources"][0]["page_number"] == 3


# ── Sortare listă de intrări (îmbunătățirea „Bibliotecă”, 2026-11) ──────────

def test_entry_list_sort_options(client):
    _mk(client, "Banana", entry_date="2026-01-02", importance=1)
    _mk(client, "apple pie", entry_date="2026-01-01", importance=5)

    titles = [e["title"] for e in client.get("/api/entries", params={"sort": "title", "limit": 10}).json()]
    assert titles == sorted(titles, key=str.lower)

    by_importance = client.get("/api/entries", params={"sort": "importance", "limit": 1}).json()
    assert by_importance[0]["importance"] == 5

    by_date_asc = client.get("/api/entries", params={"sort": "date_asc", "limit": 1}).json()
    assert by_date_asc[0]["entry_date"] == "2026-01-01"

    assert client.get("/api/entries", params={"sort": "bogus"}).status_code == 422


# ── Histograma cronologiei respectă aceleași filtre ca lista (2026-11) ──────

def test_timeline_buckets_honor_search_and_dates(client):
    _mk(client, "Memoria procedurală", entry_date="2026-02-01")
    _mk(client, "Alt subiect", entry_date="2026-02-02")

    all_buckets = client.get("/api/timeline/buckets", params={"bucket": "year"}).json()
    assert all_buckets["total"] == 2

    filtered = client.get("/api/timeline/buckets", params={"bucket": "year", "q": "memoria procedurala"}).json()
    assert filtered["total"] == 1, "bucket-urile trebuie să respecte căutarea diacritic-insensibilă"

    dated = client.get("/api/timeline/buckets",
                       params={"bucket": "year", "from_date": "2026-02-02", "to_date": "2026-02-02"}).json()
    assert dated["total"] == 1
