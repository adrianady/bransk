"""Study duplicates & merge — find and fuse library rows describing one work.

Detection combines (in order):
  1. identical ``pdf_hash`` (same uploaded file),
  2. identical normalized ``doi``,
  3. identical normalized ``isbn`` (books),
  4. fuzzy ``titles_match`` from ``app.study_dedup``.

Merging archives the duplicate row (``archived = 1``) after moving every link
(study notes, highlights, claim evidence, RAG chunks, relationships, tags) onto
the surviving entry — nothing is deleted that the user wrote.
"""
import io
import logging
import re
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.db import get_db
from app.models import normalize_doi
from app.study_dedup import titles_match
from app.studies_store import MAX_SIZE, pdf_path_for, store_entry_pdf_hash
from app.tagging import normalize_tags, sync_tags

logger = logging.getLogger("chronokg.studies")
router = APIRouter()


def _norm_isbn(value: str) -> str:
    digits = re.sub(r"[^0-9Xx]", "", (value or ""))
    return digits.upper()


def _entry_summary(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "doi": row.get("doi") or "",
        "isbn": row.get("isbn") or "",
        "study_type": row.get("study_type") or "",
        "read_status": row.get("read_status") or "unread",
        "has_pdf": bool(row.get("pdf_path")),
        "created_at": row.get("created_at") or "",
    }


@router.get("/studies/duplicates")
def study_duplicates():
    """Groups of non-archived entries that look like the same work."""
    conn = get_db()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
        has_hash = "pdf_hash" in cols
        has_isbn = "isbn" in cols
        has_read = "read_status" in cols
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM entries WHERE archived = 0 AND COALESCE(study_type, '') != 'concept' ORDER BY id"
        ).fetchall()]

        groups: list[dict] = []
        used: set[int] = set()

        def _group_keyed(ids: list[int], reason: str):
            fresh = [i for i in ids if i not in used]
            if len(fresh) >= 2:
                by_id = {r["id"]: r for r in rows}
                groups.append({
                    "reason": reason,
                    "entries": [_entry_summary(by_id[i]) for i in fresh],
                })
                used.update(fresh)

        # 1) same pdf_hash
        if has_hash:
            buckets: dict[str, list[int]] = {}
            for r in rows:
                h = (r.get("pdf_hash") or "").strip()
                if h:
                    buckets.setdefault(h, []).append(r["id"])
            for ids in buckets.values():
                _group_keyed(sorted(ids), "pdf_hash")

        # 2) same normalized doi
        buckets = {}
        for r in rows:
            try:
                doi = normalize_doi(r.get("doi") or "")
            except ValueError:
                doi = (r.get("doi") or "").strip().lower()
            if doi:
                buckets.setdefault(doi.lower(), []).append(r["id"])
        for ids in buckets.values():
            _group_keyed(sorted(ids), "doi")

        # 3) same isbn
        if has_isbn:
            buckets = {}
            for r in rows:
                isbn = _norm_isbn(r.get("isbn") or "")
                if isbn:
                    buckets.setdefault(isbn, []).append(r["id"])
            for ids in buckets.values():
                _group_keyed(sorted(ids), "isbn")

        # 4) fuzzy title match (pairwise over still-ungrouped rows)
        remaining = [r for r in rows if r["id"] not in used]
        title_grouped: set[int] = set()
        for i, first in enumerate(remaining):
            if first["id"] in title_grouped:
                continue
            group = [first]
            for other in remaining[i + 1:]:
                if other["id"] in title_grouped:
                    continue
                try:
                    if titles_match(first.get("title") or "", other.get("title") or ""):
                        group.append(other)
                except Exception:  # noqa: BLE001 - one bad title must not kill the scan
                    continue
            if len(group) >= 2:
                groups.append({
                    "reason": "title",
                    "entries": [_entry_summary(r) for r in group],
                })
                title_grouped.update(r["id"] for r in group)
        used.update(title_grouped)

        duplicate_total = sum(len(g["entries"]) - 1 for g in groups)
        return {
            "duplicate_groups": groups,
            "count": len(groups),
            "total_groups": len(groups),
            "total_duplicates": duplicate_total,
        }
    finally:
        conn.close()


