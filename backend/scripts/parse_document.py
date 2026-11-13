"""Parse a study PDF page by page: native text now, vision for scanned pages.

    ./.venv/bin/python -m scripts.parse_document 278 --dry-run
    ./.venv/bin/python -m scripts.parse_document 553 --dry-run --pages 1-3
    ./.venv/bin/python -m scripts.parse_document 553 --pages 1-3 --dpi 150

``--dry-run`` classifies every page and reports the rendered PNG size, cost and
nothing else: it opens no database and calls no model. Without it, native pages
go through the canonical-text pipeline and scanned pages are transcribed by the
configured vision model, with one row per page in ``page_texts``.

Per page the table shows characters returned, PNG bytes, DPI and wall-clock
seconds, which is the evidence needed before choosing a default DPI or claiming
local CPU parsing is realistic.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app import studies_store  # noqa: E402
from app.document_parse import (  # noqa: E402
    detect_kind,
    page_image_path,
    parse_document,
    render_page_png,
)


def parse_pages(spec: str | None) -> list[int] | None:
    """Parse ``1-3`` or ``1,3,5`` into a page list; ``None`` means all pages."""
    if not spec:
        return None
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            start, end = int(lo), int(hi)
            if end < start:
                raise SystemExit(f"Interval invalid: {part}")
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages)) or None


def resolve_pdf(entry_id: int) -> Path:
    path = studies_store.pdf_path_for(entry_id)
    if not path:
        raise SystemExit(f"Entry {entry_id} has no stored PDF in {studies_store.STUDIES_DIR}")
    return Path(path)


def _classify_line(page: int, kind: str, png_bytes: int | None, dpi: int) -> str:
    size = "-" if png_bytes is None else f"{png_bytes:,}"
    return f"{page:>4}  {kind:<7}  {size:>10}  {dpi:>4}"


def dry_run(entry_id: int, path: Path, pages: list[int] | None, dpi: int) -> int:
    """Classify + render sizes only. No DB, no model, no files written."""
    info = detect_kind(path)
    kind = info["kind"]
    pages_n = info["pages"]
    print(f"entry {entry_id} · {path.name}")
    print(
        f"kind: {kind} ({pages_n} pages: {info['pages_native']} native, "
        f"{info['pages_scanned']} scanned, {info['pages_empty']} empty)"
    )
    if info.get("reason"):
        print(f"reason: {info['reason']}")
    if kind == "empty" and pages_n == 0:
        print("nothing to render; no model would be called")
        return 0
    page_kinds = info.get("page_kinds") or []
    selected = [p for p in (pages or range(1, pages_n + 1)) if 1 <= p <= pages_n]
    print()
    print(f"{'page':>4}  {'kind':<7}  {'png bytes':>10}  {'dpi':>4}")
    for page in selected:
        page_kind = page_kinds[page - 1]
        png_bytes = None
        if page_kind == "scanned":
            started = time.monotonic()
            png_bytes = len(render_page_png(path, page, dpi=dpi))
            elapsed = time.monotonic() - started
            line = _classify_line(page, page_kind, png_bytes, dpi)
            print(f"{line}  (render {elapsed:.2f}s)")
        else:
            print(_classify_line(page, page_kind, None, dpi))
    print()
    print(f"(dry run: {len(selected)} pages inspected, nothing written)")
    return 0


def _print_summary(result: dict, entry_id: int, path: Path) -> None:
    print(f"entry {entry_id} · {path.name}")
    print(f"kind: {result.get('kind')} · status: {result.get('status')}")
    rows = result.get("pages") or []
    if rows:
        print()
        print(f"{'page':>4}  {'kind':<7}  {'status':<16}  {'chars':>6}  "
              f"{'png_B':>9}  {'dpi':>4}  {'s':>6}  reason")
        for row in rows:
            print(
                f"{row['page']:>4}  {row['kind']:<7}  {row['status']:<16}  "
                f"{row['chars']:>6}  {row['png_bytes']:>9}  {row['dpi']:>4}  "
                f"{row['elapsed_s']:>6.2f}  {row.get('reason') or ''}"
            )
    print()
    print(
        f"summary: parsed={result.get('parsed', 0)} native={result.get('native', 0)} "
        f"vision={result.get('vision', 0)} uncertain={result.get('uncertain', 0)} "
        f"empty={result.get('empty', 0)} rewritten={result.get('rewritten', 0)}"
    )
    if result.get("reason"):
        print(f"reason: {result['reason']}")


def run(entry_id: int, path: Path, pages: list[int] | None, dpi: int,
        provider: str | None, model: str | None, force: bool) -> int:
    from app.db import get_db, init_db

    init_db()
    conn = get_db()

    def progress(step: int, total: int, page_number: int, status: str) -> None:
        print(f"  [{step}/{total}] page {page_number}: {status}", flush=True)

    try:
        result = parse_document(
            conn, entry_id, path,
            provider=provider, model=model, pages=pages,
            force=force, dpi=dpi, on_progress=progress,
        )
    finally:
        conn.close()
    print()
    _print_summary(result, entry_id, path)
    return 0 if result.get("status") in ("ok", "empty") else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("entry_id", type=int, help="library entry id")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify + render sizes only; no DB writes, no model calls")
    ap.add_argument("--provider", default=None, help="vision provider (default: active)")
    ap.add_argument("--model", default=None, help="vision model for that provider")
    ap.add_argument("--pages", default=None, help="page range, e.g. 1-3 or 1,4,7")
    ap.add_argument("--dpi", type=int, default=150, help="render DPI (default 150)")
    ap.add_argument("--force", action="store_true",
                    help="re-render images and re-parse pages that already have text")
    args = ap.parse_args(argv)

    pages = parse_pages(args.pages)
    path = resolve_pdf(args.entry_id)
    if args.dry_run:
        return dry_run(args.entry_id, path, pages, args.dpi)
    return run(args.entry_id, path, pages, args.dpi,
               args.provider, args.model, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
