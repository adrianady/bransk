"""
Repair duplicated DOIs in the study library.

A DOI identifies exactly one work, so when two catalogue entries carry the same
DOI at most one of them can be right. The real library showed both failure
modes: one DOI shared by three studies — including "Predictive Processing
Theory (Friston 2013)", where the identifier belongs to a different paper
altogether — and a second pair whose titles were paraphrased differently, so
deduplicating on title could never see it.

Policy implemented here:
  * group studies by normalised DOI (trim, optional URL/doi: prefix, lowercase);
  * keep the DOI on the entry whose own title best matches its bibliographic
    title (`bib_title`, simple normalised token overlap), falling back to the
    oldest entry and then the smallest id when no bib_title is available;
  * set `doi` and `doi_checked_at` to empty on every other entry of the group
    and record the reason in `doi_source` as "golit: duplicat al id N";
  * never delete a row and never touch content/tags/title. Only those three
    DOI columns are written.

Dry run is the default and writes nothing. `--apply` copies the database to
`<db>.bak-doi-YYYYmmdd-HHMMSS` first, then writes every change in one
transaction; it is idempotent, because a second run finds no groups.

Usage:
    .venv/bin/python scripts/repair_doi_duplicates.py              # preview
    .venv/bin/python scripts/repair_doi_duplicates.py --apply      # asks first
    .venv/bin/python scripts/repair_doi_duplicates.py --apply --yes
    CHRONOKG_DB_PATH=/tmp/ck_doi.db .venv/bin/python scripts/repair_doi_duplicates.py

The database path is CHRONOKG_DB_PATH when set (the same variable the app reads
in app/db.py), otherwise backend/data/chronokg.db computed from this file.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# The only columns this script may write. A repair that touched anything else
# would be a different, riskier operation than the one advertised.
DOI_COLUMNS = ("doi", "doi_checked_at", "doi_source")

# Columns read for grouping, ranking and the report table.
ENTRY_COLUMNS = (
    "id", "title", "entry_date", "authors", "bib_year", "bib_title",
    "doi", "doi_source", "doi_checked_at",
)

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.I)
_TOKEN = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")

# Words that say nothing about which paper a title is. Short list on purpose:
# it only has to stop the matching from being flattered by "the/of/a".
STOPWORDS = {
    "the", "of", "and", "a", "an", "in", "on", "for", "to", "with", "by",
    "from", "is", "are", "was", "were", "that", "this", "as", "at", "or",
    "it", "its", "does", "do", "have", "has",
}


# ───────────────────────────── path resolution ─────────────────────────────

def resolve_db_path() -> Path:
    """CHRONOKG_DB_PATH when set, else backend/data/chronokg.db beside this file."""
    configured = os.getenv("CHRONOKG_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return BACKEND / "data" / "chronokg.db"


# ───────────────────────────── DOI grouping ─────────────────────────────

def normalize_doi(value: str | None) -> str:
    """Return the comparable form of a DOI: lowercase, trimmed, prefix-free."""
    text = (value or "").strip()
    text = _DOI_PREFIX.sub("", text)
    return text.strip().rstrip(".,;").lower()


def _fold(text: str | None) -> str:
    """Lowercase and strip accents so "Rezilienţă" and "rezilienta" compare."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return plain.lower()


def title_tokens(text: str | None) -> set[str]:
    """Meaningful tokens of a title, ignoring years, stop words and 1-letter words."""
    tokens: set[str] = set()
    for token in _TOKEN.findall(_fold(text)):
        if len(token) < 2 or token in STOPWORDS or _YEAR.match(token):
            continue
        tokens.add(token)
    return tokens


def title_match_score(title: str | None, bib_title: str | None) -> float:
    """Jaccard overlap of title tokens; 0.0 when either side is empty."""
    left, right = title_tokens(title), title_tokens(bib_title)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _entry_sort_key(row: sqlite3.Row) -> tuple[str, int]:
    """Oldest first; a missing date cannot claim to be oldest, so it sorts last."""
    date = (row["entry_date"] or "").strip()
    return (date or "9999-99-99", row["id"])


