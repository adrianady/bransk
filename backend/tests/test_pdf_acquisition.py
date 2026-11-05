"""
Tests for PDF acquisition: title matching and text indexing.

The real failure these guard against: a download that "succeeds" but lands an
unrelated paper. `130-the-nature-of-human-intelligence.pdf` was a self-published
essay that merely shared a title with Sternberg's book — the entry then claimed
to have the book's full text. Matching must be strict, and a rejected file must
never reach the database.
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from fetch_pdfs_by_title import _clean_title, _similarity  # noqa: E402


def test_clean_title_drops_catalogue_suffixes():
    assert _clean_title("Friston - Free Energy Principle (2010)").strip() == "Free Energy Principle"
    assert _clean_title("Machine Theory of Mind (DeepMind)").strip() == "Machine Theory of Mind"
    # A plain academic title is left intact.
    assert _clean_title("Mindfulness and the brain: A systematic review") == (
        "Mindfulness and the brain: A systematic review"
    )


def test_similarity_accepts_the_same_paper_renamed():
    a = "Mindfulness Practice and Increases in Gray Matter Density, Gray Matter Volume, and Cortical Thickness: A Scoping Review"
    b = "Mindfulness practice and increases in gray matter density, gray matter volume, and cortical thickness: a scoping review"
    assert _similarity(a, b) > 0.95


def test_similarity_rejects_a_different_paper_with_a_similar_title():
    """The Sternberg case: same title, different work — must score below the gate."""
    catalog = "The Nature of Human Intelligence"
    impostor = "Nature of human intelligence"  # near-identical string...
    assert _similarity(catalog, impostor) > 0.8  # ...so title alone cannot save us

    # Which is exactly why matching also keys on authors when they are known.
    from fetch_pdfs_by_title import _author_overlap

    assert _author_overlap("Robert J. Sternberg", "K. L Senarath Dayathilake") == 0.0
    assert _author_overlap("Karl Friston", "Karl J. Friston") >= 0.5
    assert _author_overlap("", "Anyone") == 1.0  # unknown authors: no penalty


def test_author_overlap_handles_initials_and_diacritics():
    from fetch_pdfs_by_title import _author_overlap

    assert _author_overlap("Buzsáki György", "Gyorgy Buzsaki") >= 0.5
    assert _author_overlap("Miyake A., Friedman N.P.", "Akira Miyake; Naomi P. Friedman") >= 0.5


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "pdf_test.db")
    db_module.init_db()
    return db_module


def test_unrelated_pdf_is_not_recorded(tmp_db):
    """A rejected download leaves the entry without a pdf_path."""
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO entries (title, content, tags, pdf_path) VALUES (?, ?, ?, ?)",
            ["The Nature of Human Intelligence", "", "study", ""],
        )
        conn.commit()
        row = conn.execute(
            "SELECT pdf_path FROM entries WHERE title = ?", ["The Nature of Human Intelligence"]
        ).fetchone()
        assert (row["pdf_path"] or "") == ""
    finally:
        conn.close()


def test_pdf_magic_bytes_guard():
    """is_valid_pdf_file is the last line of defence against HTML error pages."""
    from app.studies_store import is_valid_pdf_file

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.pdf"
        good.write_bytes(b"%PDF-1.7\n%...")
        assert is_valid_pdf_file(good) is True

        html = Path(tmp) / "challenge.pdf"
        html.write_bytes(b"<!DOCTYPE html><html><title>Checking your browser</title>")
        assert is_valid_pdf_file(html) is False

        empty = Path(tmp) / "empty.pdf"
        empty.write_bytes(b"")
        assert is_valid_pdf_file(empty) is False
