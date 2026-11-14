"""Verified note dossiers API (backend foundation lane).

A dossier belongs to exactly one ``(project_id, entry_id)``; every endpoint
is scoped to the active project and never touches another project's rows.
``verificat`` is a server-side guarantee: the stored quote must normalize
to a substring of the declared ``(entry_id, page_pdf)`` page text.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db
from app.notes.engine import STATUSES, _valid_quote, verify_dossier
from app.notes.map import (
    edge_problems,
    generate_edges,
    generate_project_map,
    project_map,
    validate_edge,
)
from app.notes.service import capture, dossier_payload, list_dossiers

router = APIRouter()

_VISUAL_VALUES = ("", "da", "nu", "necesar")


class CaptureIn(BaseModel):
    entry_id: int
    raw_note: str = Field(..., min_length=1, max_length=20000)
    page: int | None = None


class VerifyIn(BaseModel):
    page: int | None = None
    recheck: bool = False


class ClaimPatch(BaseModel):
    status: str | None = None
    quote: str | None = Field(None, max_length=8000)
    correction: str | None = Field(None, max_length=8000)
    visual_check: str | None = Field(None, max_length=16)
    page_printed: str | None = Field(None, max_length=32)
    page_pdf: int | None = None


class MapIn(BaseModel):
    replace: bool = False


class EdgeIn(BaseModel):
    from_code: str = ""
    to_code: str = ""
    motive: str = ""
    page: str | None = ""
    claim_code: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _active_project(conn) -> int:
    from app.routers.project import _get_active_project_id

    return _get_active_project_id(conn)


def _row_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def _dossier_or_404(conn, dossier_id: int, project_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM note_dossiers WHERE id = ? AND project_id = ?",
        [dossier_id, project_id],
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Dosarul nu a fost găsit în proiectul activ.")
    return _row_dict(row)


def _refresh_dossier_status(conn, dossier_id: int) -> None:
    rows = conn.execute(
        "SELECT status FROM note_claims WHERE dossier_id = ?", [dossier_id]
    ).fetchall()
    total = len(rows)
    ok = sum(1 for r in rows if _row_dict(r).get("status") == "verificat")
    if total > 0 and ok == total:
        status = "verificat"
    elif ok > 0:
        status = "partial"
    else:
        status = "draft"
    conn.execute(
        "UPDATE note_dossiers SET status = ?, updated_at = datetime('now') WHERE id = ?",
        [status, dossier_id],
    )
    conn.commit()


@router.get("/notes/dossiers")
def api_list_dossiers():
    """Dossiers of the active project, with entry titles and counters."""
    conn = get_db()
    try:
        return {"dossiers": list_dossiers(conn, _active_project(conn))}
    finally:
        conn.close()


@router.get("/notes/dossiers/{dossier_id}")
def api_get_dossier(dossier_id: int):
    """Full dossier payload; 404 when it belongs to another project."""
    conn = get_db()
    try:
        _dossier_or_404(conn, dossier_id, _active_project(conn))
        return dossier_payload(conn, dossier_id)
    finally:
        conn.close()


@router.post("/notes/capture")
async def api_capture(body: CaptureIn):
    """Capture a raw note into the entry's dossier and verify it."""
    conn = get_db()
    try:
        project_id = _active_project(conn)
        entry = conn.execute(
            "SELECT id FROM entries WHERE id = ?", [body.entry_id]
        ).fetchone()
        if entry is None:
            raise HTTPException(404, "Studiul nu a fost găsit.")
        return await capture(conn, project_id, body.entry_id, body.raw_note, body.page)
    finally:
        conn.close()


@router.post("/notes/dossiers/{dossier_id}/verify")
async def api_verify_dossier(dossier_id: int, body: VerifyIn):
    """Verify a dossier's open claims; ``recheck`` re-verifies all of them."""
    conn = get_db()
    try:
        _dossier_or_404(conn, dossier_id, _active_project(conn))
        result = await verify_dossier(
            conn, dossier_id, page_hint=body.page, apply=True, recheck=body.recheck
        )
        return {**result, **dossier_payload(conn, dossier_id)}
    finally:
        conn.close()


