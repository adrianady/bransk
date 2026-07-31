"""
ChronoKG Chat — auto-failover provider, expanded actions, chat persistence, health endpoint.
"""
import json
import logging
import re
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import anyio
from app.db import get_db
from app.llm import LLM_PROVIDER, check_deepseek, complete as llm_complete, ping_ollama, stream_complete
from app.models import ChatRequest
from app.routers.research import run_research
from app.routers.search import _sanitize_fts5
from app.web_search import search_web

logger = logging.getLogger("chronokg.chat")
router = APIRouter()


# ── Provider health endpoint ──

@router.get("/chat/health")
async def chat_health():
    """Check both providers' availability and return current active provider."""
    deepseek_ok = False
    ollama_ok = False
    deepseek_ms = 0
    ollama_ms = 0

    import time
    try:
        t0 = time.time()
        r = await ping_ollama()
        ollama_ms = int((time.time() - t0) * 1000)
        ollama_ok = r
    except Exception:
        ollama_ok = False

    try:
        t0 = time.time()
        deepseek_ok = await check_deepseek()
        deepseek_ms = int((time.time() - t0) * 1000)
    except Exception:
        deepseek_ok = False

    if deepseek_ok:
        active = "deepseek"
    elif ollama_ok:
        active = "ollama"
    else:
        active = "none"

    return {
        "deepseek": {"available": deepseek_ok, "latency_ms": deepseek_ms},
        "ollama": {"available": ollama_ok, "latency_ms": ollama_ms},
        "active": active
    }


# ── Chat endpoints ──

_READ_ACTION_TYPES = {"search_results", "timeline_results", "entry_fetched", "entries_listed", "relationships_listed", "web_results"}


@router.post("/chat", response_model=dict)
async def chat(data: ChatRequest):
    """Non-streaming chat with auto-failover."""
    try:
        provider, model, reply = await _call_with_fallback(data.message, data.history)
        # Persist chat
        _save_chat("user", data.message, provider, model)
        # Parse actions
        actions = _parse_actions(reply)
        research_req = next((a for a in actions if a["type"] == "research_request"), None)
        if research_req:
            result = await run_research(research_req["topic"], depth="quick")
            actions.append({"type": "research_done", "result": result.model_dump()})
        # Execute web searches requested by the model
        for wr in [a for a in actions if a["type"] == "web_search_request"]:
            results = await search_web(wr["query"], max_results=5)
            actions.append({"type": "web_results", "query": wr["query"], "results": results})
        # Agent read-loop: feed tool results back so the model can answer from them
        read_actions = [a for a in actions if a["type"] in _READ_ACTION_TYPES and (a.get("entry") or a.get("results"))]
        if read_actions:
            try:
                final = await _answer_with_tools(data.message, data.history, _build_context(data.message), reply, read_actions)
                reply = final
            except Exception as exc:
                logger.error(f"Follow-up synthesis failed: {exc}")
        _save_chat("assistant", reply, provider, model)
        return {"reply": reply, "provider": provider, "model": model, "actions": actions}
    except Exception as exc:
        logger.error(f"Chat failed: {exc}")
        raise HTTPException(status_code=503, detail="Chat unavailable. Check the server logs for details.")


