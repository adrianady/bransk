"""Write/Edit/Delete action handlers - executed immediately or via confirmation."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.db import get_db
from app.tagging import normalize_tags, sync_tags
from app.study_dedup import titles_match
from app.study_import import (
    find_duplicate_study,
    insert_study_entry,
)
from app.routers.relationships import RELATION_KINDS, parse_relation_label
from app.routers.spaced_repetition import _sm2_calc
from app.routers.project import _ensure_project

logger = logging.getLogger("chronokg.chat.write_actions")


# ──────────────────────────────────────────────────────────────────────────────
# Column whitelists and limits
# ──────────────────────────────────────────────────────────────────────────────
_ENTRY_UPDATE_COLUMNS = {
    "title": "title",
    "content": "content",
    "entry_date": "entry_date",
    "tags": "tags",
    "importance": "importance",
    "doi": "doi",
    "authors": "authors",
    "quality": "quality",
    "study_type": "study_type",
    "url": "source",
    "bib_title": "bib_title",
    "bib_year": "bib_year",
}

_ENTRY_UPDATE_LIMITS = {
    "title": 500, "content": 20000, "tags": 500, "doi": 200,
    "authors": 1000, "study_type": 100, "url": 2000,
    "bib_title": 500, "bib_year": 20,
}

_STUDY_UPDATE_COLUMNS = {
    "doi": "doi",
    "doi_source": "doi_source",
    "authors": "authors",
    "year": "bib_year",
    "quality": "quality",
    "study_type": "study_type",
    "url": "source",
    "pdf_path": "pdf_path",
}

_STUDY_UPDATE_LIMITS = {
    "doi": 200, "doi_source": 300, "authors": 1000, "year": 20,
    "study_type": 100, "url": 2000, "pdf_path": 500,
}

_PROJECT_UPDATE_COLUMNS = {
    "title": "title",
    "description": "description",
    "research_q": "research_q",
    "methodology": "methodology",
    "work_type": "work_type",
    "field": "field",
    "deadline": "deadline",
}

_PROJECT_UPDATE_LIMITS = {
    "title": 500, "description": 10000, "research_q": 2000,
    "methodology": 20000, "work_type": 50, "field": 200, "deadline": 20,
}

_PLAN_KEYS = (
    "participants", "instruments", "hypotheses", "variables",
    "analysis", "ethics", "schedule", "risks",
)
_THEORY_STATUSES = ("supported", "contested", "refuted", "emerging")
_WORK_TYPE_VALUES = ("licenta", "disertatie", "doctorat", "referat")
_MAX_SECTION_CHARS = 20000

_TAG_FORMATS = {
    "UPDATE_ENTRY": "[UPDATE_ENTRY: entry_id | key=value | key=value ...]",
    "WRITE_SECTION": "[WRITE_SECTION: section_id | append|replace | text]",
    "UPDATE_PROJECT": "[UPDATE_PROJECT: key=value | key=value ...]",
    "SET_PLAN": "[SET_PLAN: key | value]",
    "UPDATE_STUDY": "[UPDATE_STUDY: entry_id | key=value | key=value ...]",
    "ADD_STUDY": "[ADD_STUDY: title | authors | year | doi | url]",
    "ADD_TAGS": "[ADD_TAGS: entry_id | tag1,tag2]",
    "UPDATE_RELATIONSHIP": "[UPDATE_RELATIONSHIP: relationship_id | label=...]",
    "UPDATE_THEORY": "[UPDATE_THEORY: theory_id | name=... | description=... | status=... | parent_id=...]",
    "COMPLETE_TODO": "[COMPLETE_TODO: todo_id]",
    "SCHEDULE_REVIEW": "[SCHEDULE_REVIEW: entry_id | quality]",
    "DELETE_ENTRY": "[DELETE_ENTRY: entry_id]",
    "MERGE_ENTRIES": "[MERGE_ENTRIES: keep_id | drop_id]",
    "DELETE_RELATIONSHIP": "[DELETE_RELATIONSHIP: relationship_id]",
    "DELETE_THEORY": "[DELETE_THEORY: theory_id]",
    "UNLINK_STUDY": "[UNLINK_STUDY: evidence_id]",
    "DELETE_TODO": "[DELETE_TODO: todo_id]",
    "DELETE_REMINDER": "[DELETE_REMINDER: reminder_id]",
    "INDEX_PDF": "[INDEX_PDF: entry_id]",
    "REANALYZE_DATA": "[REANALYZE_DATA: full|issues]",
    "SCREEN_STUDY": "[SCREEN_STUDY: entry_id | include|exclude|maybe | reason]",
}


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────────────

def _as_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _action_error(tag: str, detail: str) -> Dict[str, Any]:
    if "expected format:" not in detail:
        detail = f"{detail}; expected format: {_TAG_FORMATS.get(tag, '')}"
    return {"type": "error", "detail": f"{tag} failed: {detail}"}


def _clean_doi(value: str) -> str:
    return (value or "").strip().lower()


def _normalise_tags(value: str) -> str:
    """Trim, lowercase and dedupe a comma/semicolon separated tag list."""
    seen: List[str] = []
    for chunk in re.split(r"[,;]", value or ""):
        part = chunk.strip().lower()
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen)


def _decode_text(value: str) -> str:
    """Tags own one line, so `\\n` inside a payload means a paragraph break."""
    return (value or "").replace("\\n", "\n").replace("\\t", "\t")


def _valid_iso_date(value: str):
    """A valid YYYY-MM-DD, None for an empty value, False when malformed."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return text


def _valid_year(value: str):
    """A 4-digit year, "" for empty, None when malformed."""
    text = (value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}", text):
        return None
    return text


def _parse_kv_segments(parts, tag: str):
    """Parse `key=value` segments; a segment without `=` continues the value."""
    pairs: Dict[str, str] = {}
    last = None
    for raw in parts:
        part = raw.strip()
        if not part:
            continue
        if "=" not in part:
            if last is None:
                return None, f"expected key=value, got '{part[:80]}'; expected format: {_TAG_FORMATS[tag]}"
            pairs[last] = pairs[last] + " | " + part
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        if not key:
            return None, f"empty key in '{part[:80]}'; expected format: {_TAG_FORMATS[tag]}"
        pairs[key] = value.strip()
        last = key
    return pairs, None


