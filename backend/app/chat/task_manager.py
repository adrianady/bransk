"""Chat task management - persisted multi-step workflows across turns."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from app.db import get_db

logger = logging.getLogger("chronokg.chat.tasks")

# ──────────────────────────────────────────────────────────────────────────────
# Pending destructive actions awaiting explicit user confirmation
# ──────────────────────────────────────────────────────────────────────────────
_PENDING_ACTIONS: dict[str, dict] = {}
_PENDING_TTL_SECONDS = 300.0


def _store_pending_action(action: dict) -> str:
    """Record a destructive action pending user confirmation. Returns a nonce."""
    import secrets
    import time
    nonce = secrets.token_hex(8)
    now = time.monotonic()
    for key in [k for k, v in _PENDING_ACTIONS.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]:
        _PENDING_ACTIONS.pop(key, None)
    _PENDING_ACTIONS[nonce] = {"action": action, "created_at": now}
    return nonce


# ──────────────────────────────────────────────────────────────────────────────
# Task kinds and labels
# ──────────────────────────────────────────────────────────────────────────────
_TASK_KIND_LABELS = {
    "research": "research / meta-analysis",
    "download": "PDF acquisition",
    "screening": "study screening (PRISMA)",
    "drafting": "thesis drafting",
    "task": "multi-step task",
}


def _task_row_to_dict(row) -> Dict[str, Any]:
    task = dict(row)
    try:
        task["progress"] = json.loads(task.pop("progress_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        task["progress"] = {}
    return task


def _get_active_task() -> Optional[Dict[str, Any]]:
    """The task the assistant is currently in the middle of, if any.

    An `active` row always wins. A `failed` row from the last week is still
    shown so the user can ask "încearcă din nou" without restating the goal.
    """
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT * FROM chat_tasks
                WHERE status = 'active'
                   OR (status = 'failed' AND updated_at >= datetime('now', '-7 days'))
                ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,
                         updated_at DESC, id DESC
                LIMIT 1"""
        ).fetchone()
        return _task_row_to_dict(row) if row else None
    except sqlite3.Error as exc:
        logger.info("Active chat task unavailable: %s", exc)
        return None
    finally:
        conn.close()


