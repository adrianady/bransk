"""
Download every study PDF that has a source URL, with a per-study report.

Run from backend/:
    .venv/bin/python scripts/download_all_pdfs.py            # all studies
    .venv/bin/python scripts/download_all_pdfs.py --limit 5   # a few, to test
    .venv/bin/python scripts/download_all_pdfs.py --dry-run   # only list

Nothing is invented: a study is reported as downloaded only when a real PDF
landed on disk (checked by the %PDF- magic bytes) and was written to its entry.
"""
import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db import DB_PATH  # noqa: E402
from app.studies_store import (  # noqa: E402
    MAX_SIZE, STUDIES_DIR, assert_public_download_url, is_valid_pdf_file,
    pdf_filename,
)
from app.pdf_rag import index_pdf  # noqa: E402

import httpx  # noqa: E402

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Pages that are known to hide the real file behind a JS challenge. Detected by
# the page title so the report tells the user *why* a download failed.
_JS_CHALLENGE_MARKERS = (
    b"Preparing to download",
    b"Just a moment",
    b"Checking your browser",
    b"Enable JavaScript",
    b"cf-browser-verification",
)


def _looks_like_challenge(payload: bytes) -> bool:
    head = payload[:4000]
    return any(marker in head for marker in _JS_CHALLENGE_MARKERS)


async def fetch_pdf(url: str, dest: Path) -> tuple[bool, str]:
    """Try to save a real PDF at *dest*. Returns (ok, reason)."""
    try:
        await asyncio.to_thread(assert_public_download_url, url)
    except ValueError as exc:
        return False, f"URL refuzat: {exc}"

    try:
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True, headers=_UA, max_redirects=8
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return False, f"HTTP {resp.status_code}"
                chunk = b""
                size = 0
                tmp = dest.with_suffix(".part")
                with open(tmp, "wb") as out:
                    async for piece in resp.aiter_bytes(65536):
                        size += len(piece)
                        if size > MAX_SIZE:
                            tmp.unlink(missing_ok=True)
                            return False, "fișier prea mare"
                        if len(chunk) < 8192:
                            chunk += piece[: 8192 - len(chunk)]
                        out.write(piece)
        if not chunk.startswith(b"%PDF-"):
            tmp.unlink(missing_ok=True)
            if _looks_like_challenge(chunk):
                return False, "protecție anti-bot (pagină JavaScript, nu PDF)"
            ctype = chunk[:120].decode("utf-8", "ignore").strip().replace("\n", " ")
            return False, f"nu e PDF ({ctype[:60] or 'necunoscut'})"
        tmp.replace(dest)
        return True, f"{size // 1024} KB"
    except httpx.TimeoutException:
        return False, "timeout"
    except Exception as exc:  # noqa: BLE001 - network errors are heterogeneous
        return False, f"{type(exc).__name__}"


def _set_pdf_path(entry_id: int, filename: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("UPDATE entries SET pdf_path = ? WHERE id = ?", [filename, entry_id])
        conn.commit()
    finally:
        conn.close()


def _studies(limit: int | None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        sql = """SELECT id, title, source FROM entries
                  WHERE archived = 0
                    AND source LIKE 'http%'
                    AND (pdf_path IS NULL OR pdf_path = '')
                  ORDER BY (source LIKE '%.pdf') DESC, id"""
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    rows = _studies(args.limit)
    print(f"PDF-uri în {STUDIES_DIR}")
    print(f"studii fără PDF local: {len(rows)}\n")

    if args.dry_run:
        for r in rows:
            print(f"  #{r['id']:3} {r['source'][:90]}")
        return 0

    ok, failed, indexed = [], [], 0
    for i, row in enumerate(rows, 1):
        dest = STUDIES_DIR / pdf_filename(row["id"], row["title"])
        print(f"[{i}/{len(rows)}] #{row['id']} {row['title'][:60]}", flush=True)
        if is_valid_pdf_file(dest):
            _set_pdf_path(row["id"], dest.name)
            ok.append((row["id"], row["title"], "deja pe disc"))
            print("    deja pe disc")
            continue
        good, reason = await fetch_pdf(row["source"], dest)
        if good:
            _set_pdf_path(row["id"], dest.name)
            ok.append((row["id"], row["title"], reason))
            print(f"    OK ({reason})")
            try:
                chunks = index_pdf(row["id"], dest)
                indexed += chunks or 0
                print(f"    indexat: {chunks} chunks")
            except Exception as exc:  # noqa: BLE001 - indexing is best-effort
                print(f"    indexare eșuată: {type(exc).__name__}: {exc}")
        else:
            failed.append((row["id"], row["title"], reason))
            print(f"    EȘEC: {reason}")

    print("\n" + "=" * 70)
    print(f"REZULTAT: {len(ok)} descărcate, {len(failed)} eșuate, {indexed} chunks indexați")
    if failed:
        print("\nEșuate (cu motiv):")
        for entry_id, title, reason in failed:
            print(f"  #{entry_id:3} {title[:55]:55} — {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
