"""
Tests for the DOI field.

The column existed, was rendered by the UI and counted by the project analysis,
but no HTTP route could ever write it: DOIs in the library came only from the
one-off scripts in backend/scripts/. That made "studies with a DOI" a number the
app displayed and could not influence. These tests pin the write path, including
the format check that keeps a typo from pointing at another paper.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import normalize_doi


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "doi.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


# ─────────────── the format rule ───────────────

def test_a_bare_doi_is_accepted():
    assert normalize_doi("10.1038/nrn.2016.44") == "10.1038/nrn.2016.44"
    assert normalize_doi("  10.1000/xyz  ") == "10.1000/xyz"


def test_a_doi_url_is_reduced_to_the_bare_doi():
    assert normalize_doi("https://doi.org/10.1038/nrn.2016.44") == "10.1038/nrn.2016.44"
    assert normalize_doi("http://dx.doi.org/10.1000/abc") == "10.1000/abc"
    assert normalize_doi("doi.org/10.1000/abc") == "10.1000/abc"


def test_an_empty_doi_is_allowed_and_means_none():
    assert normalize_doi("") == ""
    assert normalize_doi(None) == ""


@pytest.mark.parametrize("bad", [
    "nrn.2016.44",                 # no registrant
    "11.1038/abc",                 # not the 10. prefix
    "10.1/abc",                    # registrant too short
    "https://example.org/paper",   # a URL, not a DOI
    "10.1038/",                    # no suffix
    "just some words",
])
def test_a_malformed_doi_is_refused(bad):
    with pytest.raises(ValueError):
        normalize_doi(bad)


# ─────────────── through the API ───────────────

def test_doi_can_be_set_when_creating_an_entry(client):
    created = client.post("/api/entries", json={
        "title": "A study", "study_type": "peer-review", "doi": "10.1038/nrn.2016.44",
    })
    assert created.status_code == 201
    assert created.json()["doi"] == "10.1038/nrn.2016.44"


def test_a_doi_url_is_normalised_on_create(client):
    body = client.post("/api/entries", json={
        "title": "A study", "doi": "https://doi.org/10.1000/abc",
    }).json()
    assert body["doi"] == "10.1000/abc", "the URL prefix is stripped, not stored"


def test_a_malformed_doi_is_rejected_on_create(client):
    res = client.post("/api/entries", json={"title": "x", "doi": "not a doi"})
    assert res.status_code == 422
    assert client.get("/api/entries").json() == [], "nothing was stored"


def test_doi_can_be_updated_and_marks_its_source(client):
    eid = client.post("/api/entries", json={"title": "A study"}).json()["id"]
    updated = client.put(f"/api/entries/{eid}", json={"doi": "10.1000/xyz"}).json()
    assert updated["doi"] == "10.1000/xyz"
    assert updated["doi_source"] == "manual", "a hand-entered DOI is marked as such"
    assert updated["doi_checked_at"], "and stamped with when it was entered"

    from app.db import get_db
    conn = get_db()
    try:
        stamp = conn.execute("SELECT doi_checked_at FROM entries WHERE id = ?", [eid]).fetchone()[0]
    finally:
        conn.close()
    assert stamp != "datetime('now')", "the SQL must run, not be stored as text"
    assert stamp[:2] == "20", f"expected a real timestamp, got {stamp!r}"


def test_a_malformed_doi_is_rejected_on_update(client):
    eid = client.post("/api/entries", json={"title": "x"}).json()["id"]
    assert client.put(f"/api/entries/{eid}", json={"doi": "10.1038"}).status_code == 422
    assert client.get(f"/api/entries/{eid}").json()["doi"] == ""


def test_an_empty_doi_clears_it(client):
    eid = client.post("/api/entries", json={"title": "x", "doi": "10.1000/abc"}).json()["id"]
    cleared = client.put(f"/api/entries/{eid}", json={"doi": ""}).json()
    assert cleared["doi"] == ""


def test_updating_something_else_leaves_the_doi_alone(client):
    eid = client.post("/api/entries", json={"title": "x", "doi": "10.1000/abc"}).json()["id"]
    updated = client.put(f"/api/entries/{eid}", json={"title": "renamed"}).json()
    assert updated["doi"] == "10.1000/abc"


def test_the_project_analysis_now_counts_a_doi_set_through_the_api(client):
    eid = client.post("/api/entries", json={
        "title": "Resilience study", "study_type": "peer-review",
    }).json()["id"]
    assert client.get("/api/project/analysis").json()["stats"]["studies_with_doi"] == 0

    client.put(f"/api/entries/{eid}", json={"doi": "10.1000/abc"})
    assert client.get("/api/project/analysis").json()["stats"]["studies_with_doi"] == 1
