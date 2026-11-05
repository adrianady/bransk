"""Resolve missing bibliographic sources with the app's own model.

Two things are missing in the library and they need different treatment:

  * a study with no DOI at all — the model proposes the real bibliographic
    title, and Crossref decides whether that title exists. The model can be
    wrong without doing damage: a wrong proposal simply fails verification, so
    the database never receives a DOI nobody checked.
  * a study whose text is a bare title — no amount of DOI work makes it
    citable. The model may draft what the study found, but only when it says
    it knows the work, and the draft is marked as a model summary so the text
    is never mistaken for something read from the paper.

The module is a service, not a script: the web app calls it, the progress and
the outcome are reported back to the screen.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone

import httpx

from app.db import get_db
from app.llm import LLM_PROVIDER, complete as llm_complete

logger = logging.getLogger("chronokg.sources")

CROSSREF = "https://api.crossref.org/works"
UA = "Bransk/0.1 (local research app; contact: the owner of this machine)"
REJECTED_TYPES = {"component", "dataset", "peer-review", "grant", "posted-content"}

# Crossref throttles anonymous callers hard: without a pause between requests
# it answers 429 and every lookup looks like a failure. A short wait — and one
# retry on 429 — is the difference between "no DOI exists" and "we were too
# fast to find out".
CROSSREF_PAUSE = 1.2


async def _crossref_get(client: httpx.AsyncClient, params: dict) -> httpx.Response:
    """One Crossref query, honouring 429 with a single patient retry."""
    resp = await client.get(CROSSREF, params=params, timeout=40.0)
    if resp.status_code == 429:
        await asyncio.sleep(5.0)
        resp = await client.get(CROSSREF, params=params, timeout=40.0)
    return resp

# Marks text the model wrote about a study it did not read.
MODEL_SUMMARY_MARK = "*[Rezumat scris de model, nu citit din lucrare.]*"


# ── small text helpers ────────────────────────────────────────────────────

def fold(s: str) -> str:
    d = unicodedata.normalize("NFKD", s or "")
    return " ".join("".join(c for c in d if not unicodedata.combining(c)).lower().split())


def surnames(authors: str) -> set[str]:
    out = set()
    for part in re.split(r"[;,]| and ", authors or ""):
        words = [w for w in re.findall(r"[A-Za-zĂÂÎȘȚăâîșț\-]+", part) if len(w) > 2]
        if words:
            out.add(fold(words[-1]))
    return out


def title_similarity(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, fold(a), fold(b)).ratio()


def crossref_year(item: dict) -> int | None:
    for key in ("issued", "published", "published-print"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                return None
    return None


def entry_year(entry: dict) -> int | None:
    date = (entry.get("entry_date") or "")[:4]
    try:
        return int(date) if date else None
    except ValueError:
        return None


# ── what is missing ───────────────────────────────────────────────────────

def source_state(conn) -> dict:
    """How many studies lack a DOI, a PDF, or any real text."""
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN COALESCE(doi,'') = '' THEN 1 ELSE 0 END)              AS no_doi,
             SUM(CASE WHEN COALESCE(doi,'') = '' AND COALESCE(pdf_path,'') = '' THEN 1 ELSE 0 END) AS no_source,
             SUM(CASE WHEN LENGTH(COALESCE(content,'')) < 200 THEN 1 ELSE 0 END) AS thin,
             COUNT(*)                                                            AS total
           FROM entries
          WHERE archived = 0
            AND (COALESCE(study_type,'') != '' OR tags LIKE '%study%'
                 OR tags LIKE '%pdf%' OR COALESCE(pdf_path,'') != '')"""
    ).fetchone()
    return {
        "studies": row["total"] or 0,
        "without_doi": row["no_doi"] or 0,
        "without_any_source": row["no_source"] or 0,
        "thin": row["thin"] or 0,
    }


