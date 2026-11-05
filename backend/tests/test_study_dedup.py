"""
Tests for study identity.

The real library had 10 duplicate pairs because `_save_research` compared
normalized titles for equality. These tests pin both halves of the fix: the
cases that must merge, and — just as important — the cases that must NOT merge,
because one PDF link in the real library is attached to six different papers.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.study_dedup import (
    is_truncated,
    normalize_title,
    normalize_url,
    same_study,
    titles_match,
)

LONG = ("Mindfulness and the brain: A systematic review of the effects of "
        "meditation on brain structure and function")
CUT = "Mindfulness and the brain: A systematic review of ..."
CUT_MID_WORD = "Mindfulness and the brain: A systematic rev"


# ─────────────── normalisation ───────────────

def test_normalize_title_folds_case_accents_and_punctuation():
    assert normalize_title("The  Structure of Memory (Tulving, 1972)!") == \
        "the structure of memory tulving 1972"
    assert normalize_title("Rezilienţa şi adversitatea") == "rezilienta si adversitatea"
    assert normalize_title("") == ""
    assert normalize_title(None) == ""


def test_normalize_title_drops_a_truncation_marker():
    assert normalize_title(CUT) == "mindfulness and the brain a systematic review of"
    assert normalize_title("Some paper…") == "some paper"
    assert normalize_title("Some paper...") == "some paper"


def test_is_truncated_detects_both_ellipsis_forms():
    assert is_truncated(CUT) is True
    assert is_truncated("Some paper…") is True
    assert is_truncated(LONG) is False
    assert is_truncated("") is False


def test_normalize_url_ignores_scheme_query_and_trailing_slash():
    a = normalize_url("https://www.sciencedirect.com/science/article/pii/S2212958826000066")
    b = normalize_url("http://sciencedirect.com/science/article/pii/S2212958826000066/?utm=x#top")
    assert a == b
    assert normalize_url("") == ""


# ─────────────── titles_match ───────────────

def test_identical_titles_match():
    assert titles_match(LONG, LONG) is True
    assert titles_match(LONG, LONG.upper()) is True
    assert titles_match(LONG, LONG + ".") is True


def test_a_truncated_title_matches_its_full_version():
    assert titles_match(CUT, LONG) is True
    assert titles_match(LONG, CUT) is True


def test_a_title_cut_inside_a_word_still_matches():
    assert titles_match(CUT_MID_WORD, LONG) is True


def test_near_identical_wording_matches():
    assert titles_match(
        "Mindfulness and the brain: a systematic review of the effects of meditation",
        "Mindfulness and the brain: A systematic review of the effects of meditation on brain",
    ) is True


def test_unrelated_titles_do_not_match():
    assert titles_match("Working Memory (Baddeley & Hitch 1974)", "The g factor") is False
    assert titles_match(LONG, "Mindfulness practice and increases in gray matter density") is False


def test_a_short_truncation_is_not_a_match_for_everything():
    """"..." alone must not turn into a wildcard matching any title."""
    assert titles_match("...", LONG) is False
    assert titles_match("Mindfulness ...", "Memory and emotion") is False


def test_empty_titles_never_match():
    assert titles_match("", "") is False
    assert titles_match("", LONG) is False


# ─────────────── same_study ───────────────

def test_same_title_different_urls_is_still_the_same_study():
    """Two publishers' pages for one paper: the title decides."""
    assert same_study(LONG, "https://a.org/x", LONG, "https://b.org/y") is True


def test_same_url_with_a_related_title_is_the_same_study():
    url = "https://www.mdpi.com/2076-3425/16/5/483"
    assert same_study(
        "Mindfulness Practice and Increases in Gray Matter Density, Gray Matter Volume",
        url,
        "Mindfulness Practice and Increases in Gray Matter Density, Gray ...",
        url,
    ) is True


def test_same_url_with_an_unrelated_title_is_not_merged():
    """A shared PDF link is not proof: one link serves six different papers."""
    url = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3181763/pdf/nihms-317840.pdf"
    assert same_study("Schizophrenia and Dopamine (Kapur)", url,
                      "Executive Functions (Miyake et al. 2000)", url) is False
    assert same_study("The Emotional Brain (LeDoux 1996)", url,
                      "Multiple Memory Systems (Squire 2004)", url) is False


def test_different_urls_and_different_titles_are_not_the_same():
    assert same_study("Paper one", "https://a/1", "Paper two", "https://b/2") is False


def test_blank_url_does_not_glue_unrelated_rows_together():
    assert same_study("Paper one", "", "Paper two", "") is False


