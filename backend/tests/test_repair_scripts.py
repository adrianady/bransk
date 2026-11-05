"""
The duplicate-DOI repair must clear exactly the wrong half of a group.

The real library had two duplicated DOIs: 10.1038/nrn2787 on three studies —
one of them "Predictive Processing Theory (Friston 2013)", a different paper
altogether — and 10.1017/s0140525x00076512 on two paraphrased titles that a
title-equality check could never see. These tests pin the repair logic on a
throwaway database: only the DOI columns move, the best-matching entry keeps its
DOI, the other entries explain themselves in doi_source, no row disappears, and
a second run is a true no-op.

The scripts are loaded from `scripts/` with importlib, and only their functions
are exercised — never the CLI.
"""
import importlib.util
import io
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _load_script(module_name: str, filename: str):
    """Import a `scripts/` file by path, without making scripts a package."""
    spec = importlib.util.spec_from_file_location(module_name, BACKEND / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the defining module up in sys.modules; register it first.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


repair = _load_script("repair_doi_duplicates", "repair_doi_duplicates.py")
verify = _load_script("verify_study_sources", "verify_study_sources.py")


# A trimmed copy of the real schema: every column the repair reads or writes,
# plus content/tags/title so the tests can prove those stay untouched.
SCHEMA = """
CREATE TABLE entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    entry_date      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    tags            TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    importance      INTEGER DEFAULT 1,
    archived        INTEGER DEFAULT 0,
    study_type      TEXT DEFAULT '',
    authors         TEXT DEFAULT '',
    quality         INTEGER,
    url_validated   INTEGER DEFAULT 0,
    design          TEXT DEFAULT '',
    effect_direction TEXT DEFAULT '',
    pdf_path        TEXT DEFAULT '',
    doi             TEXT DEFAULT '',
    doi_source      TEXT DEFAULT '',
    doi_checked_at  TEXT DEFAULT '',
    bib_title       TEXT DEFAULT '',
    bib_authors     TEXT DEFAULT '',
    bib_year        TEXT DEFAULT ''
);
"""

DEFAULTS = dict(
    content="",
    entry_date="2010-01-01",
    tags="",
    source="",
    study_type="study",
    authors="",
    pdf_path="",
    doi="",
    doi_source="crossref+llm",
    doi_checked_at="2026-10-25 16:20:10",
    bib_title="",
    bib_year="2010",
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """An empty database with the real tables, inside the test's tmp dir."""
    path = tmp_path / "chronokg.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return path


def add_entry(db_path: Path, *, entry_id: int | None = None, **fields) -> int:
    """Insert one entry, overriding DEFAULTS; returns the row id."""
    values = {**DEFAULTS, **fields}
    columns = ["id", *values.keys()] if entry_id is not None else list(values.keys())
    params = [entry_id, *values.values()] if entry_id is not None else list(values.values())
    placeholders = ", ".join("?" for _ in columns)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                f"INSERT INTO entries ({', '.join(columns)}) VALUES ({placeholders})",
                params,
            )
            return int(cursor.lastrowid)
    finally:
        conn.close()


def read_all(db_path: Path, *columns: str) -> dict[int, sqlite3.Row]:
    """Read chosen columns for every entry, keyed by id."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT id, {', '.join(columns)} FROM entries ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return {row["id"]: row for row in rows}


def read_entries(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return repair.read_entries(conn)
    finally:
        conn.close()


# ───────────────────────────── DOI normalisation ─────────────────────────────

def test_doi_normalisation_ignores_case_prefix_and_padding():
    assert repair.normalize_doi("  10.1038/NRN2787 ") == "10.1038/nrn2787"
    assert repair.normalize_doi("https://doi.org/10.1038/NRN2787") == "10.1038/nrn2787"
    assert repair.normalize_doi("doi:10.1017/S0140525X00076512") == "10.1017/s0140525x00076512"
    assert repair.normalize_doi("") == ""
    assert repair.normalize_doi(None) == ""


def test_title_match_score_ignores_case_accents_and_years():
    assert repair.title_match_score("The Extended Mind", "the extended mind") == 1.0
    # Different paraphrases of the same paper still overlap on real words.
    assert repair.title_match_score(
        "Free Energy Principle (Friston 2010)",
        "The free-energy principle: a unified brain theory?",
    ) > 0.0
    # A different paper does not.
    assert repair.title_match_score("Predictive Processing Theory", "The extended mind") == 0.0


# ───────────────────────────── grouping and keeper choice ─────────────────────────────

def test_duplicate_group_detected_across_case_variants(db_path: Path):
    add_entry(db_path, entry_id=1, title="Friston - Free Energy Principle (2010)",
              doi="10.1038/NRN2787", bib_title="The free-energy principle: a unified brain theory?")
    add_entry(db_path, entry_id=2, title="Free Energy Principle (Friston 2010)",
              doi="10.1038/nrn2787", bib_title="The free-energy principle: a unified brain theory?")
    add_entry(db_path, entry_id=3, title="Predictive Processing Theory (Friston 2013)",
              doi="10.1038/nrn2787", entry_date="2013-01-01",
              bib_title="The free-energy principle: a unified brain theory?")
    add_entry(db_path, entry_id=4, title="Theory of Mind (Premack & Woodruff 1978)",
              doi="10.1017/s0140525x00076512", entry_date="1978-01-01",
              bib_title="Does the chimpanzee have a theory of mind?")
    add_entry(db_path, entry_id=5, title="No DOI study")

    groups = repair.find_duplicate_doi_groups(read_entries(db_path))
    assert len(groups) == 1
    doi, rows = groups[0]
    assert doi == "10.1038/nrn2787"
    assert [row["id"] for row in rows] == [1, 2, 3]
    # The entry whose title matches its own bib_title worst is the one to clear.
    assert repair.choose_keeper(rows)["id"] == 1
    assert [repair.entry_id for repair in repair.build_plan(read_entries(db_path))] == [2, 3]


def test_keeper_follows_bib_title_match_even_over_smaller_id(db_path: Path):
    add_entry(db_path, entry_id=1, title="Totally Different Work",
              doi="10.1000/xyz", bib_title="Something Else Entirely",
              entry_date="2010-01-01")
    add_entry(db_path, entry_id=5, title="The Extended Mind",
              doi="10.1000/XYZ", bib_title="The Extended Mind",
              entry_date="2020-01-01")

    rows = [row for row in read_entries(db_path) if row["id"] in (1, 5)]
    keeper = repair.choose_keeper(rows)
    assert keeper["id"] == 5
    plan = repair.build_plan(read_entries(db_path))
    assert [(item.entry_id, item.keeper_id) for item in plan] == [(1, 5)]
    assert plan[0].reason == "golit: duplicat al id 5"


def test_keeper_falls_back_to_oldest_then_smallest_id_without_bib_title(db_path: Path):
    add_entry(db_path, entry_id=1, title="Alpha", doi="10.1000/dup", entry_date="2015-01-01")
    add_entry(db_path, entry_id=2, title="Beta", doi="10.1000/DUP", entry_date="2012-01-01")
    add_entry(db_path, entry_id=3, title="Gamma", doi="10.1000/dup", entry_date="2012-01-01")

    rows = read_entries(db_path)
    keeper = repair.choose_keeper([row for row in rows if row["id"] in (1, 2, 3)])
    assert keeper["id"] == 2
    assert [item.entry_id for item in repair.build_plan(rows)] == [1, 3]


# ───────────────────────────── the write itself ─────────────────────────────

def test_apply_clears_only_the_duplicates_and_explains_why(db_path: Path):
    keeper = add_entry(
        db_path, entry_id=1, title="Friston - Free Energy Principle (2010)",
        content="canonical content", tags="friston,free-energy",
        doi="10.1038/NRN2787", doi_source="crossref+llm",
        doi_checked_at="2026-10-25 16:20:10",
        bib_title="The free-energy principle: a unified brain theory?",
    )
    duplicate = add_entry(
        db_path, entry_id=2, title="Free Energy Principle (Friston 2010)",
        content="other content", tags="free-energy",
        doi="10.1038/nrn2787", doi_source="crossref+llm",
        doi_checked_at="2026-10-25 16:21:27",
        bib_title="The free-energy principle: a unified brain theory?",
    )
    wrong_paper = add_entry(
        db_path, entry_id=3, title="Predictive Processing Theory (Friston 2013)",
        content="predictive processing notes", tags="predictive-processing",
        doi="10.1038/nrn2787", doi_source="crossref+llm",
        entry_date="2013-01-01", bib_title="The free-energy principle: a unified brain theory?",
    )
    correct = add_entry(
        db_path, entry_id=4, title="Theory of Mind (Premack & Woodruff 1978)",
        doi="10.1017/s0140525x00076512", entry_date="1978-01-01",
        bib_title="Does the chimpanzee have a theory of mind?",
    )

    before_bytes = db_path.read_bytes()
    plan = repair.build_plan(read_entries(db_path))
    assert [(item.entry_id, item.keeper_id) for item in plan] == [(2, 1), (3, 1)]

    written = repair.apply_plan(db_path, plan, out=io.StringIO())
    assert written == 2

    backups = list(db_path.parent.glob(f"{db_path.name}.bak-doi-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before_bytes

    rows = read_all(db_path, "title", "content", "tags", "doi", "doi_source", "doi_checked_at")
    assert len(rows) == 4  # nothing deleted

    # The best-matching entry keeps the DOI, in its original casing.
    assert rows[keeper]["doi"] == "10.1038/NRN2787"
    assert rows[keeper]["doi_source"] == "crossref+llm"
    assert rows[keeper]["doi_checked_at"] == "2026-10-25 16:20:10"
    assert rows[keeper]["content"] == "canonical content"
    assert rows[keeper]["tags"] == "friston,free-energy"

    for entry_id in (duplicate, wrong_paper):
        # Empty string, not NULL: EntryResponse declares these columns as `str`.
        assert rows[entry_id]["doi"] == ""
        assert rows[entry_id]["doi_checked_at"] == ""
        assert rows[entry_id]["doi_source"] == "golit: duplicat al id 1"

    # Non-duplicate rows and unrelated columns are untouched.
    assert rows[correct]["doi"] == "10.1017/s0140525x00076512"
    assert rows[correct]["doi_source"] == "crossref+llm"
    assert rows[duplicate]["title"] == "Free Energy Principle (Friston 2010)"
    assert rows[wrong_paper]["content"] == "predictive processing notes"


def test_second_apply_run_changes_nothing(db_path: Path):
    add_entry(db_path, entry_id=1, title="A", doi="10.1000/dup",
              bib_title="A", entry_date="2010-01-01")
    add_entry(db_path, entry_id=2, title="B", doi="10.1000/DUP",
              bib_title="A", entry_date="2011-01-01")

    first_plan = repair.build_plan(read_entries(db_path))
    assert len(first_plan) == 1
    repair.apply_plan(db_path, first_plan, out=io.StringIO())

    # A fresh read must find nothing left to repair...
    assert repair.build_plan(read_entries(db_path)) == []
    # ...and applying the empty plan must neither write nor make a new backup.
    before = read_all(db_path, "doi", "doi_source", "doi_checked_at")
    assert repair.apply_plan(db_path, [], out=io.StringIO()) == 0
    after = read_all(db_path, "doi", "doi_source", "doi_checked_at")
    assert {entry_id: dict(row) for entry_id, row in before.items()} == \
           {entry_id: dict(row) for entry_id, row in after.items()}
    assert len(list(db_path.parent.glob(f"{db_path.name}.bak-doi-*"))) == 1


def test_cleared_doi_still_satisfies_the_api_response_model(db_path: Path):
    """GET /entries passes dict(row) through EntryResponse, which wants str.

    A NULL in doi/doi_checked_at would therefore be a 500 on the live API, not
    just an empty value; this pins the empty-string contract.
    """
    from app.models import EntryResponse

    add_entry(db_path, entry_id=1, title="Keeper", doi="10.1000/dup", bib_title="Keeper")
    add_entry(db_path, entry_id=2, title="Duplicate", doi="10.1000/DUP", bib_title="Keeper")
    repair.apply_plan(db_path, repair.build_plan(read_entries(db_path)), out=io.StringIO())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = dict(conn.execute("SELECT * FROM entries WHERE id = 2").fetchone())
    finally:
        conn.close()

    model = EntryResponse(**row)
    assert model.doi == ""
    assert model.doi_checked_at == ""
    assert model.doi_source == "golit: duplicat al id 1"


# ───────────────────────────── verify_study_sources ─────────────────────────────

@pytest.fixture()
def studies_dir(tmp_path: Path) -> Path:
    path = tmp_path / "studies"
    path.mkdir()
    (path / "2-present.pdf").write_bytes(b"%PDF-1.4 fake")
    return path


def seed_study_db(db_path: Path) -> None:
    add_entry(db_path, entry_id=1, title="Has DOI and URL", doi="10.1000/one",
              source="https://example.org/paper", bib_title="Has DOI and URL")
    add_entry(db_path, entry_id=2, title="Has local PDF", pdf_path="2-present.pdf")
    add_entry(db_path, entry_id=3, title="Nothing at all", study_type="study")
    add_entry(db_path, entry_id=4, title="Missing PDF", pdf_path="4-missing.pdf")
    add_entry(db_path, entry_id=5, title="Not a study", study_type="")


def test_classify_counts_doi_pdf_url_and_nothing(db_path: Path, studies_dir: Path):
    seed_study_db(db_path)
    conn = verify.open_readonly(db_path)
    try:
        rows = verify.load_studies(conn)
    finally:
        conn.close()

    assert [row["id"] for row in rows] == [1, 2, 3, 4]  # non-study excluded
    records = [verify.classify(row, studies_dir) for row in rows]
    stats = verify.summarize(records)
    assert stats == {"total": 4, "doi": 1, "pdf": 1, "url": 1, "any": 2, "none": 2}

    by_id = {record.entry_id: record for record in records}
    assert by_id[1].url == "https://example.org/paper"
    assert by_id[2].has_pdf and by_id[2].pdf_file.name == "2-present.pdf"
    assert by_id[4].pdf_path == "4-missing.pdf" and not by_id[4].has_pdf
    assert not by_id[3].has_source


def test_readonly_connection_refuses_every_write(db_path: Path):
    seed_study_db(db_path)
    conn = verify.open_readonly(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE entries SET doi = 'changed' WHERE id = 1")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM entries WHERE id = 1")
    finally:
        conn.close()
    assert read_all(db_path, "doi")[1]["doi"] == "10.1000/one"


@dataclass
class _CountingStub:
    calls: int = 0

    def __call__(self, url: str, timeout: float = 5.0) -> str:
        self.calls += 1
        return "200"


def test_check_urls_reports_buckets_and_caps_requests(db_path: Path, monkeypatch):
    seed_study_db(db_path)
    conn = verify.open_readonly(db_path)
    try:
        records = [verify.classify(row, Path("/nonexistent")) for row in verify.load_studies(conn)]
    finally:
        conn.close()

    for record in records:
        record.url = f"https://example.org/{record.entry_id}"

    stub = _CountingStub()
    monkeypatch.setattr(verify, "check_url", stub)
    monkeypatch.setattr(verify.time, "sleep", lambda _seconds: None)

    counts = verify.check_urls(records, out=io.StringIO())
    assert counts == {"2xx": 4, "3xx": 0, "4xx": 0, "5xx": 0, "err": 0}
    assert stub.calls == 4
    assert all(record.status_url == "200" for record in records)

    # The 59-URL cap is real: 70 URLs must not become 70 requests.
    many = [verify.StudySource(entry_id=i, title=f"Study {i}", url=f"https://example.org/{i}")
            for i in range(70)]
    capped = _CountingStub()
    monkeypatch.setattr(verify, "check_url", capped)
    counts = verify.check_urls(many, out=io.StringIO())
    assert capped.calls == verify.MAX_URL_CHECKS == 59
    assert counts["2xx"] == 59


def test_csv_has_the_expected_columns_and_only_existing_pdfs(
    db_path: Path, studies_dir: Path
):
    seed_study_db(db_path)
    conn = verify.open_readonly(db_path)
    try:
        records = [verify.classify(row, studies_dir) for row in verify.load_studies(conn)]
    finally:
        conn.close()

    out_path = db_path.parent / "study_sources_report.csv"
    verify.write_csv(records, out_path)
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "id,title,doi,pdf,url,status_url"
    assert len(lines) == 5  # header + four studies
    # id 4 has a recorded path but no file: the CSV must not claim a local PDF.
    assert any(line.startswith("2,Has local PDF,10.1000/one,") or
               line.startswith("2,Has local PDF,,2-present.pdf,") for line in lines)
    assert any(line.startswith("4,Missing PDF,,,") for line in lines)


def test_first_url_and_pdf_resolution_helpers(tmp_path: Path):
    assert verify.first_url("see https://example.org/a?b=1 for details") == \
        "https://example.org/a?b=1"
    assert verify.first_url("") == ""
    assert verify.first_url(None) == ""

    studies = tmp_path / "studies"
    studies.mkdir()
    nested = studies / "2024"
    nested.mkdir()
    (nested / "paper.pdf").write_bytes(b"%PDF")
    assert verify.resolve_pdf("paper.pdf", studies) is None
    assert verify.resolve_pdf("2024/paper.pdf", studies) == nested / "paper.pdf"
    assert verify.resolve_pdf(str(nested / "paper.pdf"), studies) == nested / "paper.pdf"
    assert verify.resolve_pdf("", studies) is None
