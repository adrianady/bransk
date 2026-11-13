"""Per-page document parse over HTTP (slice 4).

Thin HTTP skin over :mod:`app.document_parse` and :mod:`app.page_text`:

* ``GET /studies/{id}/parse/summary`` — classification + per-page ledger, no model.
* ``GET /studies/{id}/parse/stream``  — SSE vision transcription with live progress.
* ``GET /studies/{id}/pages/{n}/text``  — canonical page text for the reader.
* ``GET /studies/{id}/pages/{n}/image`` — cached PNG render of one page.

SSE shape copies ``maintenance`` / ``research`` (``data: {json}\\n\\n`` on
``text/event-stream``, ``: ping`` keep-alive, client-disconnect checks,
``data: [DONE]`` terminator); event names are ``start`` / ``progress`` /
``error`` / ``done``.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from functools import partial
from pathlib import Path

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.db import get_db
from app.document_parse import (
    detect_kind,
    page_image_path,
    parse_document,
    save_page_image,
)
from app.llm import VisionUnsupportedError, active_provider, provider_model
from app.models import PageTextResponse, ParseSummaryResponse
from app.page_text import get_page_text
from app.studies_store import pdf_path_for

logger = logging.getLogger("chronokg.studies_parse")
router = APIRouter()

# One parse at a time per entry: a second concurrent stream gets 409 instead
# of corrupting rows two writers share.
_PARSE_GUARD = threading.Lock()
_PARSE_ACTIVE: set[int] = set()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _acquire_parse(entry_id: int) -> bool:
    with _PARSE_GUARD:
        if entry_id in _PARSE_ACTIVE:
            return False
        _PARSE_ACTIVE.add(entry_id)
        return True


def _release_parse(entry_id: int) -> None:
    with _PARSE_GUARD:
        _PARSE_ACTIVE.discard(entry_id)


def _entry_or_404(conn, entry_id: int) -> dict:
    row = conn.execute(
        "SELECT id, title, pdf_path FROM entries WHERE id = ?", [entry_id]
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Studiul nu a fost găsit.")
    return dict(row)


def _pdf_or_404(entry_id: int) -> Path:
    path = pdf_path_for(entry_id)
    if path is None:
        raise HTTPException(
            404, "PDF-ul nu a fost descărcat încă pentru acest studiu."
        )
    return path


def _parse_pages_param(raw: str, total: int) -> list[int]:
    """Parse ``1-5``, ``1,3,7`` (and mixes like ``1-3,5``) into sorted pages."""
    try:
        out: set[int] = set()
        for chunk in (raw or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                lo_s, hi_s = chunk.split("-", 1)
                lo, hi = int(lo_s.strip()), int(hi_s.strip())
                if lo < 1 or hi < lo:
                    raise ValueError
                out.update(range(lo, hi + 1))
            else:
                out.add(int(chunk))
    except (ValueError, TypeError):
        raise HTTPException(
            400, "Parametrul pages este invalid. Folosește forme ca 1-5 sau 1,3,7."
        )
    if not out:
        raise HTTPException(
            400, "Parametrul pages este invalid. Folosește forme ca 1-5 sau 1,3,7."
        )
    bad = sorted(p for p in out if p > total)
    if bad:
        raise HTTPException(
            400, f"Pagina {bad[0]} nu există în acest PDF (are {total} pagini)."
        )
    return sorted(out)


def _assemble_summary(entry_id: int, pdf_name: str, info: dict) -> dict:
    """Classification + per-page ledger. Never calls a model."""
    conn = get_db()
    try:
        rows = {
            r["page_number"]: dict(r)
            for r in conn.execute(
                "SELECT page_number, text, source, status, char_count"
                " FROM page_texts WHERE entry_id = ?",
                [entry_id],
            ).fetchall()
        }
    finally:
        conn.close()
    page_kinds: list[str] = info.get("page_kinds") or []
    per_page: list[dict] = []
    parsed = {
        "native": 0, "vision_ok": 0, "vision_uncertain": 0,
        "manual": 0, "error": 0, "missing": 0,
    }
    for page in range(1, info.get("pages", 0) + 1):
        kind = page_kinds[page - 1] if page - 1 < len(page_kinds) else ""
        row = rows.get(page)
        if row is None:
            per_page.append(
                {"page": page, "kind": kind, "source": "", "status": "", "chars": 0}
            )
            parsed["missing"] += 1
            continue
        source = row.get("source") or ""
        status = row.get("status") or ""
        try:
            chars = int(row.get("char_count") or 0)
        except (TypeError, ValueError):
            chars = 0
        per_page.append(
            {"page": page, "kind": kind, "source": source, "status": status,
             "chars": chars}
        )
        if status == "error":
            parsed["error"] += 1
        elif source == "manual":
            parsed["manual"] += 1
        elif source == "native" or status == "native":
            parsed["native"] += 1
        elif source == "vision" and status == "vision-ok":
            parsed["vision_ok"] += 1
        elif source == "vision" and status == "vision-uncertain":
            parsed["vision_uncertain"] += 1
        else:
            parsed["error"] += 1
    return {
        "entry_id": entry_id,
        "pdf_path": pdf_name,
        "kind": info.get("kind") or "",
        "pages": info.get("pages", 0),
        "pages_native": info.get("pages_native", 0),
        "pages_scanned": info.get("pages_scanned", 0),
        "pages_empty": info.get("pages_empty", 0),
        "parsed": parsed,
        "per_page": per_page,
    }


def _summary_for_entry(entry_id: int) -> dict:
    """Fresh classification + ledger for an entry (sync; run off the loop)."""
    conn = get_db()
    try:
        _entry_or_404(conn, entry_id)
    finally:
        conn.close()
    pdf = _pdf_or_404(entry_id)
    info = detect_kind(pdf)
    if info.get("kind") == "empty":
        reason = info.get("reason") or "no text and no images"
        raise HTTPException(400, f"PDF-ul nu poate fi interpretat: {reason}.")
    return _assemble_summary(entry_id, Path(pdf).name, info)


@router.get("/studies/{entry_id}/parse/summary", response_model=ParseSummaryResponse)
def parse_summary(entry_id: int):
    """Classification and per-page ledger. Never calls a model."""
    return _summary_for_entry(entry_id)


@router.get("/studies/{entry_id}/pages/{page_number}/text",
            response_model=PageTextResponse)
def page_text(entry_id: int, page_number: int):
    """Canonical text of one page — what the reader anchors selections to."""
    if page_number < 1:
        raise HTTPException(404, "Pagina nu există în acest PDF.")
    conn = get_db()
    try:
        _entry_or_404(conn, entry_id)
        row = get_page_text(conn, entry_id, page_number)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "Pagina nu are încă text canonical.")
    try:
        char_count = int(row.get("char_count") or 0)
    except (TypeError, ValueError):
        char_count = 0
    return {
        "page": page_number,
        "text": row.get("text") or "",
        "source": row.get("source") or "",
        "status": row.get("status") or "",
        "char_count": char_count,
    }


@router.get("/studies/{entry_id}/pages/{page_number}/image")
def page_image(entry_id: int, page_number: int, dpi: int = 150):
    """PNG render of one page, cached under ``studies/pages/<id>/<n>.png``."""
    if page_number < 1:
        raise HTTPException(404, "Pagina nu există în acest PDF.")
    if dpi < 36 or dpi > 600:
        raise HTTPException(400, "DPI-ul trebuie să fie între 36 și 600.")
    conn = get_db()
    try:
        _entry_or_404(conn, entry_id)
    finally:
        conn.close()
    pdf = _pdf_or_404(entry_id)
    info = detect_kind(pdf)
    total = info.get("pages", 0)
    if total == 0:
        reason = info.get("reason") or "no text and no images"
        raise HTTPException(400, f"PDF-ul nu poate fi interpretat: {reason}.")
    if page_number > total:
        raise HTTPException(404, "Pagina nu există în acest PDF.")
    dest = page_image_path(entry_id, page_number)
    if not dest.exists():
        try:
            save_page_image(pdf, entry_id, page_number, dpi=dpi)
        except ValueError:
            raise HTTPException(404, "Pagina nu există în acest PDF.")
        except Exception as exc:  # noqa: BLE001 - render failures are 500s
            logger.warning("Page render failed for entry %s page %s: %s",
                           entry_id, page_number, exc)
            raise HTTPException(500, "Redarea paginii a eșuat.")
    return FileResponse(
        str(dest),
        media_type="image/png",
        filename=dest.name,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ── Live parse stream ────────────────────────────────────────────────────────

def _prepare_parse(entry_id: int, pages_raw: str | None, provider: str | None,
                   model: str | None, dpi: int) -> dict:
    """Resolve entry/PDF/classification/selection off the event loop.

    Raises HTTPException (404/400) so the endpoint answers with a status
    instead of opening a broken stream. Never calls a model.
    """
    conn = get_db()
    try:
        _entry_or_404(conn, entry_id)
    finally:
        conn.close()
    pdf = _pdf_or_404(entry_id)
    info = detect_kind(pdf)
    if info.get("kind") == "empty":
        reason = info.get("reason") or "no text and no images"
        raise HTTPException(400, f"PDF-ul nu poate fi interpretat: {reason}.")
    if dpi < 36 or dpi > 600:
        raise HTTPException(400, "DPI-ul trebuie să fie între 36 și 600.")
    page_kinds: list[str] = info.get("page_kinds") or []
    total = info.get("pages", 0)
    if pages_raw is None:
        # Default: only the pages a model could add text to. Native pages
        # already have canonical text; empty ones are a rejection, not a
        # prompt — but both still appear in the ledger when selected.
        selected = [n for n, k in enumerate(page_kinds, start=1) if k != "native"]
    else:
        selected = _parse_pages_param(pages_raw, total)
    effective_provider = (provider or "").strip() or active_provider()
    effective_model = (model or "").strip() or provider_model(effective_provider)
    return {
        "entry_id": entry_id,
        "pdf": str(pdf),
        "pdf_name": Path(pdf).name,
        "kind": info.get("kind") or "",
        "selected": selected,
        "provider": effective_provider,
        "model": effective_model,
    }


def _parse_worker(entry_id: int, pdf: str, selected: list[int], provider: str,
                  model: str, force: bool, dpi: int, out: queue.Queue) -> None:
    """Run :func:`parse_document` page by page in a worker thread.

    One call per page keeps progress live with the full per-page record
    (chars, png bytes, elapsed, reason) instead of the bare
    ``(step, total, page, status)`` callback tuple. A failed page is queued
    as an error and the run continues; a ``VisionUnsupportedError`` is
    queued once and stops the run (it is a configuration problem, not a
    page problem).
    """
    conn = get_db()
    try:
        for page in selected:
            try:
                result = parse_document(
                    conn, entry_id, pdf, provider=provider, model=model,
                    pages=[page], force=force, dpi=dpi,
                )
            except VisionUnsupportedError as exc:
                out.put({
                    "t": "vision",
                    "message": getattr(exc, "public_message", None) or str(exc),
                })
                return
            except Exception as exc:  # noqa: BLE001 - one page never aborts
                logger.info("Parse of entry %s page %s failed: %s",
                            entry_id, page, exc)
                out.put({"t": "page", "entry": {
                    "page": page, "kind": "", "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}"[:300],
                    "chars": 0, "png_bytes": 0,
                    "elapsed_s": 0.0, "elapsed_ms": 0, "dpi": dpi,
                }})
                continue
            pages = result.get("pages") or []
            if pages:
                out.put({"t": "page", "entry": pages[0]})
            else:
                out.put({"t": "page", "entry": {
                    "page": page, "kind": "", "status": "error",
                    "reason": "no result", "chars": 0, "png_bytes": 0,
                    "elapsed_s": 0.0, "elapsed_ms": 0, "dpi": dpi,
                }})
    finally:
        try:
            conn.close()
        finally:
            out.put({"t": "end"})


@router.get("/studies/{entry_id}/parse/stream")
async def parse_stream(request: Request, entry_id: int,
                       pages: str | None = None,
                       provider: str | None = None,
                       model: str | None = None,
                       force: bool = False,
                       dpi: int = 150):
    """SSE vision transcription of the selected pages with live progress."""
    prep = await anyio.to_thread.run_sync(
        partial(_prepare_parse, entry_id, pages, provider, model, dpi)
    )
    if not _acquire_parse(entry_id):
        raise HTTPException(
            409, "Parsarea acestui studiu este deja în curs. "
                 "Așteaptă finalizarea ei înainte de a porni alta."
        )
    selected: list[int] = prep["selected"]

    async def generate():
        out: queue.Queue = queue.Queue()
        try:
            yield _sse({"event": "start", "entry_id": entry_id,
                        "kind": prep["kind"], "total": len(selected)})
            worker = threading.Thread(
                target=_parse_worker,
                args=(entry_id, prep["pdf"], selected, prep["provider"],
                      prep["model"], force, dpi, out),
                daemon=True,
            )
            worker.start()
            while True:
                try:
                    item = await anyio.to_thread.run_sync(
                        partial(out.get, timeout=10))
                except queue.Empty:
                    try:
                        if await request.is_disconnected():
                            logger.info("Parse stream disconnected for entry %s",
                                        entry_id)
                            break
                    except RuntimeError:
                        pass
                    yield ": ping\n\n"
                    continue
                kind = item.get("t")
                if kind == "page":
                    pg = item.get("entry") or {}
                    if pg.get("status") == "error":
                        yield _sse({
                            "event": "error", "entry_id": entry_id,
                            "page": pg.get("page"),
                            "message": pg.get("reason")
                            or "Eroare la parsarea paginii.",
                        })
                    else:
                        yield _sse({
                            "event": "progress", "entry_id": entry_id,
                            "page": pg.get("page"), "status": pg.get("status"),
                            "chars": pg.get("chars", 0),
                            "png_bytes": pg.get("png_bytes", 0),
                            "elapsed_s": pg.get("elapsed_s", 0.0),
                            "reason": pg.get("reason") or "",
                        })
                elif kind == "vision":
                    yield _sse({"event": "error", "entry_id": entry_id,
                                "message": item.get("message") or
                                "Modelul ales nu acceptă imagini."})
                elif kind == "end":
                    break
            try:
                summary = await anyio.to_thread.run_sync(
                    _summary_for_entry, entry_id)
            except HTTPException as exc:
                yield _sse({"event": "error", "entry_id": entry_id,
                            "message": exc.detail})
                return
            yield _sse({"event": "done", **summary})
        finally:
            _release_parse(entry_id)
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