def test_missing_titles_never_merge_on_url_alone():
    assert same_study("", "https://x/1", "", "https://x/1") is False


# ─────────────── the fix, end to end ───────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "dedup.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def _save(client, title, url="https://www.sciencedirect.com/science/article/pii/S2212958826000066",
          year=2026):
    from app.models import ResearchResult, ResearchStudy
    from app.routers.research import _save_research

    _save_research(ResearchResult(
        topic="mindfulness", summary="s", synthesis="x",
        studies=[ResearchStudy(title=title, authors="Unknown", year=year,
                               study_type="peer-review", url=url)],
    ), "mindfulness")


def test_a_truncated_retitle_no_longer_duplicates_the_entry(client):
    """The exact defect found on 2026-10-25."""
    _save(client, LONG)
    _save(client, CUT)

    studies = [r for r in client.get("/api/entries").json() if r["study_type"] == "peer-review"]
    assert len(studies) == 1, "the cut-off title must merge into the existing entry"
    assert studies[0]["title"] == LONG, "the complete title is kept"


def test_repeated_research_merges_a_near_identical_title(client):
    _save(client, LONG)
    _save(client, LONG.replace("meditation", "meditations"))
    studies = [r for r in client.get("/api/entries").json() if r["study_type"] == "peer-review"]
    assert len(studies) == 1


def test_six_papers_sharing_one_pdf_link_stay_six_entries(client):
    """The real library's PMC3181763 link is attached to six different papers."""
    link = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3181763/pdf/nihms-317840.pdf"
    for title in ("Schizophrenia and Dopamine (Kapur)",
                  "Multiple Memory Systems (Squire 2004)",
                  "The Attention System of the Brain (Posner & Petersen 1990)",
                  "Executive Functions (Miyake et al. 2000)",
                  "Dual-Process Theories (Evans & Stanovich 2013)",
                  "The Emotional Brain (LeDoux 1996)"):
        _save(client, title, url=link)

    studies = [r for r in client.get("/api/entries").json() if r["study_type"] == "peer-review"]
    assert len(studies) == 6, "different papers must never be merged for sharing a link"


# ─────────────── fusing the duplicates that already exist ───────────────

def _reanalysis(client, seed=0):
    res = client.post("/api/maintenance/analyze")
    import json
    done = [json.loads(l[6:]) for l in res.text.splitlines()
            if l.startswith("data: ") and '"done"' in l][0]
    return done["report"]


def _two_duplicates(client, keep_title=LONG, dup_title=CUT):
    keep = client.post("/api/entries", json={
        "title": keep_title, "study_type": "peer-review", "tags": "study",
    }).json()
    dup = client.post("/api/entries", json={
        "title": dup_title, "study_type": "peer-review", "tags": "study",
        "source": "https://www.sciencedirect.com/science/article/pii/S2212958826000066",
    }).json()
    return keep, dup


def test_maintenance_fuses_a_truncated_duplicate(client):
    keep, dup = _two_duplicates(client)
    report = _reanalysis(client)

    assert report["duplicates_merged"] == 1
    assert report["duplicate_groups"] == 1
    rows = client.get("/api/entries").json()
    assert [r["id"] for r in rows] == [keep["id"]], "the oldest row survives"
    assert rows[0]["title"] == LONG, "the complete title is kept"
    assert rows[0]["source"], "the duplicate's source URL is carried over"


def test_fusing_moves_every_link_onto_the_surviving_entry(client):
    from app.db import get_db

    keep, dup = _two_duplicates(client)
    client.post(f"/api/entries/{dup['id']}/todo", json={})
    client.post(f"/api/entries/{dup['id']}/reminders", json={"trigger_at": "2027-01-01T09:00:00"})
    client.post(f"/api/spaced-repetition/items/{dup['id']}")
    other = client.post("/api/entries", json={"title": "Some other note"}).json()
    client.post("/api/relationships", json={"from_id": dup["id"], "to_id": other["id"], "label": "x"})
    theory = client.post("/api/theories", json={"name": "Mindfulness"}).json()
    client.post(f"/api/theories/{theory['id']}/evidence",
                json={"entry_id": dup["id"], "stance": "pro"})

    _reanalysis(client)

    conn = get_db()
    try:
        for table, column in (("todos", "entry_id"), ("reminders", "entry_id"),
                              ("spaced_repetition", "entry_id"), ("theory_evidence", "entry_id")):
            n = conn.execute(f"SELECT COUNT(*) n FROM {table} WHERE {column} = ?", [keep["id"]]).fetchone()["n"]
            assert n == 1, f"{table} link was lost when the duplicate was removed"
            assert conn.execute(f"SELECT COUNT(*) n FROM {table} WHERE {column} = ?",
                                [dup["id"]]).fetchone()["n"] == 0
        rels = conn.execute("SELECT from_id, to_id FROM relationships").fetchall()
        assert [(r["from_id"], r["to_id"]) for r in rels] == [(keep["id"], other["id"])]
        assert conn.execute("SELECT COUNT(*) n FROM entries WHERE id = ?", [dup["id"]]).fetchone()["n"] == 0
    finally:
        conn.close()


