"""Chat executor - slow pipeline execution and download handling."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.db import get_db
from app.pdf_rag import index_pdf
from app.studies_store import download_study_pdf, extract_pdf_url, pdf_path_for
from app.fulltext import fetch_fulltext
from app.study_import import (
    bulk_download_studies,
    find_duplicate_study,
    insert_study_entry,
)
from app.routers.maintenance import maintenance_analyze
from app.study_dedup import titles_match, same_study

logger = logging.getLogger("chronokg.chat.executor")


# ──────────────────────────────────────────────────────────────────────────────
# Auto-acquire studies from search results
# ──────────────────────────────────────────────────────────────────────────────

async def _auto_acquire_studies(results: List[Dict[str, Any]], actions: List[Dict[str, Any]], limit: int = 4) -> None:
    """Save search hits the user asked to download, then hunt their full text."""
    for study in results[:limit]:
        title = (study.get("title") or "").strip()
        if not title:
            continue
        source = study.get("pdf_url") or study.get("url") or ""
        conn = get_db()
        try:
            duplicate = find_duplicate_study(
                conn, title, doi=study.get("doi") or "", source_url=source
            )
            if duplicate:
                entry_id = duplicate["id"]
            else:
                try:
                    entry_id = insert_study_entry(
                        conn,
                        {
                            "title": title,
                            "authors": study.get("authors") or "",
                            "year": study.get("year"),
                            "doi": study.get("doi") or "",
                            "url": study.get("url") or "",
                            "source": study.get("source") or "",
                            "pdf_url": study.get("pdf_url") or "",
                            "abstract": study.get("snippet") or "",
                            "study_type": study.get("study_type") or "peer-reviewed paper",
                            "quality": study.get("quality"),
                            "tags": "academic,auto-acquired",
                        },
                    )
                except Exception as insert_exc:  # noqa: BLE001
                    logger.info("Auto-import failed for '%s': %s", title[:80], insert_exc)
                    continue
                actions.append({"type": "entry_created", "id": entry_id, "title": title})
        finally:
            conn.close()

        try:
            ft = await fetch_fulltext(entry_id, index=True)
            if ft.get("status") == "pdf":
                actions.append({
                    "type": "study_pdf_downloaded",
                    "entry_id": entry_id,
                    "pdf_path": _pdf_name_from_row(entry_id),
                    "indexed_chunks": ft.get("chunks") or 0,
                })
            elif ft.get("status") == "html":
                actions.append({
                    "type": "study_pdf_downloaded",
                    "entry_id": entry_id,
                    "pdf_path": "",
                    "status": "html",
                    "indexed_chunks": ft.get("chunks") or 0,
                    "message": f"Text integral HTML salvat pentru #{entry_id} ({ft.get('chars', 0)} caractere).",
                })
        except Exception as exc:  # noqa: BLE001
            logger.info("Auto-fulltext fetch failed for entry %s: %s", entry_id, exc)


def _pdf_name_from_row(entry_id: int) -> str:
    conn = get_db()
    try:
        row = conn.execute("SELECT pdf_path FROM entries WHERE id = ?", [entry_id]).fetchone()
        name = (row["pdf_path"] if row else "") or ""
    finally:
        conn.close()
    if not name:
        path = pdf_path_for(entry_id)
        name = path.name if path else ""
    return name


# ──────────────────────────────────────────────────────────────────────────────
# Download target resolution
# ──────────────────────────────────────────────────────────────────────────────

async def _resolve_download_target(ref: str) -> tuple[Optional[Dict[str, Any]], str]:
    """Resolve a DOWNLOAD_STUDY reference (id, DOI, URL, title) to an entry."""
    ref = (ref or "").strip().strip("[]")
    if not ref:
        return None, "empty reference; expected an entry id, DOI, URL or exact title"

    if ref.isdigit():
        row = _entry_row(int(ref))
        return (row, "") if row else (None, f"entry #{ref} not found")

    conn = get_db()
    try:
        import re
        doi_match = re.search(r"10\.\d{4,9}/[^\s,;\"'<>]+", ref)
        if doi_match:
            doi = doi_match.group(0).rstrip(".;,").lower()
            row = conn.execute(
                "SELECT * FROM entries WHERE archived = 0 AND lower(doi) = ? LIMIT 1", [doi]
            ).fetchone()
            if row:
                return dict(row), ""
        if re.match(r"^https?://", ref, re.IGNORECASE):
            row = conn.execute(
                """SELECT * FROM entries
                    WHERE archived = 0 AND (source = ? OR source LIKE ? OR content LIKE ?)
                    ORDER BY id LIMIT 1""",
                [ref, f"{ref}%", f"%{ref}%"],
            ).fetchone()
            if row:
                return dict(row), ""
        if len(ref) >= 4:
            candidates = conn.execute(
                "SELECT id, title FROM entries WHERE archived = 0"
            ).fetchall()
            for candidate in candidates:
                if titles_match(ref, candidate["title"]):
                    return _entry_row(candidate["id"]), ""
            rows = conn.execute(
                "SELECT * FROM entries WHERE archived = 0 AND title LIKE ? ORDER BY updated_at DESC LIMIT 25",
                [f"%{ref}%"],
            ).fetchall()
            if len(rows) == 1:
                return dict(rows[0]), ""
    finally:
        conn.close()

    # Not in the library: resolve the identifier to real metadata.
    meta: Optional[Dict[str, Any]] = None
    try:
        from app.study_import import resolve_identifier_metadata
        meta = await resolve_identifier_metadata(ref)
    except Exception as exc:  # noqa: BLE001
        logger.info("Identifier resolution failed for '%s': %s", ref[:80], exc)
    if not meta and len(ref) >= 15:
        try:
            from app.study_search import search_academic_studies
            candidates = await search_academic_studies(ref, max_results=3)
        except Exception as exc:  # noqa: BLE001
            logger.info("Academic lookup failed for '%s': %s", ref[:80], exc)
            candidates = []
        for cand in candidates:
            if titles_match(ref, cand.get("title") or ""):
                meta = cand
                break
        if not meta and candidates and len(ref.split()) >= 3:
            meta = candidates[0]

    if not meta:
        if re.match(r"^https?://", ref, re.IGNORECASE):
            name = ref.rstrip("/").rsplit("/", 1)[-1] or "PDF"
            meta = {
                "title": f"PDF: {name[:180]}",
                "source": ref,
                "url": ref,
                "pdf_url": ref,
                "study_type": "paper",
                "tags": "pdf,manual-download",
            }
        else:
            return None, (
                f"no study matched '{ref[:100]}'. Give a numeric entry id, a DOI "
                "(10.xxxx/...), a direct PDF URL, or an exact title already in the library."
            )

    conn = get_db()
    try:
        duplicate = find_duplicate_study(
            conn,
            meta.get("title") or "",
            doi=meta.get("doi") or "",
            source_url=meta.get("url") or meta.get("pdf_url") or "",
        )
        if duplicate:
            entry_id = duplicate["id"]
        else:
            entry_id = insert_study_entry(conn, meta)
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not import study for download '%s': %s", ref[:80], exc)
        return None, f"could not register the study ({type(exc).__name__})"
    finally:
        conn.close()
    row = _entry_row(entry_id)
    return (row, "") if row else (None, "study imported but could not be read back")


def _entry_row(entry_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Single download execution
# ──────────────────────────────────────────────────────────────────────────────

async def _execute_download_request(ref: str, *, index: bool = True) -> Dict[str, Any]:
    """Download + index the PDF for one study reference, with clear status."""
    entry, error = await _resolve_download_target(ref)
    if not entry:
        return {"type": "error", "detail": f"DOWNLOAD_STUDY failed: {error}"}
    entry_id = entry["id"]
    title = (entry.get("title") or "")[:120]

    existing_path = pdf_path_for(entry_id)
    if not existing_path and (entry.get("pdf_path") or "").strip():
        conn = get_db()
        try:
            conn.execute("UPDATE entries SET pdf_path = '' WHERE id = ?", [entry_id])
            conn.commit()
        finally:
            conn.close()
        entry["pdf_path"] = ""
    if existing_path:
        chunks = 0
        if not (entry.get("pdf_path") or "").strip():
            _set_pdf_path(entry_id, existing_path.name)
            entry["pdf_path"] = existing_path.name
        if index:
            try:
                chunks = await index_pdf(entry_id, existing_path)
            except Exception as exc:  # noqa: BLE001
                logger.info("Re-index after download failed for #%s: %s", entry_id, exc)
        return {
            "type": "study_pdf_downloaded",
            "entry_id": entry_id,
            "title": title,
            "pdf_path": entry.get("pdf_path") or existing_path.name,
            "indexed_chunks": chunks,
            "already_present": True,
            "message": f"PDF-ul pentru #{entry_id} era deja descărcat.",
        }

    url = extract_pdf_url(entry)
    fname = await download_study_pdf(entry_id, url, entry["title"]) if url else None
    indexed = 0
    if fname:
        _set_pdf_path(entry_id, fname)
        if index:
            try:
                indexed = await index_pdf(entry_id, pdf_path_for(entry_id))
            except Exception as exc:  # noqa: BLE001
                logger.info("Indexing downloaded PDF failed for #%s: %s", entry_id, exc)
        return {
            "type": "study_pdf_downloaded",
            "entry_id": entry_id,
            "title": title,
            "pdf_path": fname,
            "indexed_chunks": indexed,
            "message": f"PDF descărcat pentru #{entry_id} ({indexed} chunk-uri indexate).",
        }

    # Direct URL failed or absent: hunt the paper across open sources.
    try:
        ft = await fetch_fulltext(entry_id, index=index)
    except Exception as exc:  # noqa: BLE001
        logger.info("Full-text acquisition failed for #%s: %s", entry_id, exc)
        return {
            "type": "error",
            "detail": f"DOWNLOAD_STUDY failed for #{entry_id}: full-text lookup error ({type(exc).__name__})",
        }
    if ft.get("status") == "pdf":
        return {
            "type": "study_pdf_downloaded",
            "entry_id": entry_id,
            "title": title,
            "pdf_path": _pdf_name_from_row(entry_id),
            "indexed_chunks": ft.get("chunks") or 0,
            "source": ft.get("source") or "",
            "message": f"PDF găsit prin {ft.get('source') or 'open access'} pentru #{entry_id}.",
        }
    if ft.get("status") == "html":
        return {
            "type": "study_pdf_downloaded",
            "entry_id": entry_id,
            "title": title,
            "pdf_path": "",
            "status": "html",
            "source": ft.get("source") or "",
            "indexed_chunks": ft.get("chunks") or 0,
            "message": (
                f"Nu există PDF deschis pentru #{entry_id}; am salvat și indexat textul "
                f"integral HTML de la {ft.get('source') or 'sursa deschisă'}."
            ),
        }
    tried = ", ".join(sorted({t.get("source") or "?" for t in ft.get("tried") or []})) or "toate sursele"
    return {
        "type": "error",
        "detail": (
            f"Nu am găsit o copie open-access pentru #{entry_id} «{title}». "
            f"Am încercat: {tried}. Adaugă manual PDF-ul în folderul de studii sau "
            "verifică DOI-ul/URL-ul sursei."
        ),
    }


def _set_pdf_path(entry_id: int, fname: str) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE entries SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?", [fname, entry_id])
        try:
            from app.studies_store import store_entry_pdf_hash

            store_entry_pdf_hash(conn, entry_id)
        except Exception:  # noqa: BLE001 - hashing is best-effort
            pass
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Batch download missing PDFs
# ──────────────────────────────────────────────────────────────────────────────

def _entries_missing_pdf(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, title, pdf_path FROM entries
                WHERE archived = 0
                  AND (study_type != '' OR tags LIKE '%study%' OR tags LIKE '%pdf%'
                       OR content LIKE '%**PDF:**%' OR (doi IS NOT NULL AND doi != ''))
                ORDER BY id LIMIT ?""",
            [limit * 4],
        ).fetchall()
    finally:
        conn.close()
    pending = [dict(r) for r in rows if not pdf_path_for(r["id"])]
    return pending[:limit]


