"""Slice 6 — passages + layer 1 signals (no LLM, no model)."""
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.stance.passages import (
    entry_passages,
    make_windows,
    page_passage,
    split_sentences,
)
from app.stance.signals import (
    DeterministicScorer,
    is_strong_signal,
    nli_availability,
    score_passage,
)


def _seed_page(entry_id, page, text):
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO page_texts
               (entry_id, page_number, text, source, status, char_count)
               VALUES (?, ?, ?, 'manual', 'manual', ?)""",
            [entry_id, page, text, len(text)],
        )
        conn.commit()
    finally:
        conn.close()


def _seed_highlight(entry_id, page, quote, start=None, end=None):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO pdf_highlights
               (entry_id, page_number, quote, start_offset, end_offset)
               VALUES (?, ?, ?, ?, ?)""",
            [entry_id, page, quote, start, end],
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _make_entry(client, title="Study"):
    resp = client.post("/api/entries", json={"title": title, "content": "c"})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── split_sentences ──

def test_split_sentences_handles_abbreviations_decimals_and_breaks():
    text = (
        "Multe studii (e.g. Smith, 2020) arată un efect clar. "
        "Autorii (et al.) au măsurat 0.05 ca prag.\n\n"
        "Al doilea paragraf începe aici."
    )
    sentences = split_sentences(text)
    # e.g. and et al. must not end a sentence; 0.05 must stay intact.
    assert any("e.g. Smith, 2020" in s for s in sentences)
    assert any("0.05" in s for s in sentences)
    assert not any(s.startswith("2020)") for s in sentences)
    assert len(sentences) == 3
    assert sentences[-1] == "Al doilea paragraf începe aici."


def test_split_sentences_normal_three_sentence_paragraph():
    sentences = split_sentences(
        "Prima propoziție este aici. A doua propoziție urmează! A treia propoziție?"
    )
    assert sentences == [
        "Prima propoziție este aici.",
        "A doua propoziție urmează!",
        "A treia propoziție?",
    ]


def test_split_sentences_empty_text():
    assert split_sentences("") == []
    assert split_sentences("   \n\n  ") == []


# ── make_windows ──

def test_make_windows_size_stride_and_short_drop():
    sentences = [chr(96 + i) * 60 for i in range(1, 6)]
    windows = make_windows(sentences, size=3, stride=2)
    assert len(windows) == 2
    first, second = windows[0].split(), windows[1].split()
    assert first[0] == "a" * 60 and first[-1] == "c" * 60
    assert second[0] == "c" * 60  # one sentence shared
    assert second[-1] == "e" * 60

    assert make_windows(["scurt", "iar scurt"], size=3, stride=2) == []


# ── passages ──

def test_page_passage_slices_canonical_text():
    with TestClient(app) as client:
        entry = _make_entry(client)
        text = "Textul canonic al paginii trei."
        _seed_page(entry["id"], 3, text)
        conn = get_db()
        try:
            passage = page_passage(conn, entry["id"], 3, 0, 10)
            assert passage is not None
            assert passage["text"] == text[0:10]
            assert passage["page"] == 3
            assert passage["start"] == 0 and passage["end"] == 10
            assert passage["source"] == "manual" and passage["status"] == "manual"
            assert page_passage(conn, entry["id"], 3, 10, 10) is None
            assert page_passage(conn, entry["id"], 99, 0, 5) is None
        finally:
            conn.close()


def test_entry_passages_keeps_offsets_and_skips_missing_ones():
    with TestClient(app) as client:
        entry = _make_entry(client)
        text = "Prima frază a paginii. A doua frază a paginii este aici."
        _seed_page(entry["id"], 1, text)
        anchored = _seed_highlight(entry["id"], 1, text[0:24], 0, 24)
        unanchored = _seed_highlight(entry["id"], 1, "fără offset-uri", None, None)

        conn = get_db()
        try:
            result = entry_passages(conn, entry["id"])
        finally:
            conn.close()

    assert len(result["passages"]) == 1
    passage = result["passages"][0]
    assert passage["highlight_id"] == anchored
    assert passage["text"] == text[0:24]
    assert passage["quote"] == text[0:24]
    assert result["skipped"] == [{"highlight_id": unanchored, "reason": "no_offsets"}]


def test_entry_passages_skips_highlight_without_page_text():
    with TestClient(app) as client:
        entry = _make_entry(client)
        highlight_id = _seed_highlight(entry["id"], 5, "citat", 0, 5)
        conn = get_db()
        try:
            result = entry_passages(conn, entry["id"])
        finally:
            conn.close()
    assert result["passages"] == []
    assert result["skipped"] == [
        {"highlight_id": highlight_id, "reason": "missing_page_text"}
    ]


# ── signals ──

def test_deterministic_scorer_returns_null_probabilities():
    score = DeterministicScorer().score(
        "X crește.", "Datele arată că X crește semnificativ, nu scade."
    )
    assert score["entailment"] is None
    assert score["contradiction"] is None
    assert score["neutral"] is None
    assert score["source"] == "heuristic"
    assert score["assertive"] is True
    assert "negation" in score["matched_markers"]

    short = DeterministicScorer().score("X crește.", "nu crește")
    assert short["assertive"] is False


def test_is_strong_signal_is_false_for_nulls():
    signal = score_passage(
        "X crește.",
        "Datele arată că X crește semnificativ, nu scade deloc în eșantion.",
        DeterministicScorer(),
    )
    assert signal["max_entailment"] is None
    assert signal["max_contradiction"] is None
    assert is_strong_signal(signal) is False
    availability = nli_availability()
    assert set(availability) >= {"available", "expected"}
    assert availability["available"] in (True, False)


class _StubScorer:
    source = "stub"

    def __init__(self, entailment=0.9, contradiction=0.2):
        self.entailment = entailment
        self.contradiction = contradiction

    def score(self, claim, window):
        return {
            "entailment": self.entailment,
            "contradiction": self.contradiction,
            "neutral": 0.1,
            "source": self.source,
        }


def test_score_passage_aggregates_real_probabilities():
    passage = (
        "Prima propoziție este destul de lungă pentru o fereastră. "
        "A doua propoziție este și ea suficient de lungă. "
        "A treia propoziție încheie pasajul acesta."
    )
    result = score_passage("X crește.", passage, _StubScorer())
    assert len(result["windows"]) == 1
    assert result["max_entailment"] == 0.9
    assert result["max_contradiction"] == 0.2
    assert result["scorer_source"] == "stub"
    assert is_strong_signal(result) is True

    deterministic = score_passage("X crește.", passage, DeterministicScorer())
    assert deterministic["max_entailment"] is None
    assert deterministic["max_contradiction"] is None
    assert is_strong_signal(deterministic) is False