@router.post("/chat/stream")
async def chat_stream(data: ChatRequest):
    """SSE streaming chat with auto-failover and research actions."""
    async def generate():
        provider, model, full_reply = "unknown", "", ""
        try:
            context = await anyio.to_thread.run_sync(_build_context, data.message)
            yield f"data: {json.dumps({'type': 'context', 'entries': len(context)})}\n\n"

            async for piece in _stream_with_fallback(data.message, data.history, context):
                if provider == "unknown":
                    provider, model = piece[0], piece[1]
                    yield f"data: {json.dumps({'type': 'provider', 'provider': provider, 'model': model})}\n\n"
                full_reply += piece[2]
                yield f"data: {json.dumps({'type': 'chunk', 'content': piece[2]})}\n\n"

            if not full_reply:
                raise RuntimeError("LLM returned an empty reply")

            actions = _parse_actions(full_reply)
            research_req = next((a for a in actions if a["type"] == "research_request"), None)
            if research_req:
                yield f"data: {json.dumps({'type': 'progress', 'phase': 'research', 'message': 'Starting qualitative research...'})}\n\n"
                try:
                    result = await run_research(research_req["topic"], depth="quick")
                    yield f"data: {json.dumps({'type': 'research', 'result': result.model_dump()})}\n\n"
                    actions.append({"type": "research_done", "result": result.model_dump()})
                except Exception as rexc:
                    logger.error(f"Research action failed: {rexc}")
                    actions.append({"type": "error", "detail": f"Research failed: {type(rexc).__name__}"})

            # Execute web searches requested by the model
            for wr in [a for a in actions if a["type"] == "web_search_request"]:
                results = await search_web(wr["query"], max_results=5)
                actions.append({"type": "web_results", "query": wr["query"], "results": results})

            # Agent read-loop: feed tool results back and stream the grounded answer
            read_actions = [a for a in actions if a["type"] in _READ_ACTION_TYPES and (a.get("entry") or a.get("results"))]
            final_reply = full_reply
            if read_actions:
                n_results = sum(len(a.get("results") or []) for a in read_actions) + sum(1 for a in read_actions if a.get("entry"))
                yield f"data: {json.dumps({'type': 'tool', 'message': f'Read {n_results} records from your knowledge base / the web to answer.', 'actions': [a['type'] for a in read_actions]})}\n\n"
                try:
                    final_reply = await _answer_with_tools(data.message, data.history, context, full_reply, read_actions)
                    for piece in _chunk_text(final_reply, 120):
                        yield f"data: {json.dumps({'type': 'chunk', 'content': piece})}\n\n"
                except Exception as rexc:
                    logger.error(f"Follow-up synthesis failed: {rexc}")

            # Persist (final reply)
            _save_chat("user", data.message, provider, model)
            _save_chat("assistant", final_reply, provider, model)

            if actions:
                yield f"data: {json.dumps({'type': 'actions', 'content': actions})}\n\n"
        except Exception as exc:
            logger.error(f"Chat stream error: {exc}")
            yield f"data: {json.dumps({'type': 'error', 'content': f'LLM Error: {type(exc).__name__}'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Chat history ──

@router.get("/chat/history")
async def get_chat_history(limit: int = 50):
    """Get recent chat messages."""
    if limit > 200: limit = 200
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT role, content, provider, model, created_at FROM chat_messages ORDER BY id DESC LIMIT ?",
            [limit]
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


@router.delete("/chat/history", status_code=204)
async def clear_chat_history():
    """Clear chat history."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM chat_messages")
        conn.commit()
    finally:
        conn.close()


def _save_chat(role: str, content: str, provider: str = "", model: str = ""):
    """Save a chat message to the database."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO chat_messages (role, content, provider, model) VALUES (?, ?, ?, ?)",
            [role, content[:10000], provider, model]
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save chat: {e}")
    finally:
        conn.close()


# ── Auto-failover provider ──

async def _call_with_fallback(message: str, history: list) -> tuple:
    """Build the prompt and complete via the shared provider layer."""
    context = _build_context(message)

    if LLM_PROVIDER == "mock":
        return ("mock", "mock-model", _mock_reply(message))

    messages = _build_messages(message, history, context)
    try:
        return await llm_complete(messages, temperature=0.7, max_tokens=4096)
    except Exception as exc:
        logger.error(f"LLM unavailable: {exc}")
        raise RuntimeError("Both DeepSeek and Ollama unavailable") from exc


async def _stream_with_fallback(message: str, history: list, context: list[dict]):
    """Async generator yielding (provider, model, chunk) tuples."""
    if LLM_PROVIDER == "mock":
        for piece in _mock_stream_chunks(message):
            yield ("mock", "mock-model", piece)
        return
    messages = _build_messages(message, history, context)
    async for piece in stream_complete(messages, temperature=0.7, max_tokens=4096):
        yield piece


def _build_messages(message: str, history: list, context: list[dict]) -> list:
    """Assemble system + history + user messages for the LLM."""
    system_prompt = _build_system_prompt(context, "ChronoKG", "auto")
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:
        messages.append(_extract_history_item(h))
    messages.append({"role": "user", "content": message})
    return messages


