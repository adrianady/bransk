"""
Data integrity & re-analysis mode (Review tab).

Scans and repairs the whole knowledge base so the graphs, the chronological
timeline and the theory tree stay correct:
  1. Normalize every entry (titles, dates, tags, sources)
  2. Clean invalid relationships and rebuild the FTS index
  3. Re-read every downloaded study PDF and re-analyze it with the LLM
  4. Recompute theory statuses from pro/contra evidence and remove orphans
  5. Report chronology/graph health

Runs as SSE so the UI can show live progress.
"""
import json
import logging
import re
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.db import get_db, rebuild_fts
from app.llm import LLM_PROVIDER, complete as llm_complete
from app.studies_store import is_valid_pdf_file, pdf_path_for
from app.pdf_rag import index_pdf
from app.study_dedup import same_study
from app.tagging import normalize_tags, tag_names

logger = logging.getLogger("chronokg.maintenance")
router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REANALYSIS_START = "<!-- reanalysis -->"
_REANALYSIS_END = "<!-- /reanalysis -->"

_REANALYZE_PROMPT = """You are a senior scientific bibliographer and research librarian re-evaluating an empirical study PDF.
From the excerpt below, extract structured metadata with rigorous academic precision.
Return ONLY JSON with this exact shape:
{
  "readable": true,
  "title": "exact formal study title",
  "authors": "author names (e.g. Lead Author et al. or comma-separated)",
  "year": 2016,
  "study_type": "peer-reviewed paper | preprint | systematic review | meta-analysis | book chapter",
  "design": "RCT | cohort | cross-sectional | case-control | systematic review | meta-analysis | qualitative | other",
  "effect_direction": "positive | negative | mixed | null | unknown",
  "key_findings": ["finding 1 with quantitative effect/mechanism", "finding 2"],
  "quality": 4,
  "summary": "2-3 sentence rigorous academic summary (sample size N, methodology/intervention, and primary causal or empirical conclusion)"
}
Rules:
- Quality score (1-5) must follow the hierarchy of evidence: 5 = multi-center blinded RCT or definitive meta-analysis, 4 = controlled experimental trial or prospective cohort, 3 = observational / cross-sectional, 2 = preliminary / underpowered, 1 = opinion / descriptive.
- In 'key_findings' and 'summary', prioritize reporting concrete effect sizes (Cohen's d, OR, r) and specific biological/cognitive/experimental mechanisms whenever present.
- If the excerpt contains no readable text (e.g., scanned PDF without OCR/text layer), return {"readable": false}.
REANALYZE marker: REANALYZE"""


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _merge_duplicate_studies(conn, report: dict) -> None:
    """Fuse study entries that describe the same paper into one row.

    Duplicates were created before `app.study_dedup` existed: the research
    pipeline compared normalized titles for equality, so a title the model cut
    short ("... and the brain: A systematic review of ...") landed as a second
    row pointing at the same article.

    Nothing is lost: evidence, relationships, todos, reminders and the review
    schedule of the dropped row are moved onto the one that stays, and any field
    it was missing (doi, source, pdf_path, quality, design...) is filled in from
    it first. The row that stays is the oldest, i.e. the one whose id the rest
    of the app already refers to.
    """
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT id, title, source, doi, pdf_path, authors, design, effect_direction,
                      quality, entry_date, tags, content, study_type, importance,
                      url_validated, created_at
               FROM entries
               WHERE archived = 0
                 AND (study_type != '' OR tags LIKE '%study%' OR tags LIKE '%pdf%'
                      OR (pdf_path IS NOT NULL AND pdf_path != ''))
               ORDER BY id"""
        ).fetchall()
    ]

    merged = dropped = 0
    kept_ids: set[int] = set()

    for i, first in enumerate(rows):
        if first["id"] in kept_ids:
            continue
        group = [first]
        for other in rows[i + 1:]:
            if other["id"] in kept_ids:
                continue
            if same_study(first["title"], first["source"], other["title"], other["source"]):
                group.append(other)
        if len(group) < 2:
            continue

        keep = group[0]
        kept_ids.add(keep["id"])
        for dup in group[1:]:
            # Move the links first: they point at the row that disappears.
            for table, column in (
                ("theory_evidence", "entry_id"),
                ("relationships", "from_id"),
                ("relationships", "to_id"),
                ("todos", "entry_id"),
                ("reminders", "entry_id"),
                ("spaced_repetition", "entry_id"),
                ("documents", "entry_id"),
            ):
                if table in ("todos", "spaced_repetition"):
                    # both tables allow a single row per entry
                    if conn.execute(
                        f"SELECT 1 FROM {table} WHERE {column} = ?", [keep["id"]]
                    ).fetchone():
                        conn.execute(f"DELETE FROM {table} WHERE {column} = ?", [dup["id"]])
                        continue
                elif table == "theory_evidence":
                    # UNIQUE(theory_id, entry_id, stance): drop what would clash
                    conn.execute(
                        """DELETE FROM theory_evidence
                           WHERE entry_id = ?
                             AND (theory_id, stance) IN (
                                 SELECT theory_id, stance FROM theory_evidence WHERE entry_id = ?)""",
                        [dup["id"], keep["id"]],
                    )
                elif table == "relationships":
                    # UNIQUE(from_id, to_id, label): a move can collide from
                    # either end, so discard the duplicate rows first.
                    other_col = "to_id" if column == "from_id" else "from_id"
                    conn.execute(
                        f"""DELETE FROM relationships
                            WHERE {column} = ?
                              AND ({other_col}, label) IN (
                                  SELECT {other_col}, label FROM relationships WHERE {column} = ?)""",
                        [dup["id"], keep["id"]],
                    )
                conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                    [keep["id"], dup["id"]],
                )

            # Then fill whatever the surviving row was missing.
            fields = {}
            for field in ("doi", "doi_source", "pdf_path", "authors", "design",
                          "effect_direction", "entry_date", "bib_title", "bib_authors",
                          "bib_year", "doi_checked_at"):
                value = dup.get(field)
                if value and not keep.get(field):
                    fields[field] = value
                    keep[field] = value
            if dup.get("quality") and not keep.get("quality"):
                fields["quality"] = dup["quality"]
                keep["quality"] = dup["quality"]
            if dup.get("url_validated") and not keep.get("url_validated"):
                fields["url_validated"] = 1
            if dup.get("source") and not keep.get("source"):
                fields["source"] = dup["source"]
                keep["source"] = dup["source"]
            # Re-analysis sections and findings are worth keeping together.
            extra = (dup.get("content") or "").strip()
            if extra and extra not in (keep.get("content") or ""):
                fields["content"] = ((keep.get("content") or "").rstrip()
                                     + "\n\n" + extra)[:20000]
                keep["content"] = fields["content"]
            if fields:
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE entries SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                    [*fields.values(), keep["id"]],
                )

            conn.execute("DELETE FROM entries WHERE id = ?", [dup["id"]])
            dropped += 1
        merged += 1

    report["duplicates_merged"] = dropped
    report["duplicate_groups"] = merged


def _clean_tags(tags: str) -> str:
    # One implementation with the write paths and the repair script, so a tag
    # accepted by /entries can never be "repaired" differently here.
    return normalize_tags(tags, sep=", ")


def _norm_updates(entry: dict) -> dict:
    """Return field updates that fix a malformed entry (empty = nothing to fix)."""
    updates = {}
    title = (entry["title"] or "").strip()
    if title != entry["title"]:
        updates["title"] = title
    raw_date = (entry["entry_date"] or "").strip()
    if raw_date and not _DATE_RE.match(raw_date):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", raw_date)
        if not m:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", entry["tags"] or "")
        if m:
            updates["entry_date"] = m.group(1)
        else:
            year = re.search(r"(\d{4})", raw_date)
            updates["entry_date"] = f"{year.group(1)}-01-01" if year else None
    tags = _clean_tags(entry["tags"])
    if tags != (entry["tags"] or "").strip():
        updates["tags"] = tags
    source = (entry["source"] or "").strip()
    if source != entry["source"]:
        updates["source"] = source
    return updates


def _extract_pdf_text(path, max_chars: int = 9000) -> str:
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        try:
            return " ".join(page.get_text() for page in doc)[:max_chars]
        finally:
            doc.close()
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return " ".join((page.extract_text() or "") for page in reader.pages)[:max_chars]
    except Exception:
        return ""


def _upsert_reanalysis_section(content: str, section_md: str) -> str:
    if _REANALYSIS_START in content:
        head = content.split(_REANALYSIS_START, 1)[0].rstrip()
        return head + "\n\n" + section_md + "\n"
    return (content.rstrip() + "\n\n" + section_md + "\n") if content.strip() else section_md


def _extract_json(reply: str) -> dict:
    text = reply.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON in LLM reply")
    return json.loads(text[start : end + 1])


def _mock_reanalysis(title: str) -> dict:
    return {
        "readable": True,
        "title": title,
        "authors": "Unknown",
        "year": None,
        "study_type": "study",
        "design": "",
        "effect_direction": "unknown",
        "key_findings": ["Re-read from downloaded PDF (offline mock re-analysis)."],
        "quality": 3,
        "summary": "Offline mock re-analysis of the downloaded PDF.",
    }


def _reanalysis_markdown(data: dict) -> str:
    lines = [_REANALYSIS_START, "### 🔬 AI re-analysis (from PDF)", ""]
    meta = []
    if data.get("title"):
        meta.append(f"**Title:** {data['title']}")
    if data.get("authors"):
        meta.append(f"**Authors:** {data['authors']}")
    if data.get("year"):
        meta.append(f"**Year:** {data['year']}")
    if data.get("study_type"):
        meta.append(f"**Type:** {data['study_type']}")
    if data.get("design"):
        meta.append(f"**Design:** {data['design']}")
    if data.get("effect_direction") and data["effect_direction"] != "unknown":
        meta.append(f"**Effect:** {data['effect_direction']}")
    if data.get("quality"):
        meta.append(f"**Quality:** {data['quality']}/5")
    if meta:
        lines.append(" | ".join(meta))
        lines.append("")
    if data.get("summary"):
        lines += [data["summary"], ""]
    if data.get("key_findings"):
        lines += ["**Key findings:**", ""]
        lines += [f"- {f}" for f in data["key_findings"][:8]]
        lines.append("")
    lines.append(_REANALYSIS_END)
    return "\n".join(lines)


async def _reanalyze_pdf(entry: dict, report: dict) -> None:
    path = pdf_path_for(entry["id"])
    if not path:
        return
    text = _extract_pdf_text(path)
    if not text.strip():
        report["pdfs_unreadable"] += 1
        return

    try:
        if LLM_PROVIDER == "mock":
            data = _mock_reanalysis(entry["title"])
        else:
            data = None
            for attempt in range(2):
                try:
                    _, _, reply = await llm_complete(
                        [
                            {"role": "system", "content": _REANALYZE_PROMPT},
                            {"role": "user", "content": f"PDF TEXT EXCERPT:\n{text[:8000]}"},
                        ],
                        temperature=0.2 if attempt == 0 else 0.0,
                        json_mode=True,
                    )
                    data = _extract_json(reply)
                    break
                except Exception:
                    if attempt == 0:
                        continue
                    raise

        if not data.get("readable", True):
            report["pdfs_unreadable"] += 1
            return

        conn = get_db()
        try:
            updates = {}
            for field, key in (("authors", "authors"), ("study_type", "study_type"), ("design", "design")):
                val = (data.get(key) or "").strip()
                if val and not (entry.get(field) or "").strip():
                    updates[field] = val
            if data.get("year"):
                year_date = f"{int(data['year'])}-01-01"
                if not entry.get("entry_date"):
                    updates["entry_date"] = year_date
            direction = str(data.get("effect_direction") or "").lower()
            if direction in ("positive", "negative", "mixed", "null") and not entry.get("effect_direction"):
                updates["effect_direction"] = direction
            if data.get("quality") and not entry.get("quality"):
                updates["quality"] = max(1, min(5, int(data["quality"])))

            content = _upsert_reanalysis_section(entry.get("content") or "", _reanalysis_markdown(data))
            updates["content"] = content
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE entries SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                    [*updates.values(), entry["id"]],
                )
                conn.commit()
            report["pdfs_reanalyzed"] += 1
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("PDF re-analysis failed for entry %s: %s", entry["id"], exc)
        report["pdf_errors"] += 1


@router.post("/maintenance/tags")
def normalize_tag_vocabulary(dry_run: bool = True):
    """Repair the tag vocabulary only — no PDF re-reading, no model call.

    The full re-analysis (`/maintenance/analyze`) also fixes tags, but it walks
    every PDF and can take minutes. This is the surgical version: drop the
    dates and stop words, de-duplicate, and fill the `tags` table from the
    column that is actually used, so the two views stop disagreeing.

    Default is a dry run; pass `?dry_run=false` to write.
    """
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, title, tags FROM entries ORDER BY id").fetchall()
        changes: list[tuple[int, str, str, str]] = []
        wanted: set[str] = set()
        for row in rows:
            old = row["tags"] or ""
            new = normalize_tags(old)
            wanted.update(tag_names(new))
            if new != old.strip():
                changes.append((row["id"], row["title"] or "", old, new))

        existing = {r["name"] for r in conn.execute("SELECT name FROM tags").fetchall()}
        missing = sorted(wanted - existing)

        inserted = 0
        if not dry_run:
            for entry_id, _title, _old, new in changes:
                conn.execute(
                    "UPDATE entries SET tags = ?, updated_at = datetime('now') WHERE id = ?",
                    [new, entry_id],
                )
            for name in missing:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO tags (name) VALUES (?)",
                    [name],
                )
                inserted += max(cur.rowcount or 0, 0)
            conn.commit()

        return {
            "dry_run": dry_run,
            "entries_scanned": len(rows),
            "entries_changed": len(changes),
            "tags_missing_from_table": len(missing),
            "tags_inserted": 0 if dry_run else inserted,
            "samples": [
                {"id": entry_id, "title": title[:80], "before": old[:160], "after": new[:160]}
                for entry_id, title, old, new in changes[:10]
            ],
        }
    finally:
        conn.close()


@router.post("/maintenance/analyze")
async def maintenance_analyze():
    """Full re-analysis: normalize data, rebuild index, re-read PDFs, recompute theories."""

    async def generate():
        report = {
            "entries_scanned": 0,
            "entries_fixed": 0,
            "dates_fixed": 0,
            "tags_fixed": 0,
            "studies_classified": 0,
            "sources_filled": 0,
            "fts_rebuilt": True,
            "relationships_removed": 0,
            "self_links_removed": 0,
            "pdfs_found": 0,
            "pdfs_reanalyzed": 0,
            "pdfs_unreadable": 0,
            "pdf_errors": 0,
            "pdfs_indexed": 0,
            "evidence_removed": 0,
            "theories_updated": 0,
            "duplicates_merged": 0,
            "duplicate_groups": 0,
            "undated_entries": 0,
            "entries_active": 0,
            "relationships_count": 0,
            "theories_count": 0,
            "warnings": [],
        }
        try:
            yield _sse({"type": "progress", "phase": "entries", "message": "Scanning and normalizing entries..."})
            conn = get_db()
            try:
                for row in conn.execute("SELECT * FROM entries").fetchall():
                    entry = dict(row)
                    report["entries_scanned"] += 1
                    if not entry["archived"]:
                        report["entries_active"] += 1
                    if not entry["entry_date"]:
                        report["undated_entries"] += 1
                    updates = _norm_updates(entry)
                    if updates:
                        set_clause = ", ".join(f"{k} = ?" for k in updates)
                        conn.execute(
                            f"UPDATE entries SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                            [*updates.values(), entry["id"]],
                        )
                        report["entries_fixed"] += 1
                        if "entry_date" in updates:
                            report["dates_fixed"] += 1
                        if "tags" in updates:
                            report["tags_fixed"] += 1

                # Study classification: entries with study/pdf tags or PDF links
                # become studies even when the AI forgot the study_type field.
                cur = conn.execute(
                    """UPDATE entries SET study_type = 'study', updated_at = datetime('now')
                       WHERE study_type = '' AND archived = 0
                         AND (tags LIKE '%study%' OR tags LIKE '%pdf%' OR content LIKE '%**PDF:**%')"""
                )
                report["studies_classified"] = cur.rowcount

                # Salvage PDF links into source for classified studies.
                for row in conn.execute(
                    "SELECT id, content FROM entries WHERE source = '' AND content LIKE '%**PDF:**%'"
                ).fetchall():
                    match = re.search(r"https?://\S+\.pdf\S*", row["content"] or "")
                    if match:
                        conn.execute("UPDATE entries SET source = ? WHERE id = ?", [match.group(0), row["id"]])
                        report["sources_filled"] += 1

                # Relationship hygiene
                cur = conn.execute(
                    "DELETE FROM relationships WHERE from_id NOT IN (SELECT id FROM entries) OR to_id NOT IN (SELECT id FROM entries)"
                )
                report["relationships_removed"] = cur.rowcount
                cur = conn.execute("DELETE FROM relationships WHERE from_id = to_id")
                report["self_links_removed"] = cur.rowcount

                # Evidence hygiene
                cur = conn.execute("DELETE FROM theory_evidence WHERE entry_id NOT IN (SELECT id FROM entries)")
                report["evidence_removed"] = cur.rowcount

                # Duplicate studies: same paper inserted twice by an older run.
                _merge_duplicate_studies(conn, report)

                report["relationships_count"] = conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
                report["theories_count"] = conn.execute("SELECT COUNT(*) FROM theories").fetchone()[0]

                # Theory status from evidence balance
                for t in conn.execute("SELECT id, status FROM theories").fetchall():
                    counts = conn.execute(
                        """SELECT
                               SUM(CASE WHEN stance = 'pro' THEN 1 ELSE 0 END) AS pro,
                               SUM(CASE WHEN stance = 'contra' THEN 1 ELSE 0 END) AS contra
                           FROM theory_evidence WHERE theory_id = ?""",
                        [t["id"]],
                    ).fetchone()
                    pro = counts["pro"] or 0
                    contra = counts["contra"] or 0
                    if pro and contra:
                        new_status = "contested"
                    elif pro and not contra:
                        new_status = "supported"
                    elif contra and not pro:
                        new_status = "refuted"
                    else:
                        new_status = "emerging"
                    if new_status != t["status"]:
                        conn.execute(
                            "UPDATE theories SET status = ?, updated_at = datetime('now') WHERE id = ?",
                            [new_status, t["id"]],
                        )
                        report["theories_updated"] += 1
                conn.commit()
            finally:
                conn.close()

            yield _sse({"type": "progress", "phase": "fts", "message": "Rebuilding the full-text search index..."})
            rebuild_fts()

            yield _sse({"type": "progress", "phase": "pdfs", "message": "Re-reading downloaded study PDFs..."})
            conn = get_db()
            try:
                studies = [
                    dict(r)
                    for r in conn.execute("SELECT * FROM entries WHERE study_type != '' AND archived = 0").fetchall()
                ]
            finally:
                conn.close()
            for entry in studies:
                path = pdf_path_for(entry["id"])
                if path:
                    report["pdfs_found"] += 1
                    if not is_valid_pdf_file(path):
                        # Blocked/redirected downloads sometimes leave an HTML
                        # page named .pdf; it cannot be read or indexed. The
                        # downloader now refuses such files, so re-downloading
                        # replaces them.
                        report["pdfs_unreadable"] += 1
                        report["pdf_errors"] += 1
                        continue
                    yield _sse({"type": "progress", "phase": "pdfs", "message": f"Re-analyzing PDF for: {entry['title'][:60]}"})
                    await _reanalyze_pdf(entry, report)
                    indexed = await index_pdf(entry["id"], path)
                    if indexed:
                        report["pdfs_indexed"] += indexed

            if report["pdfs_found"] == 0:
                report["warnings"].append("No downloaded PDFs found in the studies folder.")
            elif report["pdfs_unreadable"] > 0:
                report["warnings"].append(
                    f"{report['pdfs_unreadable']} stored PDF(s) are not valid PDF files "
                    "(blocked or error-page downloads) — use Download all PDFs to retry them."
                )
            if report["undated_entries"] > 0:
                report["warnings"].append(f"{report['undated_entries']} entries have no date and stay off the timeline.")

            yield _sse({"type": "done", "report": report})
        except Exception as exc:
            logger.error("Maintenance analysis failed: %s", exc)
            yield _sse({"type": "error", "content": f"Re-analysis failed: {type(exc).__name__}: {exc}"})
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
