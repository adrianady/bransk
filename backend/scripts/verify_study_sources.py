"""
Report where each study's evidence comes from: DOI, local PDF, URL, or nothing.

The library is opened strictly read-only (`mode=ro`), so this script cannot
change a column even by accident. It answers the audit question — how many of
the 106 studies can actually be traced back to a source — and, optionally,
whether the recorded URLs still answer.

    .venv/bin/python scripts/verify_study_sources.py                # report only
    .venv/bin/python scripts/verify_study_sources.py --csv          # + CSV
    .venv/bin/python scripts/verify_study_sources.py --check-urls  # + network
    .venv/bin/python scripts/verify_study_sources.py --check-urls --csv

Without `--check-urls` no network request is made. With it, each URL gets a
HEAD request and a GET fallback (some servers reject HEAD), a 5 s timeout, and
a 0.5 s pause between requests; at most 59 URLs are checked, the number the
library currently holds. Redirects are not followed: the report says what the
stored URL answers today, including 3xx.

`--csv` writes `data/study_sources_report.csv` next to the database (so a copy
of the database produces a report beside the copy, not in the live folder).

The database path is CHRONOKG_DB_PATH when set (the same variable the app reads
in app/db.py), otherwise backend/data/chronokg.db computed from this file. PDFs
are resolved under CHRONOKG_STUDIES_DIR when set, otherwise backend/data/studies.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

BACKEND = Path(__file__).resolve().parent.parent

STUDY_WHERE = "study_type IS NOT NULL AND trim(study_type) <> ''"
STUDY_COLUMNS = ("id", "title", "doi", "pdf_path", "source", "bib_year", "authors")

MAX_URL_CHECKS = 59
URL_PAUSE_SECONDS = 0.5
URL_TIMEOUT_SECONDS = 5.0
URL_UA = "ChronoKG/0.1 (+study-source verification)"

URL_RE = re.compile(r"https?://\S+")


# ───────────────────────────── paths ─────────────────────────────

def resolve_db_path() -> Path:
    """CHRONOKG_DB_PATH when set, else backend/data/chronokg.db beside this file."""
    configured = os.getenv("CHRONOKG_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return BACKEND / "data" / "chronokg.db"


def resolve_studies_dir() -> Path:
    """CHRONOKG_STUDIES_DIR when set, else backend/data/studies (as the app does)."""
    configured = os.getenv("CHRONOKG_STUDIES_DIR")
    if configured:
        return Path(configured).expanduser()
    return BACKEND / "data" / "studies"


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the library with SQLite's `mode=ro`, which refuses every write."""
    uri = f"file:{quote(str(db_path.resolve()), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ───────────────────────────── classification ─────────────────────────────

def load_studies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The studies (entries with a study_type), in stable id order."""
    sql = f"SELECT {', '.join(STUDY_COLUMNS)} FROM entries WHERE {STUDY_WHERE} ORDER BY id"
    return conn.execute(sql).fetchall()


def first_url(source: str | None) -> str:
    """The first http(s) URL in the `source` column, or '' when there is none."""
    match = URL_RE.search(source or "")
    return match.group(0) if match else ""


def resolve_pdf(pdf_path: str | None, studies_dir: Path) -> Path | None:
    """Existing file for a stored pdf_path, or None.

    pdf_path is normally a bare file name in the studies folder, but older rows
    may carry a relative path, so the folder and its parent are both tried.
    """
    raw = (pdf_path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    attempts = [studies_dir / candidate, studies_dir.parent / candidate,
                studies_dir / candidate.name]
    for path in attempts:
        if path.is_file():
            return path
    return None


@dataclass
class StudySource:
    """One study and where its evidence lives."""

    entry_id: int
    title: str
    doi: str = ""
    pdf_path: str = ""
    pdf_ok: bool = False
    url: str = ""
    status_url: str = ""
    pdf_file: Path | None = field(default=None, repr=False)

    @property
    def has_doi(self) -> bool:
        return bool(self.doi)

    @property
    def has_pdf(self) -> bool:
        return self.pdf_ok

    @property
    def has_url(self) -> bool:
        return bool(self.url)

    @property
    def has_source(self) -> bool:
        return self.has_doi or self.has_pdf or self.has_url


def classify(row: sqlite3.Row, studies_dir: Path) -> StudySource:
    """Read one study row into a source record, checking the PDF file on disk."""
    pdf_path = (row["pdf_path"] or "").strip()
    pdf_file = resolve_pdf(pdf_path, studies_dir)
    return StudySource(
        entry_id=row["id"],
        title=row["title"] or "",
        doi=(row["doi"] or "").strip(),
        pdf_path=pdf_path,
        pdf_ok=pdf_file is not None,
        pdf_file=pdf_file,
        url=first_url(row["source"]),
    )


def summarize(records: list[StudySource]) -> dict[str, int]:
    """Counts the report leads with; overlaps are intentional."""
    return {
        "total": len(records),
        "doi": sum(1 for record in records if record.has_doi),
        "pdf": sum(1 for record in records if record.has_pdf),
        "url": sum(1 for record in records if record.has_url),
        "any": sum(1 for record in records if record.has_source),
        "none": sum(1 for record in records if not record.has_source),
    }


# ───────────────────────────── URL checking ─────────────────────────────

def status_bucket(status: str) -> str:
    """Fold an HTTP status (or "err") into the buckets the report counts."""
    if not status or status == "err":
        return "err"
    return {"2": "2xx", "3": "3xx", "4": "4xx", "5": "5xx"}.get(status[0], "err")


def check_url(url: str, timeout: float = URL_TIMEOUT_SECONDS) -> str:
    """Return the HTTP status of *url* as text, or "err" when it did not answer.

    HEAD first because it is cheap; GET only when a server rejects HEAD
    (405/501) or blocks it as a bot (403), or when HEAD itself failed. The GET
    is streamed and closed without reading the body, so a large PDF is not
    downloaded just to learn its status.
    """
    import httpx  # imported here so the default report never touches the network

    headers = {"User-Agent": URL_UA}
    try:
        response = httpx.head(url, timeout=timeout, follow_redirects=False, headers=headers)
    except httpx.HTTPError:
        response = None

    if response is None or response.status_code in (403, 405, 501):
        try:
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=False,
                              headers=headers) as streamed:
                return str(streamed.status_code)
        except httpx.HTTPError:
            return "err"
    return str(response.status_code)


def check_urls(
    records: list[StudySource],
    out=sys.stdout,
    pause: float = URL_PAUSE_SECONDS,
) -> dict[str, int]:
    """Check every recorded URL (capped) and return counts per status bucket."""
    with_url = [record for record in records if record.has_url]
    checked = with_url[:MAX_URL_CHECKS]
    skipped = len(with_url) - len(checked)
    print(
        f"Checking {len(checked)} URL(s) — HEAD with GET fallback, "
        f"{URL_TIMEOUT_SECONDS:.0f}s timeout, {pause:.1f}s pause"
        + (f", {skipped} skipped over the {MAX_URL_CHECKS} cap" if skipped else "")
        + "...",
        file=out,
    )
    counts = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "err": 0}
    for index, record in enumerate(checked, start=1):
        if index > 1:
            time.sleep(pause)
        record.status_url = check_url(record.url)
        bucket = status_bucket(record.status_url)
        counts[bucket] += 1
        print(
            f"  [{index:>3}/{len(checked)}] {record.status_url:<4} "
            f"id {record.entry_id:<4} {record.url}",
            file=out,
        )
    return counts


# ───────────────────────────── output ─────────────────────────────

def write_csv(records: list[StudySource], path: Path) -> None:
    """Write the per-study table; pdf is filled only when the file exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "title", "doi", "pdf", "url", "status_url"])
        for record in records:
            writer.writerow([
                record.entry_id,
                record.title,
                record.doi,
                record.pdf_path if record.pdf_ok else "",
                record.url,
                record.status_url,
            ])