def studies_without_doi(conn, limit: int | None = None) -> list[dict]:
    sql = """SELECT id, title, authors, entry_date, content, tags, design
               FROM entries
              WHERE archived = 0
                AND (COALESCE(study_type,'') != '' OR tags LIKE '%study%'
                     OR tags LIKE '%pdf%' OR COALESCE(pdf_path,'') != '')
                AND (doi IS NULL OR doi = '')
              ORDER BY id"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def thin_studies(conn, limit: int | None = None) -> list[dict]:
    sql = """SELECT id, title, authors, entry_date, content, study_type
               FROM entries
              WHERE archived = 0
                AND COALESCE(doi,'') = '' AND COALESCE(pdf_path,'') = ''
                AND LENGTH(COALESCE(content,'')) < 200
              ORDER BY id"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql).fetchall()]


# ── the model's part ──────────────────────────────────────────────────────

DOI_PROMPT = """Ești bibliotecar academic. Pentru fiecare descriere de mai jos, identifică lucrarea științifică reală pe care o descrie și dă titlul ei bibliografic exact.

Reguli stricte:
- Dacă nu ești sigur de titlul exact al lucrării, scrie null. Nu inventa.
- Nu inventa autori sau ani.
- Titlul trebuie să fie cel publicat, nu o descriere a subiectului.
- Răspunde DOAR cu JSON valid, fără alt text.

Format: {{"rezultate": [{{"index": 0, "titlu": "...sau null...", "autor": "...sau null...", "an": 2018, "incredere": "mare|medie|mica"}}]}}

DESCRIERI:
{lista}
"""

SUMMARY_PROMPT = """Ești asistent de cercetare. Pentru fiecare studiu de mai jos ai doar titlul.

Scrie un rezumat de 3-5 propoziții despre ce arată studiul: întrebarea, metoda, rezultatul principal.
Reguli stricte:
- Dacă nu cunoști lucrarea, scrie null la "rezumat" și "cunosc": false. Nu inventa rezultate.
- Nu inventa cifre, autori sau reviste.
- Marchează "cunosc": true doar când ești sigur că știi despre ce lucrare e vorba.
- Răspunde DOAR cu JSON valid.

Format: {{"rezultate": [{{"index": 0, "cunosc": true, "rezumat": "...sau null..."}}]}}

STUDII:
{lista}
"""


async def _ask_json(prompt: str, max_tokens: int = 6000) -> dict:
    """Ask for JSON and parse it, with headroom for a reasoning model.

    `deepseek-flash` spends tokens thinking before it writes. At 2500 tokens the
    reply was cut mid-JSON on every call, the parse failed, and the job reported
    "the model did not know the title" — a wrong diagnosis for a truncated
    answer. The budget is generous; an unused token costs nothing.
    """
    try:
        _, _, text = await llm_complete(
            [{"role": "system", "content": prompt}], temperature=0.1, max_tokens=max_tokens
        )
    except Exception as exc:  # noqa: BLE001 - provider errors are heterogeneous
        logger.warning("Source model call failed: %s", exc)
        return {}
    text = text or ""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        logger.info("Model reply had no JSON (%d chars): %r", len(text), text[:120])
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.info("Model JSON truncated or invalid (%s): %r", exc, text[-120:])
        return {}


async def propose_titles(batch: list[dict]) -> dict[int, dict]:
    """{entry_id: {titlu, autor, an, incredere}} from the model."""
    lista = "\n".join(
        f'{i}. Titlu catalog: "{e["title"]}" | Conținut: "{(e.get("content") or "")[:220].replace(chr(10), " ")}"'
        for i, e in enumerate(batch)
    )
    data = await _ask_json(DOI_PROMPT.format(lista=lista), max_tokens=6000)
    out: dict[int, dict] = {}
    for item in data.get("rezultate") or []:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            out[batch[idx]["id"]] = item
    return out


