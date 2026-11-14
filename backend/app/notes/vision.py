"""Visual confirmation for OCR-page claims (REQ-NOTE-10).

A claim pinned to a page without a native text layer reaches ``verificat``
only after its stored quote is confirmed against the page image: the page
is rendered to PNG and :func:`app.document_parse.parse_scanned_page` must
return a cloud verdict of ``"match"`` whose own reading contains the quote.
Strict substring only — no fuzzy matching, no "close enough". Anything else
leaves the claim ``neverificat`` with the evidence (quote/page) untouched
for a human glance at the page image.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import anyio

from app.document_parse import (
    _normalize_for_compare as normalize_for_compare,
    page_image_path,
    parse_scanned_page,
    render_page_png,
)
from app.notes.engine import TRUSTED_SOURCES, verify_claim

#: Allowed visual-check states (mirrors the DB CHECK on note_claims).
VISUAL_VALUES = ("", "da", "nu", "necesar")

#: Claim statuses that count as visually confirmed.
CONFIRMED_STATUSES = ("verificat", "verificat_cu_nuanta")


def _row_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fresh_claim(conn, claim_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM note_claims WHERE id = ?", [claim_id]
    ).fetchone()
    return _row_dict(row) if row is not None else {}


def claim_image_path(conn, claim_row) -> tuple[Path | None, str]:
    """Resolve the persisted page image for a claim.

    Returns ``(path, "")`` when a page PNG was already saved next to the
    studies folder, else ``(None, <motiv in romana>)``. Rendering an
    unpersisted page is left to :func:`confirm_claim_visually`, which runs
    it off the event loop and never writes the PNG to disk.
    """
    claim = _row_dict(claim_row)
    page_pdf = claim.get("page_pdf")
    if page_pdf is None:
        return (None, "Afirmația nu are pagină declarată.")
    try:
        page_number = int(page_pdf)
    except (TypeError, ValueError):
        return (None, "Pagina declarată a afirmației este invalidă.")
    entry_id = claim.get("entry_id")
    if entry_id is None:
        try:
            dossier = conn.execute(
                "SELECT entry_id FROM note_dossiers WHERE id = ?",
                [claim.get("dossier_id")],
            ).fetchone()
        except Exception:
            dossier = None
        if dossier is None:
            return (None, "Dosarul afirmației nu există.")
        entry_id = _row_dict(dossier).get("entry_id")
    try:
        saved = page_image_path(int(entry_id), page_number)
    except (TypeError, ValueError):
        return (None, "Dosarul afirmației nu indică un studiu valid.")
    if saved.exists():
        return (saved, "")
    return (
        None,
        "Nu există încă o imagine salvată pentru această pagină; "
        "se va randa din PDF fără a o persista.",
    )


async def confirm_claim_visually(conn, claim_id: int, *, force: bool = False) -> dict:
    """Confirm one claim's quote against its page image.

    Never raises for OCR/vision failures (they become ``indisponibil`` with
    ``visual_check = "necesar"``); only a missing claim raises
    :class:`LookupError`. Writes ``visual_check`` + ``vision_json`` without
    committing — the caller owns the transaction.
    """
    row = conn.execute(
        """SELECT nc.*, d.entry_id AS entry_id
             FROM note_claims nc
             JOIN note_dossiers d ON d.id = nc.dossier_id
            WHERE nc.id = ?""",
        [claim_id],
    ).fetchone()
    if row is None:
        raise LookupError("Afirmația nu există.")
    claim = _row_dict(row)
    visual = claim.get("visual_check") or ""
    quote = (claim.get("quote") or "").strip()
    if not quote:
        return {"result": "fără citat", "visual_check": visual,
                "claim": _fresh_claim(conn, claim_id)}
    page_pdf = claim.get("page_pdf")
    try:
        page_number = int(page_pdf) if page_pdf is not None else None
    except (TypeError, ValueError):
        page_number = None
    if page_number is None:
        return {"result": "fără pagină", "visual_check": visual,
                "claim": _fresh_claim(conn, claim_id)}
    try:
        entry_id = int(claim.get("entry_id"))
    except (TypeError, ValueError):
        raise LookupError("Afirmația nu există.")
    source = ""
    try:
        page_row = conn.execute(
            "SELECT source FROM page_texts WHERE entry_id = ? AND page_number = ?",
            [entry_id, page_number],
        ).fetchone()
        if page_row is not None:
            source = _row_dict(page_row).get("source") or ""
    except Exception:
        source = ""
    if source in TRUSTED_SOURCES:
        return {"result": "text nativ", "visual_check": visual,
                "claim": _fresh_claim(conn, claim_id)}
    if visual == "da" and not force:
        return {"result": "deja confirmat", "visual_check": "da",
                "claim": _fresh_claim(conn, claim_id)}

    try:
        pdf_row = conn.execute(
            "SELECT pdf_path FROM entries WHERE id = ?", [entry_id]
        ).fetchone()
        pdf_path_value = _row_dict(pdf_row).get("pdf_path") if pdf_row else ""
    except Exception:
        pdf_path_value = ""
    image_path, _image_reason = claim_image_path(conn, claim)

    from app.notes.service import _entry_pdf_path

    def _work():
        if image_path is not None:
            return parse_scanned_page(Path(image_path).read_bytes())
        pdf = _entry_pdf_path(entry_id, pdf_path_value or "")
        if pdf is None:
            raise FileNotFoundError(
                "PDF-ul studiului nu a fost găsit în dosarul de studii; "
                "încărcați PDF-ul și reîncercați."
            )
        return parse_scanned_page(render_page_png(pdf, page_number, dpi=150))

    try:
        parsed = await anyio.to_thread.run_sync(_work)
    except Exception as exc:
        audit = {
            "verdict": "unavailable",
            "reasons": ["vision_unavailable"],
            "status": "vision-uncertain",
            "quote_found": False,
            "checked_at": _utcnow(),
            "error": str(exc)[:500],
        }
        conn.execute(
            "UPDATE note_claims SET visual_check = ?, vision_json = ? WHERE id = ?",
            ["necesar", json.dumps(audit, ensure_ascii=False), claim_id],
        )
        return {
            "result": "indisponibil",
            "visual_check": "necesar",
            "reason": f"Verificarea vizuală este indisponibilă: {exc}"[:500],
            "claim": _fresh_claim(conn, claim_id),
        }

    parsed = dict(parsed or {})
    verdict = str(parsed.get("verdict") or "")
    status = str(parsed.get("status") or "")
    reasons = parsed.get("reasons") or []
    vision_text = str(parsed.get("vision_text") or "")
    norm_quote = normalize_for_compare(quote)
    norm_vision = normalize_for_compare(vision_text)
    quote_found = bool(norm_quote) and norm_quote in norm_vision
    confirmed = verdict == "match" and status == "vision-ok" and quote_found
    audit = {
        "verdict": verdict,
        "reasons": reasons,
        "status": status,
        "quote_found": quote_found,
        "checked_at": _utcnow(),
    }
    conn.execute(
        "UPDATE note_claims SET visual_check = ?, vision_json = ? WHERE id = ?",
        ["da" if confirmed else "necesar",
         json.dumps(audit, ensure_ascii=False), claim_id],
    )
    if not confirmed:
        return {"result": "neconfirmat", "visual_check": "necesar",
                "claim": _fresh_claim(conn, claim_id)}

    # Vision confirmed: re-run the engine with the visual gate open and
    # apply its verdict exactly like verify_dossier does (same columns,
    # same verified_at rule). No commit here; the caller owns it.
    rechecked = await verify_claim(
        conn, {**claim, "visual_check": "da", "entry_id": entry_id}
    )
    now = _utcnow()
    conn.execute(
        """UPDATE note_claims SET status = ?, quote = ?, page_pdf = ?,
                  page_printed = ?, correction = ?, method = ?, engine = ?,
                  coverage = ?,
                  verified_at = CASE WHEN ? = 'verificat' THEN ? ELSE verified_at END
           WHERE id = ?""",
        [rechecked["status"], rechecked["quote"], rechecked["page_pdf"],
         rechecked["page_printed"], rechecked["correction"], rechecked["method"],
         rechecked.get("engine") or "", rechecked.get("coverage"),
         rechecked["status"], now, claim_id],
    )
    fresh = _fresh_claim(conn, claim_id)
    outcome = "confirmat" if fresh.get("status") in CONFIRMED_STATUSES else "neconfirmat"
    return {"result": outcome, "visual_check": "da", "claim": fresh}