def _mock_reply(message: str) -> str:
    if "research" in message.lower():
        topic = re.sub(r"research", "", message, count=1, flags=re.IGNORECASE)
        topic = re.sub(r"^[\s,:\-]+|[\s,:\-]+$", "", topic) or "the topic"
        return f"I'll run a qualitative research pipeline on this topic.\n\n[RESEARCH: {topic}]"
    return (
        f"I can help with that: \"{message}\".\n\n"
        "To turn this into rigorous qualitative research, I can plan sub-questions, "
        "extract key studies with critical appraisal, and save them as dated, graph-linked entries. "
        "Ask me to research any topic deeply."
    )


def _mock_stream_chunks(message: str):
    parts = _mock_reply(message).split(" ")
    for i in range(0, len(parts), 4):
        yield " ".join(parts[i : i + 4]) + " "


def _tool_results_text(read_actions: list) -> str:
    lines = []
    for a in read_actions:
        if a["type"] == "entry_fetched" and a.get("entry"):
            e = a["entry"]
            lines.append(
                f"ENTRY #{e['id']}: {e['title']} ({e['entry_date'] or 'no date'}) tags={e['tags']}\n{(e.get('content') or '')[:2000]}"
            )
        elif a["type"] in ("entries_listed", "search_results", "timeline_results"):
            for r in a.get("results", []):
                lines.append(f"- [{r.get('id')}] {r.get('title')} ({r.get('entry_date') or 'no date'}) tags={r.get('tags', '')}")
        elif a["type"] == "relationships_listed":
            for r in a.get("results", []):
                lines.append(f"- relationship {r.get('id')}: {r.get('from_id')} --{r.get('label') or 'related_to'}--> {r.get('to_id')}")
        elif a["type"] == "web_results":
            for r in a.get("results", []):
                lines.append(f"- {r.get('title')} | {r.get('url')} | {(r.get('snippet') or '')[:300]}")
    return "\n".join(lines) or "(no results found)"


async def _answer_with_tools(message: str, history: list, context: list[dict], first_reply: str, read_actions: list) -> str:
    """Second-pass grounded answer using tool results (mini agent loop)."""
    system_prompt = _build_system_prompt(context, "assistant", "tool-use")
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:
        messages.append(_extract_history_item(h))
    messages.append({"role": "user", "content": message})
    if first_reply:
        messages.append({"role": "assistant", "content": first_reply[:4000]})
    messages.append(
        {
            "role": "user",
            "content": (
                "TOOL RESULTS:\n"
                f"{_tool_results_text(read_actions)}\n\n"
                "Now answer the user's original question using these results. "
                "Be concise, cite entries by title and date, and do NOT emit any action tags."
            ),
        }
    )
    if LLM_PROVIDER == "mock":
        return "Based on the retrieved information, here is the answer to your question. The records were found in your knowledge base."
    _, _, text = await llm_complete(messages, temperature=0.4, max_tokens=2048)
    return text


def _chunk_text(text: str, size: int = 120):
    words = text.split(" ")
    for i in range(0, len(words), size):
        yield " ".join(words[i : i + size]) + " "


# ── Context building ──