async def propose_summaries(batch: list[dict]) -> dict[int, dict]:
    """{entry_id: {cunosc, rezumat}} — only for studies the model recognises."""
    lista = "\n".join(
        f'{i}. "{e["title"]}" — {(e.get("authors") or "autori necunoscuți")} ({(e.get("entry_date") or "")[:4] or "an necunoscut"})'
        for i, e in enumerate(batch)
    )
    data = await _ask_json(SUMMARY_PROMPT.format(lista=lista), max_tokens=6000)
    out: dict[int, dict] = {}
    for item in data.get("rezultate") or []:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            out[batch[idx]["id"]] = item
    return out


# ── Crossref decides ──────────────────────────────────────────────────────

async def find_doi(client: httpx.AsyncClient, title: str, author: str | None,
                   year: int | None, min_title: float = 0.85) -> tuple[dict | None, str]:
    """Crossref lookup for a proposed title; accept only a corroborated hit."""
    params = {"query.bibliographic": title, "rows": 5}
    if author:
        params["query.author"] = author
    try:
        resp = await _crossref_get(client, params)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Crossref lookup failed for %r: %s", title[:60], exc)
        return None, f"Crossref eroare: {type(exc).__name__}"
    finally:
        await asyncio.sleep(CROSSREF_PAUSE)

    for it in items:
        found = (it.get("title") or [""])[0]
        if not found or (it.get("type") or "") in REJECTED_TYPES:
            continue
        sim = title_similarity(title, found)
        if sim < min_title:
            continue
        found_surnames = {
            w for a in (it.get("author") or [])
            for w in re.findall(r"[a-z]{4,}", fold(a.get("family") or ""))
        }
        want = surnames(author or "")
        if want and found_surnames:
            if len(want & found_surnames) / len(want) < 0.34:
                logger.info("Crossref reject %r: author %s vs %s", found[:40], want, found_surnames)
                continue
        yr = crossref_year(it)
        # A work is often reissued (a 1998 paper in a 2010 collection). When the
        # title matches almost exactly and an author is shared, the year cannot
        # be trusted to reject: it is the reprint's year, not the original's.
        # Only a weaker title match leaves the year as the deciding signal.
        strong = sim >= 0.95 and bool(want and found_surnames and (want & found_surnames))
        if not strong and year and yr and abs(year - yr) > 5:
            logger.debug("Crossref reject %r: year %s vs %s", found[:40], year, yr)
            continue
        return {"doi": (it.get("DOI") or "").lower(), "title": found, "year": yr,
                "sim": round(sim, 3), "authors": sorted(found_surnames)[:3]}, ""
    return None, "niciun candidat nu trece verificarea"


def _save_doi(entry_id: int, best: dict) -> bool:
    """Write the DOI, unless another entry already carries it.

    Two catalogue rows can describe the same paper ("The Extended Mind (Clark &
    Chalmers)" and "Clark & Chalmers - Extended Mind (1998)"). Writing the DOI
    to both would hide the duplication; refusing the second keeps it visible.
    Returns True when the row was written.
    """
    conn = get_db()
    try:
        taken = conn.execute(
            "SELECT id FROM entries WHERE doi = ? AND id != ?", [best["doi"], entry_id]
        ).fetchone()
        if taken:
            logger.info("DOI %s already on entry #%s; #%s left alone",
                        best["doi"], taken["id"], entry_id)
            return False
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
        return True
    finally:
        conn.close()


def _save_summary(entry_id: int, text: str) -> None:
    conn = get_db()
    try:
        row = conn.execute("SELECT content FROM entries WHERE id = ?", [entry_id]).fetchone()
        existing = (row["content"] if row else "") or ""
        if MODEL_SUMMARY_MARK in existing:
            return  # already written once; never stack summaries
        conn.execute(
            "UPDATE entries SET content = ? WHERE id = ?",
            [f"{existing}\n\n{MODEL_SUMMARY_MARK}\n{text}".strip(), entry_id],
        )
        conn.commit()
    finally:
        conn.close()