def test_fusing_keeps_both_rows_text_together(client):
    keep = client.post("/api/entries", json={
        "title": LONG, "study_type": "peer-review", "content": "original abstract",
    }).json()
    client.post("/api/entries", json={
        "title": CUT, "study_type": "peer-review", "content": "re-analysis findings",
    })
    _reanalysis(client)
    body = client.get(f"/api/entries/{keep['id']}").json()["content"]
    assert "original abstract" in body and "re-analysis findings" in body


def test_a_single_todo_is_not_duplicated_when_both_rows_have_one(client):
    from app.db import get_db

    keep, dup = _two_duplicates(client)
    client.post(f"/api/entries/{keep['id']}/todo", json={"priority": "critical"})
    client.post(f"/api/entries/{dup['id']}/todo", json={"priority": "normal"})
    _reanalysis(client)

    conn = get_db()
    try:
        rows = conn.execute("SELECT entry_id, priority FROM todos").fetchall()
        assert len(rows) == 1, "one todo per entry is the schema's rule"
        assert rows[0]["entry_id"] == keep["id"]
        assert rows[0]["priority"] == "critical", "the survivor's own todo is kept"
    finally:
        conn.close()


def test_different_papers_sharing_a_pdf_link_are_not_fused(client):
    link = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3181763/pdf/nihms-317840.pdf"
    for title in ("Schizophrenia and Dopamine (Kapur)",
                  "Multiple Memory Systems (Squire 2004)",
                  "The Emotional Brain (LeDoux 1996)"):
        client.post("/api/entries", json={"title": title, "study_type": "peer-review", "source": link})

    report = _reanalysis(client)
    assert report["duplicates_merged"] == 0
    assert len(client.get("/api/entries").json()) == 3


def test_fusing_is_idempotent(client):
    _two_duplicates(client)
    first = _reanalysis(client)
    second = _reanalysis(client)
    assert first["duplicates_merged"] == 1
    assert second["duplicates_merged"] == 0, "a second run finds nothing left to fuse"


def test_fusing_survives_identical_relationships_on_both_rows(client):
    """UNIQUE(from_id, to_id, label) is hit when both rows already link the same
    target — found by running the fusion against the real database."""
    from app.db import get_db

    keep, dup = _two_duplicates(client)
    other = client.post("/api/entries", json={"title": "Target"}).json()
    for eid in (keep["id"], dup["id"]):
        client.post("/api/relationships", json={"from_id": eid, "to_id": other["id"], "label": "supports"})
        client.post("/api/relationships", json={"from_id": other["id"], "to_id": eid, "label": "cites"})

    report = _reanalysis(client)
    assert report["duplicates_merged"] == 1

    conn = get_db()
    try:
        rows = conn.execute("SELECT from_id, to_id, label FROM relationships ORDER BY label").fetchall()
    finally:
        conn.close()
    pairs = [(r["from_id"], r["to_id"], r["label"]) for r in rows]
    # "cites" is not part of the canonical vocabulary, so the API normalises it
    # to relates_to with the original word kept as the detail. The point of the
    # test is that exactly one copy of each link survives.
    assert pairs == [(other["id"], keep["id"], "relates_to | cites"),
                     (keep["id"], other["id"], "supports")], "one copy of each link survives"


def test_fusing_survives_identical_theory_evidence(client):
    from app.db import get_db

    keep, dup = _two_duplicates(client)
    theory = client.post("/api/theories", json={"name": "T"}).json()
    for eid in (keep["id"], dup["id"]):
        client.post(f"/api/theories/{theory['id']}/evidence",
                    json={"entry_id": eid, "stance": "pro"})

    _reanalysis(client)
    conn = get_db()
    try:
        rows = conn.execute("SELECT entry_id, stance FROM theory_evidence").fetchall()
    finally:
        conn.close()
    assert [(r["entry_id"], r["stance"]) for r in rows] == [(keep["id"], "pro")]
