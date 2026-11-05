"""
Find and download real open-access PDFs for studies, matched by TITLE.

Why not the stored URL: the research pipeline sometimes attached a plausible
but wrong PMC id (the same PMC3181763 was reused for six unrelated studies),
and PMC itself now hides PDFs behind a JS/reCAPTCHA challenge. Matching by
title against OpenAlex is both more accurate and legitimate: it uses the
metadata the paper actually has, and only takes links the publisher marks as
open access.

    .venv/bin/python scripts/fetch_pdfs_by_title.py                  # all
    .venv/bin/python scripts/fetch_pdfs_by_title.py --limit 5        # test
    .venv/bin/python scripts/fetch_pdfs_by_title.py --dry-run        # preview
    .venv/bin/python scripts/fetch_pdfs_by_title.py --min-score 0.8  # stricter

A study is only marked downloaded when a real PDF (magic bytes %PDF-) landed on
disk and the matched title was close enough to the study's own title.
"""
import argparse
import asyncio
import difflib
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from app.db import DB_PATH  # noqa: E402
from app.studies_store import (  # noqa: E402
    MAX_SIZE, STUDIES_DIR, assert_public_download_url, is_valid_pdf_file,
    pdf_filename,
)

OPENALEX = "https://api.openalex.org/works"
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
}