# ── the two jobs ──────────────────────────────────────────────────────────

async def resolve_dois(limit: int | None = None, dry_run: bool = False,
                       batch_size: int = 3, min_title: float = 0.85) -> dict:
    """Ask the model for real titles, let Crossref accept or refuse them.

    The batch is small on purpose: with six studies in one prompt the model
    answered `null` for most of them, while with two it named the work
    confidently. Asking more often is cheaper than losing the answer.
    """
    if LLM_PROVIDER == "mock":
        return {"ok": False, "note": "Fără LLM configurat nu pot propune titluri.",
                "found": [], "missed": [], "checked": 0}

    conn = get_db()
    try:
        rows = studies_without_doi(conn, limit)
        before = source_state(conn)
    finally:
        conn.close()

    found, missed = [], []
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            proposals = await propose_titles(batch)
            for entry in batch:
                prop = proposals.get(entry["id"]) or {}
                title = (prop.get("titlu") or "").strip()
                if not title or title.lower() in {"null", "none", "-"}:
                    missed.append({"id": entry["id"], "catalog": entry["title"],
                                   "why": "modelul nu a știut titlul"})
                    continue
                author = (prop.get("autor") or "").strip() or (entry.get("authors") or "")
                try:
                    year = int(prop.get("an")) if prop.get("an") else entry_year(entry)
                except (TypeError, ValueError):
                    year = entry_year(entry)
                best, why = await find_doi(client, title, author, year, min_title)
                if best:
                    written = False if dry_run else _save_doi(entry["id"], best)
                    found.append({"id": entry["id"], "catalog": entry["title"],
                                  "proposed": title, "doi": best["doi"],
                                  "crossref_title": best["title"], "year": best["year"],
                                  "confidence": prop.get("incredere") or "",
                                  "written": written,
                                  "duplicate_of": None if written or dry_run else "altă intrare"})
                else:
                    missed.append({"id": entry["id"], "catalog": entry["title"],
                                   "proposed": title, "why": why})

    conn = get_db()
    try:
        after = source_state(conn)
    finally:
        conn.close()
    return {
        "ok": True, "checked": len(rows), "dry_run": dry_run,
        "found": found, "missed": missed,
        "before": before, "after": after,
        "note": ("Probă: nimic scris." if dry_run else
                 f"{len(found)} DOI scrise, {len(missed)} rămase fără."),
    }


async def fill_thin_studies(limit: int | None = None, dry_run: bool = False,
                            batch_size: int = 6) -> dict:
    """Ask the model what thin studies found; write only what it is sure of."""
    if LLM_PROVIDER == "mock":
        return {"ok": False, "note": "Fără LLM configurat nu pot scrie rezumate.",
                "written": [], "skipped": [], "checked": 0}

    conn = get_db()
    try:
        rows = thin_studies(conn, limit)
        before = source_state(conn)
    finally:
        conn.close()

    written, skipped = [], []
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        proposals = await propose_summaries(batch)
        for entry in batch:
            prop = proposals.get(entry["id"]) or {}
            text = (prop.get("rezumat") or "").strip()
            sure = bool(prop.get("cunosc"))
            if not sure or not text or text.lower() in {"null", "none", "-"}:
                skipped.append({"id": entry["id"], "title": entry["title"],
                                "why": "modelul nu cunoaște lucrarea" if not sure
                                       else "fără text util"})
                continue
            written.append({"id": entry["id"], "title": entry["title"], "chars": len(text)})
            if not dry_run:
                _save_summary(entry["id"], text)

    conn = get_db()
    try:
        after = source_state(conn)
    finally:
        conn.close()
    return {
        "ok": True, "checked": len(rows), "dry_run": dry_run,
        "written": written, "skipped": skipped,
        "before": before, "after": after,
        "note": ("Probă: nimic scris." if dry_run else
                 f"{len(written)} studii au primit rezumat marcat, {len(skipped)} sărite."),
    }