def _begin_task(kind: str, goal: str, details: str = "") -> Optional[Dict[str, Any]]:
    """Start (or refresh) the active workflow. Previous active work is cancelled."""
    goal = " ".join((goal or "").split())[:500]
    if not goal:
        return None
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM chat_tasks WHERE status = 'active' ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if existing and existing["kind"] == kind:
            old_goal = " ".join((existing["goal"] or "").lower().split())
            new_goal = goal.lower()
            if old_goal == new_goal or old_goal in new_goal or new_goal in old_goal:
                conn.execute(
                    "UPDATE chat_tasks SET details = ?, updated_at = datetime('now') WHERE id = ?",
                    [(details or existing["details"] or "")[:2000], existing["id"]],
                )
                conn.commit()
                row = conn.execute("SELECT * FROM chat_tasks WHERE id = ?", [existing["id"]]).fetchone()
                return _task_row_to_dict(row)
        conn.execute(
            "UPDATE chat_tasks SET status = 'cancelled', updated_at = datetime('now') WHERE status = 'active'"
        )
        cur = conn.execute(
            "INSERT INTO chat_tasks (kind, goal, status, details) VALUES (?, ?, 'active', ?)",
            [kind[:40], goal, (details or "")[:2000]],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM chat_tasks WHERE id = ?", [cur.lastrowid]).fetchone()
        logger.info("Chat task #%s started: %s — %s", cur.lastrowid, kind, goal[:120])
        return _task_row_to_dict(row)
    except sqlite3.Error as exc:
        logger.warning("Could not record chat task: %s", exc)
        return None
    finally:
        conn.close()


def _update_task(
    *,
    progress: Optional[Dict[str, Any]] = None,
    details: Optional[str] = None,
    kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Merge progress into the active task; never raises."""
    task = _get_active_task()
    if not task or (kind and task["kind"] != kind):
        return task
    merged = dict(task.get("progress") or {})
    if progress:
        merged.update(progress)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE chat_tasks SET progress_json = ?, details = ?, updated_at = datetime('now') WHERE id = ?",
            [
                json.dumps(merged, ensure_ascii=False),
                (details if details is not None else task.get("details") or "")[:2000],
                task["id"],
            ],
        )
        conn.commit()
        return _get_active_task()
    except sqlite3.Error as exc:
        logger.info("Could not update chat task: %s", exc)
        return task
    finally:
        conn.close()


def _finish_task(
    status: str = "done",
    *,
    progress: Optional[Dict[str, Any]] = None,
    kind: Optional[str] = None,
    details: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Close the active task (done / failed / cancelled)."""
    task = _get_active_task()
    if not task or (kind and task["kind"] != kind):
        return task
    merged = dict(task.get("progress") or {})
    if progress:
        merged.update(progress)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE chat_tasks SET status = ?, progress_json = ?, details = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            [
                status,
                json.dumps(merged, ensure_ascii=False),
                (details if details is not None else task.get("details") or "")[:2000],
                task["id"],
            ],
        )
        conn.commit()
        logger.info("Chat task #%s finished: %s", task["id"], status)
        return _task_row_to_dict(
            conn.execute("SELECT * FROM chat_tasks WHERE id = ?", [task["id"]]).fetchone()
        )
    except sqlite3.Error as exc:
        logger.info("Could not finish chat task: %s", exc)
        return task
    finally:
        conn.close()


def _active_task_context() -> str:
    """Prompt block that keeps the current workflow in front of the model."""
    task = _get_active_task()
    if not task:
        return (
            "=== ACTIVE CHAT TASK ===\n"
            "No persistent task is active. If the user starts a multi-step workflow "
            "(research, batch PDF download, screening, drafting), the app records it "
            "and it will be shown here on later turns.\n"
            "=== END ACTIVE CHAT TASK ===\n\n"
        )
    progress = task.get("progress") or {}
    progress_line = "; ".join(f"{k}={v}" for k, v in progress.items()) or "(no progress recorded yet)"
    state = {
        "active": "IN PROGRESS",
        "failed": "LAST ATTEMPT FAILED — offer to retry",
    }.get(task["status"], task["status"])
    return (
        "=== ACTIVE CHAT TASK (persisted — this outlives chat history) ===\n"
        f"Kind: {_TASK_KIND_LABELS.get(task['kind'], task['kind'])} | Status: {state}\n"
        f"Goal: {task['goal']}\n"
        f"Progress: {progress_line}\n"
        f"Updated: {task['updated_at']}\n"
        "This task is still open from earlier turns. Continue it unless the user "
        "changes the subject or explicitly stops it; do not ask the user to restate "
        "the goal. If the user asks what you are working on, summarize this task and "
        "its progress. Completion is recorded by the app when the workflow ends.\n"
        "=== END ACTIVE CHAT TASK ===\n\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Workflow detection from natural language
# ──────────────────────────────────────────────────────────────────────────────
_TASK_DONE_RE = None  # will be set from nlp
_TASK_CONTINUE_RE = None
_TASK_DRAFT_RE = None
_TASK_SCREEN_RE = None

_DOWNLOAD_BATCH_VERB_RE = None
_DOWNLOAD_BATCH_SCOPE_RE = None
_DOWNLOAD_TARGET_RE = None
_DOWNLOAD_STRONG_VERB_RE = None
_DOWNLOAD_FILE_TARGET_RE = None
_KB_SCOPE_RE = None
_RESEARCH_VERBS = None


def _init_regex():
    """Lazy init regex from nlp module."""
    global _TASK_DONE_RE, _TASK_CONTINUE_RE, _TASK_DRAFT_RE, _TASK_SCREEN_RE
    global _DOWNLOAD_BATCH_VERB_RE, _DOWNLOAD_BATCH_SCOPE_RE, _DOWNLOAD_TARGET_RE
    global _DOWNLOAD_STRONG_VERB_RE, _DOWNLOAD_FILE_TARGET_RE
    global _KB_SCOPE_RE, _RESEARCH_VERBS

    if _TASK_DONE_RE is not None:
        return

    from app.nlp import (
        RESEARCH_VERBS_RE,
        KB_SCOPE_RE,
        DOWNLOAD_BATCH_VERB_RE,
        DOWNLOAD_BATCH_SCOPE_RE,
        DOWNLOAD_STRONG_VERB_RE,
        DOWNLOAD_TARGET_RE,
        DOWNLOAD_FILE_TARGET_RE,
        TASK_SCREEN_RE,
    )

    _RESEARCH_VERBS = RESEARCH_VERBS_RE
    _KB_SCOPE_RE = KB_SCOPE_RE
    _DOWNLOAD_BATCH_VERB_RE = DOWNLOAD_BATCH_VERB_RE
    _DOWNLOAD_BATCH_SCOPE_RE = DOWNLOAD_BATCH_SCOPE_RE
    _DOWNLOAD_STRONG_VERB_RE = DOWNLOAD_STRONG_VERB_RE
    _DOWNLOAD_TARGET_RE = DOWNLOAD_TARGET_RE
    _DOWNLOAD_FILE_TARGET_RE = DOWNLOAD_FILE_TARGET_RE
    _TASK_SCREEN_RE = TASK_SCREEN_RE

    _TASK_DONE_RE = re.compile(
        r"^\s*(?:gata|terminat|am\s+terminat|e\s+gata|done|finished|stop|opre[șs]te|"
        r"anuleaz[aă]\s+(?:taskul|sarcina))\b",
        re.IGNORECASE,
    )
    _TASK_CONTINUE_RE = re.compile(
        r"\b(continu[aă]|mai\s+departe|next\s+step|continue|mergi\s+mai\s+departe|"
        r"[șs]i\s+apoi|apoi\s+f[aă])\b",
        re.IGNORECASE,
    )
    _TASK_DRAFT_RE = re.compile(
        r"\b(scrie|redacteaz[aă]|draft|write|completeaz[aă])\b"
        r"[\s\S]{0,60}\b(sec[țt]iune|capitol|section|chapter|tez[aă]|thesis|introducere|"
        r"concluzii|metodologie|discu[țt]ii|abstract|rezumat)\b",
        re.IGNORECASE,
    )


def _detect_research_intent(message: str) -> Optional[str]:
    """If the user explicitly asks for research/studies but the model forgot the
    [RESEARCH:] action, extract a topic so we can run the pipeline anyway."""
    _init_regex()
    if _KB_SCOPE_RE.search(message):
        return None
    if not re.search(
        r"\b(research|stud(?:y|ies|i\w*)|paper\w*|articol\w*|cercetar\w*|"
        r"lucr[aăâ]r\w*|literatur\w*)\b",
        message,
        re.IGNORECASE,
    ):
        return None
    if not (_RESEARCH_VERBS.search(message) or re.match(r"^\s*(research|cerceteaz[aăâ])\b", message, re.IGNORECASE)):
        return None

    cleaned = message.strip()
    for phrase in (
        "te rog să cauți", "te rog sa cauti", "te rog să găsești", "te rog sa gasesti",
        "te rog caută", "te rog cauta", "caută te rog", "cauta te rog",
        "vreau să cauți", "vreau sa cauti", "vreau să găsești", "vreau sa gasesti",
        "găsește-mi", "gaseste-mi", "caută-mi", "cauta-mi", "descarcă-mi", "descarca-mi",
        "vreau să descarci", "vreau sa descarci", "vreau sa descarce", "vreau să descarce",
        "i want you to", "i would like you to", "could you please", "can you please",
        "please", "pls", "could you", "can you", "would you", "i want", "i need",
        "help me", "you",
    ):
        cleaned = re.sub(rf"^{re.escape(phrase)}\b[\s,:]*", "", cleaned, flags=re.IGNORECASE)

    ro_match = re.search(
        r"(?:(?:caut[aăâ]|g[aăâ]se[sșş]te|desc[aăâ]rc[aăâe]|adun[aăâ]|str[aâ]nge|cerceteaz[aăâ]|vreau)\s*(?:[sșş]i\s*(?:desc[aăâ]rc[aăâe]|salveaz[aăâ]|caut[aăâ]|g[aăâ]se[sșş]te))*\s*)?(?:studii(?:le)?|articole(?:le)?|lucr[aăâ]ri(?:le)?|cercetare|cercet[aăâ]ri(?:le)?|tema)\s*(?:[sșş]i\s*(?:desc[aăâ]rc[aăâe]|salveaz[aăâ]|caut[aăâ]))*\s*(?:despre|pentru|privind|pe\s*tema|legate\s*de|referitoare\s*la|cu\s*privire\s*la|asupra)?\s*[:\\-]?\s*(.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if ro_match:
        topic = ro_match.group(1).strip(" :.,!?")
        topic = re.sub(r"^(?:(?:[sșş]i|si)\s+)?(?:desc[aăâ]rc[aăâe]|salveaz[aăâ]|caut[aăâ])\s*(?:despre|pentru|privind|pe\s*tema|asupra)?\s*[:\\-]?\s*", "", topic, flags=re.IGNORECASE)
        topic = re.sub(r"\s+(?:[sșş]i|si|and)?\s*(?:desc[aăâ]rc[aăâe]-le|download them|salveaz[aăâ]-le|full\s*text).*$", "", topic, flags=re.IGNORECASE)
        topic = topic.strip(" :.,!?\\\"'")
        if len(topic) >= 3:
            return topic[:200]

    for kw in (
        "research for studies regarding",
        "research for studies about",
        "research for studies on",
        "research regarding",
        "research about",
        "research on",
        "research for",
        "studies regarding",
        "studies about",
        "studies on",
        "find studies about",
        "find studies on",
        "search studies on",
        "search studies about",
        "research",
    ):
        idx = cleaned.lower().find(kw)
        if idx != -1:
            topic = cleaned[idx + len(kw):].strip(" :.,!?")
            topic = __import__("re").sub(r"\s+(?:and)?\s*(?:download them|save them).*$", "", topic, flags=__import__("re").IGNORECASE)
            if topic:
                return topic[:200]
    return None


def _detect_workflow_task(message: str) -> Optional[Dict[str, Any]]:
    """Record a multi-step workflow the user starts in natural language."""
    _init_regex()
    text = (message or "").strip()
    if not text:
        return None
    if _TASK_DONE_RE.match(text):
        return _finish_task("done", details="User marked the task as finished.")
    
    # Check for explicit new workflow triggers first
    # Check for screening first - it has specific Romanian verbs that should take precedence
    if _TASK_SCREEN_RE.search(text) and re.search(
        r"\b(studi\w*|studies|papers?|articol\w*|bibliotec\w*|library|rezultat\w*|results?)\b",
        text,
        re.IGNORECASE,
    ):
        return _begin_task("screening", "screen the study library", details=text)

    # Check for research intent
    research_topic = _detect_research_intent(text)
    if research_topic:
        return _begin_task("research", research_topic, details=text)

    if (
        _DOWNLOAD_BATCH_VERB_RE.search(text)
        and _DOWNLOAD_BATCH_SCOPE_RE.search(text)
        and _DOWNLOAD_TARGET_RE.search(text)
        and (_DOWNLOAD_STRONG_VERB_RE.search(text) or _DOWNLOAD_FILE_TARGET_RE.search(text))
    ):
        return _begin_task("download", "download all missing study PDFs", details=text)

    if _TASK_DRAFT_RE.search(text):
        return _begin_task("drafting", text[:300], details=text)

    # If no new workflow detected, check for continuation of existing task
    task = _get_active_task()
    if task and _TASK_CONTINUE_RE.search(text):
        return _update_task(details=task.get("details") or text)

    if _TASK_DONE_RE.match(text):
        return _finish_task("done", details="User marked the task as finished.")

    return task