def _split_id_and_kv(payload: str, tag: str):
    head, _sep, rest = payload.partition("|")
    item_id = _as_int(head)
    if item_id is None:
        return None, None, f"the first field must be a numeric id; expected format: {_TAG_FORMATS[tag]}"
    pairs, err = _parse_kv_segments(rest.split("|"), tag)
    return item_id, pairs, err


def _pending(action: Dict[str, Any], message: str, **extra) -> Dict[str, Any]:
    """Wrap a destructive action in a confirmation request (no execution yet)."""
    from app.chat.task_manager import _store_pending_action, _update_task
    nonce = _store_pending_action(action)
    out = {"type": "pending_confirm", "confirm_id": nonce, "action": action, "message": message}
    out.update(extra)
    return out


def _entry_dependency_counts(conn, entry_id: int) -> Dict[str, int]:
    """How many dependent rows a hard delete of this entry would remove."""
    return {
        "relationships": conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE from_id = ? OR to_id = ?",
            [entry_id, entry_id],
        ).fetchone()[0],
        "theory_evidence": conn.execute(
            "SELECT COUNT(*) FROM theory_evidence WHERE entry_id = ?", [entry_id]
        ).fetchone()[0],
        "documents": conn.execute(
            "SELECT COUNT(*) FROM documents WHERE entry_id = ?", [entry_id]
        ).fetchone()[0],
        "todos": conn.execute(
            "SELECT COUNT(*) FROM todos WHERE entry_id = ?", [entry_id]
        ).fetchone()[0],
        "reminders": conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE entry_id = ?", [entry_id]
        ).fetchone()[0],
        "spaced_repetition": conn.execute(
            "SELECT COUNT(*) FROM spaced_repetition WHERE entry_id = ?", [entry_id]
        ).fetchone()[0],
        "review_history": conn.execute(
            "SELECT COUNT(*) FROM review_history WHERE item_id IN "
            "(SELECT id FROM spaced_repetition WHERE entry_id = ?)",
            [entry_id],
        ).fetchone()[0],
    }


def _counts_phrase(counts: Dict[str, int]) -> str:
    labels = (
        ("relationships", "relații"),
        ("theory_evidence", "dovezi"),
        ("documents", "documente"),
        ("todos", "task-uri"),
        ("reminders", "memento-uri"),
        ("spaced_repetition", "programări de recapitulare"),
        ("review_history", "recapitulări"),
    )
    parts = [f"{counts[key]} {label}" for key, label in labels if counts.get(key)]
    return ", ".join(parts) if parts else "nicio dependență"


# ──────────────────────────────────────────────────────────────────────────────
# Edit handlers (auto-executed; they apply immediately)
# ──────────────────────────────────────────────────────────────────────────────

