"""
Rebuild the study library's bibliographic identifiers with real Crossref DOIs.

The problem this fixes: the research pipeline attached plausible but wrong
source URLs (19 of 29 PMC links pointed at an unrelated paper; PMC3181763 was
reused for six different studies). A DOI resolved against Crossref is a real
identifier, so it replaces guessing.

Rules — a DOI is accepted ONLY when all of these hold:
  * the found title matches closely (normalised similarity ≥ --min-title)
  * the found year is within --year-window of the catalogue year, when both known
  * if the catalogue lists authors, at least one surname matches
Everything else is left empty and reported as unmatched. Nothing is guessed:
an entry with no DOI is honest, an entry with a wrong DOI is worse than none.

    .venv/bin/python scripts/rebuild_dois.py --dry-run        # preview only
    .venv/bin/python scripts/rebuild_dois.py --limit 10       # a few
    .venv/bin/python scripts/rebuild_dois.py                  # all studies
    .venv/bin/python scripts/rebuild_dois.py --report out.csv # export details

Crossref asks for a contact address in the User-Agent; set CROSSREF_MAILTO to
yours to be polite (it also gets you the faster "polite pool").
"""
import argparse
import asyncio
import csv
import difflib
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from app.db import DB_PATH  # noqa: E402

CROSSREF = "https://api.crossref.org/works"
MAILTO = os.getenv("CROSSREF_MAILTO", "chronokg@localhost")
UA = f"ChronoKG/0.1 (mailto:{MAILTO})"

# Titles the pipeline paraphrased often carry a trailing catalogue annotation
# such as "(DeepMind)" or "(2016)"; strip that before searching.
_YEAR_PAREN = re.compile(r"\([^)]*\d{4}[^)]*\)")
_TRAIL_INST = re.compile(r"\s*\((?:[A-Z][\w&.'-]*|et al\.?)\)\s*$")
_AUTHOR_PREFIX = re.compile(r"^[A-Z][\w.'&-]*(?:\s*&\s*[A-Z][\w.'&-]*)?\s+[-–—]\s+")


def _fold(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.lower().split())


_ARTICLES = re.compile(r"^(the|a|an)\s+", re.I)


def clean_title(title: str) -> str:
    t = _YEAR_PAREN.sub(" ", title or "")
    t = _AUTHOR_PREFIX.sub("", t)
    t = _TRAIL_INST.sub("", t.strip())
    t = _ARTICLES.sub("", t)
    return " ".join(t.split())


def title_similarity(a: str, b: str) -> float:
    """Token-set aware similarity: word order and punctuation vary between sources."""
    fa, fb = _fold(clean_title(a)), _fold(clean_title(b))
    if not fa or not fb:
        return 0.0
    ratio = difflib.SequenceMatcher(None, fa, fb).ratio()
    # Also compare as word sets, which forgives "A: B" vs "B — A" reshuffles.
    wa = set(re.findall(r"[a-z0-9]{3,}", fa))
    wb = set(re.findall(r"[a-z0-9]{3,}", fb))
    jaccard = len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0
    return max(ratio, jaccard)


def surnames(authors: str) -> set[str]:
    """Lowercase surname-ish tokens (4+ letters) from a free-form author string."""
    if not authors:
        return set()
    parts = re.split(r"[,;&]|\band\b|\s{3,}", _fold(authors))
    return {w for part in parts for w in re.findall(r"[a-z]{4,}", part)}


_TRAIL_AUTHORS = re.compile(r"\(([^)]{3,80})\)\s*$")


