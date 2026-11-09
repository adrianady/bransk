"""Tests for the citations router: BibTeX/RIS export, cite snippets, bibliography."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "citations.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def _study(client, title, **kw):
    payload = {"title": title, "study_type": "study", **kw}
    res = client.post("/api/entries", json=payload)
    assert res.status_code == 201
    return res.json()


def test_bibtex_export_contains_resolved_fields(client):
    _study(client, "Falling asleep", authors="Ioan Smith, Jane Roe et al.",
           entry_date="2021-05-01", doi="10.1038/s41586-021-03830-w")
    res = client.get("/api/citations/export", params={"format": "bibtex"})
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    body = res.text
    assert "@article{smith2021falling" in body
    assert "author = {Ioan Smith and Jane Roe and others}" in body
    assert "title = {Falling asleep}" in body
    assert "doi = {10.1038/s41586-021-03830-w}" in body
    assert "year = {2021}" in body


def test_bibtex_keys_stay_unique(client):
    # Same author+year+first title word: the raw keys would collide, so the
    # second one gets a suffix. (Titles differ: the API rejects duplicates.)
    _study(client, "Memory models", authors="A. One", entry_date="2021-01-01", doi="10.1000/bib-x")
    _study(client, "Memory loss", authors="A. One", entry_date="2021-01-02", doi="10.1000/bib-y")
    body = client.get("/api/citations/export", params={"format": "bibtex"}).text
    keys = [line for line in body.splitlines() if line.startswith("@article")]
    assert len(keys) == 2
    assert len({k.split("{")[1].rstrip(",") for k in keys}) == 2


def test_ris_export_shape(client):
    _study(client, "A paper", authors="Ioan Smith, Jane Roe",
           entry_date="2020-01-01", doi="10.1000/ris-abc")
    res = client.get("/api/citations/export", params={"format": "ris"})
    assert res.status_code == 200
    lines = res.text.splitlines()
    assert lines[0] == "TY  - JOUR"
    assert "TI  - A paper" in lines
    assert "AU  - Ioan Smith" in lines
    assert "DO  - 10.1000/ris-abc" in lines
    assert lines[-1] == "ER  - "


def test_export_filters_by_ids_and_doi(client):
    s1 = _study(client, "With doi", authors="A. One", entry_date="2021-01-01", doi="10.1000/cit-a")
    _study(client, "No doi", authors="B. Two", entry_date="2021-01-02")

    only_doi = client.get("/api/citations/export", params={"doi_only": "true"})
    assert "With doi" in only_doi.text and "No doi" not in only_doi.text

    picked = client.get("/api/citations/export", params={"ids": str(s1["id"])})
    assert "With doi" in picked.text and "No doi" not in picked.text

    bad = client.get("/api/citations/export", params={"ids": "abc"})
    assert bad.status_code == 400

    missing = client.get("/api/citations/export", params={"ids": "99999"})
    assert missing.status_code == 404


def test_cite_snippet_author_variants(client):
    one = _study(client, "One author", authors="Ioan Smith", entry_date="2021-01-01")
    two = _study(client, "Two authors", authors="Ioan Smith, Jane Roe", entry_date="2021-01-01")
    many = _study(client, "Many authors", authors="Ioan Smith, Jane Roe, Bob Lee et al.", entry_date="2021-01-01")
    assert " (Smith, 2021)" in client.get(f"/api/citations/cite/{one['id']}").text or \
           client.get(f"/api/citations/cite/{one['id']}").text == "(Smith, 2021)"
    assert client.get(f"/api/citations/cite/{two['id']}").text == "(Smith & Roe, 2021)"
    assert client.get(f"/api/citations/cite/{many['id']}").text == "(Smith et al., 2021)"
    pubmed = _study(client, "PubMed author style", authors="Descartes F, Doba K et al.", entry_date="2026-01-01")
    assert client.get(f"/api/citations/cite/{pubmed['id']}").text == "(Descartes et al., 2026)"
    editor = _study(client, "Edited book", authors="Robert J. Sternberg (Ed.)", entry_date="2018-01-01")
    assert client.get(f"/api/citations/cite/{editor['id']}").text == "(Sternberg, 2018)"


def test_bibliography_marks_cited_studies(client):
    _study(client, "Sleep and memory", authors="Ioan Smith, Jane Roe",
           entry_date="2021-01-01", doi="10.1000/sleep-01")
    _study(client, "Uncited work", authors="Bob Lee", entry_date="2020-01-01")
    client.put("/api/project", json={
        "structure": [{"id": "c1", "title": "Cap 1", "content": "Conform Smith (2021), somnul consolidează memoria."}],
    })
    data = client.get("/api/citations/bibliography").json()
    assert data["total"] == 2
    assert data["cited_count"] == 1
    cited = next(r for r in data["references"] if r["cited"])
    assert cited["title"] == "Sleep and memory"
    assert cited["apa"].startswith("Smith, I., & Roe, J. (2021). Sleep and memory.")
    assert cited["apa"].endswith("https://doi.org/10.1000/sleep-01")
    assert cited["in_text"] == "(Smith & Roe, 2021)"