def print_report(
    records: list[StudySource],
    studies_dir: Path,
    db_path: Path,
    url_counts: dict[str, int] | None = None,
    out=sys.stdout,
) -> None:
    """Print the coverage summary, the no-source list and the PDF anomalies."""
    stats = summarize(records)
    print(f"Study sources (read-only): {stats['total']} studies in {db_path}", file=out)
    print(f"  with DOI        : {stats['doi']}", file=out)
    print(f"  with local PDF  : {stats['pdf']}  (studies dir: {studies_dir})", file=out)
    print(f"  with URL        : {stats['url']}", file=out)
    print(f"  with any source : {stats['any']}", file=out)
    print(f"  with no source  : {stats['none']}", file=out)

    missing_pdf = [record for record in records if record.pdf_path and not record.pdf_ok]
    if missing_pdf:
        print(f"\npdf_path recorded but file missing ({len(missing_pdf)}):", file=out)
        for record in missing_pdf:
            print(f"  #{record.entry_id:<4} {record.pdf_path}  ({record.title[:60]})", file=out)

    no_source = [record for record in records if not record.has_source]
    print(f"\nNo source at all ({len(no_source)}):", file=out)
    if not no_source:
        print("  (none — every study has a DOI, a local PDF or a URL)", file=out)
    for record in no_source:
        print(f"  #{record.entry_id:<4} {record.title[:78]}", file=out)

    if url_counts is not None:
        total = sum(url_counts.values())
        print(
            f"\nURL check ({total} checked): "
            f"2xx={url_counts['2xx']}  3xx={url_counts['3xx']}  "
            f"4xx={url_counts['4xx']}  5xx={url_counts['5xx']}  err={url_counts['err']}",
            file=out,
        )


# ───────────────────────────── entry point ─────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check-urls", action="store_true",
                        help="send HEAD/GET requests to the recorded URLs (network)")
    parser.add_argument("--csv", action="store_true",
                        help="write study_sources_report.csv next to the database")
    parser.add_argument("--db", type=Path, default=None,
                        help="database path (default: CHRONOKG_DB_PATH or data/chronokg.db)")
    args = parser.parse_args(argv)

    db_path = (args.db or resolve_db_path()).expanduser()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    studies_dir = resolve_studies_dir()
    conn = open_readonly(db_path)
    try:
        rows = load_studies(conn)
    finally:
        conn.close()

    records = [classify(row, studies_dir) for row in rows]
    url_counts = check_urls(records) if args.check_urls else None
    print_report(records, studies_dir, db_path, url_counts)

    if args.csv:
        csv_path = db_path.parent / "study_sources_report.csv"
        write_csv(records, csv_path)
        print(f"\nCSV written: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
