"""
Resolve real DOIs for studies whose catalogue titles are paraphrases.

The catalogue's own titles are often descriptions ("Brain Decoding Visual
Reconstruction (Kamitani Lab)") rather than bibliographic titles, so a plain
Crossref title search fails on 93 of 111 studies. Here an LLM proposes the real
title, then Crossref — not the model — decides: the DOI is accepted only when a
candidate matches on title, author and year. The model can therefore be wrong
without doing damage; a wrong proposal simply fails verification.

    .venv/bin/python scripts/resolve_dois_with_llm.py --limit 15 --dry-run
    .venv/bin/python scripts/resolve_dois_with_llm.py                  # all
    .venv/bin/python scripts/resolve_dois_with_llm.py --report out.csv

Every study that already has a DOI is skipped, so the script is safe to re-run.
"""
import argparse
import asyncio
import csv
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import httpx  # noqa: E402

from app.db import DB_PATH  # noqa: E402
from app.llm import LLM_PROVIDER, complete as llm_complete  # noqa: E402
from rebuild_dois import (  # noqa: E402
    crossref_year, entry_year, surnames, title_similarity,
)

CROSSREF = "https://api.crossref.org/works"

# Crossref record types that are *about* a work rather than the work itself:
# a dataset record named "How the Mind Works" is not Pinker's book.
REJECTED_TYPES = {
    "book-review", "dataset", "component", "peer-review", "editorial",
    "letter", "erratum", "correction", "grant",
}
MAILTO = "chronokg@localhost"
UA = f"ChronoKG/0.1 (mailto:{MAILTO})"

PROMPT = """Ești bibliotecar academic. Pentru fiecare descriere de mai jos, identifică lucrarea științifică reală pe care o descrie și dă titlul ei bibliografic exact.

Reguli stricte:
- Dacă nu ești sigur de titlul exact al lucrării, scrie null. Nu inventa.
- Nu inventa autori sau ani.
- Titlul trebuie să fie cel publicat, nu o descriere a subiectului.
- Răspunde DOAR cu JSON valid, fără alt text.

Format: {{"rezultate": [{{"index": 0, "titlu": "...sau null...", "autor": "...sau null...", "an": 2018, "incredere": "mare|medie|mica"}}]}}

DESCRIERI:
{lista}
"""


def _fold(s: str) -> str:
    d = unicodedata.normalize("NFKD", s or "")
    return " ".join("".join(c for c in d if not unicodedata.combining(c)).lower().split())


