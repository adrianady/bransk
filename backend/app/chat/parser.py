"""Chat action parser - extracts and validates structured action tags from AI replies."""
from __future__ import annotations

import logging
import re
from typing import List, Dict, Any, Optional, Set

from app.nlp import (
    CREATE_ENTRY_STRICT_RE,
    FENCED_CODE_RE,
    INLINE_CODE_RE,
    TASK_SCREEN_RE,
)
from app.chat.handlers.write_actions import _TAG_FORMATS

logger = logging.getLogger("chronokg.chat.parser")


# ──────────────────────────────────────────────────────────────────────────────
# Action tag extraction
# ──────────────────────────────────────────────────────────────────────────────

def _extract_line_tags(reply: str, tag: str) -> List[str]:
    """Extract the payload of `[TAG: ...]` when the tag owns its whole line.

    Tags must be the only content of a line (leading whitespace allowed) and
    the payload runs to the LAST ']' on that line, so queries containing
    brackets (e.g. "brain [regions] and memory") parse intact. This also
    stops tags echoed inside prose (e.g. 'You asked: [ARCHIVE_ENTRY: 1]')
    from executing.
    """
    out = []
    for line in reply.splitlines():
        stripped = line.strip()
        m = re.match(rf"\[{re.escape(tag)}:\s*(.*)\]", stripped, re.DOTALL)
        if m:
            out.append(m.group(1).strip())
    return out


def _parse_actions(reply: str) -> List[Dict[str, Any]]:
    """Parse AI actions. Malformed input degrades to error actions, never crashes."""
    try:
        # Strip markdown code before parsing: examples/echoes inside fences or
        # backticks are instructional text, never real tool calls.
        cleaned = FENCED_CODE_RE.sub(" ", reply or "")
        cleaned = INLINE_CODE_RE.sub(" ", cleaned)
        return _parse_actions_inner(cleaned)
    except Exception as exc:
        logger.warning("Action parsing failed: %s", exc)
        return [{"type": "error", "detail": f"Could not parse AI actions: {type(exc).__name__}"}]


