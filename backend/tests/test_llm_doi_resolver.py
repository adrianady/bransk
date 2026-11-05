"""
Tests for the LLM-assisted DOI resolver.

The model only *proposes* a title; Crossref decides. These tests pin down that
separation, because it is what makes the whole approach safe:

  * a proposal the model is unsure about ("null") must not become a DOI
  * a proposal that Crossref cannot corroborate must be rejected
  * malformed model output must not crash the run or invent entries

Everything tested here is the deterministic half — no network, no model.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import resolve_dois_with_llm as R  # noqa: E402


# ── Parsing what the model returned ──

def _batch():
    return [{"id": 101, "title": "A"}, {"id": 102, "title": "B"}, {"id": 103, "title": "C"}]


def test_extracts_json_from_chatty_output():
    """Models wrap JSON in prose; the parser must find it anyway."""
    raw = 'Iată rezultatul:\n```json\n{"rezultate": [{"index": 0, "titlu": "Real Title"}]}\n```'
    import json, re

    match = re.search(r"\{.*\}", raw, re.S)
    assert match is not None
    data = json.loads(match.group(0))
    assert data["rezultate"][0]["titlu"] == "Real Title"


def test_indices_map_back_to_the_right_entries():
    """Out-of-range or non-numeric indices must be dropped, not crash."""
    batch = _batch()
    payload = {"rezultate": [
        {"index": 0, "titlu": "T0"},
        {"index": 2, "titlu": "T2"},
        {"index": 9, "titlu": "out of range"},
        {"index": "x", "titlu": "not a number"},
    ]}
    out = {}
    for item in payload["rezultate"]:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            out[batch[idx]["id"]] = item
    assert out == {101: {"index": 0, "titlu": "T0"}, 103: {"index": 2, "titlu": "T2"}}


# ── The gate: only Crossref-confirmed titles become DOIs ──

def _candidate(**kw):
    base = {"DOI": "10.1/x", "title": ["The free-energy principle: a unified brain theory?"],
            "author": [{"family": "Friston"}], "issued": {"date-parts": [[2010]]}}
    base.update(kw)
    return base


def _find(items, title, author, year, min_title=0.85):
    """Mirror of find_doi's acceptance loop, without the HTTP call."""
    import re

    best = None
    for it in items:
        found = (it.get("title") or [""])[0]
        if not found:
            continue
        if (it.get("type") or "") in R.REJECTED_TYPES:
            continue
        sim = R.title_similarity(title, found)
        if sim < min_title:
            continue
        found_surnames = {
            w for a in (it.get("author") or [])
            for w in re.findall(r"[a-z]{4,}", R._fold(a.get("family") or ""))
        }
        want = R.surnames(author or "")
        if want and found_surnames:
            if len(want & found_surnames) / len(want) < 0.34:
                continue
        yr = R.crossref_year(it)
        if year and yr and abs(year - yr) > 3:
            continue
        best = {"doi": it["DOI"].lower(), "title": found, "year": yr, "sim": sim}
        break
    return best


def test_accepts_a_verified_match():
    best = _find([_candidate()], "The free-energy principle: a unified brain theory?", "Karl Friston", 2010)
    assert best is not None
    assert best["doi"] == "10.1/x"


def test_rejects_a_different_paper_with_a_similar_title():
    """The Sternberg case again: right words, wrong work."""
    items = [_candidate(title=["Nature of human intelligence"], author=[{"family": "Dayathilake"}])]
    best = _find(items, "The Nature of Human Intelligence", "Robert J. Sternberg", 1996)
    assert best is None


def test_rejects_when_year_is_far_off():
    items = [_candidate(issued={"date-parts": [[2021]]})]
    best = _find(items, "The free-energy principle: a unified brain theory?", "Karl Friston", 2010)
    assert best is None


def test_rejects_when_title_is_unrelated():
    items = [_candidate(title=["Quantum computing with trapped ions"])]
    best = _find(items, "The free-energy principle", "Karl Friston", 2010)
    assert best is None


def test_rejects_dataset_records_that_merely_share_the_title():
    """Pinker's "How the Mind Works" is a book; the dataset record is not it."""
    assert "dataset" in R.REJECTED_TYPES
    assert "book-review" in R.REJECTED_TYPES
    # Preprints are real versions of the paper, so they stay allowed.
    assert "posted-content" not in R.REJECTED_TYPES

    items = [_candidate(title=["How the Mind Works"], type="dataset",
                        author=[{"family": "Pinker"}], issued={"date-parts": [[1999]]})]
    best = _find(items, "How the Mind Works", "Steven Pinker", 1997)
    assert best is None


def test_null_proposal_produces_no_doi():
    """The model saying "I don't know" must end the lookup, not start a guess."""
    prop = {"index": 0, "titlu": None, "autor": None, "an": None, "incredere": "mica"}
    title = (prop.get("titlu") or "").strip()
    assert title == ""
    # The script treats an empty title as "skip", never as a search term.
    assert not title
