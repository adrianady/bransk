"""
Backfill highlight character offsets from canonical page texts.

For every `pdf_highlights` row with NULL offsets and a non-empty quote: make
sure `page_texts` exist for that entry (locating the PDF via
`studies_store.pdf_path_for`), then `locate_quote`; with `--apply`, write the
offsets. Default is a dry run that writes nothing.

Usage (from backend/):
    .venv/bin/python -m scripts.backfill_highlight_offsets
    .venv/bin/python -m scripts.backfill_highlight_offsets --limit 5
    .venv/bin/python -m scripts.backfill_highlight_offsets --entry-id 42
    .venv/bin/python -m scripts.backfill_highlight_offsets --apply

Only `pdf_highlights.start_offset/end_offset` change — quote/note text is
never deleted or rewritten.
"""
import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db import get_db, init_db  # noqa: E402


def _count_occurrences(norm_page: str, norm_quote: str) -> int:
    count = 0
    pos = 0
    while True:
        idx = norm_page.find(norm_quote, pos)
        if idx == -1:
            return count
        count += 1
        pos = idx + 1
        if count > 2:  # enough to call it ambiguous
            return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="scrie offset-urile găsite")
    ap.add_argument("--entry-id", type=int, default=0, help="doar highlight-urile acestei intrări")
    ap.add_argument("--limit", type=int, default=0, help="procesează cel mult N rânduri (0 = toate)")
    args = ap.parse_args()

    init_db()

    from app.page_text import (
        _normalize,
        _normalize_with_map,
        ensure_page_texts,
        get_page_text,
        locate_quote,
    )
    from app.studies_store import pdf_path_for

    conn = get_db()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pdf_highlights)").fetchall()}
        if "start_offset" not in cols or "end_offset" not in cols:
            print("Tabela pdf_highlights nu are coloanele start_offset/end_offset — rulează mai întâi migrația.")
            return 2
        sql = (
            "SELECT id, entry_id, page_number, quote FROM pdf_highlights "
            "WHERE (start_offset IS NULL OR end_offset IS NULL) "
            "AND COALESCE(quote, '') != ''"
        )
        params: list = []
        if args.entry_id:
            sql += " AND entry_id = ?"
            params.append(args.entry_id)
        sql += " ORDER BY id"
        if args.limit and args.limit > 0:
            sql += " LIMIT ?"
            params.append(args.limit)
        rows = conn.execute(sql, params).fetchall()

        scanned = len(rows)
        matched = 0
        ambiguous = 0
        unmatched = 0
        written = 0
        # Cache which entries already had ensure_page_texts attempted.
        ensured: set[int] = set()

        for row in rows:
            hid = row["id"]
            entry_id = row["entry_id"]
            page_number = row["page_number"]
            quote = row["quote"] or ""
            if page_number is None or not quote.strip():
                unmatched += 1
                continue
            if args.apply:
                if entry_id not in ensured:
                    ensured.add(entry_id)
                    try:
                        has = conn.execute(
                            "SELECT 1 FROM page_texts WHERE entry_id = ? LIMIT 1",
                            [entry_id],
                        ).fetchone()
                    except Exception:
                        has = True
                    if not has:
                        try:
                            pdf_path = pdf_path_for(entry_id)
                        except Exception:
                            pdf_path = None
                        if pdf_path is not None and Path(pdf_path).exists():
                            try:
                                ensure_page_texts(conn, entry_id, pdf_path)
                            except Exception:
                                pass
                page = get_page_text(conn, entry_id, page_number)
                page_text = (page.get("text") if page else "") or ""
                if not page_text.strip():
                    unmatched += 1
                    continue
            else:
                # Dry run: strictly read-only — no page_texts writes. Use the
                # stored row when present, else extract the PDF in memory.
                page = get_page_text(conn, entry_id, page_number)
                page_text = (page.get("text") if page else "") or ""
                if not page_text.strip():
                    try:
                        pdf_path = pdf_path_for(entry_id)
                    except Exception:
                        pdf_path = None
                    if pdf_path is not None and Path(pdf_path).exists():
                        try:
                            from app.page_text import extract_canonical_pages

                            mem_pages = dict(extract_canonical_pages(pdf_path))
                            page_text = mem_pages.get(page_number, "")
                        except Exception:
                            page_text = ""
                    if not (page_text or "").strip():
                        unmatched += 1
                        continue
            norm_quote = _normalize(quote)
            if not norm_quote:
                unmatched += 1
                continue
            norm_page, _origin = _normalize_with_map(page_text)
            if not norm_page:
                unmatched += 1
                continue
            occ = _count_occurrences(norm_page, norm_quote)
            if occ == 0:
                unmatched += 1
            elif occ > 1:
                ambiguous += 1
            else:
                matched += 1
                if args.apply:
                    found = locate_quote(conn, entry_id, page_number, quote)
                    if found is None:
                        matched -= 1
                        unmatched += 1
                        continue
                    conn.execute(
                        "UPDATE pdf_highlights SET start_offset = ?, end_offset = ? WHERE id = ?",
                        [found[0], found[1], hid],
                    )
                    written += 1
        if args.apply:
            conn.commit()

        scope = f" pentru intrarea {args.entry_id}" if args.entry_id else ""
        print(f"Highlight-uri scanate{scope}: {scanned} (scanned)")
        print(f"Potrivite univoc (matched): {matched}")
        print(f"Ambigue (ambiguous, mai multe apariții): {ambiguous}")
        print(f"Nepotrivite (unmatched, fără text canonic sau fără potrivire): {unmatched}")
        if args.apply:
            print(f"Scrise (written): {written}")
        else:
            print("\nProbă (dry run). Nu s-a scris nimic; scrie cu --apply.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
