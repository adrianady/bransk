"""Dossier lifecycle: capture notes, build payloads, list dossiers."""

import json
import logging

import anyio

from app.notes.engine import SECTION_KINDS, verify_dossier

logger = logging.getLogger("chronokg.notes.service")

#: Default section titles in display order (position = index).
SECTION_TITLES: tuple[tuple[str, str], ...] = (
    ("fisa_sursei", "Fișa sursei"),
    ("ce_spune", "Ce spune sursa"),
    ("continut", "Conținut"),
    ("teza", "Teză"),
    ("corectii", "Corecții"),
    ("lipsea", "Ce lipsea"),
    ("glosar", "Glosar"),
    ("formulari", "Formulări"),
    ("de_verificat", "De verificat"),
)


def _row_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def get_or_create_dossier(conn, project_id: int, entry_id: int) -> dict:
    """Return the dossier for ``(project_id, entry_id)``, creating it once."""
    row = conn.execute(
        "SELECT * FROM note_dossiers WHERE project_id = ? AND entry_id = ?",
        [project_id, entry_id],
    ).fetchone()
    if row is not None:
        return _row_dict(row)
    entry = conn.execute("SELECT title FROM entries WHERE id = ?", [entry_id]).fetchone()
    title = ""
    if entry is not None:
        try:
            title = entry["title"] or ""
        except Exception:
            title = entry[0] or ""
    cur = conn.execute(
        "INSERT INTO note_dossiers (project_id, entry_id, title) VALUES (?, ?, ?)",
        [project_id, entry_id, title],
    )
    conn.commit()
    created = conn.execute(
        "SELECT * FROM note_dossiers WHERE id = ?", [cur.lastrowid]
    ).fetchone()
    return _row_dict(created)


def _ensure_sections(conn, dossier_id: int, entry_id: int) -> None:
    """Create the nine dossier sections once; prefill the source sheet."""
    for position, (kind, title) in enumerate(SECTION_TITLES):
        if kind not in SECTION_KINDS:
            raise ValueError(f"Unknown dossier section kind: {kind!r}")
        conn.execute(
            """INSERT OR IGNORE INTO note_sections (dossier_id, kind, title, position, body_md)
               VALUES (?, ?, ?, ?, '')""",
            [dossier_id, kind, title, position],
        )
    row = conn.execute(
        "SELECT body_md FROM note_sections WHERE dossier_id = ? AND kind = 'fisa_sursei'",
        [dossier_id],
    ).fetchone()
    if row is not None and not (_row_dict(row).get("body_md") or "").strip():
        entry = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        if entry is not None:
            data = _row_dict(entry)
            year = data.get("bib_year") or data.get("entry_date") or ""
            lines = [
                f"Titlu: {data.get('title') or ''}",
                f"Autori: {data.get('authors') or ''}",
                f"An: {year}",
                f"DOI: {data.get('doi') or ''}",
                f"Sursă: {data.get('source') or ''}",
                f"PDF: {data.get('pdf_path') or ''}",
            ]
            conn.execute(
                "UPDATE note_sections SET body_md = ?, updated_at = datetime('now')"
                " WHERE dossier_id = ? AND kind = 'fisa_sursei'",
                ["\n".join(lines), dossier_id],
            )
    conn.commit()


def _entry_pdf_path(entry_id: int, pdf_path_value: str):
    """Resolve a readable PDF file for *entry_id* (studies dir first).

    ``entries.pdf_path`` is a file name inside the studies folder; the
    ``pdf_path_for`` glob is preferred because it also repairs rows whose
    link went stale. An absolute path is honoured when it exists on disk.
    """
    from pathlib import Path

    from app.studies_store import STUDIES_DIR, pdf_path_for

    found = pdf_path_for(entry_id)
    if found is not None:
        return found
    name = (pdf_path_value or "").strip()
    if not name:
        return None
    candidate = Path(name)
    if not candidate.is_absolute():
        candidate = STUDIES_DIR / name
    return candidate if candidate.is_file() else None


def _extract_in_thread(entry_id: int, path: str) -> int:
    """Extract page texts off the event loop, on a private connection.

    sqlite3 connections are not thread-safe, so this never touches the
    caller's connection: it opens its own, extracts, commits and closes.
    """
    from app.db import get_db
    from app.page_text import ensure_page_texts

    conn = get_db()
    try:
        return int(ensure_page_texts(conn, entry_id, path))
    finally:
        conn.close()