def _clean_title(title: str) -> str:
    """Strip the year/authors the catalogue adds, which confuse the search."""
    t = re.sub(r"\([^)]*\d{4}[^)]*\)", " ", title or "")
    # Catalogue entries often look like "Friston - Free Energy Principle (2010)"
    # or "Kamitani - Brain Decoding (2022)"; the author prefix is not part of
    # the paper's title and wrecks a title search.
    t = re.sub(r"^[A-Z][\w.'&-]*(?:\s*&\s*[A-Z][\w.'&-]*)?\s+[-–—]\s+", "", t.strip())
    t = re.sub(r"\s*-\s*[A-Z][^,]{2,40}\(\d{4}\)\s*$", " ", t)
    # A trailing institution/lab in parentheses is catalogue metadata too:
    # "Machine Theory of Mind (DeepMind)".
    t = re.sub(r"\s*\((?:[A-Z][\w&.'-]*|et al\.?)\)\s*$", "", t.strip())
    return " ".join(t.split())


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _fold(text: str) -> str:
    """Lowercase and strip diacritics so Buzsáki matches Buzsaki."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Romanian letters that NFKD leaves alone.
    return plain.lower().translate(str.maketrans("șțăîşţ", "stais t".replace(" ", "")))


def _author_overlap(catalog_authors: str, found_authors: str) -> float:
    """Share of the catalogue's surnames that appear in the found author list.

    Title matching alone is not enough: a self-published essay called
    "Nature of human intelligence" scores ~90% against Sternberg's book of the
    same name. Authors are the tie-breaker. Empty input means "unknown", which
    is not penalised — 1.0.
    """
    def surnames(text: str) -> set[str]:
        # Split on separators, keep word tokens of 4+ letters (initials dropped).
        parts = re.split(r"[,;&]|\band\b|\s{2,}", _fold(text))
        words = {w for part in parts for w in re.findall(r"[a-z]{4,}", part)}
        return words

    known = surnames(catalog_authors)
    if not known:
        return 1.0
    if not (found_authors or "").strip():
        return 0.0
    found = surnames(found_authors)
    if not found:
        return 0.0
    return len(known & found) / len(known)


async def search_openalex(client: httpx.AsyncClient, title: str) -> dict | None:
    """Best open-access candidate for *title*, or None."""
    try:
        resp = await client.get(
            OPENALEX,
            params={"search": _clean_title(title), "per_page": 5, "mailto": "chronokg@localhost"},
            timeout=30.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:  # noqa: BLE001 - network/metadata errors are best-effort
        return None

    best = None
    for work in results:
        found = work.get("title") or ""
        score = _similarity(_clean_title(title), _clean_title(found))
        oa = work.get("open_access") or {}
        url = oa.get("oa_url") or ""
        if not url and work.get("doi"):
            url = ""  # DOI alone is not a PDF; skip unless a direct link exists
        if not url:
            continue
        authors = "; ".join(
            a.get("author", {}).get("display_name", "")
            for a in (work.get("authorships") or [])[:12]
            if a.get("author", {}).get("display_name")
        )
        candidate = {
            "title": found, "score": score, "url": url,
            "doi": work.get("doi") or "", "authors": authors,
        }
        if best is None or candidate["score"] > best["score"]:
            best = candidate
    return best


async def download(client: httpx.AsyncClient, url: str, dest: Path) -> tuple[bool, str]:
    try:
        await asyncio.to_thread(assert_public_download_url, url)
    except ValueError as exc:
        return False, f"URL refuzat: {exc}"

    tmp = dest.with_suffix(".part")
    try:
        async with client.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}"
            size = 0
            head = b""
            with open(tmp, "wb") as out:
                async for piece in resp.aiter_bytes(65536):
                    size += len(piece)
                    if size > MAX_SIZE:
                        tmp.unlink(missing_ok=True)
                        return False, "prea mare"
                    if len(head) < 1024:
                        head += piece[: 1024 - len(head)]
                    out.write(piece)
        if not head.startswith(b"%PDF-"):
            tmp.unlink(missing_ok=True)
            if b"<" in head[:20]:
                return False, "link duce la pagină web, nu la PDF"
            return False, "nu e PDF"
        tmp.replace(dest)
        return True, f"{size // 1024} KB"
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return False, type(exc).__name__


def set_pdf(entry_id: int, filename: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("UPDATE entries SET pdf_path = ? WHERE id = ?", [filename, entry_id])
        conn.commit()
    finally:
        conn.close()


def studies(limit: int | None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        sql = """SELECT id, title, authors FROM entries
                  WHERE archived = 0
                    AND (pdf_path IS NULL OR pdf_path = '')
                    AND (study_type IS NOT NULL AND study_type != ''
                         OR tags LIKE '%study%' OR tags LIKE '%pdf%')
                  ORDER BY id"""
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-score", type=float, default=0.72,
                    help="how close the found title must be (0-1)")
    args = ap.parse_args()

    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    rows = studies(args.limit)
    print(f"studii fără PDF: {len(rows)} | prag potrivire titlu: {args.min_score:.0%}")
    print(f"destinație: {STUDIES_DIR}\n")

    saved, skipped, failed = [], [], []
    async with httpx.AsyncClient(headers=_UA, follow_redirects=True) as client:
        for i, row in enumerate(rows, 1):
            title = row["title"]
            cand = await search_openalex(client, title)
            if not cand:
                skipped.append((row["id"], title, "nicio lucrare în acces liber"))
                print(f"[{i}/{len(rows)}] #{row['id']} — nimic în acces liber")
                continue
            if cand["score"] < args.min_score:
                skipped.append((row["id"], title, f"potrivire slabă ({cand['score']:.0%}): {cand['title'][:40]}"))
                print(f"[{i}/{len(rows)}] #{row['id']} — potrivire slabă {cand['score']:.0%}")
                continue
            authors_ok = _author_overlap(row.get("authors") or "", cand.get("authors") or "")
            if authors_ok < 0.34:
                skipped.append((
                    row["id"], title,
                    f"autori diferiți ({authors_ok:.0%}): găsit «{cand.get('authors','')[:40]}»",
                ))
                print(f"[{i}/{len(rows)}] #{row['id']} — autori diferiți {authors_ok:.0%} "
                      f"(găsit: {cand.get('authors','')[:35]})")
                continue
            if args.dry_run:
                print(f"[{i}/{len(rows)}] #{row['id']} {cand['score']:.0%} {cand['url'][:70]}")
                continue

            dest = STUDIES_DIR / pdf_filename(row["id"], title)
            ok, reason = await download(client, cand["url"], dest)
            if ok:
                set_pdf(row["id"], dest.name)
                saved.append((row["id"], title, cand["score"], reason))
                print(f"[{i}/{len(rows)}] #{row['id']} OK ({reason}, potrivire {cand['score']:.0%}) — {title[:40]}")
            else:
                failed.append((row["id"], title, f"{reason} — {cand['url'][:50]}"))
                print(f"[{i}/{len(rows)}] #{row['id']} eșec: {reason}")

    print("\n" + "=" * 70)
    print(f"descărcate: {len(saved)} | fără acces liber: {len(skipped)} | eșuate: {len(failed)}")
    if saved:
        print("\nDescărcate:")
        for entry_id, title, score, size in saved:
            print(f"  #{entry_id:3} ({score:.0%}, {size}) {title[:55]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