def catalogue_authors(entry: dict) -> str:
    """Author text from the authors column, or from the title itself.

    The pipeline often left `authors` empty and wrote the names into the title,
    in either shape: "Clark & Chalmers - Extended Mind (1998)" (prefix) or
    "Dual-Process Theories (Evans & Stanovich 2013)" (trailing bracket).
    """
    authors = (entry.get("authors") or "").strip()
    if authors and "unknown" not in authors.lower():
        return authors

    title = entry.get("title") or ""

    # Trailing bracket, minus the year.
    m = _TRAIL_AUTHORS.search(title)
    if m:
        inner = re.sub(r"\b\d{3,4}\b", " ", m.group(1))
        cleaned = " ".join(inner.replace("&", " ").replace(",", " ").split()).strip(" ;-")
        if cleaned:
            return cleaned

    # Leading "Surname [& Surname] - " before the real title.
    p = _AUTHOR_PREFIX.search(title)
    if p:
        prefix = p.group(0).rstrip(" -–—")
        cleaned = " ".join(prefix.replace("&", " ").replace(",", " ").split()).strip(" ;-")
        if cleaned and cleaned.lower() not in {"the", "a", "an"}:
            return cleaned
    return ""


def author_match(catalogue: str, found: set[str]) -> float:
    """Share of catalogue surnames present in the found set.

    1.0 when the catalogue has no authors (nothing to contradict); 0.0 when it
    has authors but the candidate lists none.
    """
    known = surnames(catalogue)
    if not known:
        return 1.0
    if not found:
        return 0.0
    return len(known & found) / len(known)


def entry_year(entry: dict) -> int | None:
    for key in ("entry_date", "bib_year"):
        raw = str(entry.get(key) or "")
        m = re.search(r"(\d{3,4})", raw)
        if m:
            year = int(m.group(1))
            # "0350-01-01" means 350 BCE; do not treat that as a modern year.
            if raw.strip().startswith(("-", "0")):
                return year
            return year
    return None