class StudyMergeRequest(BaseModel):
    keep_id: int
    duplicate_id: int
    preferred_title: Optional[str] = Field(None, max_length=500)
    merge_notes: bool = True
    merge_highlights: bool = True
    merge_evidence: bool = True
    merge_relations: bool = True


def _table_has(conn, table: str, column: str) -> bool:
    try:
        return any(r["name"] == column for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
    except Exception:
        return False


@router.post("/studies/merge")
def merge_studies(body: StudyMergeRequest):
    """Fuse ``duplicate_id`` into ``keep_id``; the duplicate is archived."""
    if body.keep_id == body.duplicate_id:
        raise HTTPException(400, "keep_id and duplicate_id must differ")
    conn = get_db()
    try:
        keep = conn.execute("SELECT * FROM entries WHERE id = ?", [body.keep_id]).fetchone()
        dup = conn.execute("SELECT * FROM entries WHERE id = ?", [body.duplicate_id]).fetchone()
        if not keep or not dup:
            raise HTTPException(404, "One or both entries not found")
        keep, dup = dict(keep), dict(dup)
        if dup.get("archived"):
            raise HTTPException(400, "duplicate_id is already archived")

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}

        # Move the PDF file when keep has none but the duplicate does.
        if not (keep.get("pdf_path") or "").strip() and (dup.get("pdf_path") or "").strip():
            import shutil

            src = pdf_path_for(body.duplicate_id)
            if src and src.exists():
                from app.studies_store import ensure_dir, pdf_filename

                dest = ensure_dir() / pdf_filename(body.keep_id, keep.get("title") or "study")
                try:
                    shutil.copyfile(src, dest)
                    conn.execute(
                        "UPDATE entries SET pdf_path = ? WHERE id = ?",
                        [dest.name, body.keep_id],
                    )
                    keep["pdf_path"] = dest.name
                    if "pdf_hash" in cols and (dup.get("pdf_hash") or "").strip():
                        conn.execute(
                            "UPDATE entries SET pdf_hash = ? WHERE id = ?",
                            [dup["pdf_hash"], body.keep_id],
                        )
                        keep["pdf_hash"] = dup["pdf_hash"]
                except OSError as exc:
                    logger.info("PDF move during merge failed: %s", exc)
        if "pdf_hash" in cols and not (keep.get("pdf_hash") or "").strip():
            digest = store_entry_pdf_hash(conn, body.keep_id)
            if digest:
                keep["pdf_hash"] = digest

        # Re-point every link table at the survivor.
        if body.merge_notes:
            for table in ("study_notes", "documents"):
                try:
                    conn.execute(
                        f"UPDATE {table} SET entry_id = ? WHERE entry_id = ?",
                        [body.keep_id, body.duplicate_id],
                    )
                except Exception as exc:  # noqa: BLE001 - table may not exist on old DBs
                    logger.info("Merge skipped %s: %s", table, exc)
        if body.merge_highlights:
            try:
                conn.execute(
                    "UPDATE pdf_highlights SET entry_id = ? WHERE entry_id = ?",
                    [body.keep_id, body.duplicate_id],
                )
            except Exception as exc:  # noqa: BLE001
                logger.info("Merge skipped pdf_highlights: %s", exc)
        if body.merge_evidence:
            for table, col in (("project_claim_evidence", "entry_id"),):
                try:
                    conn.execute(
                        f"UPDATE {table} SET {col} = ? WHERE {col} = ?",
                        [body.keep_id, body.duplicate_id],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.info("Merge skipped %s: %s", table, exc)
        if body.merge_relations:
            for col in ("from_id", "to_id"):
                other = "to_id" if col == "from_id" else "from_id"
                # Drop rows that would collide on UNIQUE(from_id, to_id, label).
                conn.execute(
                    f"""DELETE FROM relationships WHERE {col} = ?
                       AND ({other}, label) IN (
                           SELECT {other}, label FROM relationships WHERE {col} = ?)""",
                    [body.duplicate_id, body.keep_id],
                )
                conn.execute(
                    f"UPDATE relationships SET {col} = ? WHERE {col} = ?",
                    [body.keep_id, body.duplicate_id],
                )
            # Never leave self-loops behind.
            conn.execute(
                "DELETE FROM relationships WHERE from_id = to_id AND (from_id = ? OR to_id = ?)",
                [body.keep_id, body.keep_id],
            )

        # Union tags.
        keep_tags = normalize_tags(
            ((keep.get("tags") or "") + "," + (dup.get("tags") or "")).strip(",")
        )
        conn.execute("UPDATE entries SET tags = ? WHERE id = ?", [keep_tags, body.keep_id])
        try:
            sync_tags(conn, keep_tags)
        except Exception:  # noqa: BLE001 - tags table is best-effort
            pass

        # Fill gaps on the survivor from the duplicate.
        fillable = [c for c in (
            "doi", "doi_source", "pdf_path", "pdf_hash", "authors", "design",
            "effect_direction", "entry_date", "bib_title", "bib_authors",
            "bib_year", "doi_checked_at", "isbn", "read_status", "source",
        ) if c in cols]
        fill_updates = {}
        for field in fillable:
            if not (keep.get(field) or "") and (dup.get(field) or ""):
                fill_updates[field] = dup[field]
        if body.preferred_title and body.preferred_title.strip():
            fill_updates["title"] = body.preferred_title.strip()[:500]
        if body.merge_notes:
            extra = (dup.get("content") or "").strip()
            if extra and extra not in (keep.get("content") or ""):
                fill_updates["content"] = ((keep.get("content") or "").rstrip()
                                           + "\n\n" + extra)[:20000]
        if fill_updates:
            set_clause = ", ".join(f"{k} = ?" for k in fill_updates)
            conn.execute(
                f"UPDATE entries SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                [*fill_updates.values(), body.keep_id],
            )

        # Archive the loser, recording where it went.
        dup_tags = ((dup.get("tags") or "") + f",merged_into:{body.keep_id}").strip(",")[:500]
        if "pdf_hash" in cols:
            conn.execute(
                "UPDATE entries SET archived = 1, tags = ?, updated_at = datetime('now') WHERE id = ?",
                [normalize_tags(dup_tags), body.duplicate_id],
            )
        else:
            conn.execute(
                "UPDATE entries SET archived = 1, tags = ?, updated_at = datetime('now') WHERE id = ?",
                [normalize_tags(dup_tags), body.duplicate_id],
            )
        conn.commit()
        row = conn.execute("SELECT * FROM entries WHERE id = ?", [body.keep_id]).fetchone()
        return {"success": True, "keep_entry": dict(row)}
    finally:
        conn.close()


# ── PDF metadata extraction ─────────────────────────────────────────────
# A dropped PDF often carries the title/author in its own metadata, and the
# first page usually shows the DOI or ISBN. Reading those here means the user
# no longer has to retype what the file already knows, and the study/book
# classification can be suggested instead of guessed at upload time.

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _clean_isbn(value: str) -> str:
    digits = re.sub(r"[^0-9Xx]", "", value or "").upper()
    if len(digits) == 13 and digits.startswith(("978", "979")):
        return digits
    if len(digits) == 10:
        return digits
    return ""


def _find_isbn(text: str) -> str:
    """Find an ISBN-13 (preferred) or ISBN-10 embedded in free text."""
    candidates = [m.group(0) for m in re.finditer(r"[0-9Xx][0-9Xx\s-]{8,25}[0-9Xx]", text or "")]
    for candidate in candidates:
        digits = _clean_isbn(candidate)
        if len(digits) == 13:
            return digits
    for candidate in candidates:
        digits = _clean_isbn(candidate)
        if len(digits) == 10:
            return digits
    return ""


def _first_page_title(text: str) -> str:
    """Return the first plausible title line from page-one text."""
    for raw in (text or "").splitlines():
        line = " ".join(raw.split()).strip()
        if not line:
            continue
        lowered = line.casefold()
        if len(line) < 12 or len(line) > 220:
            continue
        if lowered.startswith(("doi", "http", "www.", "issn", "isbn", "vol.", "volume")):
            continue
        if sum(ch.isdigit() for ch in line) > len(line) * 0.4:
            continue
        return line
    return ""


def _read_pdf_metadata(data: bytes) -> dict:
    """Extract embedded metadata, page count and first-page text from bytes."""
    meta: dict = {}
    page_count = 0
    first_text = ""
    try:
        import pymupdf  # PyMuPDF

        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            page_count = doc.page_count
            meta = dict(doc.metadata or {})
            if page_count:
                first_text = doc[0].get_text() or ""
        finally:
            doc.close()
    except Exception:  # noqa: BLE001 - fall back to pypdf below
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            page_count = len(reader.pages)
            raw = reader.metadata or {}
            meta = {
                "title": getattr(raw, "title", "") or "",
                "author": getattr(raw, "author", "") or "",
                "subject": getattr(raw, "subject", "") or "",
                "keywords": getattr(raw, "keywords", "") or "",
                "creationDate": getattr(raw, "creation_date", "") or "",
            }
            if page_count:
                first_text = reader.pages[0].extract_text() or ""
        except Exception:  # noqa: BLE001
            return {"page_count": 0, "first_text": ""}
    return {"meta": meta, "page_count": page_count, "first_text": first_text}


@router.post("/studies/extract-pdf-metadata")
async def extract_pdf_metadata(file: UploadFile = File(...)):
    """Suggest bibliographic fields + study/book type from an uploaded PDF."""
    data = await file.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise HTTPException(413, "PDF-ul depășește limita de 150 MB.")
    if not data or not data[:5].startswith(b"%PDF-"):
        raise HTTPException(400, "Fișierul nu este un PDF valid (lipsește antetul %PDF-).")

    parsed = _read_pdf_metadata(data)
    meta = parsed.get("meta") or {}
    first_text = parsed.get("first_text") or ""
    page_count = int(parsed.get("page_count") or 0)

    title = (meta.get("title") or "").strip()
    if not title or len(title) < 6:
        title = _first_page_title(first_text)

    authors = (meta.get("author") or "").strip()
    if not authors:
        # A common first-page layout: author line right below the title.
        lines = [" ".join(l.split()).strip() for l in first_text.splitlines()]
        lines = [l for l in lines if l]
        for idx, line in enumerate(lines[:12]):
            if title and title[:40].casefold() in line.casefold():
                if idx + 1 < len(lines):
                    candidate = lines[idx + 1]
                    if 2 <= len(candidate) <= 160 and _YEAR_RE.search(candidate) is None:
                        authors = candidate
                break

    doi_match = _DOI_RE.search(first_text) or _DOI_RE.search(meta.get("subject") or "")
    doi = doi_match.group(0).rstrip(".,;") if doi_match else ""

    isbn = _find_isbn(first_text)

    year = ""
    creation = str(meta.get("creationDate") or "")
    creation_match = re.search(r"(19|20)\d{2}", creation)
    if creation_match:
        year = creation_match.group(0)
    elif first_text:
        year_match = _YEAR_RE.search(first_text[:4000])
        if year_match:
            year = year_match.group(0)

    # Books are the ones with an ISBN, or long/no-DOI documents. Articles
    # almost always have a DOI and stay short.
    if isbn or ((page_count >= 60 or len(first_text) > 12000) and not doi):
        study_type = "book"
    else:
        study_type = "peer-reviewed paper"

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "isbn": isbn,
        "study_type": study_type,
        "page_count": page_count,
        "has_text_layer": bool(first_text.strip()),
        "source": "pdf_metadata" if meta.get("title") or meta.get("author") else "first_page",
    }
