"""Verification engine for verified note dossiers (server-enforced product rule)."""

import json
import logging
import re

from app.page_text import _normalize, get_page_text, get_printed_page

logger = logging.getLogger("chronokg.notes.engine")

#: Allowed claim statuses, in UI order.
STATUSES = (
    "verificat", "verificat_cu_nuanta", "incomplet", "subestimat",
    "corectat", "depasit", "neverificat",
)

#: Dossier section kinds, in display order.
SECTION_KINDS = (
    "fisa_sursei", "ce_spune", "continut", "teza", "corectii",
    "lipsea", "glosar", "formulari", "de_verificat",
)

#: Page sources readable without extra checks.
TRUSTED_SOURCES = ("native", "manual")

#: A shorter quote is a fragment, not evidence.
MIN_QUOTE_CHARS = 40


def _row_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", _normalize(text or ""))


def _codes_in_json(value: str) -> list[str]:
    try:
        data = json.loads(value or "[]")
    except Exception:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def next_code(conn, dossier_id: int) -> str:
    """Next stable claim code (``A{n}``); numbers are never reused.

    A monotonic per-dossier counter (``note_dossiers.code_seq``) is bumped
    atomically in a single UPDATE, then reconciled with the largest suffix
    ever seen in the dossier (live claims plus round/edge history, for
    dossiers that predate the counter). No commit here: the caller owns the
    transaction.
    """
    best = 0
    try:
        rows = conn.execute(
            "SELECT code FROM note_claims WHERE dossier_id = ?", [dossier_id]
        ).fetchall()
        codes = [str(r["code"]) for r in rows]
        edges = conn.execute(
            "SELECT claim_code FROM note_edges WHERE dossier_id = ?", [dossier_id]
        ).fetchall()
        codes += [str(r["claim_code"]) for r in edges]
        rounds = conn.execute(
            "SELECT changed_json, open_json FROM note_rounds WHERE dossier_id = ?",
            [dossier_id],
        ).fetchall()
        for rnd in rounds:
            codes += _codes_in_json(rnd["changed_json"])
            codes += _codes_in_json(rnd["open_json"])
        for code in codes:
            match = re.fullmatch(r"A(\d+)", (code or "").strip())
            if match:
                best = max(best, int(match.group(1)))
    except Exception:
        pass
    conn.execute(
        "UPDATE note_dossiers SET code_seq = max(code_seq, ?) + 1 WHERE id = ?",
        [best, dossier_id],
    )
    row = conn.execute(
        "SELECT code_seq FROM note_dossiers WHERE id = ?", [dossier_id]
    ).fetchone()
    if row is None:
        raise LookupError("Dosarul nu există.")
    try:
        seq = int(row["code_seq"])
    except (TypeError, ValueError):
        seq = best + 1
    return f"A{seq}"


def _page_index(conn, entry_id: int) -> dict[int, dict]:
    """``{page_pdf: {text, source, printed_page, tokens}}`` for an entry.

    Token sets are computed once so a dossier verification over a long PDF
    does not retokenize every page for every claim.
    """
    try:
        rows = conn.execute(
            "SELECT page_number, text, source FROM page_texts"
            " WHERE entry_id = ? ORDER BY page_number", [entry_id],
        ).fetchall()
    except Exception:
        return {}
    pages: dict[int, dict] = {}
    for row in rows:
        data = _row_dict(row)
        try:
            number = int(data.get("page_number"))
        except (TypeError, ValueError):
            continue
        text = data.get("text") or ""
        pages[number] = {
            "text": text,
            "source": data.get("source") or "",
            "printed_page": get_printed_page(conn, entry_id, number) or "",
            "tokens": set(_tokens(text)),
        }
    return pages