def crossref_year(item: dict) -> int | None:
    for key in ("issued", "published", "published-print", "published-online"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


async def search(client: httpx.AsyncClient, entry: dict, rows: int = 8) -> list[dict]:
    """Crossref candidates for one study, best first by title similarity."""
    title = clean_title(entry["title"])
    if len(title) < 8:
        return []
    params = {
        "query.bibliographic": title,
        "rows": rows,
        "select": "DOI,title,author,issued,type,container-title",
    }
    # Author names sharpen the search when the catalogue has them.
    known_authors = catalogue_authors(entry)
    if known_authors:
        params["query.author"] = known_authors[:120]
    try:
        resp = await client.get(CROSSREF, params=params, timeout=40.0)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception:  # noqa: BLE001 - network/metadata errors are best-effort
        return []

    out = []
    for item in items:
        found_title = (item.get("title") or [""])[0]
        if not found_title:
            continue
        found_authors = {
            w for a in (item.get("author") or [])
            for w in re.findall(r"[a-z]{4,}", _fold(a.get("family") or ""))
        }
        out.append({
            "doi": (item.get("DOI") or "").lower(),
            "title": found_title,
            "title_sim": title_similarity(title, found_title),
            "year": crossref_year(item),
            "authors_found": found_authors,
            "author_sim": author_match(catalogue_authors(entry), found_authors),
            "type": item.get("type") or "",
            "venue": (item.get("container-title") or [""])[0],
        })
    out.sort(key=lambda c: (c["title_sim"], c["author_sim"]), reverse=True)
    return out


def decide(entry: dict, candidates: list[dict], min_title: float, year_window: int,
           min_author: float = 0.5) -> tuple[dict | None, str]:
    """Pick the best candidate that passes every check, or explain refusal.

    Strict on purpose. A DOI that points at a different paper is worse than no
    DOI at all: it makes the thesis cite something the student never read. The
    bar is a near-exact title AND corroborated authorship (or a year match when
    the catalogue lists no authors to compare).
    """
    if not candidates:
        return None, "nicio potrivire în Crossref"
    cat_authors = catalogue_authors(entry)
    catalogue_has_authors = bool(surnames(cat_authors))
    year = entry_year(entry)

    # Walk candidates in order: the top hit is sometimes a commentary *about*
    # the paper, while a lower hit is the paper itself.
    best = None
    for cand in candidates:
        if cand["title_sim"] < min_title:
            continue
        if catalogue_has_authors:
            # No author metadata at all means identity cannot be corroborated;
            # a bare title match is how "Working Memory" became a 2007 chapter
            # and "Descartes' Error" a Spanish translation.
            if not cand["authors_found"]:
                continue
            if cand["author_sim"] < min_author:
                continue
        else:
            # No authors to confirm identity: demand a tighter title and, when
            # the catalogue has a year, agreement on it.
            if cand["title_sim"] < min(min_title + 0.1, 0.98):
                continue
            if year and cand["year"] and abs(year - cand["year"]) > year_window:
                continue
        best = cand
        break

    # Right authors, wrong year is still the wrong work: a 2007 chapter and the
    # 1974 paper share the title "Working Memory" and the same two authors.
    if best is not None and year and best["year"] and abs(year - best["year"]) > year_window:
        return None, f"an diferit ({year} vs {best['year']}): «{best['title'][:35]}»"

    if best is None:
        top = candidates[0]
        if top["title_sim"] < min_title:
            return None, f"titlu prea diferit ({top['title_sim']:.0%}): «{top['title'][:40]}»"
        if catalogue_has_authors and top["author_sim"] < min_author:
            return None, f"autori diferiți ({top['author_sim']:.0%})"
        return None, f"an/încredere insuficientă ({top['year']} vs {year})"

    if year and best["year"] and abs(year - best["year"]) > year_window:
        best = dict(best, year_note=f"an catalog {year} vs Crossref {best['year']}")
    return best, ""



def studies(limit: int | None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        sql = """SELECT id, title, authors, entry_date, source, doi, study_type, tags
                   FROM entries
                  WHERE archived = 0
                    AND (study_type IS NOT NULL AND study_type != ''
                         OR tags LIKE '%study%' OR tags LIKE '%pdf%')
                  ORDER BY id"""
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def save(entry_id: int, cand: dict) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """UPDATE entries
                  SET doi = ?, doi_source = 'crossref', doi_checked_at = ?,
                      bib_title = ?, bib_year = ?
                WHERE id = ?""",
            [
                cand["doi"],
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                cand["title"],
                str(cand["year"] or ""),
                entry_id,
            ],
        )
        conn.commit()
    finally:
        conn.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-title", type=float, default=0.82)
    ap.add_argument("--year-window", type=int, default=2)
    ap.add_argument("--report", default="")
    ap.add_argument("--delay", type=float, default=0.35, help="seconds between requests")
    args = ap.parse_args()

    rows = studies(args.limit)
    print(f"studii de procesat: {len(rows)}")
    print(f"praguri: titlu ≥ {args.min_title:.0%}, autori ≥ 34%, an ±{args.year_window}\n")

    matched, unmatched = [], []
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        for i, entry in enumerate(rows, 1):
            cands = await search(client, entry)
            best, why = decide(entry, cands, args.min_title, args.year_window)
            if best:
                matched.append((entry["id"], entry["title"], best))
                print(f"[{i}/{len(rows)}] #{entry['id']:3} ✓ {best['doi']}")
                print(f"      catalog: {clean_title(entry['title'])[:66]}")
                print(f"      găsit  : {best['title'][:66]} ({best['year']}) "
                      f"titlu {best['title_sim']:.0%}, autori {best['author_sim']:.0%}")
                if not args.dry_run:
                    save(entry["id"], best)
            else:
                unmatched.append((entry["id"], entry["title"], why,
                                  cands[0]["title"] if cands else "", cands[0]["doi"] if cands else ""))
                print(f"[{i}/{len(rows)}] #{entry['id']:3} ✗ {why}")
            if args.delay:
                await asyncio.sleep(args.delay)

    total = len(rows)
    print("\n" + "=" * 72)
    print(f"DOI atribuit: {len(matched)}/{total} ({len(matched)/total*100:.0f}%) | "
          f"neatribuit: {len(unmatched)}")
    if args.report:
        with open(args.report, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["id", "status", "titlu_catalog", "doi", "titlu_gasit", "an", "motiv"])
            for entry_id, title, best in matched:
                writer.writerow([entry_id, "atribuit", title, best["doi"], best["title"], best["year"], ""])
            for entry_id, title, why, found, doi in unmatched:
                writer.writerow([entry_id, "neatribuit", title, "", found, "", why])
        print(f"raport scris în {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