def _handle_update_entry(payload: str) -> List[Dict[str, Any]]:
    tag = "UPDATE_ENTRY"
    entry_id, pairs, err = _split_id_and_kv(payload, tag)
    if err:
        return [_action_error(tag, err)]
    if not pairs:
        return [_action_error(tag, f"no fields given; expected format: {_TAG_FORMATS[tag]}")]

    updates: Dict[str, Any] = {}
    for key, value in pairs.items():
        column = _ENTRY_UPDATE_COLUMNS.get(key)
        if column is None:
            allowed = ", ".join(_ENTRY_UPDATE_COLUMNS)
            return [_action_error(tag, f"unknown key '{key}' (allowed: {allowed}); expected format: {_TAG_FORMATS[tag]}")]
        if key in ("importance", "quality"):
            number = _as_int(value)
            if number is None:
                return [_action_error(tag, f"{key} must be a number 1-5, got '{value[:40]}'")]
            updates[column] = max(1, min(5, number))
        elif key == "entry_date":
            date = _valid_iso_date(value)
            if date is False:
                return [_action_error(tag, f"entry_date must be YYYY-MM-DD, got '{value[:40]}'")]
            updates[column] = date
        elif key == "title":
            title = value.strip()
            if not title:
                return [_action_error(tag, "title cannot be empty")]
            updates[column] = title[:500]
        elif key == "content":
            updates[column] = _decode_text(value)[:20000]
        elif key == "tags":
            updates[column] = _normalise_tags(value)[:500]
        elif key == "doi":
            updates[column] = _clean_doi(value)[:200]
        elif key == "url":
            updates[column] = value.strip()[:2000]
        elif key == "bib_year":
            year = _valid_year(value)
            if year is None:
                return [_action_error(tag, f"bib_year must be a 4-digit year, got '{value[:20]}'")]
            updates[column] = year
        else:
            limit = _ENTRY_UPDATE_LIMITS.get(key, 500)
            updates[column] = value.strip()[:limit]

    conn = get_db()
    try:
        row = conn.execute("SELECT id, title FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            return [_action_error(tag, f"entry #{entry_id} not found")]
        columns = list(updates)
        set_clause = ", ".join(f"{column} = ?" for column in columns)
        conn.execute(
            f"UPDATE entries SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            [*(updates[column] for column in columns), entry_id],
        )
        if "tags" in updates:
            sync_tags(conn, updates["tags"])
        conn.commit()
        changed = ", ".join(sorted(pairs))
        return [{
            "type": "entry_updated",
            "entry_id": entry_id,
            "title": updates.get("title") or row["title"],
            "fields": sorted(pairs),
            "message": f"Am actualizat intrarea #{entry_id}: {changed}.",
        }]
    finally:
        conn.close()


def _handle_write_section(payload: str) -> List[Dict[str, Any]]:
    tag = "WRITE_SECTION"
    parts = payload.split("|", 2)
    if len(parts) < 3:
        return [_action_error(tag, f"expected three fields; expected format: {_TAG_FORMATS[tag]}")]
    section_id, mode, text = (part.strip() for part in parts)
    mode = mode.lower()
    if not section_id:
        return [_action_error(tag, "section_id is empty")]
    if mode not in ("append", "replace"):
        return [_action_error(tag, f"mode must be append or replace, got '{mode[:20]}'")]
    text = _decode_text(text)
    if len(text) > _MAX_SECTION_CHARS:
        return [_action_error(tag, f"text is {len(text)} characters (limit {_MAX_SECTION_CHARS})")]

    conn = get_db()
    try:
        project_id = _ensure_project(conn)["id"]
        row = conn.execute("SELECT structure_json FROM project WHERE id = ?", [project_id]).fetchone()
        try:
            structure = json.loads((row["structure_json"] if row else "") or "[]")
        except (json.JSONDecodeError, TypeError):
            structure = None
        if not isinstance(structure, list):
            return [_action_error(tag, "project.structure_json is not a JSON list; open the Project tab and save the outline first")]
        section = next(
            (item for item in structure
             if isinstance(item, dict) and str(item.get("id")) == section_id),
            None,
        )
        if section is None:
            ids = ", ".join(str(item.get("id")) for item in structure if isinstance(item, dict)) or "(none)"
            return [_action_error(tag, f"section '{section_id}' not found (available: {ids})")]
        old = section.get("content") or ""
        new_text = old.rstrip() + "\n\n" + text if mode == "append" and old.strip() else text
        if len(new_text) > _MAX_SECTION_CHARS:
            return [_action_error(
                tag,
                f"the section would reach {len(new_text)} characters (limit {_MAX_SECTION_CHARS}); "
                "use replace or shorten the text",
            )]
        section["content"] = new_text
        conn.execute(
            "UPDATE project SET structure_json = ?, updated_at = datetime('now') WHERE id = ?",
            [json.dumps(structure, ensure_ascii=False), project_id],
        )
        conn.commit()
        words = len(text.split())
        total = len(new_text.split())
        title = section.get("title") or section_id
        return [{
            "type": "section_updated",
            "id": section_id,
            "section_id": section_id,
            "title": title,
            "section_title": title,
            "words": words,
            "total_words": total,
            "message": f"Am scris {words} cuvinte în «{title}» (total {total} cuvinte).",
        }]
    finally:
        conn.close()


def _handle_update_project(payload: str) -> List[Dict[str, Any]]:
    tag = "UPDATE_PROJECT"
    pairs, err = _parse_kv_segments(payload.split("|"), tag)
    if err:
        return [_action_error(tag, err)]
    if not pairs:
        return [_action_error(tag, f"no fields given; expected format: {_TAG_FORMATS[tag]}")]

    updates: Dict[str, Any] = {}
    for key, value in pairs.items():
        column = _PROJECT_UPDATE_COLUMNS.get(key)
        if column is None:
            allowed = ", ".join(_PROJECT_UPDATE_COLUMNS)
            return [_action_error(tag, f"unknown key '{key}' (allowed: {allowed}); expected format: {_TAG_FORMATS[tag]}")]
        if key == "deadline":
            date = _valid_iso_date(value)
            if date is False:
                return [_action_error(tag, f"deadline must be YYYY-MM-DD, got '{value[:40]}'")]
            updates[column] = date or ""
        elif key == "work_type":
            work_type = value.strip().lower()
            if work_type not in _WORK_TYPE_VALUES:
                return [_action_error(tag, f"work_type must be one of {', '.join(_WORK_TYPE_VALUES)}, got '{value[:30]}'")]
            updates[column] = work_type
        else:
            updates[column] = _decode_text(value).strip()[:_PROJECT_UPDATE_LIMITS[key]]

    conn = get_db()
    try:
        project_id = _ensure_project(conn)["id"]
        columns = list(updates)
        set_clause = ", ".join(f"{column} = ?" for column in columns)
        conn.execute(
            f"UPDATE project SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            [*[updates[column] for column in columns], project_id],
        )
        conn.commit()
        fields = sorted(pairs)
        return [{
            "type": "project_updated",
            "fields": fields,
            "message": f"Am actualizat proiectul: {', '.join(fields)}.",
        }]
    finally:
        conn.close()


def _handle_set_plan(payload: str) -> List[Dict[str, Any]]:
    tag = "SET_PLAN"
    key, separator, value = payload.partition("|")
    key = key.strip().lower()
    if not separator or not key:
        return [_action_error(tag, f"expected a key and a value; expected format: {_TAG_FORMATS[tag]}")]
    if key not in _PLAN_KEYS:
        return [_action_error(tag, f"unknown plan key '{key}' (allowed: {', '.join(_PLAN_KEYS)})")]
    value = _decode_text(value.strip())
    if len(value) > 10000:
        return [_action_error(tag, f"value is {len(value)} characters (limit 10000)")]

    conn = get_db()
    try:
        project_id = _ensure_project(conn)["id"]
        row = conn.execute("SELECT plan_json FROM project WHERE id = ?", [project_id]).fetchone()
        try:
            plan = json.loads((row["plan_json"] if row else "") or "{}")
        except (json.JSONDecodeError, TypeError):
            plan = {}
        if not isinstance(plan, dict):
            plan = {}
        plan[key] = value
        conn.execute(
            "UPDATE project SET plan_json = ?, updated_at = datetime('now') WHERE id = ?",
            [json.dumps(plan, ensure_ascii=False), project_id],
        )
        conn.commit()
        return [{
            "type": "plan_updated",
            "key": key,
            "value_chars": len(value),
            "message": f"Am actualizat planul de cercetare: {key}.",
        }]
    finally:
        conn.close()


def _handle_update_study(payload: str) -> List[Dict[str, Any]]:
    tag = "UPDATE_STUDY"
    entry_id, pairs, err = _split_id_and_kv(payload, tag)
    if err:
        return [_action_error(tag, err)]
    if not pairs:
        return [_action_error(tag, f"no fields given; expected format: {_TAG_FORMATS[tag]}")]

    updates: Dict[str, Any] = {}
    for key, value in pairs.items():
        column = _STUDY_UPDATE_COLUMNS.get(key)
        if column is None:
            allowed = ", ".join(_STUDY_UPDATE_COLUMNS)
            return [_action_error(tag, f"unknown key '{key}' (allowed: {allowed}); expected format: {_TAG_FORMATS[tag]}")]
        if key == "year":
            year = _valid_year(value)
            if year is None:
                return [_action_error(tag, f"year must be a 4-digit year, got '{value[:20]}'")]
            updates[column] = year
        elif key == "quality":
            number = _as_int(value)
            if number is None:
                return [_action_error(tag, f"quality must be a number 1-5, got '{value[:40]}'")]
            updates[column] = max(1, min(5, number))
        elif key == "doi":
            updates[column] = _clean_doi(value)[:200]
        elif key == "pdf_path":
            name = value.strip()
            if "/" in name or "\\" in name or name in (".", ".."):
                return [_action_error(tag, "pdf_path must be a file name, not a path")]
            updates[column] = name[:500]
        elif key == "url":
            updates[column] = value.strip()[:2000]
        else:
            updates[column] = value.strip()[:_STUDY_UPDATE_LIMITS.get(key, 500)]

    conn = get_db()
    try:
        row = conn.execute("SELECT id, title, study_type FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            return [_action_error(tag, f"entry #{entry_id} not found")]
        if not (row["study_type"] or "").strip():
            return [_action_error(
                tag,
                f"entry #{entry_id} is not a study (study_type is empty); use [UPDATE_ENTRY: {entry_id} | ...] instead",
            )]
        columns = list(updates)
        set_clause = ", ".join(f"{column} = ?" for column in columns)
        conn.execute(
            f"UPDATE entries SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            [*(updates[column] for column in columns), entry_id],
        )
        conn.commit()
        return [{
            "type": "study_updated",
            "entry_id": entry_id,
            "title": row["title"],
            "fields": sorted(pairs),
            "message": f"Am actualizat studiul #{entry_id}: {', '.join(sorted(pairs))}.",
        }]
    finally:
        conn.close()


def _handle_add_study(payload: str) -> List[Dict[str, Any]]:
    tag = "ADD_STUDY"
    parts = [part.strip() for part in payload.split("|", 4)]
    fields = (parts + [""] * 5)[:5]
    title, authors, year_raw, doi_raw, url = fields
    if not title:
        return [_action_error(tag, f"title is required; expected format: {_TAG_FORMATS[tag]}")]
    title = title[:500]
    year = _valid_year(year_raw)
    if year is None:
        return [_action_error(tag, f"year must be a 4-digit year, got '{year_raw[:20]}'")]
    doi = _clean_doi(doi_raw)
    if len(doi) > 200:
        return [_action_error(tag, "DOI is longer than 200 characters")]

    conn = get_db()
    try:
        if doi:
            duplicate = conn.execute(
                "SELECT id, title FROM entries WHERE lower(trim(coalesce(doi, ''))) = ? LIMIT 1",
                [doi],
            ).fetchone()
            if duplicate:
                return [_action_error(
                    tag,
                    f"DOI '{doi}' is already used by entry #{duplicate['id']} «{duplicate['title']}». "
                    f"Merge the rows with [MERGE_ENTRIES: {duplicate['id']} | new_id] or edit the existing one with [UPDATE_STUDY: ...].",
                )]
        entry_date = f"{year}-01-01" if year else None
        cursor = conn.execute(
            """INSERT INTO entries
                   (title, content, entry_date, tags, study_type, authors, doi, source, bib_title, bib_year)
               VALUES (?, '', ?, 'study', 'study', ?, ?, ?, ?, ?)""",
            [title, entry_date, authors[:1000], doi, url[:2000], title, year],
        )
        sync_tags(conn, "study")
        conn.commit()
        study_id = cursor.lastrowid
        return [{
            "type": "study_added",
            "id": study_id,
            "title": title,
            "doi": doi,
            "year": year,
            "message": f"Am adăugat studiul #{study_id}: «{title}».",
        }]
    finally:
        conn.close()


def _handle_add_tags(payload: str) -> List[Dict[str, Any]]:
    tag = "ADD_TAGS"
    head, separator, csv = payload.partition("|")
    entry_id = _as_int(head)
    if entry_id is None or not separator:
        return [_action_error(tag, f"expected an entry id and a tag list; expected format: {_TAG_FORMATS[tag]}")]
    requested: List[str] = []
    for chunk in re.split(r"[,;]", csv):
        part = chunk.strip().lower()
        if part and part not in requested:
            requested.append(part)
    if not requested:
        return [_action_error(tag, "no tags given")]
    if any(len(part) > 100 for part in requested):
        return [_action_error(tag, "each tag must be at most 100 characters")]

    conn = get_db()
    try:
        row = conn.execute("SELECT id, tags FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            return [_action_error(tag, f"entry #{entry_id} not found")]
        existing = [
            part.strip().lower()
            for part in re.split(r"[,;]", row["tags"] or "")
            if part.strip()
        ]
        merged = list(dict.fromkeys(existing + requested))
        merged_csv = ", ".join(merged)
        if len(merged_csv) > 500:
            return [_action_error(tag, "the entry tag list would exceed 500 characters")]
        conn.execute(
            "UPDATE entries SET tags = ?, updated_at = datetime('now') WHERE id = ?",
            [merged_csv, entry_id],
        )
        for name in requested:
            conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, '#6366f1')", [name])
        conn.commit()
        added = [name for name in requested if name not in existing]
        return [{
            "type": "tags_added",
            "entry_id": entry_id,
            "added": added,
            "tags": merged,
            "message": f"Am adăugat etichetele la #{entry_id}: {', '.join(added) or '(existau deja)'}.",
        }]
    finally:
        conn.close()


def _handle_update_relationship(payload: str) -> List[Dict[str, Any]]:
    tag = "UPDATE_RELATIONSHIP"
    relationship_id, pairs, err = _split_id_and_kv(payload, tag)
    if err:
        return [_action_error(tag, err)]
    if not pairs:
        return [_action_error(tag, f"no fields given; only label= is supported: {_TAG_FORMATS[tag]}")]
    unknown = [key for key in pairs if key != "label"]
    if unknown:
        return [_action_error(tag, f"unknown key '{unknown[0]}'; only label= is supported")]
    label = pairs["label"].strip()[:200]

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, from_id, to_id, label FROM relationships WHERE id = ?",
            [relationship_id],
        ).fetchone()
        if not row:
            return [_action_error(tag, f"relationship #{relationship_id} not found")]
        conn.execute("UPDATE relationships SET label = ? WHERE id = ?", [label, relationship_id])
        conn.commit()
        return [{
            "type": "relationship_updated",
            "id": relationship_id,
            "from": row["from_id"],
            "to": row["to_id"],
            "label": label,
            "message": f"Am actualizat legătura #{relationship_id}.",
        }]
    finally:
        conn.close()


def _handle_update_theory(payload: str) -> List[Dict[str, Any]]:
    tag = "UPDATE_THEORY"
    theory_id, pairs, err = _split_id_and_kv(payload, tag)
    if err:
        return [_action_error(tag, err)]
    if not pairs:
        return [_action_error(tag, f"no fields given; expected format: {_TAG_FORMATS[tag]}")]

    updates: Dict[str, Any] = {}
    for key, value in pairs.items():
        if key not in ("name", "description", "status", "parent_id"):
            return [_action_error(tag, f"unknown key '{key}' (allowed: name, description, status, parent_id)")]
        if key == "name":
            name = value.strip()
            if not name:
                return [_action_error(tag, "name cannot be empty")]
            updates["name"] = name[:300]
        elif key == "description":
            updates["description"] = _decode_text(value)[:2000]
        elif key == "status":
            status = value.strip().lower()
            if status not in _THEORY_STATUSES:
                return [_action_error(tag, f"status must be one of {', '.join(_THEORY_STATUSES)}, got '{value[:30]}'")]
            updates["status"] = status
        else:
            if not value.strip():
                updates["parent_id"] = None
            else:
                parent_id = _as_int(value)
                if parent_id is None:
                    return [_action_error(tag, f"parent_id must be a numeric theory id, got '{value[:20]}'")]
                if parent_id == theory_id:
                    return [_action_error(tag, "a theory cannot be its own parent")]
                updates["parent_id"] = parent_id

    conn = get_db()
    try:
        row = conn.execute("SELECT id, name, status FROM theories WHERE id = ?", [theory_id]).fetchone()
        if not row:
            return [_action_error(tag, f"theory #{theory_id} not found")]
        if updates.get("parent_id") is not None:
            parent = conn.execute("SELECT id FROM theories WHERE id = ?", [updates["parent_id"]]).fetchone()
            if not parent:
                return [_action_error(tag, f"parent theory #{updates['parent_id']} not found")]
        columns = list(updates)
        set_clause = ", ".join(f"{column} = ?" for column in columns)
        conn.execute(
            f"UPDATE theories SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            [updates[column] for column in columns] + [theory_id],
        )
        conn.commit()
        return [{
            "type": "theory_updated",
            "id": theory_id,
            "name": updates.get("name") or row["name"],
            "status": updates.get("status") or row["status"],
            "fields": sorted(pairs),
            "message": f"Am actualizat teoria #{theory_id}.",
        }]
    finally:
        conn.close()


def _handle_complete_todo(payload: str) -> List[Dict[str, Any]]:
    tag = "COMPLETE_TODO"
    todo_id = _as_int(payload)
    if todo_id is None:
        return [_action_error(tag, f"expected a numeric todo id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute("SELECT id, entry_id, completed FROM todos WHERE id = ?", [todo_id]).fetchone()
        if not row:
            return [_action_error(tag, f"todo #{todo_id} not found")]
        conn.execute("UPDATE todos SET completed = 1 WHERE id = ?", [todo_id])
        conn.commit()
        return [{
            "type": "todo_completed",
            "id": todo_id,
            "entry_id": row["entry_id"],
            "message": f"Am marcat taskul #{todo_id} ca finalizat.",
        }]
    finally:
        conn.close()


def _handle_schedule_review(payload: str) -> List[Dict[str, Any]]:
    tag = "SCHEDULE_REVIEW"
    parts = [part.strip() for part in payload.split("|", 1)]
    entry_id = _as_int(parts[0] if parts else "")
    quality = _as_int(parts[1] if len(parts) > 1 else "")
    if entry_id is None or quality is None or not 0 <= quality <= 5:
        return [_action_error(tag, f"expected an entry id and a quality 0-5; expected format: {_TAG_FORMATS[tag]}")]

    conn = get_db()
    try:
        entry = conn.execute("SELECT id, title FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not entry:
            return [_action_error(tag, f"entry #{entry_id} not found")]
        conn.execute("INSERT OR IGNORE INTO spaced_repetition (entry_id) VALUES (?)", [entry_id])
        item = conn.execute(
            "SELECT * FROM spaced_repetition WHERE entry_id = ?", [entry_id]
        ).fetchone()
        ease, interval, repetitions = _sm2_calc(
            quality, item["ease_factor"], item["interval"], item["repetitions"]
        )
        next_review = (datetime.now(timezone.utc) + timedelta(days=interval)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO review_history (item_id, quality) VALUES (?, ?)",
            [item["id"], quality],
        )
        conn.execute(
            "UPDATE spaced_repetition SET ease_factor = ?, interval = ?, repetitions = ?, next_review = ? WHERE id = ?",
            [ease, interval, repetitions, next_review, item["id"]],
        )
        conn.commit()
        return [{
            "type": "sr_reviewed",
            "entry_id": entry_id,
            "item_id": item["id"],
            "quality": quality,
            "ease_factor": round(ease, 3),
            "interval": interval,
            "repetitions": repetitions,
            "next_review": next_review,
            "message": f"Am programat recapitularea pentru #{entry_id}: calitate {quality}/5, următoarea în {interval} zile.",
        }]
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Destructive handlers: they only prepare a confirmation
# ──────────────────────────────────────────────────────────────────────────────

def _handle_delete_entry(payload: str) -> List[Dict[str, Any]]:
    tag = "DELETE_ENTRY"
    entry_id = _as_int(payload)
    if entry_id is None:
        return [_action_error(tag, f"expected a numeric entry id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute("SELECT id, title FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            return [_action_error(tag, f"entry #{entry_id} not found")]
        counts = _entry_dependency_counts(conn, entry_id)
        total = sum(counts.values())
        message = (
            f"Șterg intrarea #{entry_id} „{row['title']}”. "
            f"Se șterg {total} legături dependente ({_counts_phrase(counts)}). Confirmi?"
        )
        return [_pending(
            {"type": "delete_entry", "entry_id": entry_id},
            message,
            title=row["title"],
            links=counts,
        )]
    finally:
        conn.close()


def _handle_merge_entries(payload: str) -> List[Dict[str, Any]]:
    tag = "MERGE_ENTRIES"
    parts = [part.strip() for part in payload.split("|", 1)]
    keep_id = _as_int(parts[0] if parts else "")
    drop_id = _as_int(parts[1] if len(parts) > 1 else "")
    if keep_id is None or drop_id is None:
        return [_action_error(tag, f"expected keep_id and drop_id; expected format: {_TAG_FORMATS[tag]}")]
    if keep_id == drop_id:
        return [_action_error(tag, "keep_id and drop_id must be different")]
    conn = get_db()
    try:
        keep = conn.execute("SELECT id, title FROM entries WHERE id = ?", [keep_id]).fetchone()
        drop = conn.execute("SELECT id, title FROM entries WHERE id = ?", [drop_id]).fetchone()
        if not keep:
            return [_action_error(tag, f"keep entry #{keep_id} not found")]
        if not drop:
            return [_action_error(tag, f"drop entry #{drop_id} not found")]
        counts = _entry_dependency_counts(conn, drop_id)
        message = (
            f"Unesc intrarea #{drop_id} „{drop['title']}” în #{keep_id} „{keep['title']}”. "
            f"Mut pe #{keep_id}: {_counts_phrase(counts)}, apoi șterg #{drop_id}. Confirmi?"
        )
        return [_pending(
            {"type": "merge_entries", "keep_id": keep_id, "drop_id": drop_id},
            message,
            keep_id=keep_id,
            drop_id=drop_id,
            keep_title=keep["title"],
            drop_title=drop["title"],
            moved=counts,
        )]
    finally:
        conn.close()


def _handle_delete_relationship(payload: str) -> List[Dict[str, Any]]:
    tag = "DELETE_RELATIONSHIP"
    relationship_id = _as_int(payload)
    if relationship_id is None:
        return [_action_error(tag, f"expected a numeric relationship id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, from_id, to_id, label FROM relationships WHERE id = ?",
            [relationship_id],
        ).fetchone()
        if not row:
            return [_action_error(tag, f"relationship #{relationship_id} not found")]
        message = (
            f"Șterg legătura #{relationship_id}: "
            f"#{row['from_id']} --{row['label'] or 'related_to'}--> #{row['to_id']}. Confirmi?"
        )
        return [_pending({"type": "delete_relationship", "relationship_id": relationship_id}, message)]
    finally:
        conn.close()


def _handle_delete_theory(payload: str) -> List[Dict[str, Any]]:
    tag = "DELETE_THEORY"
    theory_id = _as_int(payload)
    if theory_id is None:
        return [_action_error(tag, f"expected a numeric theory id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute("SELECT id, name FROM theories WHERE id = ?", [theory_id]).fetchone()
        if not row:
            return [_action_error(tag, f"theory #{theory_id} not found")]
        evidence = conn.execute(
            "SELECT id FROM theory_evidence WHERE theory_id = ? ORDER BY id", [theory_id]
        ).fetchall()
        if evidence:
            ids = ", ".join(str(item["id"]) for item in evidence[:5])
            more = "…" if len(evidence) > 5 else ""
            return [_action_error(
                tag,
                f"theory #{theory_id} «{row['name']}» still has {len(evidence)} evidence links; "
                f"unlink them first with [UNLINK_STUDY: evidence_id] (evidence ids: {ids}{more})",
            )]
        message = f"Șterg teoria #{theory_id} «{row['name']}»? Nu are dovezi atașate. Confirmi?"
        return [_pending({"type": "delete_theory", "theory_id": theory_id}, message, name=row["name"])]
    finally:
        conn.close()


def _handle_unlink_study(payload: str) -> List[Dict[str, Any]]:
    tag = "UNLINK_STUDY"
    evidence_id = _as_int(payload)
    if evidence_id is None:
        return [_action_error(tag, f"expected a numeric evidence id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT te.id, te.theory_id, te.entry_id, te.stance,
                      t.name AS theory_name, e.title AS entry_title
                 FROM theory_evidence te
                 LEFT JOIN theories t ON t.id = te.theory_id
                 LEFT JOIN entries e ON e.id = te.entry_id
                WHERE te.id = ?""",
            [evidence_id],
        ).fetchone()
        if not row:
            return [_action_error(tag, f"evidence #{evidence_id} not found")]
        message = (
            f"Scos dovada #{evidence_id} (intrarea #{row['entry_id']} «{row['entry_title']}», "
            f"{row['stance']} pentru teoria #{row['theory_id']} «{row['theory_name']}»). Confirmi?"
        )
        return [_pending({"type": "unlink_study", "evidence_id": evidence_id}, message)]
    finally:
        conn.close()


def _handle_delete_todo(payload: str) -> List[Dict[str, Any]]:
    tag = "DELETE_TODO"
    todo_id = _as_int(payload)
    if todo_id is None:
        return [_action_error(tag, f"expected a numeric todo id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, entry_id, priority, completed FROM todos WHERE id = ?", [todo_id]
        ).fetchone()
        if not row:
            return [_action_error(tag, f"todo #{todo_id} not found")]
        message = f"Șterg taskul #{todo_id} (intrarea #{row['entry_id']}, prioritate {row['priority']}). Confirmi?"
        return [_pending({"type": "delete_todo", "todo_id": todo_id}, message)]
    finally:
        conn.close()


def _handle_delete_reminder(payload: str) -> List[Dict[str, Any]]:
    tag = "DELETE_REMINDER"
    reminder_id = _as_int(payload)
    if reminder_id is None:
        return [_action_error(tag, f"expected a numeric reminder id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, entry_id, title, trigger_at FROM reminders WHERE id = ?", [reminder_id]
        ).fetchone()
        if not row:
            return [_action_error(tag, f"reminder #{reminder_id} not found")]
        message = (
            f"Șterg memento-ul #{reminder_id} „{row['title']}” "
            f"pentru intrarea #{row['entry_id']} ({row['trigger_at']}). Confirmi?"
        )
        return [_pending({"type": "delete_reminder", "reminder_id": reminder_id}, message)]
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline triggers: parsed here, executed by the chat endpoints
# ──────────────────────────────────────────────────────────────────────────────

def _handle_index_pdf(payload: str) -> List[Dict[str, Any]]:
    tag = "INDEX_PDF"
    entry_id = _as_int(payload)
    if entry_id is None:
        return [_action_error(tag, f"expected a numeric entry id; expected format: {_TAG_FORMATS[tag]}")]
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            return [_action_error(tag, f"entry #{entry_id} not found")]
        return [{"type": "index_pdf_request", "entry_id": entry_id}]
    finally:
        conn.close()


def _handle_reanalyze_data(payload: str) -> List[Dict[str, Any]]:
    tag = "REANALYZE_DATA"
    mode = payload.strip().lower()
    if mode not in ("full", "issues"):
        return [_action_error(tag, f"mode must be full or issues, got '{mode[:20]}'; expected format: {_TAG_FORMATS[tag]}")]
    return [{"type": "reanalyze_request", "mode": mode}]


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────

_AI_WRITE_HANDLERS = (
    ("UPDATE_ENTRY", _handle_update_entry),
    ("WRITE_SECTION", _handle_write_section),
    ("UPDATE_PROJECT", _handle_update_project),
    ("SET_PLAN", _handle_set_plan),
    ("UPDATE_STUDY", _handle_update_study),
    ("ADD_STUDY", _handle_add_study),
    ("ADD_TAGS", _handle_add_tags),
    ("UPDATE_RELATIONSHIP", _handle_update_relationship),
    ("UPDATE_THEORY", _handle_update_theory),
    ("COMPLETE_TODO", _handle_complete_todo),
    ("SCHEDULE_REVIEW", _handle_schedule_review),
    ("DELETE_ENTRY", _handle_delete_entry),
    ("MERGE_ENTRIES", _handle_merge_entries),
    ("DELETE_RELATIONSHIP", _handle_delete_relationship),
    ("DELETE_THEORY", _handle_delete_theory),
    ("UNLINK_STUDY", _handle_unlink_study),
    ("DELETE_TODO", _handle_delete_todo),
    ("DELETE_REMINDER", _handle_delete_reminder),
    ("INDEX_PDF", _handle_index_pdf),
    ("REANALYZE_DATA", _handle_reanalyze_data),
)


# ──────────────────────────────────────────────────────────────────────────────
# Confirmation executors: only called from POST /api/chat/confirm
# ──────────────────────────────────────────────────────────────────────────────

from fastapi import HTTPException


def _exec_delete_entry(conn, action: Dict[str, Any]) -> Dict[str, Any]:
    entry_id = int(action["entry_id"])
    row = conn.execute("SELECT id, title FROM entries WHERE id = ?", [entry_id]).fetchone()
    if not row:
        raise HTTPException(404, f"Entry #{entry_id} not found")
    deleted = {
        "review_history": conn.execute(
            "DELETE FROM review_history WHERE item_id IN "
            "(SELECT id FROM spaced_repetition WHERE entry_id = ?)",
            [entry_id],
        ).rowcount,
    }
    for table in ("spaced_repetition", "todos", "reminders", "documents", "theory_evidence"):
        deleted[table] = conn.execute(
            f"DELETE FROM {table} WHERE entry_id = ?", [entry_id]
        ).rowcount
    deleted["relationships"] = conn.execute(
        "DELETE FROM relationships WHERE from_id = ? OR to_id = ?", [entry_id, entry_id]
    ).rowcount
    conn.execute("DELETE FROM entries WHERE id = ?", [entry_id])
    conn.commit()
    summary = ", ".join(f"{value} {name}" for name, value in deleted.items() if value)
    return {
        "type": "entry_deleted",
        "entry_id": entry_id,
        "title": row["title"],
        "deleted": deleted,
        "message": f"Am șters intrarea #{entry_id} „{row['title']}” ({summary or 'fără dependențe'}).",
    }


def _exec_merge_entries(conn, action: Dict[str, Any]) -> Dict[str, Any]:
    keep_id, drop_id = int(action["keep_id"]), int(action["drop_id"])
    if keep_id == drop_id:
        raise HTTPException(400, "keep_id and drop_id must be different")
    keep = conn.execute("SELECT id, title FROM entries WHERE id = ?", [keep_id]).fetchone()
    drop = conn.execute("SELECT id, title FROM entries WHERE id = ?", [drop_id]).fetchone()
    if not keep:
        raise HTTPException(404, f"Entry #{keep_id} not found")
    if not drop:
        raise HTTPException(404, f"Entry #{drop_id} not found")

    moved: Dict[str, int] = {}
    skipped: Dict[str, int] = {}

    def bump(bucket: Dict[str, int], key: str, amount: int = 1) -> None:
        bucket[key] = bucket.get(key, 0) + amount

    # Relationships: substitute the dropped id, drop UNIQUE collisions and
    # self-links (an edge keep->drop becomes keep->keep, which is not a link).
    for rel in conn.execute(
        "SELECT id, from_id, to_id, label FROM relationships WHERE from_id = ? OR to_id = ?",
        [drop_id, drop_id],
    ).fetchall():
        new_from = keep_id if rel["from_id"] == drop_id else rel["from_id"]
        new_to = keep_id if rel["to_id"] == drop_id else rel["to_id"]
        conn.execute("DELETE FROM relationships WHERE id = ?", [rel["id"]])
        if new_from == new_to:
            bump(skipped, "relationships_self")
            continue
        cursor = conn.execute(
            "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
            [new_from, new_to, rel["label"]],
        )
        if cursor.rowcount:
            bump(moved, "relationships")
        else:
            bump(skipped, "relationships_duplicate")

    # Theory evidence: UNIQUE(theory_id, entry_id, stance).
    for evidence in conn.execute(
        "SELECT id, theory_id, stance, note, weight FROM theory_evidence WHERE entry_id = ?",
        [drop_id],
    ).fetchall():
        conn.execute("DELETE FROM theory_evidence WHERE id = ?", [evidence["id"]])
        cursor = conn.execute(
            """INSERT OR IGNORE INTO theory_evidence (theory_id, entry_id, stance, note, weight)
               VALUES (?, ?, ?, ?, ?)""",
            [evidence["theory_id"], keep_id, evidence["stance"], evidence["note"], evidence["weight"]],
        )
        if cursor.rowcount:
            bump(moved, "theory_evidence")
        else:
            bump(skipped, "theory_evidence_duplicate")

    bump(moved, "documents", conn.execute(
        "UPDATE documents SET entry_id = ? WHERE entry_id = ?", [keep_id, drop_id]
    ).rowcount)

    # Todos allow a single row per entry, so two rows must collapse into one.
    keep_todo = conn.execute(
        "SELECT id, due_date FROM todos WHERE entry_id = ?", [keep_id]
    ).fetchone()
    drop_todo = conn.execute(
        "SELECT id, due_date FROM todos WHERE entry_id = ?", [drop_id]
    ).fetchone()
    if drop_todo:
        if keep_todo:
            if not (keep_todo["due_date"] or "").strip() and (drop_todo["due_date"] or "").strip():
                conn.execute("UPDATE todos SET due_date = ? WHERE id = ?", [drop_todo["due_date"], keep_todo["id"]])
            conn.execute("DELETE FROM todos WHERE id = ?", [drop_todo["id"]])
            bump(skipped, "todos_merged")
        else:
            conn.execute("UPDATE todos SET entry_id = ? WHERE id = ?", [keep_id, drop_todo["id"]])
            bump(moved, "todos")

    bump(moved, "reminders", conn.execute(
        "UPDATE reminders SET entry_id = ? WHERE entry_id = ?", [keep_id, drop_id]
    ).rowcount)

    # Spaced repetition allows a single row per entry; review history follows
    # the item id, so it is re-pointed before the dropped item disappears.
    keep_item = conn.execute(
        "SELECT id FROM spaced_repetition WHERE entry_id = ?", [keep_id]
    ).fetchone()
    drop_item = conn.execute(
        "SELECT id FROM spaced_repetition WHERE entry_id = ?", [drop_id]
    ).fetchone()
    if drop_item:
        if keep_item:
            bump(moved, "review_history", conn.execute(
                "UPDATE review_history SET item_id = ? WHERE item_id = ?",
                [keep_item["id"], drop_item["id"]],
            ).rowcount)
            conn.execute("DELETE FROM spaced_repetition WHERE id = ?", [drop_item["id"]])
            bump(skipped, "spaced_repetition_merged")
        else:
            conn.execute("UPDATE spaced_repetition SET entry_id = ? WHERE id = ?", [keep_id, drop_item["id"]])
            bump(moved, "spaced_repetition")
            bump(moved, "review_history", conn.execute(
                "SELECT COUNT(*) FROM review_history WHERE item_id = ?", [drop_item["id"]]
            ).fetchone()[0])

    conn.execute("DELETE FROM entries WHERE id = ?", [drop_id])
    conn.commit()
    moved_text = ", ".join(f"{value} {name}" for name, value in moved.items() if value) or "nimic"
    skipped_text = ", ".join(f"{value} {name}" for name, value in skipped.items() if value) or "nimic"
    return {
        "type": "entries_merged",
        "keep_id": keep_id,
        "drop_id": drop_id,
        "keep_title": keep["title"],
        "drop_title": drop["title"],
        "moved": moved,
        "skipped": skipped,
        "message": (
            f"Am unit intrarea #{drop_id} „{drop['title']}” în #{keep_id} „{keep['title']}”. "
            f"Mutate: {moved_text}; sărite ca duplicate: {skipped_text}."
        ),
    }


def _exec_delete_relationship(conn, action: Dict[str, Any]) -> Dict[str, Any]:
    relationship_id = int(action["relationship_id"])
    row = conn.execute(
        "SELECT id, from_id, to_id, label FROM relationships WHERE id = ?", [relationship_id]
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Relationship #{relationship_id} not found")
    conn.execute("DELETE FROM relationships WHERE id = ?", [relationship_id])
    conn.commit()
    return {
        "type": "relationship_deleted",
        "relationship_id": relationship_id,
        "message": f"Am șters legătura #{relationship_id}.",
    }


def _exec_delete_theory(conn, action: Dict[str, Any]) -> Dict[str, Any]:
    theory_id = int(action["theory_id"])
    row = conn.execute("SELECT id, name FROM theories WHERE id = ?", [theory_id]).fetchone()
    if not row:
        raise HTTPException(404, f"Theory #{theory_id} not found")
    evidence = conn.execute(
        "SELECT COUNT(*) FROM theory_evidence WHERE theory_id = ?", [theory_id]
    ).fetchone()[0]
    if evidence:
        raise HTTPException(
            409,
            f"Theory #{theory_id} still has {evidence} evidence links; unlink them first with UNLINK_STUDY",
        )
    conn.execute("DELETE FROM theories WHERE id = ?", [theory_id])
    conn.commit()
    return {
        "type": "theory_deleted",
        "theory_id": theory_id,
        "name": row["name"],
        "message": f"Am șters teoria #{theory_id} «{row['name']}».",
    }


def _exec_unlink_study(conn, action: Dict[str, Any]) -> Dict[str, Any]:
    evidence_id = int(action["evidence_id"])
    row = conn.execute(
        "SELECT id, theory_id, entry_id FROM theory_evidence WHERE id = ?", [evidence_id]
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Evidence #{evidence_id} not found")
    conn.execute("DELETE FROM theory_evidence WHERE id = ?", [evidence_id])
    conn.commit()
    return {
        "type": "study_unlinked",
        "evidence_id": evidence_id,
        "theory_id": row["theory_id"],
        "entry_id": row["entry_id"],
        "message": f"Am scos dovada #{evidence_id}.",
    }


def _exec_delete_todo(conn, action: Dict[str, Any]) -> Dict[str, Any]:
    todo_id = int(action["todo_id"])
    row = conn.execute("SELECT id, entry_id FROM todos WHERE id = ?", [todo_id]).fetchone()
    if not row:
        raise HTTPException(404, f"Todo #{todo_id} not found")
    conn.execute("DELETE FROM todos WHERE id = ?", [todo_id])
    conn.commit()
    return {
        "type": "todo_deleted",
        "todo_id": todo_id,
        "entry_id": row["entry_id"],
        "message": f"Am șters taskul #{todo_id}.",
    }


def _exec_delete_reminder(conn, action: Dict[str, Any]) -> Dict[str, Any]:
    reminder_id = int(action["reminder_id"])
    row = conn.execute("SELECT id, entry_id FROM reminders WHERE id = ?", [reminder_id]).fetchone()
    if not row:
        raise HTTPException(404, f"Reminder #{reminder_id} not found")
    conn.execute("DELETE FROM reminders WHERE id = ?", [reminder_id])
    conn.commit()
    return {
        "type": "reminder_deleted",
        "reminder_id": reminder_id,
        "entry_id": row["entry_id"],
        "message": f"Am șters memento-ul #{reminder_id}.",
    }


_CONFIRM_EXECUTORS = {
    "delete_entry": _exec_delete_entry,
    "merge_entries": _exec_merge_entries,
    "delete_relationship": _exec_delete_relationship,
    "delete_theory": _exec_delete_theory,
    "unlink_study": _exec_unlink_study,
    "delete_todo": _exec_delete_todo,
    "delete_reminder": _exec_delete_reminder,
}