def collect_candidates(conn, entry_id, statement, page_hint=None, limit_chars=6000,
                       pages=None):
    """Rank an entry's pages for a statement. Deterministic, no LLM.

    Order: ``page_hint`` first, then token-overlap score (distinct statement
    tokens of length >= 5, case-folded), with the best hit's neighbours
    pulled right behind it. Truncated to ``limit_chars`` total characters.
    Pass a prebuilt ``pages`` index (see :func:`_page_index`) to avoid
    retokenizing the whole entry on every call.
    """
    if pages is None:
        pages = _page_index(conn, entry_id)
    if not pages:
        return []
    wanted = {tok for tok in _tokens(statement) if len(tok) >= 5}
    scored = [(n, len(wanted & i.get("tokens", set())) if wanted else 0)
              for n, i in pages.items()]
    scored.sort(key=lambda item: (-item[1], item[0]))
    ordered = [n for n, _ in scored]
    if page_hint in pages:
        ordered = [page_hint] + [n for n in ordered if n != page_hint]
    best = next((n for n, s in scored if s > 0), ordered[0])
    near = [n for n in (best - 1, best + 1) if n in pages and n != best]
    ordered = [best] + near + [n for n in ordered if n not in ({best} | set(near))]
    out, budget = [], max(0, limit_chars)
    for number in ordered:
        if budget <= 0:
            break
        text = pages[number]["text"] or ""
        if not text.strip():
            continue
        clipped = text[:budget]
        budget -= len(clipped)
        out.append({"page_pdf": number, "page_printed": pages[number]["printed_page"],
                    "text": clipped, "source": pages[number]["source"]})
    return out


def _valid_quote(conn, entry_id, page_pdf, quote) -> bool:
    """True when the normalized quote sits inside the page text."""
    if page_pdf is None:
        return False
    if len(_normalize(quote or "")) < MIN_QUOTE_CHARS:
        return False
    page = get_page_text(conn, entry_id, page_pdf)
    if page is None or not (page.get("text") or "").strip():
        return False
    return _normalize(quote) in _normalize(page["text"])