# ──────────────────────────────────────────────────────────────────────────────
# Inner parser with all action handlers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_actions_inner(reply: str) -> List[Dict[str, Any]]:
    """Parse and execute structured actions from AI reply."""
    from app.db import get_db
    from app.tagging import normalize_tags, sync_tags
    from app.study_dedup import titles_match
    from app.study_import import (
        find_duplicate_study,
        insert_study_entry,
        resolve_identifier_metadata,
    )
    from app.routers.relationships import RELATION_KINDS, parse_relation_label
    from app.routers.spaced_repetition import _sm2_calc

    actions = []
    seen: Set[tuple] = set()

    def to_int(value) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def first_time(*key_parts) -> bool:
        """Dedupe: only the first occurrence of an identical action runs."""
        key = tuple(key_parts)
        if key in seen:
            return False
        seen.add(key)
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # CREATE_ENTRY
    # ─────────────────────────────────────────────────────────────────────────
    for m in CREATE_ENTRY_STRICT_RE.finditer(reply):
        if not first_time("CREATE_ENTRY", m.group(0).strip()):
            continue
        title, content, importance = m.group(1).strip()[:500], m.group(2).strip()[:5000], max(1, min(5, int(m.group(5))))
        tags = normalize_tags(m.group(4).strip())[:500]
        entry_date, date_problem = _iso_entry_date(m.group(3).strip())
        if date_problem:
            actions.append({"type": "error", "detail": date_problem})
            continue
        conn = None
        try:
            conn = get_db()
            cur = conn.execute("INSERT INTO entries (title, content, entry_date, tags, importance) VALUES (?, ?, ?, ?, ?)", [title, content, entry_date, tags, importance])
            sync_tags(conn, tags)
            conn.commit()
            actions.append({"type": "entry_created", "id": cur.lastrowid, "title": title})
            logger.info(f"AI created entry #{cur.lastrowid}: {title}")
        except Exception as e:
            actions.append({"type": "error", "detail": "Could not save entry"})
            logger.error(f"AI entry creation failed: {e}")
        finally:
            if conn: conn.close()

    # Near-miss CREATE_ENTRY feedback
    strict_count = len(CREATE_ENTRY_STRICT_RE.findall(reply))
    attempted_count = len(re.findall(r'\[CREATE_ENTRY:', reply))
    if attempted_count > strict_count:
        actions.append({
            "type": "error",
            "detail": (
                "CREATE_ENTRY was not executed — expected exact format: "
                "[CREATE_ENTRY: Title | Content | YYYY-MM-DD | tag1,tag2 | 1-5] "
                "with a numeric importance (1-5)."
            ),
        })

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH_KB
    # ─────────────────────────────────────────────────────────────────────────
    from app.chat.context import _search_kb_rows
    for q in _extract_line_tags(reply, "SEARCH_KB"):
        if not first_time("SEARCH_KB", q):
            continue
        conn = get_db()
        try:
            results = _search_kb_rows(conn, q, limit=8)
            notes: List[Dict] = []
            tokens = [t for t in re.split(r"\W+", q) if len(t) > 2][:6] or [q]
            where = " OR ".join(["n.content LIKE ?"] * len(tokens))
            try:
                notes = [
                    dict(row) for row in conn.execute(
                        f"""SELECT n.id, n.entry_id, n.content, n.course_context,
                                   n.page_number, e.title AS entry_title
                              FROM study_notes n JOIN entries e ON e.id = n.entry_id
                             WHERE e.archived = 0 AND ({where})
                             ORDER BY n.created_at DESC LIMIT 5""",
                        [*[f"%{token}%" for token in tokens]],
                    ).fetchall()
                ]
            except sqlite3.Error:
                notes = []
        except Exception as exc:  # noqa: BLE001 - search must never kill the reply
            logger.warning("SEARCH_KB failed for '%s': %s", q, exc)
            actions.append({"type": "error", "detail": f"SEARCH_KB failed: {type(exc).__name__}"})
            continue
        finally:
            conn.close()
        actions.append({"type": "search_results", "query": q, "results": results, "notes": notes})

    # ─────────────────────────────────────────────────────────────────────────
    # CREATE_RELATIONSHIP
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "CREATE_RELATIONSHIP"):
        parts = [p.strip() for p in payload.split("|", 2)]
        from_id, to_id = to_int(parts[0] if parts else ""), to_int(parts[1] if len(parts) > 1 else "")
        label = parts[2] if len(parts) > 2 else ""
        if not first_time("CREATE_RELATIONSHIP", payload):
            continue
        if from_id is None or to_id is None or from_id == to_id:
            actions.append({"type": "error", "detail": "CREATE_RELATIONSHIP needs two different numeric entry ids"})
            continue
        conn = get_db()
        try:
            code, detail = parse_relation_label(label)
            stored = code if not detail else f"{code} | {detail}"
            conn.execute(
                "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
                [from_id, to_id, stored],
            )
            conn.commit()
            actions.append({
                "type": "relationship_created",
                "from": from_id, "to": to_id,
                "label": code,
                "label_display": RELATION_KINDS.get(code, RELATION_KINDS["relates_to"]),
            })
        except Exception as exc:
            actions.append({"type": "error", "detail": f"CREATE_RELATIONSHIP failed: {type(exc).__name__}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH_TIMELINE
    # ─────────────────────────────────────────────────────────────────────────
    from app.chat.context import _timeline_rows
    for payload in _extract_line_tags(reply, "SEARCH_TIMELINE"):
        parts = [p.strip() for p in payload.split("|", 2)]
        fd, td, kw = (parts + ["", "", ""])[:3]
        if not first_time("SEARCH_TIMELINE", payload):
            continue
        conn = get_db()
        try:
            results = _timeline_rows(conn, fd, td, kw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SEARCH_TIMELINE failed for '%s': %s", payload, exc)
            actions.append({"type": "error", "detail": f"SEARCH_TIMELINE failed: {type(exc).__name__}"})
            continue
        finally:
            conn.close()
        actions.append({"type": "timeline_results", "from": fd, "to": td, "results": results})

    # ─────────────────────────────────────────────────────────────────────────
    # ADD_TODO
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "ADD_TODO"):
        parts = [p.strip() for p in payload.split("|", 2)]
        entry_id = to_int(parts[0] if parts else "")
        priority = parts[1] if len(parts) > 1 else ""
        due = parts[2] if len(parts) > 2 else ""
        if not first_time("ADD_TODO", payload):
            continue
        if entry_id is None:
            actions.append({"type": "error", "detail": "ADD_TODO needs a numeric entry id"})
            continue
        if priority not in ("critical", "important", "normal"):
            actions.append({"type": "error", "detail": f"ADD_TODO failed: invalid priority '{priority}'"})
            continue
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO todos (entry_id, priority, due_date) VALUES (?, ?, ?)", [entry_id, priority, due or None])
            conn.commit()
            actions.append({"type": "todo_added", "entry_id": entry_id, "priority": priority})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"ADD_TODO failed: {type(exc).__name__}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # SPACED_REPETITION
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "SPACED_REPETITION"):
        eid = to_int(payload)
        if not first_time("SPACED_REPETITION", payload):
            continue
        if eid is None:
            actions.append({"type": "error", "detail": "SPACED_REPETITION needs a numeric entry id"})
            continue
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO spaced_repetition (entry_id) VALUES (?)", [eid])
            conn.commit()
            actions.append({"type": "sr_scheduled", "entry_id": eid})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"SPACED_REPETITION failed: {type(exc).__name__}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH_STUDIES — scholarly database search
    # ─────────────────────────────────────────────────────────────────────────
    for q in _extract_line_tags(reply, "SEARCH_STUDIES"):
        if not first_time("SEARCH_STUDIES", q):
            continue
        actions.append({"type": "studies_search_request", "query": q})

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH_WEB — detection only (executed async by the endpoint)
    # ─────────────────────────────────────────────────────────────────────────
    for q in _extract_line_tags(reply, "SEARCH_WEB"):
        if not first_time("SEARCH_WEB", q):
            continue
        actions.append({"type": "web_search_request", "query": q})

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH_DOCUMENTS — semantic search over indexed study PDFs
    # ─────────────────────────────────────────────────────────────────────────
    for q in _extract_line_tags(reply, "SEARCH_DOCUMENTS"):
        if not first_time("SEARCH_DOCUMENTS", q):
            continue
        actions.append({"type": "documents_search_request", "query": q})

    # ─────────────────────────────────────────────────────────────────────────
    # FETCH_ENTRY — read a full entry
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "FETCH_ENTRY"):
        eid = to_int(payload)
        if not first_time("FETCH_ENTRY", payload):
            continue
        if eid is None:
            actions.append({"type": "error", "detail": "FETCH_ENTRY needs a numeric entry id"})
            continue
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT id, title, content, entry_date, tags, source, importance, study_type, authors FROM entries WHERE id = ?",
                [eid],
            ).fetchone()
            actions.append({"type": "entry_fetched", "entry_id": eid, "entry": dict(row) if row else None})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # LIST_ENTRIES — browse by tag (empty = most important/recent)
    # ─────────────────────────────────────────────────────────────────────────
    for tag in _extract_line_tags(reply, "LIST_ENTRIES"):
        if not first_time("LIST_ENTRIES", tag):
            continue
        conn = get_db()
        try:
            if tag:
                rows = conn.execute(
                    "SELECT id, title, entry_date, tags, importance FROM entries WHERE archived = 0 AND tags LIKE ? ORDER BY entry_date IS NULL, entry_date DESC LIMIT 25",
                    [f"%{tag}%"],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, entry_date, tags, importance FROM entries WHERE archived = 0 ORDER BY importance DESC, updated_at DESC LIMIT 25"
                ).fetchall()
            actions.append({"type": "entries_listed", "tag": tag, "results": [dict(r) for r in rows]})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # LIST_RELATIONSHIPS — read an entry's graph links
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "LIST_RELATIONSHIPS"):
        eid = to_int(payload)
        if not first_time("LIST_RELATIONSHIPS", payload):
            continue
        if eid is None:
            actions.append({"type": "error", "detail": "LIST_RELATIONSHIPS needs a numeric entry id"})
            continue
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT r.id, r.from_id, r.to_id, r.label FROM relationships r WHERE r.from_id = ? OR r.to_id = ?",
                [eid, eid],
            ).fetchall()
            actions.append({"type": "relationships_listed", "entry_id": eid, "results": [dict(r) for r in rows]})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # APPEND_ENTRY — extend an existing entry's content
    # ─────────────────────────────────────────────────────────────────────────
    for m in re.finditer(r'\[APPEND_ENTRY:\s*(\d+)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        eid = to_int(m.group(1))
        text = m.group(2).strip()
        if not first_time("APPEND_ENTRY", m.group(0).strip()):
            continue
        if eid is None:
            actions.append({"type": "error", "detail": "APPEND_ENTRY needs a numeric entry id"})
            continue
        conn = get_db()
        try:
            row = conn.execute("SELECT content FROM entries WHERE id = ?", [eid]).fetchone()
            if row:
                new_content = (row["content"] + "\n\n" + text)[:20000]
                conn.execute(
                    "UPDATE entries SET content = ?, updated_at = datetime('now') WHERE id = ?",
                    [new_content, eid],
                )
                conn.commit()
                actions.append({"type": "entry_appended", "entry_id": eid})
            else:
                actions.append({"type": "error", "detail": f"Entry {eid} not found"})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"APPEND_ENTRY failed: {type(exc).__name__}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # ARCHIVE_ENTRY — soft-delete
    # ─────────────────────────────────────────────────────────────────────────
    from app.chat.task_manager import _store_pending_action
    for payload in _extract_line_tags(reply, "ARCHIVE_ENTRY"):
        eid = to_int(payload)
        if not first_time("ARCHIVE_ENTRY", payload):
            continue
        if eid is None:
            actions.append({"type": "error", "detail": "ARCHIVE_ENTRY needs a numeric entry id"})
            continue
        # Destructive action: do not execute until the user confirms.
        nonce = _store_pending_action({"type": "archive_entry", "entry_id": eid})
        actions.append({
            "type": "pending_confirm",
            "confirm_id": nonce,
            "action": {"type": "archive_entry", "entry_id": eid},
            "message": f"Archive entry #{eid}?",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # CREATE_THOUGHT
    # ─────────────────────────────────────────────────────────────────────────
    for m in re.finditer(r'\[CREATE_THOUGHT:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        name, desc, tag_ids = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if not first_time("CREATE_THOUGHT", m.group(0).strip()):
            continue
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO thoughts (name, description, tag_ids) VALUES (?, ?, ?)",
                [name[:200], desc[:2000], tag_ids[:500]],
            )
            conn.commit()
            actions.append({"type": "thought_created", "id": cur.lastrowid, "name": name})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"Could not create thought: {exc}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # CREATE_TAG
    # ─────────────────────────────────────────────────────────────────────────
    for m in re.finditer(r'\[CREATE_TAG:\s*(.*?)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        name, color = m.group(1).strip(), m.group(2).strip() or "#6366f1"
        if not first_time("CREATE_TAG", m.group(0).strip()):
            continue
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)", [name[:100], color[:20]])
            conn.commit()
            actions.append({"type": "tag_created", "name": name})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # ADD_REMINDER
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "ADD_REMINDER"):
        parts = [p.strip() for p in payload.split("|", 3)]
        eid = to_int(parts[0] if parts else "")
        title = parts[1] if len(parts) > 1 else ""
        trigger = parts[2] if len(parts) > 2 else ""
        rtype = parts[3] if len(parts) > 3 else "once"
        if not first_time("ADD_REMINDER", payload):
            continue
        if eid is None:
            actions.append({"type": "error", "detail": "ADD_REMINDER needs a numeric entry id"})
            continue
        if rtype not in ("once", "daily", "weekly", "monthly"):
            actions.append({"type": "error", "detail": f"ADD_REMINDER failed: invalid reminder_type '{rtype}' (once|daily|weekly|monthly)"})
            continue
        try:
            from datetime import datetime
            datetime.fromisoformat(trigger.replace("Z", "+00:00"))
        except ValueError:
            actions.append({"type": "error", "detail": f"ADD_REMINDER failed: invalid trigger time '{trigger}' (expected YYYY-MM-DDTHH:MM:SS)"})
            continue
        conn = get_db()
        try:
            recurrence = rtype if rtype in ("daily", "weekly", "monthly") else None
            cur = conn.execute(
                "INSERT INTO reminders (entry_id, title, trigger_at, reminder_type, recurrence) VALUES (?, ?, ?, ?, ?)",
                [eid, title[:200], trigger, rtype, recurrence],
            )
            conn.commit()
            actions.append({"type": "reminder_added", "id": cur.lastrowid, "entry_id": eid})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"ADD_REMINDER failed: {type(exc).__name__}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # CREATE_THEORY
    # ─────────────────────────────────────────────────────────────────────────
    _THEORY_STATUSES = ("supported", "contested", "refuted", "emerging")
    for m in re.finditer(r'\[CREATE_THEORY:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\d*)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        name, desc, parent_id, status = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4).strip() or "emerging"
        if not first_time("CREATE_THEORY", m.group(0).strip()):
            continue
        if status not in _THEORY_STATUSES:
            actions.append({
                "type": "error",
                "detail": (
                    f"CREATE_THEORY refused: status '{status[:40]}' is invalid; use one of "
                    f"{', '.join(sorted(_THEORY_STATUSES))}."
                ),
            })
            continue
        conn = get_db()
        try:
            pid = to_int(parent_id) if parent_id else None
            cur = conn.execute(
                "INSERT INTO theories (name, description, parent_id, status) VALUES (?, ?, ?, ?)",
                [name[:300], desc[:2000], pid, status],
            )
            conn.commit()
            actions.append({"type": "theory_created", "id": cur.lastrowid, "name": name})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"Could not create theory: {exc}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # LINK_STUDY — attach pro/contra evidence to a theory
    # ─────────────────────────────────────────────────────────────────────────
    for m in re.finditer(r'\[LINK_STUDY:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(pro|contra)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        theory_id, entry_id = to_int(m.group(1)), to_int(m.group(2))
        stance, note = m.group(3), m.group(4).strip()
        if not first_time("LINK_STUDY", m.group(0).strip()):
            continue
        if theory_id is None or entry_id is None:
            actions.append({"type": "error", "detail": "LINK_STUDY needs numeric theory and entry ids"})
            continue
        conn = get_db()
        try:
            theory = conn.execute("SELECT id FROM theories WHERE id = ?", [theory_id]).fetchone()
            entry = conn.execute("SELECT id, study_type, content, tags FROM entries WHERE id = ?", [entry_id]).fetchone()
            if not theory or not entry:
                actions.append({"type": "error", "detail": f"LINK_STUDY failed: theory #{theory_id} or entry #{entry_id} not found"})
            else:
                st = str(entry["study_type"] or "").lower()
                txt = f"{st} {str(entry['content'] or '')} {str(entry['tags'] or '')}".lower()
                weight = 3
                if any(w in txt for w in ["meta-analysis", "systematic review"]):
                    weight = 5
                elif any(w in txt for w in ["rct", "randomized", "controlled trial"]):
                    weight = 5
                elif any(w in txt for w in ["experimental", "interventional", "electrophysiology"]):
                    weight = 4
                elif any(w in txt for w in ["cohort", "longitudinal", "imaging", "fmri"]):
                    weight = 3
                elif any(w in txt for w in ["cross-sectional", "observational", "survey"]):
                    weight = 2
                elif any(w in txt for w in ["theoretical", "computational", "review", "framework", "perspective"]):
                    weight = 1
                conn.execute(
                    "INSERT OR IGNORE INTO theory_evidence (theory_id, entry_id, stance, note, weight) VALUES (?, ?, ?, ?, ?)",
                    [theory_id, entry_id, stance, note[:1000], weight],
                )
                conn.commit()
                actions.append({"type": "study_linked", "theory_id": theory_id, "entry_id": entry_id, "stance": stance, "weight": weight})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"LINK_STUDY failed: {exc}"})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # LIST_THEORIES
    # ─────────────────────────────────────────────────────────────────────────
    for q in _extract_line_tags(reply, "LIST_THEORIES"):
        if not first_time("LIST_THEORIES", q):
            continue
        conn = get_db()
        try:
            if q:
                rows = conn.execute(
                    """SELECT t.id, t.name, t.status, t.parent_id,
                              (SELECT COUNT(*) FROM theory_evidence te WHERE te.theory_id = t.id AND te.stance = 'pro') AS pro_count,
                              (SELECT COUNT(*) FROM theory_evidence te WHERE te.theory_id = t.id AND te.stance = 'contra') AS contra_count
                       FROM theories t WHERE t.name LIKE ? ORDER BY t.name LIMIT 25""",
                    [f"%{q}%"],
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT t.id, t.name, t.status, t.parent_id,
                              (SELECT COUNT(*) FROM theory_evidence te WHERE te.theory_id = t.id AND te.stance = 'pro') AS pro_count,
                              (SELECT COUNT(*) FROM theory_evidence te WHERE te.theory_id = t.id AND te.stance = 'contra') AS contra_count
                       FROM theories t ORDER BY t.name LIMIT 50"""
                ).fetchall()
            actions.append({"type": "theories_listed", "query": q, "results": [dict(r) for r in rows]})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # RESEARCH
    # ─────────────────────────────────────────────────────────────────────────
    for topic in _extract_line_tags(reply, "RESEARCH"):
        if not first_time("RESEARCH", topic):
            continue
        if not topic:
            actions.append({
                "type": "error",
                "detail": "RESEARCH needs a topic; expected format: [RESEARCH: research topic]",
            })
            continue
        actions.append({"type": "research_request", "topic": topic[:300]})

    # ─────────────────────────────────────────────────────────────────────────
    # DOWNLOAD_STUDY
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "DOWNLOAD_STUDY"):
        ref = payload.strip()
        if not first_time("DOWNLOAD_STUDY", payload):
            continue
        if not ref:
            actions.append({
                "type": "error",
                "detail": "DOWNLOAD_STUDY needs a reference; expected format: [DOWNLOAD_STUDY: entry_id|DOI|URL|exact title|missing]",
            })
            continue
        if ref.lower().replace(" ", "_") in ("missing", "all", "lipsa", "lipsă", "toate", "all_missing"):
            actions.append({"type": "download_missing_request"})
            continue
        eid = to_int(ref)
        if eid is not None:
            actions.append({"type": "download_study_request", "entry_id": eid})
        else:
            actions.append({"type": "download_study_request", "ref": ref})

    # ─────────────────────────────────────────────────────────────────────────
    # LIST_STUDY_NOTES
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "LIST_STUDY_NOTES"):
        if not first_time("LIST_STUDY_NOTES", payload):
            continue
        conn = get_db()
        try:
            eid = to_int(payload) if payload else None
            if eid:
                rows = conn.execute(
                    "SELECT id, entry_id, course_context, content, page_number, created_at FROM study_notes WHERE entry_id = ? ORDER BY created_at DESC",
                    [eid],
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT n.id, n.entry_id, n.course_context, n.content, n.page_number, n.created_at, e.title as study_title
                       FROM study_notes n JOIN entries e ON n.entry_id = e.id
                       WHERE e.archived = 0 ORDER BY n.created_at DESC LIMIT 20"""
                ).fetchall()
            res = [dict(r) for r in rows]
            actions.append({"type": "study_notes_listed", "notes": res, "count": len(res)})
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # ADD_STUDY_NOTE
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "ADD_STUDY_NOTE"):
        parts = [p.strip() for p in payload.split("|")]
        if not first_time("ADD_STUDY_NOTE", payload):
            continue
        if len(parts) >= 2:
            eid = to_int(parts[0])
            course = parts[1] if len(parts) >= 3 else ""
            content = parts[2] if len(parts) >= 3 else parts[1]
            if eid and content:
                conn = get_db()
                try:
                    cur = conn.execute(
                        "INSERT INTO study_notes (entry_id, course_context, content) VALUES (?, ?, ?)",
                        [eid, course, content],
                    )
                    conn.commit()
                    actions.append({"type": "study_note_added", "note_id": cur.lastrowid, "entry_id": eid, "course_context": course})
                except Exception as exc:
                    actions.append({"type": "error", "detail": f"ADD_STUDY_NOTE failed: {exc}"})
                finally:
                    conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # NOTE_CAPTURE / NOTE_VERIFY / LIST_NOTE_DOSSIERS — verified note dossiers
    # (async dispatch by the endpoint; the parser never touches the dossiers)
    # ─────────────────────────────────────────────────────────────────────────
    for payload in _extract_line_tags(reply, "NOTE_CAPTURE"):
        if not first_time("NOTE_CAPTURE", payload):
            continue
        if "|" in payload:
            eid_raw, text = [p.strip() for p in payload.split("|", 1)]
        else:
            eid_raw, text = "", payload.strip()
        eid = to_int(eid_raw) if eid_raw else None
        if eid_raw and eid is None:
            actions.append({"type": "error", "detail": "NOTE_CAPTURE needs a numeric entry id (or an empty one, meaning the entry in the reading context)"})
            continue
        if not text:
            actions.append({"type": "error", "detail": "NOTE_CAPTURE needs the raw note text; expected format: [NOTE_CAPTURE: entry_id | raw note text]"})
            continue
        actions.append({"type": "note_capture_request", "entry_id": eid, "text": text, "page": None})

    for payload in _extract_line_tags(reply, "NOTE_VERIFY"):
        if not first_time("NOTE_VERIFY", payload):
            continue
        did = to_int(payload.strip())
        if did is None:
            actions.append({"type": "error", "detail": "NOTE_VERIFY needs a numeric dossier id; expected format: [NOTE_VERIFY: dossier_id]"})
            continue
        actions.append({"type": "note_verify_request", "dossier_id": did})

    for payload in _extract_line_tags(reply, "NOTE_MAP"):
        if not first_time("NOTE_MAP", payload):
            continue
        word = payload.strip()
        if word.lower() == "project":
            actions.append({"type": "note_map_request", "dossier_id": None, "project": True})
            continue
        did = to_int(word)
        if did is None:
            actions.append({"type": "error", "detail": "NOTE_MAP: lipsește dossier_id (sau scrie 'project')"})
            continue
        actions.append({"type": "note_map_request", "dossier_id": did})

    for payload in _extract_line_tags(reply, "NOTE_EXPORT"):
        if not first_time("NOTE_EXPORT", payload):
            continue
        parts = [p.strip() for p in payload.split("|", 1)]
        ref = parts[0] if parts else ""
        fmt = (parts[1] if len(parts) > 1 else "").strip().lower()
        if fmt not in ("md", "html"):
            fmt = "html"
        if ref.lower() == "project":
            actions.append({"type": "note_export_request", "dossier_id": None, "project": True, "format": fmt})
            continue
        did = to_int(ref)
        if did is None:
            actions.append({"type": "error", "detail": "NOTE_EXPORT: lipsește dossier_id (sau scrie 'project')"})
            continue
        actions.append({"type": "note_export_request", "dossier_id": did, "format": fmt})

    for payload in _extract_line_tags(reply, "LIST_NOTE_DOSSIERS"):
        if not first_time("LIST_NOTE_DOSSIERS", payload):
            continue
        actions.append({"type": "note_list_request"})

    # ─────────────────────────────────────────────────────────────────────────
    # SCREEN_STUDY
    # ─────────────────────────────────────────────────────────────────────────
    from app.chat.task_manager import _update_task
    for payload in _extract_line_tags(reply, "SCREEN_STUDY"):
        parts = [p.strip() for p in payload.split("|", 2)]
        eid = to_int(parts[0] if parts else "")
        decision = (parts[1] if len(parts) > 1 else "").strip().lower()
        reason = parts[2] if len(parts) > 2 else ""
        if not first_time("SCREEN_STUDY", payload):
            continue
        if eid is None:
            actions.append(_action_error("SCREEN_STUDY", "first field must be a numeric entry id"))
            continue
        if decision not in ("include", "exclude", "maybe"):
            actions.append(_action_error("SCREEN_STUDY", f"invalid decision '{decision[:30]}'"))
            continue
        conn = get_db()
        try:
            if not conn.execute("SELECT id FROM entries WHERE id = ? AND archived = 0", [eid]).fetchone():
                actions.append(_action_error("SCREEN_STUDY", f"entry #{eid} not found"))
                continue
            conn.execute(
                """INSERT INTO screening_decisions (entry_id, decision, reason, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(entry_id) DO UPDATE SET
                     decision = excluded.decision,
                     reason = excluded.reason,
                     updated_at = datetime('now')""",
                [eid, decision, reason[:1000]],
            )
            conn.commit()
            actions.append({
                "type": "study_screened",
                "entry_id": eid,
                "decision": decision,
                "reason": reason[:300],
                "message": f"Studiu #{eid} marcat «{decision}».",
            })
            _update_task(kind="screening", progress={f"last_{decision}": f"#{eid}"})
        except Exception as exc:  # noqa: BLE001
            actions.append(_action_error("SCREEN_STUDY", f"unexpected error ({type(exc).__name__})"))
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Edit / delete / pipeline handlers (dispatch table)
    # ─────────────────────────────────────────────────────────────────────────
    from app.chat.handlers.write_actions import _AI_WRITE_HANDLERS
    for tag_name, handler in _AI_WRITE_HANDLERS:
        for payload in _extract_line_tags(reply, tag_name):
            if first_time(tag_name, payload):
                actions.extend(_run_handler(tag_name, handler, payload))

    return actions


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions for handlers
# ──────────────────────────────────────────────────────────────────────────────

def _action_error(tag: str, detail: str) -> Dict[str, Any]:
    from app.chat.handlers.write_actions import _TAG_FORMATS
    if "expected format:" not in detail:
        detail = f"{detail}; expected format: {_TAG_FORMATS.get(tag, '')}"
    return {"type": "error", "detail": f"{tag} failed: {detail}"}


def _run_handler(tag: str, handler, payload: str) -> List[Dict[str, Any]]:
    """Run one action handler; a bug or a DB error becomes a retryable error action."""
    try:
        return handler(payload)
    except Exception as exc:  # noqa: BLE001 - action parsing must never raise
        logger.error("AI action %s failed: %s", tag, exc)
        return [_action_error(
            tag,
            f"unexpected error ({type(exc).__name__}); expected format: {_TAG_FORMATS[tag]}",
        )]


# ──────────────────────────────────────────────────────────────────────────────
# Date coercion helper
# ──────────────────────────────────────────────────────────────────────────────
_ISO_DATE_RE = re.compile(r"^-?\d{4}-\d{2}-\d{2}$")


def _iso_entry_date(raw: str) -> tuple[str | None, str | None]:
    """Coerce a model-written date to ISO ``YYYY-MM-DD``.

    Returns ``(value, error)``: exactly one is None. A bare year becomes
    ``YYYY-01-01``; anything else unparseable is refused with a Romanian-and-
    English hint the model can retry on, instead of writing free text into a
    column the timeline orders on.
    """
    text = (raw or "").strip().strip("[]")
    if not text or text in ("-", "|"):
        return None, None
    if _ISO_DATE_RE.match(text):
        return text, None
    year = re.fullmatch(r"(\d{1,4})", text)
    if year:
        return f"{int(year.group(1)):04d}-01-01", None
    dotted = re.fullmatch(r"(\d{1,4})[./](\d{1,2})[./](\d{1,4})", text)
    if dotted:
        first, second, third = (int(x) for x in dotted.groups())
        # Romanian style day.month.year (12.03.1999) unless the first number
        # cannot be a day, in which case it is year.month.day.
        if first > 31:
            y, mo, d = first, second, third
        else:
            d, mo, y = first, second, third
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}", None
    return None, (
        f"CREATE_ENTRY refused: entry_date '{text[:40]}' is not ISO YYYY-MM-DD "
        "(data, nu text liber). Retrimite cu [CREATE_ENTRY: ... | YYYY-MM-DD | ...]."
    )