async def _execute_download_missing() -> Dict[str, Any]:
    """Batch-download every study that has no PDF yet."""
    import anyio
    pending = await anyio.to_thread.run_sync(_entries_missing_pdf)
    if not pending:
        return {
            "type": "download_batch",
            "downloaded": [], "failed": [], "indexed_chunks": 0,
            "message": "Toate studiile au deja un PDF descărcat.",
        }
    try:
        result = await bulk_download_studies([row["id"] for row in pending])
    except Exception as exc:  # noqa: BLE001
        logger.error("Batch PDF download failed: %s", exc)
        return {"type": "error", "detail": f"DOWNLOAD_STUDY failed: batch download error ({type(exc).__name__})"}
    downloaded = result.get("downloaded") or []
    failed = result.get("failed") or []
    text_only = result.get("text_only") or []
    return {
        "type": "download_batch",
        "downloaded": downloaded,
        "failed": failed,
        "text_only": text_only,
        "indexed_chunks": result.get("indexed_chunks", 0),
        "message": (
            f"PDF-uri descărcate: {len(downloaded)}/{len(pending)}; "
            f"doar text integral (fără PDF): {len(text_only)}; "
            f"eșuate: {len(failed)}; chunk-uri indexate: {result.get('indexed_chunks', 0)}."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Dispatch download actions
# ──────────────────────────────────────────────────────────────────────────────

async def _dispatch_download_actions(actions: List[Dict[str, Any]]) -> None:
    """Execute every download request gathered from one model reply."""
    from app.chat.task_manager import _get_active_task, _begin_task, _update_task, _finish_task
    if any(a["type"] == "download_missing_request" for a in actions):
        current = _get_active_task()
        if not current or current.get("kind") != "download":
            _begin_task("download", "download all missing study PDFs")
    for request in [a for a in actions if a["type"] == "download_study_request"]:
        ref = request.get("ref") or str(request.get("entry_id") or "")
        try:
            result = await _execute_download_request(ref)
        except Exception as exc:  # noqa: BLE001
            logger.error("DOWNLOAD_STUDY crashed for '%s': %s", ref, exc)
            result = {
                "type": "error",
                "detail": f"DOWNLOAD_STUDY failed for '{ref[:100]}': {type(exc).__name__}",
            }
        actions.append(result)
    for _batch in [a for a in actions if a["type"] == "download_missing_request"]:
        result = await _execute_download_missing()
        actions.append(result)
        if result["type"] == "download_batch":
            _update_task(
                kind="download",
                progress={
                    "downloaded": len(result["downloaded"]),
                    "text_only": len(result.get("text_only") or []),
                    "failed": len(result["failed"]),
                    "indexed_chunks": result["indexed_chunks"],
                },
            )
            _finish_task("done", kind="download")


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline triggers (INDEX_PDF, REANALYZE_DATA)
# ──────────────────────────────────────────────────────────────────────────────

async def _execute_index_pdf(entry_id: int) -> Dict[str, Any]:
    """Re-index one study PDF; returns how many chunks were stored."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id, title FROM entries WHERE id = ?", [entry_id]).fetchone()
    finally:
        conn.close()
    if not row:
        return {"type": "error", "detail": f"INDEX_PDF failed: entry #{entry_id} not found"}
    path = pdf_path_for(entry_id)
    if not path:
        return {
            "type": "error",
            "detail": f"INDEX_PDF failed: no downloaded PDF for entry #{entry_id}; run [DOWNLOAD_STUDY: {entry_id}] first.",
        }
    chunks = await index_pdf(entry_id, path)
    if not chunks:
        return {
            "type": "error",
            "detail": f"INDEX_PDF failed: the PDF for entry #{entry_id} could not be read (empty or no text layer).",
        }
    return {
        "type": "pdf_indexed",
        "entry_id": entry_id,
        "chunks": chunks,
        "message": f"Am re-indexat PDF-ul pentru #{entry_id}: {chunks} chunk-uri.",
    }


def _scan_issues() -> tuple[Dict[str, Any], List[str]]:
    """Read-only integrity scan (the `issues` mode never mutates the KB)."""
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM entries WHERE archived = 0").fetchone()[0]
        undated = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE archived = 0 AND (entry_date IS NULL OR trim(entry_date) = '')"
        ).fetchone()[0]
        without_source = conn.execute(
            """SELECT COUNT(*) FROM entries
                WHERE archived = 0 AND study_type != ''
                  AND trim(coalesce(doi, '')) = ''
                  AND trim(coalesce(source, '')) = ''
                  AND trim(coalesce(pdf_path, '')) = ''"""
        ).fetchone()[0]
        theories_without_evidence = conn.execute(
            """SELECT COUNT(*) FROM theories t
                WHERE NOT EXISTS (SELECT 1 FROM theory_evidence te WHERE te.theory_id = t.id)"""
        ).fetchone()[0]
        rows = [
            dict(row)
            for row in conn.execute(
                """SELECT id, title, source FROM entries
                    WHERE archived = 0
                      AND (study_type != '' OR tags LIKE '%study%' OR tags LIKE '%pdf%'
                           OR (pdf_path IS NOT NULL AND pdf_path != ''))
                    ORDER BY id"""
            ).fetchall()
        ]
        seen: set[int] = set()
        groups: List[List[int]] = []
        for index, first in enumerate(rows):
            if first["id"] in seen:
                continue
            group = [first["id"]]
            for other in rows[index + 1:]:
                if other["id"] in seen:
                    continue
                if same_study(first["title"], first["source"], other["title"], other["source"]):
                    group.append(other["id"])
            if len(group) > 1:
                seen.update(group)
                groups.append(group)

        issues = {
            "entries": total,
            "undated": undated,
            "studies_without_source": without_source,
            "theories_without_evidence": theories_without_evidence,
            "duplicate_groups": len(groups),
        }
        lines = [
            f"Intrări active: {total}; fără dată: {undated}.",
            f"Studii fără DOI/URL/PDF: {without_source}.",
            f"Teorii fără dovezi: {theories_without_evidence}.",
        ]
        if groups:
            lines.append(f"Grupuri de studii duplicate: {len(groups)}.")
            for group in groups[:5]:
                lines.append("  Duplicat: " + ", ".join(f"#{entry_id}" for entry_id in group))
        else:
            lines.append("Nu am găsit studii duplicate.")
        if undated or groups:
            lines.append("Rulează [REANALYZE_DATA: full] pentru normalizare și fuzionare.")
        return issues, lines[:10]
    finally:
        conn.close()


async def _execute_reanalyze(mode: str) -> Dict[str, Any]:
    """`issues` runs a read-only scan; `full` reuses the maintenance pipeline."""
    if mode == "issues":
        try:
            issues, lines = _scan_issues()
        except Exception as exc:  # noqa: BLE001
            return {"type": "error", "detail": f"REANALYZE_DATA failed: scan error ({type(exc).__name__})"}
        return {
            "type": "reanalysis_done",
            "mode": "issues",
            "issues": issues,
            "summary": lines,
            "message": "Verificare (doar citire): " + (lines[0] if lines else "nicio problemă găsită."),
        }
    if mode != "full":
        return {
            "type": "error",
            "detail": f"REANALYZE_DATA failed: mode must be full or issues",
        }
    try:
        response = await maintenance_analyze()
        report: Dict[str, Any] = {}
        error = ""
        async for chunk in response.body_iterator:
            text = chunk.decode("utf-8", "replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            for line in text.splitlines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    continue
                if event.get("type") == "done":
                    report = event.get("report") or {}
                elif event.get("type") == "error":
                    error = str(event.get("content") or "unknown error")
        if error:
            return {"type": "error", "detail": f"REANALYZE_DATA failed: {error[:500]}"}
        if not report:
            return {"type": "error", "detail": "REANALYZE_DATA failed: the maintenance pipeline returned no report"}
        lines = [
            f"Intrări scanate: {report.get('entries_scanned', 0)} (corectate: {report.get('entries_fixed', 0)}).",
            f"Date corectate: {report.get('dates_fixed', 0)}; etichete: {report.get('tags_fixed', 0)}; studii clasificate: {report.get('studies_classified', 0)}.",
            f"Relații eliminate: {report.get('relationships_removed', 0)}; auto-legături: {report.get('self_links_removed', 0)}; dovezi orfane: {report.get('evidence_removed', 0)}.",
            f"PDF-uri: găsite {report.get('pdfs_found', 0)}, re-analizate {report.get('pdfs_reanalyzed', 0)}, chunk-uri indexate {report.get('pdfs_indexed', 0)}.",
            f"Grupuri duplicate: {report.get('duplicate_groups', 0)}; intrări fuzionate: {report.get('duplicates_merged', 0)}.",
            f"Teorii actualizate: {report.get('theories_updated', 0)}.",
            f"Fără dată: {report.get('undated_entries', 0)}; active: {report.get('entries_active', 0)}.",
            f"Total: {report.get('relationships_count', 0)} relații, {report.get('theories_count', 0)} teorii.",
        ]
        for warning in (report.get("warnings") or [])[:2]:
            lines.append(f"Avertisment: {warning}")
        lines = lines[:10]
        return {
            "type": "reanalysis_done",
            "mode": "full",
            "report": report,
            "summary": lines,
            "message": "Analiză completă: " + "; ".join(lines[:3]),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("REANALYZE_DATA failed: %s", exc)
        return {"type": "error", "detail": f"REANALYZE_DATA failed: {type(exc).__name__}"}