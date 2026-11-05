"""Choosing the topic is measured against the library, not praised blindly.

A topic is only worth proposing if the library can carry it. Two defects found
while building this, both pinned below:

  * the coverage count used `_score_study`, which adds a quality bonus — so
    every study scored above zero and every topic looked equally covered;
  * the topics are written in Romanian and the studies in English, so a literal
    term match found nothing and real topics came out "unsupported".
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("CHRONOKG_DB_PATH", str(db))
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    with TestClient(app) as c:
        yield c


def _coverage_of(client, fragment):
    body = client.get("/api/project/topics").json()
    for c in body["candidates"]:
        if fragment.lower() in c["title"].lower():
            return c["coverage"]
    raise AssertionError(f"no candidate matching {fragment!r}")


def test_quality_alone_does_not_count_as_coverage(client):
    """A rated study about something else is not coverage."""
    client.post("/api/entries", json={
        "title": "A highly rated paper about cooking pasta",
        "content": "Boil water, add salt, cook twelve minutes.",
        "study_type": "study", "tags": "study", "quality": 5,
    })
    cov = _coverage_of(client, "memoria")
    assert cov["matched"] == 0, "an unrelated study must not match on quality"
    assert cov["verdict"] == "nepotrivită"


def test_romanian_topic_matches_english_studies(client):
    """The library is English; a Romanian topic must still find it."""
    client.post("/api/entries", json={
        "title": "Mindfulness meditation and cortical thickness in healthy adults",
        "content": "Structural MRI shows increased cortical thickness after eight weeks.",
        "study_type": "study", "tags": "study,neuroscience",
    })
    cov = _coverage_of(client, "medita")
    assert cov["matched"] >= 1, "Romanian 'meditație' must find English 'meditation'"
    assert any("mindfulness" in t.lower() for t in cov["top_titles"])


def test_a_thin_library_gives_a_fragile_verdict(client):
    client.post("/api/entries", json={
        "title": "A single study on adverse childhood experience",
        "content": "One sample, one result.", "study_type": "study", "tags": "study",
    })
    cov = _coverage_of(client, "Experiențele adverse și reziliența")
    assert cov["verdict"] in ("fragilă", "posibilă")
    assert cov["note"]


def test_coverage_reports_what_can_be_cited(client):
    """DOI and body text are the two things a claim needs behind it."""
    client.post("/api/entries", json={
        "title": "Theory of mind in children after adversity",
        "content": "x" * 500, "study_type": "study", "tags": "study",
        "doi": "10.1038/nrn.2016.44",
    })
    cov = _coverage_of(client, "Theory of mind")
    assert cov["with_doi"] >= 1
    assert cov["with_body"] >= 1


def test_the_current_topic_is_measured_too(client):
    client.put("/api/project", json={"title": "My own odd topic about penguins",
                                     "research_q": "Why do penguins huddle?"})
    body = client.get("/api/project/topics").json()
    current = [c for c in body["candidates"] if c["source"] == "current"]
    assert len(current) == 1
    assert current[0]["coverage"]["verdict"] == "nepotrivită"


def test_candidates_are_ordered_by_what_the_library_can_carry(client):
    for i in range(6):
        client.post("/api/entries", json={
            "title": f"Theory of mind study {i}",
            "content": "text " * 60, "study_type": "study", "tags": "study",
        })
    body = client.get("/api/project/topics").json()
    order = {"solidă": 0, "posibilă": 1, "fragilă": 2, "nepotrivită": 3}
    ranks = [order[c["coverage"]["verdict"]] for c in body["candidates"]]
    assert ranks == sorted(ranks), "the best-supported topic must come first"


def test_choosing_a_topic_writes_title_and_question(client):
    res = client.post("/api/project/topics/choose", json={
        "title": "Reziliența la adolescenți după adversitate",
        "question": "Cum se leagă adversitatea de reziliență la adolescenți?",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Reziliența la adolescenți după adversitate"
    assert body["research_q"].startswith("Cum se leagă")

    project = client.get("/api/project").json()
    assert project["title"] == body["title"]


def test_choosing_a_topic_drops_the_stale_analysis(client, monkeypatch):
    """An analysis written for the old topic must not be shown as current."""
    import app.routers.project as project

    async def fake_llm(messages, **kwargs):
        return "analiză pentru tema veche"

    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    client.put("/api/project", json={"title": "Tema veche despre somn"})
    client.post("/api/project/analysis")

    client.post("/api/project/topics/choose", json={
        "title": "Tema nouă despre reziliență", "question": "Ce face reziliența?",
    })
    body = client.get("/api/project/analysis").json()
    assert body["cached"] is False
    assert body["needs_generation"] is True


def test_one_generic_word_is_not_a_match(client):
    """'structur' appears everywhere; a lone generic word must not count."""
    client.post("/api/entries", json={
        "title": "The structure of intelligence in children",
        "content": "Factor analysis of cognitive tests.", "study_type": "study", "tags": "study",
    })
    cov = _coverage_of(client, "Modificări structurale cerebrale")
    titles = " ".join(cov["top_titles"]).lower()
    assert "intelligence" not in titles, "a single generic word must not make a match"


def test_entries_without_study_type_still_count(client):
    """Notes and summaries carry a chapter too, so they must be measured."""
    client.post("/api/entries", json={
        "title": "Notă despre conștiință și experiența subiectivă",
        "content": "y" * 400,
        # no study_type, no tags: an ordinary entry
    })
    cov = _coverage_of(client, "Conștiința")
    assert cov["matched"] >= 1, "a plain entry must be able to support a topic"
    assert cov["with_body"] >= 1


def test_a_too_short_topic_is_refused(client):
    assert client.post("/api/project/topics/choose", json={"title": "ab"}).status_code == 422
