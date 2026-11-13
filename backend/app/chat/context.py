"""Chat context building - retrieves relevant KB entries for the model."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import List, Dict, Any

from app.db import get_db
from app.embeddings import rerank
from app.routers.search import _sanitize_fts5

logger = logging.getLogger("chronokg.chat.context")

# How much of the window the knowledge base may spend on context for one chat message.
CONTEXT_WINDOW_SHARE = 0.10

# Even inside the character budget, sending the whole library on every message
# is wasteful: a question about meditation does not need all 137 entries. The
# re-ranking puts the best matches first, so a cap keeps the relevant ones and
# drops the tail. This is what actually controls the cost per message.
MAX_CONTEXT_ENTRIES = 25


def _context_char_budget() -> int:
    """How many characters of entry text the model can take."""
    from app.llm import active_context_window

    try:
        window = active_context_window()
    except Exception:  # noqa: BLE001 - never break a chat over a budget guess
        window = 8192
    return max(4_000, int(window * CONTEXT_WINDOW_SHARE) * 3)


def _build_context(message: str) -> List[Dict[str, Any]]:
    """Fetch relevant entries from the KB, filling the model's real window.

    Candidates come from FTS5 ranking, a keyword fallback and a recency fill,
    then are re-ranked by semantic similarity when embeddings are available.
    They are taken in that order until the character budget is spent, so a
    large window gets many entries with their full text instead of ten short
    excerpts. Offline this degrades to FTS5 order, as before.
    """
    budget = _context_char_budget()
    conn = get_db()
    try:
        candidates, seen = [], set()

        # Personal notes are the user's own writing, so the table is small:
        # one load up front beats one query per candidate row (the old N+1).
        notes_by_entry: Dict[int, List[Dict]] = {}
        try:
            all_notes = conn.execute(
                "SELECT id, entry_id, course_context, content, page_number, created_at FROM study_notes ORDER BY created_at DESC"
            ).fetchall()
            for n in all_notes:
                notes_by_entry.setdefault(n["entry_id"], []).append(n)
        except Exception:
            notes_by_entry = {}

        # Reader highlights: the passages the user marked in the PDFs. They
        # are the strongest relevance signal the chat gets about a study.
        highlights_by_entry: Dict[int, List[Dict]] = {}
        try:
            for h in conn.execute(
                "SELECT id, entry_id, page_number, quote, note FROM pdf_highlights ORDER BY COALESCE(page_number, 0), id"
            ).fetchall():
                highlights_by_entry.setdefault(h["entry_id"], []).append(h)
        except Exception:
            highlights_by_entry = {}

        def add(row, room: int) -> int:
            """Add one row, cutting its text only to what room is left."""
            if row["id"] in seen or room <= 0:
                return room
            seen.add(row["id"])
            text = row["content"] or ""

            # Attach this study's personal user notes, if any.
            note_rows = notes_by_entry.get(row["id"]) or []
            if note_rows:
                note_lines = []
                for n in note_rows:
                    ctx = f"Curs/Context: '{n['course_context']}'" if n["course_context"] else "General"
                    pg = f", Pagina {n['page_number']}" if n["page_number"] else ""
                    dt = f", data: {str(n['created_at'])[:10]}" if n["created_at"] else ""
                    note_lines.append(f"  • [Notiță personală luată de tine | {ctx}{pg}{dt}]: \"{n['content']}\"")
                notes_block = "\n\n[NOTIȚE SCRISE DE TINE (UTILIZATORUL) LA ACEST STUDIU]:\n" + "\n".join(note_lines)
                text = text + notes_block

            # Attach the passages the user highlighted in the PDF reader.
            hl_rows = highlights_by_entry.get(row["id"]) or []
            if hl_rows:
                hl_lines = []
                for h in hl_rows:
                    pg = f"pagina {h['page_number']}" if h["page_number"] else "pagină necunoscută"
                    note = f" — notiță: {h['note']}" if h["note"] else ""
                    hl_lines.append(f"  • [Evidențiat de tine în PDF | {pg}]: \"{h['quote']}\"{note}")
                hl_block = "\n\n[EVIDENȚIERI MARCATE DE TINE (UTILIZATORUL) ÎN PDF-UL ACESTUI STUDIU]:\n" + "\n".join(hl_lines)
                text = text + hl_block

            keep = max(0, min(len(text), room))
            candidates.append({
                "id": row["id"],
                "title": row["title"],
                "content": text[:keep],
                "date": row["entry_date"] or "unknown",
                "tags": row["tags"],
                "importance": row["importance"],
            })
            return room - (len(row["title"] or "") + keep + 40)  # 40 ≈ tags + framing

        # Check if the user query explicitly refers to personal notes or courses
        is_notes_query = bool(re.search(r"\b(noti[tț][aăe]|notat|curs|cursul|seminar|ce am scris|ce am zis)\b", message, re.IGNORECASE))
        if is_notes_query:
            try:
                note_matches = conn.execute(
                    """SELECT e.id, e.title, e.content, e.entry_date, e.tags, e.importance
                       FROM study_notes n
                       JOIN entries e ON n.entry_id = e.id
                       WHERE e.archived = 0
                       ORDER BY n.updated_at DESC
                       LIMIT 10"""
                ).fetchall()
                for nr in note_matches:
                    budget = add(nr, budget)
            except Exception:
                pass

        # Primary retrieval: FTS5 ranked search. No LIMIT: the budget governs.
        try:
            safe = _sanitize_fts5(message)
            rows = conn.execute(
                """SELECT e.id, e.title, e.content, e.entry_date, e.tags, e.importance
                   FROM entries_fts JOIN entries e ON entries_fts.rowid = e.id
                   WHERE e.archived = 0 AND entries_fts MATCH ?
                   ORDER BY rank""",
                [safe],
            ).fetchall()
            for r in rows:
                budget = add(r, budget)

        except Exception:
            rows = []

        # Fallback / fill: keyword LIKE on significant words
        if not candidates:
            words = [w.lower() for w in re.split(r"\W+", message) if len(w) > 3][:5]
            for word in words:
                for row in conn.execute(
                    "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE archived = 0 AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)",
                    [f"%{word}%", f"%{word}%", f"%{word}%"]
                ).fetchall():
                    budget = add(row, budget)

        # Fill with the most important entries so context never starves
        if budget > 0:
            for r in conn.execute(
                "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE archived = 0 ORDER BY importance DESC, updated_at DESC"
            ).fetchall():
                budget = add(r, budget)
                if budget <= 0:
                    break

        # Re-rank the whole set: the semantic order decides which entries earn
        # a place in the prompt, and the tail is dropped to keep the cost sane.
        return rerank(message, candidates)[:MAX_CONTEXT_ENTRIES]
    finally:
        conn.close()


def _search_kb_rows(conn, query: str, limit: int = 5) -> list[dict]:
    """Search entries by FTS5 with a token-wise LIKE fallback.

    The old implementation wrapped the whole query in one LIKE (`%sleep and
    memory%`), so a natural-language query returned nothing even when both
    words existed in the entry. FTS5 ranking handles multi-word queries; the
    token fallback covers partial words and queries FTS tokenization misses.
    """
    q = (query or "").strip()
    if not q:
        return []
    rows: list[dict] = []
    seen: set[int] = set()

    def add(row) -> None:
        entry_id = row["id"]
        if entry_id in seen or len(rows) >= limit:
            return
        seen.add(entry_id)
        rows.append(dict(row))

    select = (
        "SELECT e.id, e.title, e.entry_date, e.tags, e.importance, "
        "substr(e.content, 1, 600) AS snippet, e.doi, e.study_type, e.pdf_path"
    )
    try:
        safe = _sanitize_fts5(q)
        for row in conn.execute(
            f"""{select}
                FROM entries_fts JOIN entries e ON entries_fts.rowid = e.id
                WHERE e.archived = 0 AND entries_fts MATCH ?
                ORDER BY rank LIMIT ?""",
            [safe, limit],
        ).fetchall():
            add(row)
    except sqlite3.OperationalError:
        pass

    if len(rows) < limit:
        # Exact phrase substring: finds names/citations FTS tokenization skips.
        for row in conn.execute(
            f"""{select} FROM entries e
                WHERE e.archived = 0 AND (e.title LIKE ? OR e.content LIKE ? OR e.tags LIKE ?)
                ORDER BY e.importance DESC, e.updated_at DESC LIMIT ?""",
            [f"%{q}%", f"%{q}%", f"%{q}%", limit],
        ).fetchall():
            add(row)

    if len(rows) < limit:
        tokens = [t for t in re.split(r"\W+", q) if len(t) > 2][:6]
        for token in tokens:
            for row in conn.execute(
                f"""{select} FROM entries e
                    WHERE e.archived = 0
                      AND (e.title LIKE ? OR e.content LIKE ? OR e.tags LIKE ?)
                    ORDER BY e.importance DESC, e.updated_at DESC LIMIT ?""",
                [f"%{token}%", f"%{token}%", f"%{token}%", limit],
            ).fetchall():
                add(row)
            if len(rows) >= limit:
                break
    return rows


def _timeline_rows(conn, from_date: str, to_date: str, keyword: str, limit: int = 10) -> list[dict]:
    """Entries inside a date range, matching any significant keyword token."""
    where = ["archived = 0"]
    params: list = []
    if from_date:
        where.append("entry_date >= ?")
        params.append(from_date)
    if to_date:
        where.append("entry_date <= ?")
        params.append(to_date)
    tokens = [t for t in re.split(r"\W+", keyword or "") if len(t) > 2][:6]
    if tokens:
        where.append(
            "(" + " OR ".join(["title LIKE ? OR content LIKE ? OR tags LIKE ?"] * len(tokens)) + ")"
        )
        for token in tokens:
            params.extend([f"%{token}%", f"%{token}%", f"%{token}%"])
    params.append(limit)
    rows = conn.execute(
        "SELECT id, title, entry_date, tags, importance, substr(content, 1, 400) AS snippet "
        f"FROM entries WHERE {' AND '.join(where)} ORDER BY entry_date LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def _search_pdf_chunks_like(query: str, limit: int = 5) -> list[dict]:
    """Keyword fallback for SEARCH_DOCUMENTS when embeddings are unavailable.

    The semantic search is intentionally empty offline (its contract), but the
    indexed chunk text is still in SQLite. Returning nothing made the chat
    answer "no PDF matches" even though the passage was right there.
    """
    q = (query or "").strip()
    if not q:
        return []
    tokens = [t for t in re.split(r"\W+", q) if len(t) > 2][:6] or [q]
    where = " OR ".join(["d.content LIKE ?"] * len(tokens))
    conn = get_db()
    try:
        rows = conn.execute(
            f"""SELECT d.id, d.entry_id, d.chunk_index, d.page_number,
                       substr(d.content, 1, 1500) AS content,
                       e.title AS entry_title
                  FROM documents d JOIN entries e ON e.id = d.entry_id
                 WHERE e.archived = 0 AND ({where})
                 ORDER BY d.id LIMIT ?""",
            [*[f"%{token}%" for token in tokens], limit],
        ).fetchall()
        return [{**dict(row), "score": 0.0, "mode": "keyword"} for row in rows]
    finally:
        conn.close()