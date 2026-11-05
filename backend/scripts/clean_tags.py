"""
Clean the tag vocabulary in entries.tags.

What it fixes, and why each is a defect rather than a taste:

  * dates stored as tags — "1973-01-01" beside "study, neurobiology". A date is
    not a subject: it duplicated `entry_date` and turned the "All tags" filter
    into a list of years.
  * stop words from topic splitting — "the", "of", "and" arrived when the
    research pipeline cut a topic into its first four words. Fixed at the
    source in research.py; this repairs the rows already written.
  * duplicates and stray whitespace inside one row.

What it does NOT touch: real subject tags, however rare. "Carey" and "Fodor"
are authors someone chose to tag; they are not this script's business.

Usage:
    python -m scripts.clean_tags              # dry run: print what would change
    python -m scripts.clean_tags --apply      # write, after making a backup
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db import DB_PATH, get_db  # noqa: E402
from app.tagging import ISO_DATE, STOPWORDS, is_noise_tag, normalize_tags  # noqa: E402


def is_noise(tag: str) -> bool:
    """True for a tag that should not be in the list at all."""
    return is_noise_tag(tag)


def clean_tags(raw: str | None) -> str:
    """Return the tag string with dates, stop words and duplicates removed.

    The separator stays `,` (no space) on purpose: this script rewrites rows
    in place, and the smallest diff is the one a reviewer can read.
    """
    return normalize_tags(raw, sep=",")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (a backup is made first)")
    args = ap.parse_args()

    conn = get_db()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, title, tags FROM entries ORDER BY id").fetchall()
        changes = []
        for row in rows:
            old = row["tags"] or ""
            new = clean_tags(old)
            if new != old:
                changes.append((row["id"], row["title"], old, new))

        if not changes:
            print("Nimic de curățat: tag-urile sunt deja curate.")
            return 0

        dates = sum(1 for _, _, o, n in changes
                    if any(ISO_DATE.match(t.strip()) for t in o.split(",") if t.strip()))
        stop = sum(1 for _, _, o, n in changes
                   if any(t.strip().lower() in STOPWORDS for t in o.split(",") if t.strip()))
        print(f"{len(changes)} intrări de schimbat "
              f"({dates} cu date în tag-uri, {stop} cu cuvinte de umplutură)\n")
        for entry_id, title, old, new in changes[:5]:
            print(f"  #{entry_id} {title[:50]}")
            print(f"     înainte: {old[:90]}")
            print(f"     după   : {new[:90]}")
        if len(changes) > 5:
            print(f"  … și alte {len(changes) - 5}")

        if not args.apply:
            print("\nProbă (dry run). Scrie cu --apply.")
            return 0

        backup = DB_PATH.with_name(
            f"{DB_PATH.name}.bak-tags-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(DB_PATH, backup)
        print(f"\nBackup: {backup}")

        with conn:
            for entry_id, _title, _old, new in changes:
                conn.execute("UPDATE entries SET tags = ? WHERE id = ?", [new, entry_id])

        # The FTS index mirrors the tags column through a trigger, but a stored
        # index can drift; one check keeps search honest.
        n_rows = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        n_fts = conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0]
        print(f"Scris: {len(changes)} rânduri. FTS {n_fts} rânduri / {n_rows} intrări.")
        if n_fts != n_rows:
            print("ATENȚIE: indexul FTS nu mai corespunde. Rulează db.rebuild_fts().")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