def studies_without_doi(limit: int | None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        sql = """SELECT id, title, authors, entry_date, content, tags
                   FROM entries
                  WHERE archived = 0
                    AND (study_type IS NOT NULL AND study_type != ''
                         OR tags LIKE '%study%' OR tags LIKE '%pdf%')
                    AND (doi IS NULL OR doi = '')
                  ORDER BY id"""
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


async def propose(client: httpx.AsyncClient, batch: list[dict]) -> dict[int, dict]:
    """Ask the model for real titles for a batch. Returns {entry_id: proposal}."""
    lista = "\n".join(
        f'{i}. Titlu catalog: "{e["title"]}" | Conținut: "{(e.get("content") or "")[:220].replace(chr(10), " ")}"'
        for i, e in enumerate(batch)
    )
    messages = [{"role": "system", "content": PROMPT.format(lista=lista)}]
    try:
        provider, model, text = await llm_complete(messages, temperature=0.1, max_tokens=2000)
    except Exception as exc:  # noqa: BLE001 - provider errors are heterogeneous
        print(f"    LLM a eșuat: {type(exc).__name__}: {exc}")
        return {}
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        print("    LLM nu a răspuns cu JSON")
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        print(f"    JSON invalid: {exc}")
        return {}
    out = {}
    for item in data.get("rezultate") or []:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            out[batch[idx]["id"]] = item
    return out


async def find_doi(client: httpx.AsyncClient, title: str, author: str | None,
                   year: int | None, min_title: float) -> tuple[dict | None, str]:
    """Crossref lookup for a proposed title; accept only a corroborated hit."""
    params = {"query.bibliographic": title, "rows": 5,
              "select": "DOI,title,author,issued,container-title,type"}
    if author:
        params["query.author"] = author
    try:
        resp = await client.get(CROSSREF, params=params, timeout=40.0)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception as exc:  # noqa: BLE001
        return None, f"Crossref eroare: {type(exc).__name__}"

    best = None
    for it in items:
        found = (it.get("title") or [""])[0]
        if not found:
            continue
        if (it.get("type") or "") in REJECTED_TYPES:
            continue
        sim = title_similarity(title, found)
        if sim < min_title:
            continue
        found_surnames = {
            w for a in (it.get("author") or [])
            for w in re.findall(r"[a-z]{4,}", _fold(a.get("family") or ""))
        }
        want = surnames(author or "")
        if want and found_surnames:
            overlap = len(want & found_surnames) / len(want)
            if overlap < 0.34:
                continue
        yr = crossref_year(it)
        if year and yr and abs(year - yr) > 3:
            continue
        best = {"doi": (it.get("DOI") or "").lower(), "title": found, "year": yr,
                "sim": sim, "authors": sorted(found_surnames)[:3]}
        break
    if best:
        return best, ""
    return None, "niciun candidat nu trece verificarea"


def save(entry_id: int, best: dict, proposal: dict) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """UPDATE entries
                  SET doi = ?, doi_source = 'crossref+llm', doi_checked_at = ?,
                      bib_title = ?, bib_year = ?, bib_authors = ?
                WHERE id = ?""",
            [best["doi"], datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
             best["title"], str(best["year"] or ""),
             ", ".join(best["authors"]), entry_id],
        )
        conn.commit()
    finally:
        conn.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--min-title", type=float, default=0.85)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    if LLM_PROVIDER == "mock":
        print("LLM_PROVIDER=mock — nimic de făcut.")
        return 1

    rows = studies_without_doi(args.limit)
    print(f"studii fără DOI: {len(rows)} | lot: {args.batch} | prag titlu: {args.min_title:.0%}\n")

    found, missed = [], []
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        for start in range(0, len(rows), args.batch):
            batch = rows[start:start + args.batch]
            print(f"— lot {start // args.batch + 1}: #{batch[0]['id']}…#{batch[-1]['id']}", flush=True)
            proposals = await propose(client, batch)
            for entry in batch:
                prop = proposals.get(entry["id"]) or {}
                title = (prop.get("titlu") or "").strip()
                if not title or title.lower() in {"null", "none", "-"}:
                    missed.append((entry["id"], entry["title"], "modelul nu a știut titlul", "", ""))
                    print(f"   #{entry['id']:3} ✗ fără titlu de la model")
                    continue
                author = (prop.get("autor") or "").strip() or entry.get("authors") or ""
                try:
                    year = int(prop.get("an")) if prop.get("an") else entry_year(entry)
                except (TypeError, ValueError):
                    year = entry_year(entry)
                best, why = await find_doi(client, title, author, year, args.min_title)
                if best:
                    found.append((entry["id"], entry["title"], title, best, prop.get("incredere") or ""))
                    print(f"   #{entry['id']:3} ✓ {best['doi']}  «{best['title'][:44]}» ({best['year']})")
                    if not args.dry_run:
                        save(entry["id"], best, prop)
                else:
                    missed.append((entry["id"], entry["title"], why, title, str(year or "")))
                    print(f"   #{entry['id']:3} ✗ {why}  (propus: «{title[:40]}»)")
                await asyncio.sleep(0.3)

    print("\n" + "=" * 72)
    print(f"DOI nou atribuit: {len(found)} | rămas fără: {len(missed)}")
    if args.report:
        with open(args.report, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "status", "titlu_catalog", "titlu_propus", "doi", "titlu_crossref", "an", "incredere", "motiv"])
            for entry_id, cat, prop_title, best, conf in found:
                w.writerow([entry_id, "atribuit", cat, prop_title, best["doi"], best["title"], best["year"], conf, ""])
            for entry_id, cat, why, prop_title, year in missed:
                w.writerow([entry_id, "neatribuit", cat, prop_title, "", "", year, "", why])
        print(f"raport: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