def _coerce_proposals(raw: object) -> list[dict]:
    """Normalize one LLM reply into proposal dicts."""
    if isinstance(raw, dict):
        for key in ("verdicts", "claims", "results", "items", "propuneri"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            page = entry.get("page_pdf", entry.get("page"))
            page_pdf = int(page) if page is not None else None
        except (TypeError, ValueError):
            page_pdf = None
        status = (entry.get("status") or "").strip()
        items.append({"page_pdf": page_pdf, "quote": entry.get("quote") or "",
                      "status": status if status in STATUSES else "",
                      "correction": entry.get("correction") or "",
                      "reason": entry.get("reason") or ""})
    return items


async def _llm_judge(statement, candidates, api_language_note=""):
    """Return ``[{page_pdf, quote, status, correction, reason}]`` or ``[]``."""
    from app.llm import complete

    pages_block = "\n\n".join(
        f"[PDF {c['page_pdf']} | tip. {c['page_printed'] or '?'}]\n{c['text']}" for c in candidates)
    keys = ", ".join(STATUSES)
    system_prompt = (
        "Ești un verificator academic. Compari afirmația DOAR cu pasajele primite. "
        "Returnezi DOAR JSON valid: "
        '{"verdicts": [{"page_pdf": 1, "quote": "citat EXACT din pasaj", '
        f'"status": "verificat", "correction": "", "reason": ""}}]}} '
        "status ∈ {" + keys + "}. `quote` ≥ 40 caractere, copiat caracter cu caracter; "
        "fără sprijin: {\"verdicts\": []}.")
    user_prompt = f"Afirmația:\n{statement}\n\nPasaje:\n{pages_block}"
    if api_language_note:
        user_prompt += f"\n\nNotă: {api_language_note}"
    try:
        _, _, text = await complete(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            json_mode=True, temperature=0)
    except Exception as exc:  # noqa: BLE001 - judge failure means "no proposal"
        logger.info("LLM judge failed, treating as no proposal: %s", exc)
        return []
    clean = re.sub(r"^```(?:json)?\s*", "", (text or "").strip())
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return _coerce_proposals(json.loads(clean))
    except Exception as exc:  # noqa: BLE001 - malformed JSON means "no proposal"
        logger.info("LLM judge returned unparsable JSON: %s", exc)
        return []


def _coverage(statement: str, page_text: str) -> float:
    tokens = _tokens(statement)
    if not tokens:
        return 0.0
    present = set(_tokens(page_text))
    return round(sum(1 for tok in tokens if tok in present) / len(tokens), 4)


def _entry_of(conn, claim: dict) -> int:
    if claim.get("entry_id") is not None:
        return int(claim["entry_id"])
    dossier = conn.execute(
        "SELECT entry_id FROM note_dossiers WHERE id = ?", [claim.get("dossier_id")],
    ).fetchone()
    if dossier is None:
        raise LookupError("Dosarul afirmației nu există.")
    return int(_row_dict(dossier)["entry_id"])


def _ocr_block(source: str, visual: str, correction: str) -> tuple[bool, str]:
    """OCR/vision text needs a human glance at the page image first."""
    if source in TRUSTED_SOURCES or visual == "da":
        return False, correction
    suffix = "Text OCR neverificat vizual; necesită comparație cu imaginea paginii."
    return True, f"{correction} {suffix}".strip() if correction else suffix


#: Told to the user when the PDF text was never extracted at all.
NO_PAGES_CORRECTION = ("Textul paginilor nu a fost încă extras din acest PDF;"
                       " rulează extragerea și reîncearcă.")

#: Told to the user when every page exists but carries no text (a scan).
SCANNED_PAGES_CORRECTION = ("Pagina este scanată și nu are text; rulează extragerea OCR"
                            " pentru acest studiu, apoi reverifică.")


async def verify_claim(conn, claim_row: dict, page_hint=None, pages=None) -> dict:
    """Verify one claim: exact path, then LLM judge, else ``neverificat``.

    Accepts a prebuilt ``pages`` index (see :func:`_page_index`); builds it
    when omitted. The OCR gate keeps the found quote/page and only forces
    the status to ``neverificat`` — the evidence stays visible for the
    human visual check.
    """
    from app.note_check import find_support

    claim = _row_dict(claim_row)
    statement = (claim.get("statement") or "").strip()
    entry_id = _entry_of(conn, claim)
    visual = claim.get("visual_check") or ""
    if not statement:
        return {"status": "neverificat", "quote": "", "page_pdf": None,
                "page_printed": "", "correction": "Afirmația este goală.",
                "method": "exact", "engine": "", "coverage": 0.0}
    if pages is None:
        pages = _page_index(conn, entry_id)
    if not pages:
        return {"status": "neverificat", "quote": "", "page_pdf": None,
                "page_printed": "", "correction": NO_PAGES_CORRECTION,
                "method": "exact", "engine": "", "coverage": 0.0}
    if all(not (info.get("text") or "").strip() for info in pages.values()):
        return {"status": "neverificat", "quote": "", "page_pdf": None,
                "page_printed": "", "correction": SCANNED_PAGES_CORRECTION,
                "method": "exact", "engine": "", "coverage": 0.0}

    support = find_support(conn, entry_id, statement)
    if support is not None and _valid_quote(
            conn, entry_id, support["page_pdf"], support["quote"]):
        number = int(support["page_pdf"])
        source = pages.get(number, {}).get("source", "")
        blocked, correction = _ocr_block(source, visual, "")
        if blocked:
            return {"status": "neverificat", "quote": support["quote"],
                    "page_pdf": number,
                    "page_printed": support.get("page_printed") or "",
                    "correction": correction, "method": "exact", "engine": source,
                    "coverage": support.get("coverage", 0.0)}
        return {"status": "verificat", "quote": support["quote"],
                "page_pdf": number,
                "page_printed": support.get("page_printed") or "", "correction": "",
                "method": "exact", "engine": source,
                "coverage": support.get("coverage", 0.0)}

    candidates = collect_candidates(conn, entry_id, statement,
                                    page_hint=page_hint, pages=pages)
    proposals = await _llm_judge(statement, candidates, "Răspunde în limba română.")
    rejected = support is not None
    for proposal in proposals:
        page_pdf, quote = proposal.get("page_pdf"), proposal.get("quote") or ""
        if page_pdf is None or not quote or not _valid_quote(conn, entry_id, page_pdf, quote):
            rejected = True
            continue
        page = pages.get(int(page_pdf), {})
        blocked, correction = _ocr_block(
            page.get("source") or "", visual, proposal.get("correction") or "")
        if blocked:
            return {"status": "neverificat", "quote": quote, "page_pdf": int(page_pdf),
                    "page_printed": page.get("printed_page") or "",
                    "correction": correction, "method": "llm",
                    "engine": page.get("source") or "",
                    "coverage": _coverage(statement, page.get("text") or "")}
        return {"status": proposal.get("status") or "verificat", "quote": quote,
                "page_pdf": int(page_pdf),
                "page_printed": page.get("printed_page") or "",
                "correction": proposal.get("correction") or "", "method": "llm",
                "engine": page.get("source") or "",
                "coverage": _coverage(statement, page.get("text") or "")}
    correction = ("Citatul propus nu există pe pagina declarată; afirmația rămâne "
                  "neverificată." if (proposals and rejected) or support is not None
                  else "Nu am găsit pasajul în sursă; afirmația rămâne neverificată.")
    top = candidates[0] if candidates else {}
    return {"status": "neverificat", "quote": "", "page_pdf": None, "page_printed": "",
            "correction": correction, "method": "exact",
            "engine": top.get("source", ""), "coverage": 0.0}


def _dossier_status(verificat: int, total: int) -> str:
    if total > 0 and verificat == total:
        return "verificat"
    return "partial" if verificat > 0 else "draft"


async def verify_dossier(conn, dossier_id, *, page_hint=None, apply=True, recheck=False):
    """Verify open claims; write (claims, status, one round) only if ``apply``."""
    from datetime import datetime, timezone

    dossier = conn.execute(
        "SELECT * FROM note_dossiers WHERE id = ?", [dossier_id]).fetchone()
    if dossier is None:
        raise LookupError("Dosarul nu există.")
    entry_id = int(_row_dict(dossier)["entry_id"])
    rows = conn.execute(
        "SELECT * FROM note_claims WHERE dossier_id = ? ORDER BY id", [dossier_id]).fetchall()
    pages = _page_index(conn, entry_id)
    claims, changed = [], []
    for row in rows:
        current = _row_dict(row)
        if current.get("status") == "verificat" and not (apply and recheck):
            claims.append(current)
            continue
        verdict = await verify_claim(conn, {**current, "entry_id": entry_id},
                                     page_hint=page_hint, pages=pages)
        if (verdict["status"], verdict["quote"]) != (current.get("status"), current.get("quote") or ""):
            changed.append(current.get("code", ""))
        if apply:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """UPDATE note_claims SET status = ?, quote = ?, page_pdf = ?, page_printed = ?,
                          correction = ?, method = ?, engine = ?, coverage = ?,
                          verified_at = CASE WHEN ? = 'verificat' THEN ? ELSE verified_at END
                   WHERE id = ?""",
                [verdict["status"], verdict["quote"], verdict["page_pdf"], verdict["page_printed"],
                 verdict["correction"], verdict["method"], verdict.get("engine") or "",
                 verdict.get("coverage"), verdict["status"], now, current["id"]])
        claims.append({**current, **verdict})
    summary = {"verificat": sum(1 for c in claims if c.get("status") == "verificat"),
               "neverificat": sum(1 for c in claims if c.get("status") == "neverificat"),
               "total": len(claims)}
    status = _dossier_status(summary["verificat"], summary["total"])
    round_id = None
    if apply:
        try:
            conn.execute("UPDATE note_dossiers SET status = ?, updated_at = datetime('now')"
                         " WHERE id = ?", [status, dossier_id])
            cur = conn.execute(
                """INSERT INTO note_rounds (dossier_id, summary, changed_json, open_json)
                   VALUES (?, ?, ?, ?)""",
                [dossier_id,
                 f"Verificare dosar {dossier_id}: {summary['verificat']}/{summary['total']} verificat.",
                 json.dumps(changed, ensure_ascii=False),
                 json.dumps([c.get("code", "") for c in claims
                             if c.get("status") == "neverificat"], ensure_ascii=False)])
            round_id = cur.lastrowid
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"dossier_id": dossier_id, "claims": claims,
            "summary": summary, "round_id": round_id}
