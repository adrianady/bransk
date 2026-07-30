"""
AI Chat router — DeepSeek integration with context injection and SSE streaming.
Uses the user's existing DeepSeek API key from environment.
"""
import json
import os
from typing import AsyncGenerator
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.db import get_db
from app.models import ChatRequest

router = APIRouter()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


@router.post("/chat", response_model=dict)
async def chat(data: ChatRequest):
    """Simple non-streaming chat."""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(400, "DEEPSEEK_API_KEY not configured. Set it in .env or environment variables.")

    context = _build_context(data.message)
    reply = await _call_deepseek(data.message, data.history, context)
    return {"reply": reply}


@router.post("/chat/stream")
async def chat_stream(data: ChatRequest):
    """SSE streaming chat endpoint."""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(400, "DEEPSEEK_API_KEY not configured. Set it in .env or environment variables.")

    context = _build_context(data.message)

    async def generate():
        yield f"data: {json.dumps({'type': 'context', 'entries': len(context)})}\n\n"
        async for chunk in _stream_deepseek(data.message, data.history, context):
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _build_context(message: str) -> list[dict]:
    """Fetch relevant entries from the knowledge base as context for the AI."""
    conn = get_db()
    try:
        # Simple keyword-based context retrieval
        words = [w.lower() for w in message.split() if len(w) > 3]
        related = set()
        if words:
            for word in words[:5]:
                rows = conn.execute(
                    "SELECT id, title, content, entry_date, tags FROM entries WHERE archived = 0 AND (title LIKE ? OR content LIKE ? OR tags LIKE ?) LIMIT 5",
                    [f"%{word}%", f"%{word}%", f"%{word}%"]
                ).fetchall()
                for r in rows:
                    related.add(r["id"])

        # Always include most recent important entries as general context
        recent = conn.execute(
            "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE archived = 0 ORDER BY importance DESC, updated_at DESC LIMIT 10"
        ).fetchall()

        context = []
        seen = set()

        # First add entries specifically related to the user's query
        for entry_id in related:
            if entry_id not in seen:
                seen.add(entry_id)
                row = conn.execute(
                    "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE id = ?",
                    [entry_id]
                ).fetchone()
                if row:
                    context.append({
                        "id": row["id"],
                        "title": row["title"],
                        "content": row["content"][:500],
                        "date": row["entry_date"] or "unknown",
                        "tags": row["tags"],
                        "importance": row["importance"],
                    })

        # Then fill remaining slots with recent important entries
        for r in recent:
            if len(context) >= 10:
                break
            if r["id"] not in seen:
                seen.add(r["id"])
                context.append({
                    "id": r["id"],
                    "title": r["title"],
                    "content": r["content"][:500],
                    "date": r["entry_date"] or "unknown",
                    "tags": r["tags"],
                    "importance": r["importance"],
                })

        return context
    finally:
        conn.close()


async def _call_deepseek(message: str, history: list[dict], context: list[dict]) -> str:
    """Call DeepSeek API (non-streaming)."""
    import httpx

    system_prompt = _build_system_prompt(context)
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history
    for h in history[-10:]:
        messages.append(h)

    messages.append({"role": "user", "content": message})

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": messages,
                "max_tokens": 2048,
                "temperature": 0.7,
                "stream": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _stream_deepseek(message: str, history: list[dict], context: list[dict]) -> AsyncGenerator[str, None]:
    """Call DeepSeek API with SSE streaming."""
    import httpx

    system_prompt = _build_system_prompt(context)
    messages = [{"role": "system", "content": system_prompt}]

    for h in history[-10:]:
        messages.append(h)

    messages.append({"role": "user", "content": message})

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.7,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if delta := data.get("choices", [{}])[0].get("delta", {}):
                            if content := delta.get("content", ""):
                                yield content
                    except json.JSONDecodeError:
                        continue


def _build_system_prompt(context: list[dict]) -> str:
    """Build system prompt with knowledge base context."""
    context_str = ""
    if context:
        context_parts = []
        for c in context:
            date_str = f" ({c['date']})" if c['date'] and c['date'] != "unknown" else ""
            context_parts.append(
                f"- [{c['id']}] **{c['title']}**{date_str} [importance: {c['importance']}]\n"
                f"  Tags: {c['tags']}\n"
                f"  {c['content'][:300]}"
            )
        context_str = "\n\n".join(context_parts)

    return f"""You are the AI assistant for ChronoKG, a chronological knowledge graph application.
You help the user explore and understand their knowledge base.

You have full read access to the user's entries. Below are the most relevant entries
from their knowledge base that relate to the current conversation.

Use this information to answer questions accurately. When you reference an entry,
mention it by title and date. If you don't know something, say so.

=== KNOWLEDGE BASE CONTEXT ===
{context_str or "No specific entries matched. The user's knowledge base may not have relevant entries yet."}
=== END CONTEXT ===

You can help with:
- Answering questions about entries in the knowledge base
- Summarizing what happened in a time period
- Finding connections between entries
- Suggesting relationships between entries
- Explaining concepts found in the knowledge base
- Helping organize and tag entries
"""
