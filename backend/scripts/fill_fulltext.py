"""Fill missing full text for studies, from every open source we may use.

    .venv/bin/python scripts/fill_fulltext.py --dry-run            # who has a candidate
    .venv/bin/python scripts/fill_fulltext.py --limit 10 --apply  # first ten, for real
    .venv/bin/python scripts/fill_fulltext.py --id 53 --apply     # one study
    .venv/bin/python scripts/fill_fulltext.py --apply --no-index  # keep the PDF, skip embeddings

Dry-run is the default: it only lists what each study can be fetched from
(copy lives on the HDD next to the project: backend/data/studies). `--apply`
writes PDFs and text; a backup of the database is taken first, because the
HTML path updates `entries.content`.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db import DB_PATH, get_db  # noqa: E402
from app.fulltext import fetch_fulltext, find_fulltext  # noqa: E402


def studies_without_text(limit: int | None, only_id: int | None) -> list[dict]:
    conn = get_db()
    try:
        if only_id is not None:
            rows = conn.execute(
                "SELECT id, title, doi, source, length(coalesce(content,'')) AS n, pdf_path "
                "FROM entries WHERE id = ?",
                [only_id],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, doi, source, length(coalesce(content,'')) AS n, pdf_path "
                "FROM entries WHERE study_type != '' AND archived = 0 "
                "AND coalesce(pdf_path,'') = '' AND length(coalesce(content,'')) < 2000 "
                "ORDER BY (coalesce(doi,'') != '') DESC, id"
            ).fetchall()
    finally:
        conn.close()
    out = [dict(r) for r in rows]
    return out[:limit] if limit else out


def backup_database() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(DB_PATH).with_name(f"chronokg.db.bak-fulltext-{stamp}")
    shutil.copy2(DB_PATH, dest)
    return dest


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write PDFs/text (default: dry run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit form of the default")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--id", type=int, default=None, dest="only_id")
    ap.add_argument("--no-index", action="store_true", help="do not embed the PDF for RAG")
    ap.add_argument("--sleep", type=float, default=1.0, help="pause between studies")
    args = ap.parse_args()

    rows = studies_without_text(args.limit, args.only_id)
    print(f"studii fără text integral: {len(rows)} | mod: {'APPLY' if args.apply else 'dry-run'} | "
          f"fișiere în: {BACKEND / 'data' / 'studies'}")
    if args.apply and rows:
        print("backup:", backup_database())

    stats = {"pdf": 0, "html": 0, "none": 0}
    for i, row in enumerate(rows, 1):
        prefix = f"[{i}/{len(rows)}] #{row['id']} {row['title'][:45]:45s}"
        if not args.apply:
            cands = await find_fulltext(row)
            if cands:
                srcs = ", ".join(f"{c.source}{'(html)' if c.kind == 'html' else ''}" for c in cands[:4])
                print(f"{prefix} candidați: {len(cands)} → {srcs}")
            else:
                print(f"{prefix} niciun candidat")
                stats["none"] += 1
            await asyncio.sleep(0.4)
            continue

        result = await fetch_fulltext(row["id"], index=not args.no_index)
        stats[result["status"]] += 1
        extra = f"{result['chunks']} chunks" if result["status"] == "pdf" else f"{result['chars']} car."
        tried = ", ".join(t.get("source", "?") for t in result["tried"]) or "-"
        print(f"{prefix} {result['status']:4s} prin {result['source'] or '-':14s} {extra:12s} (încercate: {tried})")
        await asyncio.sleep(max(0.0, args.sleep))

    print("\n" + "=" * 60)
    print(f"pdf: {stats['pdf']} | html: {stats['html']} | fără text: {stats['none']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