def _build_context(message: str) -> list[dict]:
    """Fetch relevant entries from the KB."""
    conn = get_db()
    try:
        context, seen = [], set()

        # Primary retrieval: FTS5 ranked search (semantic-ish keyword match)
        try:
            safe = _sanitize_fts5(message)
            rows = conn.execute(
                """SELECT e.id, e.title, e.content, e.entry_date, e.tags, e.importance
                   FROM entries_fts JOIN entries e ON entries_fts.rowid = e.id
                   WHERE e.archived = 0 AND entries_fts MATCH ?
                   ORDER BY rank LIMIT 10""",
                [safe],
            ).fetchall()
            for r in rows:
                if len(context) >= 10:
                    break
                seen.add(r["id"])
                context.append({"id": r["id"], "title": r["title"], "content": r["content"][:500], "date": r["entry_date"] or "unknown", "tags": r["tags"], "importance": r["importance"]})
        except Exception:
            rows = []

        # Fallback / fill: keyword LIKE on significant words
        if not context:
            words = [w.lower() for w in re.split(r"\W+", message) if len(w) > 3][:5]
            for word in words:
                for row in conn.execute(
                    "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE archived = 0 AND (title LIKE ? OR content LIKE ? OR tags LIKE ?) LIMIT 5",
                    [f"%{word}%", f"%{word}%", f"%{word}%"]
                ).fetchall():
                    if row["id"] not in seen and len(context) < 10:
                        seen.add(row["id"])
                        context.append({"id": row["id"], "title": row["title"], "content": row["content"][:500], "date": row["entry_date"] or "unknown", "tags": row["tags"], "importance": row["importance"]})

        recent = conn.execute(
            "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE archived = 0 ORDER BY importance DESC, updated_at DESC LIMIT 10"
        ).fetchall()

        for r in recent:
            if len(context) >= 10: break
            if r["id"] not in seen:
                seen.add(r["id"])
                context.append({"id": r["id"], "title": r["title"], "content": r["content"][:500], "date": r["entry_date"] or "unknown", "tags": r["tags"], "importance": r["importance"]})
        return context
    finally:
        conn.close()


# ── Action parsing ──

