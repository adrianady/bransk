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
from app.studies_store import pdf_path_for

logger = logging.getLogger("chronokg.maintenance")
router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REANALYSIS_START = "<!-- reanalysis -->"
_REANALYSIS_END = "<!-- /reanalysis -->"

_REANALYZE_PROMPT = """You are a research librarian re-reading a downloaded study PDF. From the text excerpt below, produce structured metadata.
Return ONLY JSON with this exact shape:
{
  "readable": true,
  "title": "exact study title",
  "authors": "author names",
  "year": 2016,
  "study_type": "peer-reviewed paper",
  "design": "RCT | cohort | review | ...",
  "effect_direction": "positive | negative | mixed | null | unknown",
  "key_findings": ["finding 1", "finding 2"],
  "quality": 4,
  "summary": "2-3 sentence summary"
}
If the excerpt contains no readable text (scanned PDF without text layer), return {"readable": false}.
REANALYZE marker: REANALYZE"""


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _clean_tags(tags: str) -> str:
    seen, out = set(), []
    for part in re.split(r"[|,;]", tags or ""):
        part = part.strip().strip('"').strip()
        if part and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return ", ".join(out)[:500]


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
            _, _, reply = await llm_complete(
                [
                    {"role": "system", "content": _REANALYZE_PROMPT},
                    {"role": "user", "content": f"PDF TEXT EXCERPT:\n{text[:8000]}"},
                ],
                temperature=0.2,
                json_mode=True,
            )
            data = _extract_json(reply)

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
            "evidence_removed": 0,
            "theories_updated": 0,
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
                if pdf_path_for(entry["id"]):
                    report["pdfs_found"] += 1
                    yield _sse({"type": "progress", "phase": "pdfs", "message": f"Re-analyzing PDF for: {entry['title'][:60]}"})
                    await _reanalyze_pdf(entry, report)

            if report["pdfs_found"] == 0:
                report["warnings"].append("No downloaded PDFs found in the studies folder.")
            if report["undated_entries"] > 0:
                report["warnings"].append(f"{report['undated_entries']} entries have no date and stay off the timeline.")

            yield _sse({"type": "done", "report": report})
        except Exception as exc:
            logger.error("Maintenance analysis failed: %s", exc)
            yield _sse({"type": "error", "content": f"Re-analysis failed: {type(exc).__name__}: {exc}"})
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
