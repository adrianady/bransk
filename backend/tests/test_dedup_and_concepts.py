"""Hermetic tests: pdf_hash dedup, /studies/duplicates+merge, read_status, graph filters, AI concepts."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.studies_store import compute_pdf_hash, find_entry_by_pdf_hash


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "dedup2.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def _entry(client, **kwargs):
    base = {"title": kwargs.pop("title", "Paper")}
    base.update(kwargs)
    return client.post("/api/entries", json=base).json()


def test_compute_pdf_hash_is_sha256():
    import hashlib

    assert compute_pdf_hash(b"%PDF-1.4 x") == hashlib.sha256(b"%PDF-1.4 x").hexdigest()
    assert compute_pdf_hash(b"") == ""


def test_upload_pdf_sets_hash_and_warns_on_duplicate(client):
    from app.db import get_db

    pdf = b"%PDF-1.4 fake content for hash test"
    a = _entry(client, title="Study A", study_type="paper")
    b = _entry(client, title="Study B", study_type="paper")

    r1 = client.post(f"/api/entries/{a['id']}/pdf", files={"file": ("a.pdf", pdf, "application/pdf")})
    assert r1.status_code == 200, r1.text
    assert r1.json()["pdf_hash"] == compute_pdf_hash(pdf)
    assert "duplicate_entry_id" not in r1.json()

    r2 = client.post(f"/api/entries/{b['id']}/pdf", files={"file": ("b.pdf", pdf, "application/pdf")})
    assert r2.status_code == 200, r2.text
    assert r2.json()["duplicate_entry_id"] == a["id"]

    conn = get_db()
    try:
        assert find_entry_by_pdf_hash(conn, compute_pdf_hash(pdf), exclude_id=b["id"])["id"] == a["id"]
    finally:
        conn.close()


def test_duplicates_endpoint_groups_by_hash_doi_and_title(client):
    pdf = b"%PDF-1.4 same-bytes"
    a = _entry(client, title="Deep Learning for Memory", study_type="paper",
               doi="10.1038/nrn.2016.44")
    b = _entry(client, title="Deep learning for memory.", study_type="paper",
               doi="10.1038/nrn.2016.44")
    client.post(f"/api/entries/{a['id']}/pdf", files={"file": ("a.pdf", pdf, "application/pdf")})
    client.post(f"/api/entries/{b['id']}/pdf", files={"file": ("b.pdf", pdf, "application/pdf")})
    # A title-only pair (no pdf/doi in common).
    c = _entry(client, title="Mindfulness and the brain: A systematic review of the effects of meditation on brain structure and function", study_type="paper")
    d = _entry(client, title="Mindfulness and the brain: A systematic review of ...", study_type="paper")

    res = client.get("/api/studies/duplicates").json()
    groups = res["duplicate_groups"]
    assert len(groups) >= 2
    assert res["total_groups"] == len(groups)
    assert res["total_duplicates"] >= 2
    flat = [sorted(e["id"] for e in g["entries"]) for g in groups]
    assert sorted([a["id"], b["id"]]) in flat
    assert sorted([c["id"], d["id"]]) in flat


def test_merge_moves_links_and_archives_duplicate(client):
    from app.db import get_db

    keep = _entry(client, title="Keep paper", study_type="paper", tags="alpha")
    dup = _entry(client, title="Dup paper", study_type="paper", tags="beta")
    other = _entry(client, title="Other note")
    client.post(f"/api/studies/{dup['id']}/notes", json={"content": "dup note"})
    client.post(f"/api/studies/{dup['id']}/highlights",
                json={"quote": "q", "page_number": 3})
    client.post("/api/relationships",
                json={"from_id": dup["id"], "to_id": other["id"], "label": "supports"})
    # Self-loop-prone edge: dup -> keep must not survive as keep -> keep.
    client.post("/api/relationships",
                json={"from_id": dup["id"], "to_id": keep["id"], "label": "relates_to"})

    res = client.post("/api/studies/merge",
                      json={"keep_id": keep["id"], "duplicate_id": dup["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["success"] is True

    conn = get_db()
    try:
        assert conn.execute("SELECT archived FROM entries WHERE id = ?",
                            [dup["id"]]).fetchone()["archived"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM study_notes WHERE entry_id = ?",
                            [keep["id"]]).fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM pdf_highlights WHERE entry_id = ?",
                            [keep["id"]]).fetchone()["n"] == 1
        rels = conn.execute("SELECT from_id, to_id FROM relationships").fetchall()
        assert all(r["from_id"] != r["to_id"] for r in rels)
        assert any((r["from_id"], r["to_id"]) == (keep["id"], other["id"]) for r in rels)
        tags = conn.execute("SELECT tags FROM entries WHERE id = ?",
                            [keep["id"]]).fetchone()["tags"]
        assert "alpha" in tags and "beta" in tags
    finally:
        conn.close()

    # Merged-away row no longer shows up as a duplicate.
    res = client.get("/api/studies/duplicates").json()
    assert all(keep["id"] not in [e["id"] for e in g["entries"]] or
               dup["id"] not in [e["id"] for e in g["entries"]]
               for g in res["duplicate_groups"])


def test_read_status_patch_and_put(client):
    e = _entry(client, title="Status paper", study_type="paper")
    r = client.patch(f"/api/entries/{e['id']}/status", json={"read_status": "read"})
    assert r.status_code == 200, r.text
    assert r.json()["read_status"] == "read"

    bad = client.patch(f"/api/entries/{e['id']}/status", json={"read_status": "bogus"})
    assert bad.status_code == 400

    put = client.put(f"/api/entries/{e['id']}",
                     json={"read_status": "annotated", "isbn": "978-0-123456-78-9"})
    assert put.status_code == 200, put.text
    assert put.json()["read_status"] == "annotated"
    assert put.json()["isbn"] == "978-0-123456-78-9"


def test_graph_filters_only_read_and_study_type(client):
    e1 = _entry(client, title="Read paper", study_type="paper")
    e2 = _entry(client, title="Unread paper", study_type="paper")
    e3 = _entry(client, title="A book", study_type="book")
    client.patch(f"/api/entries/{e1['id']}/status", json={"read_status": "read"})
    client.post("/api/relationships",
                json={"from_id": e1["id"], "to_id": e2["id"], "label": "relates_to"})

    full = client.get("/api/relationships/graph").json()
    assert {n["id"] for n in full["nodes"]} >= {e1["id"], e2["id"], e3["id"]}

    only = client.get("/api/relationships/graph", params={"only_read": True}).json()
    ids = {n["id"] for n in only["nodes"]}
    assert e1["id"] in ids and e2["id"] not in ids

    books = client.get("/api/relationships/graph", params={"study_type": "book"}).json()
    assert {n["id"] for n in books["nodes"]} == {e3["id"]}
    assert books["edges"] == []


def test_ai_extract_concepts_mock_and_project_listing(client):
    e = _entry(client, title="Concept source", study_type="paper")
    client.post(f"/api/studies/{e['id']}/notes", json={"content": "stress impairs memory encoding"})
    res = client.post("/api/ai/extract-concepts", json={"entry_ids": [e["id"]]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["concepts"]) >= 2
    assert body["project_id"] is not None

    listing = client.get(f"/api/project/{body['project_id']}/concepts").json()
    assert len(listing["concepts"]) >= 2
    assert all("evidence" in c for c in listing["concepts"])


def test_concept_synthesis_is_idempotent(client):
    e = _entry(client, title="Resilience study", study_type="peer-reviewed paper")
    client.post(f"/api/studies/{e['id']}/notes", json={"content": "social support buffers stress"})
    first = client.post("/api/ai/extract-concepts", json={"entry_ids": [e["id"]]}).json()
    second = client.post("/api/ai/extract-concepts", json={"entry_ids": [e["id"]]}).json()

    assert [c["id"] for c in first["concepts"]] == [c["id"] for c in second["concepts"]]
    listing = client.get(f"/api/project/{first['project_id']}/concepts").json()
    names = [c["name"] for c in listing["concepts"]]
    assert len(names) == len(set(names))

    graph = client.get("/api/relationships/graph", params={"study_type": "concept"}).json()
    concept_nodes = [n for n in graph["nodes"] if n.get("study_type") == "concept"]
    assert len(concept_nodes) == len(set(n["label"] for n in concept_nodes))


def test_concept_map_keeps_source_studies(client):
    e = _entry(client, title="Family study", study_type="peer-reviewed paper")
    client.post(f"/api/studies/{e['id']}/notes",
                json={"content": "family is a social environment factor"})
    client.patch(f"/api/entries/{e['id']}/status", json={"read_status": "read"})

    body = client.post("/api/ai/extract-concepts", json={"entry_ids": [e["id"]]}).json()
    assert body["concepts"], body

    graph = client.get("/api/relationships/graph", params={"study_type": "concept"}).json()
    labels = {n["label"] for n in graph["nodes"]}
    assert "Main factor" in labels
    # The grounding study is pulled in even though it is not a concept.
    assert "Family study" in labels
    assert any(
        ed["label"] == "derives_from" and ed["to"] == e["id"]
        for ed in graph["edges"]
    )


def test_concept_map_scopes_to_project(client):
    e1 = _entry(client, title="Study one", study_type="peer-reviewed paper")
    client.post(f"/api/studies/{e1['id']}/notes", json={"content": "family as social environment"})
    first = client.post("/api/ai/extract-concepts", json={"entry_ids": [e1["id"]]}).json()
    project_one = first["project_id"]

    created = client.post("/api/projects", json={"title": "Second project"}).json()
    second_pid = created["id"]
    e2 = _entry(client, title="Study two", study_type="peer-reviewed paper")
    client.post(f"/api/studies/{e2['id']}/notes", json={"content": "school climate matters"})
    client.post("/api/ai/extract-concepts",
                json={"entry_ids": [e2["id"]], "project_id": second_pid})

    scoped = client.get("/api/relationships/graph", params={
        "study_type": "concept", "project_id": project_one,
    }).json()
    labels = {n["label"] for n in scoped["nodes"]}
    assert "Main factor" in labels
    assert "Study one" in labels


def test_extract_pdf_metadata_suggests_book_and_fields(tmp_path):
    """A dropped PDF's own metadata + first page seeding title/DOI/ISBN/type."""
    pymupdf = pytest.importorskip("pymupdf")

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Social Environment and the Family")
    page.insert_text((72, 100), "Maria Ionescu")
    page.insert_text((72, 130), "ISBN 978-0-123456-78-9")
    page.insert_text((72, 160), "Editura Academiei, 2021")
    doc.set_metadata({"title": "Social Environment and the Family", "author": "Maria Ionescu"})
    pdf_bytes = doc.tobytes()
    doc.close()

    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        res = c.post(
            "/api/studies/extract-pdf-metadata",
            files={"file": ("book.pdf", pdf_bytes, "application/pdf")},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["title"].startswith("Social Environment")
        assert "Maria" in body["authors"]
        assert body["isbn"] == "9780123456789"
        assert body["study_type"] == "book"
        assert body["page_count"] >= 1

        bad = c.post(
            "/api/studies/extract-pdf-metadata",
            files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
        )
        assert bad.status_code == 400
