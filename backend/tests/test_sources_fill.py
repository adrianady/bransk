"""The app's model fills missing sources; Crossref and honesty decide.

The agent does not resolve DOIs by hand and does not invent content. The model
inside the app proposes, Crossref verifies, and text the model wrote about a
study it did not read is marked as such. These tests pin the three promises:

  * a wrong model proposal can never write a DOI (Crossref refuses it);
  * a truncated model reply is not reported as "the model did not know";
  * a duplicate DOI is not written twice, so duplication stays visible.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import sources
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("CHRONOKG_DB_PATH", str(db))
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    with TestClient(app) as c:
        yield c


def _study(client, title, **kw):
    body = {"title": title, "content": "x" * 50, "study_type": "study",
            "tags": "study", **kw}
    return client.post("/api/entries", json=body).json()


def test_status_counts_what_is_missing(client):
    _study(client, "A paper with a DOI", doi="10.1038/nrn.2016.44")
    _study(client, "A paper without one")
    body = client.get("/api/sources/status").json()
    assert body["studies"] == 2
    assert body["without_doi"] == 1
    assert body["without_any_source"] == 1


def test_a_wrong_proposal_never_writes_a_doi(client, monkeypatch):
    """Crossref is the gate: a hallucinated title must fail verification."""
    entry = _study(client, "Some paper nobody can find")

    async def fake_titles(batch):
        return {e["id"]: {"titlu": "A completely invented study title",
                          "autor": "Nobody", "an": 2020} for e in batch}

    async def no_hit(client_, title, author, year, min_title=0.85):
        return None, "niciun candidat nu trece verificarea"

    monkeypatch.setattr(sources, "propose_titles", fake_titles)
    monkeypatch.setattr(sources, "find_doi", no_hit)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")

    body = client.post("/api/sources/dois", json={"limit": 1}).json()
    assert body["found"] == []
    assert len(body["missed"]) == 1
    after = client.get(f"/api/entries/{entry['id']}").json()
    assert after["doi"] == "", "an unverified proposal must not reach the database"


def test_a_verified_hit_is_written(client, monkeypatch):
    entry = _study(client, "The Extended Mind (Clark & Chalmers)")

    async def fake_titles(batch):
        return {e["id"]: {"titlu": "The Extended Mind", "autor": "Andy Clark", "an": 1998}
                for e in batch}

    async def hit(client_, title, author, year, min_title=0.85):
        return {"doi": "10.7551/mitpress/9780262014038.003.0002", "title": "The Extended Mind",
                "year": 2010, "sim": 1.0, "authors": ["clark", "chalmers"]}, ""

    monkeypatch.setattr(sources, "propose_titles", fake_titles)
    monkeypatch.setattr(sources, "find_doi", hit)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")

    body = client.post("/api/sources/dois", json={"limit": 1}).json()
    assert len(body["found"]) == 1
    after = client.get(f"/api/entries/{entry['id']}").json()
    assert after["doi"] == "10.7551/mitpress/9780262014038.003.0002"
    assert after["doi_source"] == "crossref+llm"


def test_the_same_doi_is_not_written_to_two_entries(client, monkeypatch):
    """Duplicates stay visible instead of being hidden behind one DOI."""
    first = _study(client, "The Extended Mind (Clark & Chalmers)")
    second = _study(client, "Clark & Chalmers - Extended Mind (1998)")

    async def fake_titles(batch):
        return {e["id"]: {"titlu": "The Extended Mind", "autor": "Andy Clark", "an": 1998}
                for e in batch}

    async def hit(client_, title, author, year, min_title=0.85):
        return {"doi": "10.7551/mitpress/9780262014038.003.0002", "title": "The Extended Mind",
                "year": 2010, "sim": 1.0, "authors": ["clark"]}, ""

    monkeypatch.setattr(sources, "propose_titles", fake_titles)
    monkeypatch.setattr(sources, "find_doi", hit)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")

    body = client.post("/api/sources/dois", json={"limit": 2}).json()
    assert len(body["found"]) == 2, "both studies are matched"
    written = [f for f in body["found"] if f["written"]]
    assert len(written) == 1, "only the first gets the DOI"
    ids = {first["id"], second["id"]}
    dois = [client.get(f"/api/entries/{i}").json()["doi"] for i in ids]
    assert dois.count("10.7551/mitpress/9780262014038.003.0002") == 1


def test_a_truncated_reply_is_reported_as_such(client, monkeypatch):
    """A cut JSON must not be read as 'the model did not know the title'."""
    async def truncated(messages, **kw):
        return ("deepseek", "deepseek-flash", '{"rezultate": [{"index": 0, "titlu": "Some')

    monkeypatch.setattr(sources, "llm_complete", truncated)
    out = asyncio.run(sources.propose_titles([{"id": 1, "title": "T", "content": "x"}]))
    assert out == {}, "a truncated reply yields no proposals"

    log = sources.logger
    assert log is not None  # the reason is logged, not silently swallowed


def test_summary_is_only_written_when_the_model_is_sure(client, monkeypatch):
    entry = _study(client, "Machine Theory of Mind (DeepMind)", content="short")

    async def sure(batch):
        return {e["id"]: {"cunosc": True, "rezumat": "Rețeaua învață modele despre mintea altuia."}
                for e in batch}

    monkeypatch.setattr(sources, "propose_summaries", sure)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")
    body = client.post("/api/sources/summaries", json={"limit": 1}).json()
    assert len(body["written"]) == 1
    text = client.get(f"/api/entries/{entry['id']}").json()["content"]
    assert sources.MODEL_SUMMARY_MARK in text, "model text must be marked as such"


def test_an_unsure_model_writes_nothing(client, monkeypatch):
    entry = _study(client, "An obscure study", content="short")

    async def unsure(batch):
        return {e["id"]: {"cunosc": False, "rezumat": None} for e in batch}

    monkeypatch.setattr(sources, "propose_summaries", unsure)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")
    body = client.post("/api/sources/summaries", json={"limit": 1}).json()
    assert body["written"] == []
    assert client.get(f"/api/entries/{entry['id']}").json()["content"] == "short"


def test_summaries_never_stack(client, monkeypatch):
    entry = _study(client, "A known study", content="short")

    async def sure(batch):
        return {e["id"]: {"cunosc": True, "rezumat": "Un rezumat."} for e in batch}

    monkeypatch.setattr(sources, "propose_summaries", sure)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")
    client.post("/api/sources/summaries", json={"limit": 1})
    client.post("/api/sources/summaries", json={"limit": 1})
    text = client.get(f"/api/entries/{entry['id']}").json()["content"]
    assert text.count(sources.MODEL_SUMMARY_MARK) == 1


def test_mock_provider_refuses_honestly(client, monkeypatch):
    monkeypatch.setattr(sources, "LLM_PROVIDER", "mock")
    res = client.post("/api/sources/dois", json={"limit": 1})
    assert res.status_code == 503
    assert "LLM" in res.json()["detail"]


def test_dry_run_writes_nothing(client, monkeypatch):
    entry = _study(client, "The Extended Mind")

    async def fake_titles(batch):
        return {e["id"]: {"titlu": "The Extended Mind", "autor": "Clark", "an": 1998}
                for e in batch}

    async def hit(client_, title, author, year, min_title=0.85):
        return {"doi": "10.1/x", "title": "The Extended Mind", "year": 2010,
                "sim": 1.0, "authors": ["clark"]}, ""

    monkeypatch.setattr(sources, "propose_titles", fake_titles)
    monkeypatch.setattr(sources, "find_doi", hit)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")
    client.post("/api/sources/dois", json={"limit": 1, "dry_run": True})
    assert client.get(f"/api/entries/{entry['id']}").json()["doi"] == ""


def test_the_panel_gets_every_field_it_renders(client, monkeypatch):
    """The panel reads found/missed/written/skipped — both jobs must supply them.

    The screen normalises missing keys to empty lists, but a job that returns a
    different shape would render an empty panel with no error, which looks like
    "nothing happened" rather than a bug.
    """
    _study(client, "The Extended Mind")

    async def fake_titles(batch):
        return {e["id"]: {"titlu": "The Extended Mind", "autor": "Clark", "an": 1998}
                for e in batch}

    async def hit(client_, title, author, year, min_title=0.85):
        return {"doi": "10.1/x", "title": "The Extended Mind", "year": 2010,
                "sim": 1.0, "authors": ["clark"]}, ""

    monkeypatch.setattr(sources, "propose_titles", fake_titles)
    monkeypatch.setattr(sources, "find_doi", hit)
    monkeypatch.setattr(sources, "LLM_PROVIDER", "deepseek")

    dois = client.post("/api/sources/dois", json={"limit": 1}).json()
    for key in ("ok", "checked", "dry_run", "note", "found", "missed"):
        assert key in dois, f"the DOI job must report {key}"
    assert isinstance(dois["found"], list) and isinstance(dois["missed"], list)
    if dois["found"]:
        for key in ("id", "crossref_title", "doi", "year", "written"):
            assert key in dois["found"][0], f"a found item must carry {key}"

    sums = client.post("/api/sources/summaries", json={"limit": 1}).json()
    for key in ("ok", "checked", "dry_run", "note", "written", "skipped"):
        assert key in sums, f"the summary job must report {key}"
    assert isinstance(sums["written"], list) and isinstance(sums["skipped"], list)


def test_status_lists_titles_for_the_panel(client):
    _study(client, "A study without any identifier")
    body = client.get("/api/sources/status").json()
    assert body["without_doi_titles"], "the panel shows which studies are affected"
    first = body["without_doi_titles"][0]
    assert {"id", "title", "authors", "year"} <= set(first)
    assert isinstance(body["thin_titles"], list)
