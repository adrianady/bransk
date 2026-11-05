"""
Tests for DOI rebuilding against Crossref.

The bar is deliberately high: a wrong DOI is worse than none, because it makes
the thesis cite a paper the student never read. These tests pin down the rules
that keep bad matches out, and each one came from a real failure:

  * "Dual-Process Theories (Evans & Stanovich 2013)" — the year inside the title
    bracket was being read as an author name.
  * "Working Memory (Baddeley & Hitch 1974)" — right authors, wrong year (a 2007
    chapter matched the 1974 paper), and the API returned no authors at all for
    some candidates, which silently passed the author check.
  * "The Nature of Human Intelligence" — a self-published essay sharing the
    title with Sternberg's book.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from rebuild_dois import (  # noqa: E402
    author_match, catalogue_authors, clean_title, decide, surnames,
    title_similarity,
)


# ── Reading authors out of catalogue titles ──

def test_authors_from_trailing_bracket():
    entry = {"title": "Dual-Process Theories (Evans & Stanovich 2013)", "authors": ""}
    assert catalogue_authors(entry) == "Evans Stanovich"


def test_authors_from_prefix():
    entry = {"title": "Clark & Chalmers - Extended Mind (1998)", "authors": ""}
    assert catalogue_authors(entry) == "Clark Chalmers"


def test_authors_column_wins_over_title():
    entry = {"title": "Something (Wrong 1999)", "authors": "Karl Friston"}
    assert catalogue_authors(entry) == "Karl Friston"


def test_unknown_authors_are_treated_as_missing():
    entry = {"title": "The Origin of Concepts", "authors": "Unknown (ScienceDirect)"}
    assert catalogue_authors(entry) == ""


def test_year_is_not_mistaken_for_an_author():
    """The bug that let a wrong paper through: "2013" became a surname."""
    authors = surnames(catalogue_authors({"title": "Dual-Process Theories (Evans & Stanovich 2013)",
                                          "authors": ""}))
    assert "2013" not in authors
    assert authors == {"evans", "stanovich"}


# ── Title normalisation ──

def test_leading_article_does_not_change_similarity():
    """'The' and 'A' carry no meaning in a title match."""
    assert title_similarity("The Modularity of Mind", "Modularity of Mind") > 0.9
    assert title_similarity("The Origin of Concepts", "Origin of Concepts") > 0.9


def test_author_prefix_and_year_are_stripped_from_catalogue_titles():
    assert clean_title("Friston - Free Energy Principle (2010)") == "Free Energy Principle"
    assert clean_title("Machine Theory of Mind (DeepMind)") == "Machine Theory of Mind"


# ── Author matching ──

def test_author_match_rejects_a_different_author():
    assert author_match("Robert J. Sternberg", {"dayathilake"}) == 0.0


def test_author_match_accepts_the_real_author():
    assert author_match("Baddeley Hitch", {"baddeley", "hitch", "smith"}) == 1.0


def test_author_match_is_neutral_when_catalogue_has_no_authors():
    assert author_match("", {"anyone"}) == 1.0


def test_author_match_fails_when_candidate_lists_nobody():
    """Right title, but nobody to confirm identity with."""
    assert author_match("Baddeley Hitch", set()) == 0.0


# ── The decision gate ──

def _cand(**kw):
    base = {
        "doi": "10.1/x", "title": "Working memory", "title_sim": 1.0,
        "year": 2007, "authors_found": {"baddeley", "hitch"}, "author_sim": 1.0,
    }
    base.update(kw)
    return base


def test_rejects_right_authors_wrong_year():
    """A 2007 chapter must not stand in for the 1974 paper."""
    entry = {"title": "Working Memory (Baddeley & Hitch 1974)", "authors": "", "entry_date": "1974-01-01"}
    best, why = decide(entry, [_cand()], min_title=0.88, year_window=2)
    assert best is None
    assert "an diferit" in why


def test_rejects_candidate_with_no_authors_when_catalogue_has_some():
    entry = {"title": "Descartes' Error (Damasio 1994)", "authors": "", "entry_date": "1994-01-01"}
    blind = _cand(title="El error de Descartes.", title_sim=0.95, year=1994,
                  authors_found=set(), author_sim=0.0)
    best, why = decide(entry, [blind], min_title=0.88, year_window=2)
    assert best is None


def test_accepts_a_correct_match():
    entry = {"title": "The Modularity of Mind (Fodor 1983)", "authors": "", "entry_date": "1983-01-01"}
    good = _cand(title="The Modularity of Mind", title_sim=1.0, year=1983,
                 authors_found={"fodor"}, author_sim=1.0, doi="10.7551/mitpress/4737.001.0001")
    best, why = decide(entry, [good], min_title=0.88, year_window=2)
    assert best is not None, why
    assert best["doi"] == "10.7551/mitpress/4737.001.0001"


def test_skips_a_commentary_to_find_the_paper_itself():
    """Crossref sometimes ranks a commentary above the paper it discusses."""
    entry = {"title": "The Attention System of the Brain (Posner & Petersen 1990)",
             "authors": "", "entry_date": "1990-01-01"}
    commentary = _cand(title="Commentary on attention", title_sim=0.89, year=1990,
                       authors_found={"someone"}, author_sim=0.0, doi="10.1/commentary")
    paper = _cand(title="The Attention System of the Human Brain", title_sim=0.97,
                  year=1990, authors_found={"posner", "petersen"}, author_sim=1.0,
                  doi="10.1146/annurev.ne.13.030190.000325")
    best, why = decide(entry, [commentary, paper], min_title=0.88, year_window=2)
    assert best is not None, why
    assert best["doi"] == "10.1146/annurev.ne.13.030190.000325"