async def capture(
    conn, project_id: int, entry_id: int, raw_note: str, page: int | None = None
) -> dict:
    """Capture a raw note into a dossier and verify its statements.

    Splits the note with ``app.note_check.split_statements``, stores one
    claim per statement with a stable ``A{n}`` code, ensures the nine
    sections exist, then awaits :func:`verify_dossier`.
    """
    from app.note_check import split_statements
    from app.notes.engine import next_code
    from app.page_text import _normalize

    entry = conn.execute(
        "SELECT id, pdf_path FROM entries WHERE id = ?", [entry_id]
    ).fetchone()
    if entry is None:
        raise LookupError("Studiul nu a fost găsit.")
    pdf_path_value = _row_dict(entry).get("pdf_path") or ""

    dossier = get_or_create_dossier(conn, project_id, entry_id)
    dossier_id = int(dossier["id"])
    _ensure_sections(conn, dossier_id, entry_id)

    seen = {
        _normalize(r["statement"])
        for r in conn.execute(
            "SELECT statement FROM note_claims WHERE dossier_id = ?", [dossier_id]
        ).fetchall()
    }
    statements = split_statements(raw_note or "")
    inserted = 0
    for statement in statements:
        norm = _normalize(statement)
        if norm in seen:
            continue  # re-sending the same note adds nothing
        seen.add(norm)
        code = next_code(conn, dossier_id)
        conn.execute(
            """INSERT INTO note_claims (dossier_id, code, raw_note, statement)
               VALUES (?, ?, ?, ?)""",
            [dossier_id, code, statement, statement],
        )
        inserted += 1
    if inserted:
        conn.commit()

    try:
        page_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM page_texts WHERE entry_id = ?", [entry_id]
        ).fetchone()
        has_pages = int(page_rows["n"]) > 0
    except Exception:
        has_pages = True  # on doubt, skip extraction instead of thread-hopping
    if not has_pages:
        pdf_file = _entry_pdf_path(entry_id, pdf_path_value)
        if pdf_file is not None:
            try:
                await anyio.to_thread.run_sync(
                    _extract_in_thread, entry_id, str(pdf_file)
                )
            except Exception as exc:  # noqa: BLE001 - extraction is best-effort
                logger.info(
                    "Extragerea paginilor a eșuat pentru #%s: %s", entry_id, exc
                )

    await verify_dossier(conn, dossier_id, page_hint=page, apply=True)
    return dossier_payload(conn, dossier_id)


def _claim_sort_key(code: str) -> tuple:
    import re

    match = re.fullmatch(r"A(\d+)", (code or "").strip())
    if match:
        return (0, int(match.group(1)))
    return (1, code or "")


def dossier_payload(conn, dossier_id: int) -> dict:
    """Full dossier view: dossier row, claims, sections, edges, rounds."""
    dossier = conn.execute(
        "SELECT * FROM note_dossiers WHERE id = ?", [dossier_id]
    ).fetchone()
    if dossier is None:
        raise LookupError("Dosarul nu există.")
    dossier_dict = _row_dict(dossier)

    claim_rows = conn.execute(
        "SELECT * FROM note_claims WHERE dossier_id = ?", [dossier_id]
    ).fetchall()
    claims = sorted(
        (_row_dict(r) for r in claim_rows),
        key=lambda c: _claim_sort_key(c.get("code") or ""),
    )
    sections = [
        _row_dict(r)
        for r in conn.execute(
            "SELECT * FROM note_sections WHERE dossier_id = ? ORDER BY position, id",
            [dossier_id],
        ).fetchall()
    ]
    edges = [
        _row_dict(r)
        for r in conn.execute(
            "SELECT * FROM note_edges WHERE dossier_id = ? ORDER BY id", [dossier_id]
        ).fetchall()
    ]
    rounds = [
        _row_dict(r)
        for r in conn.execute(
            "SELECT * FROM note_rounds WHERE dossier_id = ?"
            " ORDER BY id DESC LIMIT 20",
            [dossier_id],
        ).fetchall()
    ]
    by_status: dict[str, int] = {}
    for claim in claims:
        by_status[claim.get("status", "")] = by_status.get(claim.get("status", ""), 0) + 1
    summary = {
        "verificat": by_status.get("verificat", 0),
        "neverificat": by_status.get("neverificat", 0),
        "total": len(claims),
    }
    return {
        "dossier": dossier_dict,
        "claims": claims,
        "sections": sections,
        "edges": edges,
        "rounds": rounds,
        "summary": summary,
        "by_status": by_status,
    }


def list_dossiers(conn, project_id: int) -> list[dict]:
    """Dossiers of one project with entry titles and claim counters."""
    rows = conn.execute(
        """SELECT d.*, e.title AS entry_title, e.authors AS entry_authors,
                  e.bib_year AS entry_year, e.entry_date AS entry_date
             FROM note_dossiers d
             JOIN entries e ON e.id = d.entry_id
            WHERE d.project_id = ?
            ORDER BY d.updated_at DESC, d.id DESC""",
        [project_id],
    ).fetchall()
    dossiers: list[dict] = []
    for row in rows:
        item = _row_dict(row)
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM note_claims"
            " WHERE dossier_id = ? GROUP BY status",
            [item["id"]],
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in (_row_dict(r) for r in counts)}
        total = sum(by_status.values())
        item["claims_total"] = total
        item["claims_verificat"] = by_status.get("verificat", 0)
        item["by_status"] = by_status
        dossiers.append(item)
    return dossiers