@router.patch("/notes/claims/{claim_id}")
def api_patch_claim(claim_id: int, body: ClaimPatch):
    """Human override of one claim; always recorded as ``manual``."""
    if body.status is not None and body.status not in STATUSES:
        raise HTTPException(
            422, "Status invalid. Acceptat: " + ", ".join(STATUSES) + "."
        )
    if body.visual_check is not None and body.visual_check not in _VISUAL_VALUES:
        raise HTTPException(
            422, "Verificare vizuală invalidă. Acceptat: da, nu, necesar (sau gol)."
        )
    conn = get_db()
    try:
        project_id = _active_project(conn)
        row = conn.execute(
            """SELECT nc.*, d.project_id, d.entry_id
                 FROM note_claims nc
                 JOIN note_dossiers d ON d.id = nc.dossier_id
                WHERE nc.id = ?""",
            [claim_id],
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Afirmația nu a fost găsită.")
        current = _row_dict(row)
        if current.get("project_id") != project_id:
            raise HTTPException(404, "Afirmația nu a fost găsită în proiectul activ.")

        status = body.status if body.status is not None else current.get("status")
        quote = body.quote if body.quote is not None else (current.get("quote") or "")
        page_pdf = body.page_pdf if body.page_pdf is not None else current.get("page_pdf")
        entry_id = int(current["entry_id"])

        # The product rule also binds human edits: `verificat` needs a
        # quote the server can find on the declared page.
        if status == "verificat" and not _valid_quote(conn, entry_id, page_pdf, quote):
            raise HTTPException(
                400,
                "Citatul nu a fost găsit pe pagina declarată; "
                "statusul `verificat` necesită un citat exact din acea pagină.",
            )

        fields = ["method = 'manual'"]
        params: list = []
        for column, value in (
            ("status", body.status),
            ("quote", body.quote),
            ("correction", body.correction),
            ("visual_check", body.visual_check),
            ("page_printed", body.page_printed),
            ("page_pdf", body.page_pdf),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        if status == "verificat":
            # Stamp the verification moment; any other status leaves the
            # original timestamp untouched instead of erasing it.
            fields.append("verified_at = ?")
            params.append(_now())
        params.append(claim_id)
        conn.execute(
            f"UPDATE note_claims SET {', '.join(fields)} WHERE id = ?", params
        )
        conn.commit()
        _refresh_dossier_status(conn, int(current["dossier_id"]))
        updated = conn.execute(
            "SELECT * FROM note_claims WHERE id = ?", [claim_id]
        ).fetchone()
        return _row_dict(updated)
    finally:
        conn.close()


@router.delete("/notes/dossiers/{dossier_id}")
def api_delete_dossier(dossier_id: int):
    """Delete a dossier of the active project (claims follow via cascade)."""
    conn = get_db()
    try:
        _dossier_or_404(conn, dossier_id, _active_project(conn))
        conn.execute("DELETE FROM note_dossiers WHERE id = ?", [dossier_id])
        conn.commit()
        return {"status": "ok", "deleted_id": dossier_id}
    finally:
        conn.close()


@router.get("/notes/project-map")
def api_project_map():
    """Read-only map of the active project: documents, concepts, bridges."""
    conn = get_db()
    try:
        return project_map(conn, _active_project(conn))
    finally:
        conn.close()


@router.post("/notes/project-map")
async def api_generate_project_map(body: MapIn):
    """Propose project concepts from verified claims; returns the fresh map too."""
    conn = get_db()
    try:
        project_id = _active_project(conn)
        result = await generate_project_map(conn, project_id, replace=body.replace)
        return {**result, **project_map(conn, project_id)}
    finally:
        conn.close()


@router.post("/notes/dossiers/{dossier_id}/map")
async def api_generate_edges(dossier_id: int, body: MapIn):
    """Propose edges between a dossier's verified claims."""
    conn = get_db()
    try:
        _dossier_or_404(conn, dossier_id, _active_project(conn))
        result = await generate_edges(conn, dossier_id, replace=body.replace)
        return {**result, **dossier_payload(conn, dossier_id)}
    finally:
        conn.close()


@router.post("/notes/dossiers/{dossier_id}/edges")
def api_create_edge(dossier_id: int, body: EdgeIn):
    """Manual edge with a motive and a page; 400 lists what failed."""
    conn = get_db()
    try:
        _dossier_or_404(conn, dossier_id, _active_project(conn))
        proposal = {
            "from_code": body.from_code,
            "to_code": body.to_code,
            "motive": body.motive,
            "page": body.page or "",
            "claim_code": body.claim_code or "",
        }
        problems = edge_problems(conn, dossier_id, proposal)
        if problems:
            raise HTTPException(
                400, "Muchie invalidă: " + ", ".join(problems) + "."
            )
        row = validate_edge(conn, dossier_id, proposal)
        assert row is not None  # noqa: S101 - problems were empty above
        cur = conn.execute(
            """INSERT INTO note_edges
               (dossier_id, from_code, to_code, motive, page, claim_code)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [dossier_id, row["from_code"], row["to_code"],
             row["motive"], row["page"], row["claim_code"]],
        )
        conn.commit()
        created = conn.execute(
            "SELECT * FROM note_edges WHERE id = ?", [cur.lastrowid]
        ).fetchone()
        return _row_dict(created)
    finally:
        conn.close()


@router.delete("/notes/edges/{edge_id}")
def api_delete_edge(edge_id: int):
    """Delete an edge of the active project."""
    conn = get_db()
    try:
        project_id = _active_project(conn)
        row = conn.execute(
            """SELECT e.id FROM note_edges e
                JOIN note_dossiers d ON d.id = e.dossier_id
               WHERE e.id = ? AND d.project_id = ?""",
            [edge_id, project_id],
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Muchia nu a fost găsită în proiectul activ.")
        conn.execute("DELETE FROM note_edges WHERE id = ?", [edge_id])
        conn.commit()
        return {"status": "ok", "deleted_id": edge_id}
    finally:
        conn.close()


@router.delete("/notes/concepts/{concept_id}")
def api_delete_concept(concept_id: int):
    """Delete a concept of the active project (links follow via cascade)."""
    conn = get_db()
    try:
        project_id = _active_project(conn)
        row = conn.execute(
            "SELECT id FROM note_concepts WHERE id = ? AND project_id = ?",
            [concept_id, project_id],
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Conceptul nu a fost găsit în proiectul activ.")
        conn.execute(
            "DELETE FROM note_concept_links WHERE concept_id = ?", [concept_id]
        )
        conn.execute("DELETE FROM note_concepts WHERE id = ?", [concept_id])
        conn.commit()
        return {"status": "ok", "deleted_id": concept_id}
    finally:
        conn.close()


# ── EXPORT: offline Markdown/HTML downloads (EXPORT lane appends below) ──

_ALLOWED_EXPORT_FORMATS = ("md", "html")


def _load_dossier_for_export(project_id: int, dossier_id: int) -> dict:
    """Blocking read of one dossier payload with its entry header."""
    from app.db import get_db
    from app.notes.service import dossier_payload

    conn = get_db()
    try:
        _dossier_or_404(conn, dossier_id, project_id)
        payload = dossier_payload(conn, dossier_id)
        entry = conn.execute(
            "SELECT title, authors, bib_year, entry_date FROM entries WHERE id = ?",
            [payload["dossier"].get("entry_id")],
        ).fetchone()
        if entry is not None:
            row = _row_dict(entry)
            year = row.get("bib_year") or _str_date_year(row.get("entry_date"))
            payload["entry"] = {"title": row.get("title") or "",
                                "authors": row.get("authors") or "",
                                "year": year or ""}
        return payload
    finally:
        conn.close()


def _str_date_year(value) -> str:
    text = "" if value is None else str(value)
    return text[:4] if text.strip() else ""


def _load_project_for_export(project_id: int) -> tuple[dict, list[dict]]:
    """Blocking read of the project row, its concepts and dossier payloads."""
    from app.db import get_db
    from app.notes.map import project_map
    from app.notes.service import dossier_payload, list_dossiers

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM project WHERE id = ?", [project_id]
        ).fetchone()
        project = _row_dict(row) if row is not None else {}
        project["concepts"] = project_map(conn, project_id).get("concepts", [])
        payloads = []
        for item in list_dossiers(conn, project_id):
            payload = dossier_payload(conn, item["id"])
            year = item.get("entry_year") or _str_date_year(item.get("entry_date"))
            payload["entry"] = {"title": item.get("entry_title") or "",
                                "authors": item.get("entry_authors") or "",
                                "year": year or ""}
            payloads.append(payload)
        return project, payloads
    finally:
        conn.close()


def _check_export_format(fmt: str) -> str:
    cleaned = (fmt or "").lower()
    if cleaned not in _ALLOWED_EXPORT_FORMATS:
        raise HTTPException(400, "Format necunoscut. Folosește `md` sau `html`.")
    return cleaned


@router.get("/notes/dossiers/{dossier_id}/export")
async def api_export_dossier(dossier_id: int, format: str = "html"):
    """Download one dossier as Markdown or self-contained HTML."""
    import anyio
    from fastapi.responses import HTMLResponse, PlainTextResponse

    from app.notes.export import dossier_html, dossier_markdown, slugify

    fmt = _check_export_format(format)

    def _load():
        from app.db import get_db

        conn = get_db()
        try:
            return _load_dossier_for_export(_active_project(conn), dossier_id)
        finally:
            conn.close()

    payload = await anyio.to_thread.run_sync(_load)
    entry = payload.get("entry") or {}
    slug = slugify(entry.get("title") or payload.get("dossier", {}).get("title"))
    if fmt == "md":
        return PlainTextResponse(
            dossier_markdown(payload),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{slug}.md"'},
        )
    return HTMLResponse(
        dossier_html(payload),
        headers={"Content-Disposition": f'attachment; filename="{slug}.html"'},
    )


@router.get("/notes/export")
async def api_export_project(format: str = "html"):
    """Download the whole project: one HTML document (or concatenated Markdown)."""
    import anyio
    from fastapi.responses import HTMLResponse, PlainTextResponse

    from app.notes.export import dossier_markdown, project_html, slugify

    fmt = _check_export_format(format)

    def _load():
        from app.db import get_db

        conn = get_db()
        try:
            return _load_project_for_export(_active_project(conn))
        finally:
            conn.close()

    project, payloads = await anyio.to_thread.run_sync(_load)
    slug = slugify(project.get("title"))
    if fmt == "md":
        body = "\n\n---\n\n".join(dossier_markdown(p) for p in payloads)
        return PlainTextResponse(
            body,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{slug}.md"'},
        )
    return HTMLResponse(
        project_html(project, payloads),
        headers={"Content-Disposition": f'attachment; filename="{slug}.html"'},
    )


# ── VISUAL CHECK: confirm an OCR-page claim against the page image (REQ-NOTE-10) ──

class VisualCheckIn(BaseModel):
    force: bool = False


@router.post("/notes/claims/{claim_id}/visual-check")
async def api_visual_check_claim(claim_id: int, body: VisualCheckIn):
    """Confirm a claim's quote against the page image; project-scoped.

    404 when the claim does not exist or its dossier belongs to another
    project. The blocking render/vision work runs off the event loop inside
    ``confirm_claim_visually``; this endpoint only does fast scoped reads.
    """
    from app.notes.vision import confirm_claim_visually

    conn = get_db()
    try:
        project_id = _active_project(conn)
        row = conn.execute(
            """SELECT nc.id, d.project_id FROM note_claims nc
                 JOIN note_dossiers d ON d.id = nc.dossier_id
                WHERE nc.id = ?""",
            [claim_id],
        ).fetchone()
        if row is None or _row_dict(row).get("project_id") != project_id:
            raise HTTPException(404, "Afirmația nu a fost găsită în proiectul activ.")
        result = await confirm_claim_visually(conn, claim_id, force=bool(body.force))
        conn.commit()
        return result
    finally:
        conn.close()


# ── STRUCTURE: compose dossier prose sections from verified claims (REQ-NOTE-09) ──


@router.post("/notes/dossiers/{dossier_id}/compose")
async def api_compose_sections(dossier_id: int):
    """Fill the generated sections from verified claims; project-scoped.

    404 when the dossier belongs to another project. The blocking page-text
    read runs off the event loop inside ``compose_sections``; this endpoint
    owns the transaction and returns the refreshed dossier in one round trip.
    """
    from app.notes.compose import compose_sections

    conn = get_db()
    try:
        _dossier_or_404(conn, dossier_id, _active_project(conn))
        result = await compose_sections(conn, dossier_id, apply=True)
        conn.commit()
        return {**result, **dossier_payload(conn, dossier_id)}
    finally:
        conn.close()