def choose_keeper(entries: list[sqlite3.Row]) -> sqlite3.Row:
    """Pick the entry that should keep the DOI.

    Best title/bib_title match wins; ties (and groups with no usable bib_title)
    fall back to the oldest entry, then the smallest id.
    """
    scored = [(title_match_score(row["title"], row["bib_title"]), row) for row in entries]
    best = max(score for score, _row in scored)
    candidates = [row for score, row in scored if score == best] if best > 0.0 else list(entries)
    return min(candidates, key=_entry_sort_key)


def read_entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Read the columns the repair and its report need, in stable id order."""
    sql = f"SELECT {', '.join(ENTRY_COLUMNS)} FROM entries ORDER BY id"
    return conn.execute(sql).fetchall()


def find_duplicate_doi_groups(
    entries: list[sqlite3.Row],
) -> list[tuple[str, list[sqlite3.Row]]]:
    """Groups of entries sharing a normalised DOI, biggest group first."""
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in entries:
        doi = normalize_doi(row["doi"])
        if doi:
            groups.setdefault(doi, []).append(row)
    duplicates = [(doi, rows) for doi, rows in groups.items() if len(rows) > 1]
    # Deterministic order: most-shared DOI first, then DOI itself.
    duplicates.sort(key=lambda item: (-len(item[1]), item[0]))
    return duplicates


@dataclass(frozen=True)
class Repair:
    """One entry whose DOI will be cleared, and why."""

    entry_id: int
    keeper_id: int
    doi: str
    title: str

    @property
    def reason(self) -> str:
        return f"golit: duplicat al id {self.keeper_id}"


def build_plan(entries: list[sqlite3.Row]) -> list[Repair]:
    """Turn duplicate groups into the exact list of rows to clear."""
    plan: list[Repair] = []
    for doi, rows in find_duplicate_doi_groups(entries):
        keeper = choose_keeper(rows)
        for row in rows:
            if row["id"] == keeper["id"]:
                continue
            plan.append(Repair(
                entry_id=row["id"],
                keeper_id=keeper["id"],
                doi=doi,
                title=row["title"] or "",
            ))
    return sorted(plan, key=lambda repair: (repair.keeper_id, repair.entry_id))


# ───────────────────────────── writing ─────────────────────────────

def backup_database(db_path: Path) -> Path:
    """Copy the database beside itself, after folding the WAL into the main file.

    The library runs SQLite in WAL mode, so copying the main file while frames
    still sit in `-wal` would back up a stale database. A FULL checkpoint first
    makes `shutil.copy2` capture every committed change.
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            conn.close()
    except sqlite3.Error:
        # Not a WAL database, or another writer holds it; the plain copy still
        # captures the main file, which is what this backup promises.
        pass
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-doi-{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def apply_plan(
    db_path: Path,
    plan: list[Repair],
    out=sys.stdout,
) -> int:
    """Back up, then clear the duplicate DOI columns in one transaction.

    Returns the number of rows written. An empty plan writes nothing at all —
    no backup, no transaction — so a second `--apply` run is a true no-op.
    """
    if not plan:
        print("Nothing to write: no duplicate DOI groups found.", file=out)
        return 0

    backup = backup_database(db_path)
    print(f"Backup: {backup}", file=out)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        for repair in plan:
            # Empty string, not NULL: entries.doi and entries.doi_checked_at
            # default to '' and the API response model declares them as `str`
            # (app/models.py EntryResponse), so a NULL would break GET /entries.
            cursor = conn.execute(
                "UPDATE entries SET doi = '', doi_checked_at = '', doi_source = ? "
                "WHERE id = ?",
                (repair.reason, repair.entry_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"entry {repair.entry_id} was not updated (rowcount={cursor.rowcount})"
                )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

    keepers = ", ".join(str(keeper_id) for keeper_id in sorted({r.keeper_id for r in plan}))
    print(f"Wrote {len(plan)} row(s); DOI kept on id(s) {keepers}.", file=out)
    return len(plan)


# ───────────────────────────── reporting ─────────────────────────────

def _trunc(text: str | None, width: int) -> str:
    """Collapse whitespace and shorten to *width*, with an ellipsis when cut."""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= width else clean[: width - 1] + "…"


def print_report(
    groups: list[tuple[str, list[sqlite3.Row]]],
    plan: list[Repair],
    out=sys.stdout,
) -> None:
    """Print the duplicate table and what would be cleared, as plain text."""
    total = sum(len(rows) for _doi, rows in groups)
    print(
        f"Duplicate DOI groups: {len(groups)} "
        f"({total} studies, {len(plan)} DOI(s) to clear)\n",
        file=out,
    )
    header = (
        f"  {'id':>4}  {'title':<60}  {'authors':<20}  {'year':<4}  "
        f"{'doi_source':<14}  {'doi_checked_at':<19}"
    )
    for doi, rows in groups:
        keeper = choose_keeper(rows)
        print(f"DOI {doi}  — {len(rows)} entries; keep on id {keeper['id']}", file=out)
        print(header, file=out)
        for row in sorted(rows, key=_entry_sort_key):
            mark = "*" if row["id"] == keeper["id"] else " "
            print(
                f"  {mark}{row['id']:>3}  {_trunc(row['title'], 60):<60}  "
                f"{_trunc(row['authors'], 20):<20}  {_trunc(row['bib_year'], 4):<4}  "
                f"{_trunc(row['doi_source'], 14):<14}  "
                f"{_trunc(row['doi_checked_at'], 19):<19}",
                file=out,
            )
        print(file=out)

    print("Plan (marker * = entry that keeps the DOI):", file=out)
    for repair in plan:
        print(
            f"  clear id {repair.entry_id:>3}  {_trunc(repair.title, 50):<50}  "
            f"-> doi_source = {repair.reason!r}",
            file=out,
        )


def _confirm(plan: list[Repair]) -> bool:
    """Ask for the literal answer DA unless --yes was given."""
    if not sys.stdin.isatty():
        print(
            "Refusing to write: stdin is not a TTY and --yes was not given.\n"
            "Re-run with --yes to apply non-interactively.",
            file=sys.stderr,
        )
        return False
    print()
    answer = input(
        f"Clear the DOI on {len(plan)} entr{'y' if len(plan) == 1 else 'ies'} "
        f"of the database? Type DA to continue: "
    )
    if answer.strip() != "DA":
        print("Aborted: no changes written.")
        return False
    return True


# ───────────────────────────── entry point ─────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (backup + one transaction)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the interactive confirmation (requires --apply)")
    parser.add_argument("--db", type=Path, default=None,
                        help="database path (default: CHRONOKG_DB_PATH or data/chronokg.db)")
    args = parser.parse_args(argv)

    db_path = (args.db or resolve_db_path()).expanduser()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        entries = read_entries(conn)
    finally:
        conn.close()

    groups = find_duplicate_doi_groups(entries)
    if not groups:
        print("No duplicate DOIs found; nothing to repair.")
        return 0

    plan = build_plan(entries)
    print_report(groups, plan)
    print()

    if not args.apply:
        print("Dry run: the database was not modified. Re-run with --apply to write.")
        return 0

    if not args.yes and not _confirm(plan):
        return 1

    apply_plan(db_path, plan)

    # Re-read and verify: a repair that did not take is worse than none.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        remaining = find_duplicate_doi_groups(read_entries(conn))
    finally:
        conn.close()
    if remaining:
        print(f"WARNING: {len(remaining)} duplicate group(s) still present.", file=sys.stderr)
        return 1
    print("Verified: no duplicate DOI groups remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