def _parse_actions(reply: str) -> list[dict]:
    """Parse and execute structured actions from AI reply."""
    actions = []

    # CREATE_ENTRY
    for m in re.finditer(r'\[CREATE_ENTRY:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\d+)\]', reply, re.DOTALL):
        title, content, entry_date, tags, importance = m.group(1).strip()[:500], m.group(2).strip()[:5000], m.group(3).strip() or None, m.group(4).strip()[:200], max(1, min(5, int(m.group(5))))
        conn = None
        try:
            conn = get_db()
            cur = conn.execute("INSERT INTO entries (title, content, entry_date, tags, importance) VALUES (?, ?, ?, ?, ?)", [title, content, entry_date, tags, importance])
            conn.commit()
            actions.append({"type": "entry_created", "id": cur.lastrowid, "title": title})
            logger.info(f"AI created entry #{cur.lastrowid}: {title}")
        except Exception as e:
            actions.append({"type": "error", "detail": "Could not save entry"})
            logger.error(f"AI entry creation failed: {e}")
        finally:
            if conn: conn.close()

    # SEARCH_KB
    for m in re.finditer(r'\[SEARCH_KB:\s*(.*?)\]', reply):
        q = m.group(1).strip()
        conn = get_db()
        try:
            rows = conn.execute("SELECT id, title, entry_date, tags FROM entries WHERE archived = 0 AND (title LIKE ? OR content LIKE ?) LIMIT 5", [f"%{q}%", f"%{q}%"]).fetchall()
            actions.append({"type": "search_results", "query": q, "results": [dict(r) for r in rows]})
        finally:
            conn.close()

    # CREATE_RELATIONSHIP
    for m in re.finditer(r'\[CREATE_RELATIONSHIP:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        from_id, to_id, label = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)", [from_id, to_id, label])
            conn.commit()
            actions.append({"type": "relationship_created", "from": from_id, "to": to_id, "label": label})
        finally:
            conn.close()

    # SEARCH_TIMELINE
    for m in re.finditer(r'\[SEARCH_TIMELINE:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        fd, td, kw = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        conn = get_db()
        try:
            rows = conn.execute("SELECT id, title, entry_date, tags FROM entries WHERE archived = 0 AND entry_date >= ? AND entry_date <= ? AND (title LIKE ? OR content LIKE ?) ORDER BY entry_date LIMIT 10", [fd or "0001", td or "9999", f"%{kw}%", f"%{kw}%"]).fetchall()
            actions.append({"type": "timeline_results", "from": fd, "to": td, "results": [dict(r) for r in rows]})
        finally:
            conn.close()

    # ADD_TODO
    for m in re.finditer(r'\[ADD_TODO:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        entry_id_str, priority, due = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO todos (entry_id, priority, due_date) VALUES (?, ?, ?)", [int(entry_id_str), priority, due or None])
            conn.commit()
            actions.append({"type": "todo_added", "entry_id": int(entry_id_str), "priority": priority})
        finally:
            conn.close()

    # SPACED_REPETITION
    for m in re.finditer(r'\[SPACED_REPETITION:\s*(\d+)\]', reply):
        eid = int(m.group(1))
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO spaced_repetition (entry_id) VALUES (?)", [eid])
            conn.commit()
            actions.append({"type": "sr_scheduled", "entry_id": eid})
        finally:
            conn.close()

    # SEARCH_WEB — detection only (executed async by the endpoint)
    for m in re.finditer(r'\[SEARCH_WEB:\s*(.*?)\]', reply):
        actions.append({"type": "web_search_request", "query": m.group(1).strip()})

    # FETCH_ENTRY — read a full entry
    for m in re.finditer(r'\[FETCH_ENTRY:\s*(\d+)\]', reply):
        eid = int(m.group(1))
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT id, title, content, entry_date, tags, source, importance, study_type, authors FROM entries WHERE id = ?",
                [eid],
            ).fetchone()
            actions.append({"type": "entry_fetched", "entry": dict(row) if row else None})
        finally:
            conn.close()

    # LIST_ENTRIES — browse by tag (empty = most important/recent)
    for m in re.finditer(r'\[LIST_ENTRIES:\s*([^\|\]]*)\]', reply):
        tag = m.group(1).strip()
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

    # LIST_RELATIONSHIPS — read an entry's graph links
    for m in re.finditer(r'\[LIST_RELATIONSHIPS:\s*(\d+)\]', reply):
        eid = int(m.group(1))
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT r.id, r.from_id, r.to_id, r.label FROM relationships r WHERE r.from_id = ? OR r.to_id = ?",
                [eid, eid],
            ).fetchall()
            actions.append({"type": "relationships_listed", "entry_id": eid, "results": [dict(r) for r in rows]})
        finally:
            conn.close()

    # APPEND_ENTRY — extend an existing entry's content
    for m in re.finditer(r'\[APPEND_ENTRY:\s*(\d+)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        eid, text = int(m.group(1)), m.group(2).strip()
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
        finally:
            conn.close()

    # ARCHIVE_ENTRY — soft-delete
    for m in re.finditer(r'\[ARCHIVE_ENTRY:\s*(\d+)\]', reply):
        eid = int(m.group(1))
        conn = get_db()
        try:
            conn.execute("UPDATE entries SET archived = 1, updated_at = datetime('now') WHERE id = ?", [eid])
            conn.commit()
            actions.append({"type": "entry_archived", "entry_id": eid})
        finally:
            conn.close()

    # CREATE_THOUGHT
    for m in re.finditer(r'\[CREATE_THOUGHT:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        name, desc, tag_ids = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
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

    # CREATE_TAG
    for m in re.finditer(r'\[CREATE_TAG:\s*(.*?)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        name, color = m.group(1).strip(), m.group(2).strip() or "#6366f1"
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)", [name[:100], color[:20]])
            conn.commit()
            actions.append({"type": "tag_created", "name": name})
        finally:
            conn.close()

    # ADD_REMINDER
    for m in re.finditer(r'\[ADD_REMINDER:\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        eid, title, trigger, rtype = int(m.group(1)), m.group(2).strip(), m.group(3).strip(), m.group(4).strip() or "once"
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO reminders (entry_id, title, trigger_at, reminder_type) VALUES (?, ?, ?, ?)",
                [eid, title[:200], trigger, rtype],
            )
            conn.commit()
            actions.append({"type": "reminder_added", "id": cur.lastrowid, "entry_id": eid})
        finally:
            conn.close()

    # CREATE_THEORY — add a node to the theory aggregator tree
    for m in re.finditer(r'\[CREATE_THEORY:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\d*)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        name, desc, parent_id, status = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4).strip() or "emerging"
        conn = get_db()
        try:
            pid = int(parent_id) if parent_id else None
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

    # LINK_STUDY — attach pro/contra evidence to a theory
    for m in re.finditer(r'\[LINK_STUDY:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(pro|contra)\s*\|\s*(.*?)\]', reply, re.DOTALL):
        theory_id, entry_id, stance, note = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4).strip()
        conn = get_db()
        try:
            theory = conn.execute("SELECT id FROM theories WHERE id = ?", [theory_id]).fetchone()
            entry = conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone()
            if not theory or not entry:
                actions.append({"type": "error", "detail": f"LINK_STUDY failed: theory #{theory_id} or entry #{entry_id} not found"})
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO theory_evidence (theory_id, entry_id, stance, note) VALUES (?, ?, ?, ?)",
                    [theory_id, entry_id, stance, note[:1000]],
                )
                conn.commit()
                actions.append({"type": "study_linked", "theory_id": theory_id, "entry_id": entry_id, "stance": stance})
        except Exception as exc:
            actions.append({"type": "error", "detail": f"LINK_STUDY failed: {exc}"})
        finally:
            conn.close()

    # RESEARCH — qualitative research pipeline (executed by the endpoint)
    for m in re.finditer(r'\[RESEARCH:\s*(.*?)\]', reply, re.DOTALL):
        topic = m.group(1).strip()
        if topic:
            actions.append({"type": "research_request", "topic": topic[:300]})

    return actions


def _extract_history_item(h: any) -> dict:
    if isinstance(h, dict):
        return {"role": h.get("role", "user"), "content": h.get("content", "")}
    return {"role": getattr(h, "role", "user"), "content": getattr(h, "content", str(h))}


def _build_system_prompt(context: list[dict], provider: str, model: str) -> str:
    ctx = ""
    if context:
        parts = []
        for c in context:
            d = f" ({c['date']})" if c['date'] and c['date'] != "unknown" else ""
            parts.append(f"- [{c['id']}] **{c['title']}**{d} [importance: {c['importance']}]\n  Tags: {c['tags']}\n  {c['content'][:300]}")
        ctx = "\n\n".join(parts)

    return f"""You are ChronoKG AI, an assistant for a chronological knowledge graph.
Provider: {provider} / Model: {model}

=== KNOWLEDGE BASE CONTEXT ===
{ctx or "No entries yet."}
=== END ===

## CAPABILITIES

1. **Answer questions** — Use the context above. Reference by title and date.

2. **Register information** — Save new entries with:
[CREATE_ENTRY: Title | Content | YYYY-MM-DD | tag1,tag2 | 1-5]

3. **Read your knowledge** — Fetch full entries, browse lists, or search:
[FETCH_ENTRY: entry_id]
[LIST_ENTRIES: tag_or_empty]
[LIST_RELATIONSHIPS: entry_id]
[SEARCH_KB: search query]
[SEARCH_TIMELINE: 1900-01-01 | 2026-12-31 | keyword]

4. **Edit your knowledge** — Extend or archive entries:
[APPEND_ENTRY: entry_id | additional content]
[ARCHIVE_ENTRY: entry_id]

5. **Connect entries** — Create relationships:
[CREATE_RELATIONSHIP: from_entry_id | to_entry_id | relationship_label]

6. **Manage tasks, reviews and reminders** — Add todos, spaced repetition and reminders:
[ADD_TODO: entry_id | important | 2026-08-15]
[SPACED_REPETITION: entry_id]
[ADD_REMINDER: entry_id | title | 2026-08-15T09:00 | once|daily|weekly|monthly]

7. **Organize** — Create tags and thoughts:
[CREATE_TAG: name | #6366f1]
[CREATE_THOUGHT: name | description | comma,separated,tag_ids]

8. **Meta-analysis research** — Run a full meta-analysis-style research pipeline (a priori protocol with PICO + inclusion/exclusion criteria, systematic web search with PRISMA flow, risk-of-bias extraction, pooled synthesis with heterogeneity + publication bias + confidence, and automatic theory-map building):
[RESEARCH: research topic]

9. **Web research** — Search the live web:
[SEARCH_WEB: your research query about studies or books]

10. **Books and studies** — When the user mentions a book or study, offer to save it with tags like "book","study","research"

11. **Theory Aggregator** — Build or extend the theory tree (a theory can derive into sub-theories) and attach evidence:
[CREATE_THEORY: name | description | parent_id | supported|contested|refuted|emerging]
[LINK_STUDY: theory_id | entry_id | pro|contra | note]
"""
