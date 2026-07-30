"""
AI Chat router — Uses Reasonix Ollama (default) or DeepSeek for AI responses.
Context injection from the knowledge base + SSE streaming.
"""
import json
import os
from pathlib import Path
from typing import AsyncGenerator
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.db import get_db
from app.models import ChatRequest

from dotenv import load_dotenv

router = APIRouter()

# Load .env file
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# ── Provider configuration ──
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# DeepSeek (fallback)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


@router.post("/chat", response_model=dict)
async def chat(data: ChatRequest):
    """Simple non-streaming chat."""
    context = _build_context(data.message)
    reply = await _call_llm_nonstream(data.message, data.history, context)
    return {"reply": reply}


@router.post("/chat/stream")
async def chat_stream(data: ChatRequest):
    """SSE streaming chat endpoint."""
    context = _build_context(data.message)

    async def generate():
        yield f"data: {json.dumps({'type': 'context', 'entries': len(context)})}\n\n"
        async for chunk in _call_llm_stream(data.message, data.history, context):
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _build_context(message: str) -> list[dict]:
    """Fetch relevant entries from the knowledge base as context for the AI."""
    conn = get_db()
    try:
        words = [w.lower() for w in message.split() if len(w) > 3]
        related: dict[int, int] = {}  # id -> importance
        if words:
            for word in words[:5]:
                rows = conn.execute(
                    "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE archived = 0 AND (title LIKE ? OR content LIKE ? OR tags LIKE ?) LIMIT 5",
                    [f"%{word}%", f"%{word}%", f"%{word}%"]
                ).fetchall()
                for r in rows:
                    related[r["id"]] = r["importance"]

        recent = conn.execute(
            "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE archived = 0 ORDER BY importance DESC, updated_at DESC LIMIT 10"
        ).fetchall()

        context = []
        seen = set()

        # First add entries related to the user's query
        for entry_id in sorted(related, key=lambda eid: related[eid], reverse=True):
            if entry_id not in seen:
                seen.add(entry_id)
                row = conn.execute(
                    "SELECT id, title, content, entry_date, tags, importance FROM entries WHERE id = ?",
                    [entry_id]
                ).fetchone()
                if row:
                    context.append({
                        "id": row["id"], "title": row["title"],
                        "content": row["content"][:500],
                        "date": row["entry_date"] or "unknown",
                        "tags": row["tags"], "importance": row["importance"],
                    })

        # Fill remaining slots with recent important entries
        for r in recent:
            if len(context) >= 10:
                break
            if r["id"] not in seen:
                seen.add(r["id"])
                context.append({
                    "id": r["id"], "title": r["title"],
                    "content": r["content"][:500],
                    "date": r["entry_date"] or "unknown",
                    "tags": r["tags"], "importance": r["importance"],
                })

        return context
    finally:
        conn.close()


def _get_provider_info() -> dict:
    """Return the active LLM provider config."""
    if LLM_PROVIDER == "deepseek" and DEEPSEEK_API_KEY:
        return {
            "provider": "deepseek",
            "api_url": DEEPSEEK_API_URL,
            "model": DEEPSEEK_MODEL,
            "api_key": DEEPSEEK_API_KEY,
            "needs_auth": True,
        }
    # Default: Ollama (Reasonix)
    return {
        "provider": "ollama",
        "api_url": f"{OLLAMA_BASE_URL}/v1/chat/completions",
        "model": OLLAMA_MODEL,
        "api_key": "",
        "needs_auth": False,
    }


async def _call_llm_stream(
    message: str, history: list[dict], context: list[dict],
) -> AsyncGenerator[str, None]:
    """Call the configured LLM provider with SSE streaming."""
    import httpx

    provider = _get_provider_info()
    system_prompt = _build_system_prompt(context, provider["provider"], provider["model"])

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": message})

    headers = {"Content-Type": "application/json"}
    if provider["api_key"]:
        headers["Authorization"] = f"Bearer {provider['api_key']}"

    request_body = {
        "model": provider["model"],
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.7,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        async with client.stream(
            "POST", provider["api_url"], headers=headers, json=request_body,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str in ("[DONE]", ""):
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        if content := delta.get("content", ""):
                            yield content
                    except json.JSONDecodeError:
                        continue


async def _call_llm_nonstream(
    message: str, history: list[dict], context: list[dict],
) -> str:
    """Call the configured LLM provider (non-streaming)."""
    import httpx

    provider = _get_provider_info()
    system_prompt = _build_system_prompt(context, provider["provider"], provider["model"])

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": message})

    headers = {"Content-Type": "application/json"}
    if provider["api_key"]:
        headers["Authorization"] = f"Bearer {provider['api_key']}"

    request_body = {
        "model": provider["model"],
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.7,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(provider["api_url"], headers=headers, json=request_body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _build_system_prompt(context: list[dict], provider: str, model: str) -> str:
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

Running on: {provider.upper()} / {model}

You have full read access to the user's entries. Below are the most relevant entries
from their knowledge base that relate to the current conversation.

Use this information to answer questions accurately. When you reference an entry,
mention it by title and date. If you don't know something, say so.

=== KNOWLEDGE BASE CONTEXT ===
{context_str or "No specific entries matched."}
=== END CONTEXT ===

You can help with:
- Answering questions about entries in the knowledge base
- Summarizing what happened in a time period
- Finding connections between entries
- Suggesting relationships between entries
- Explaining concepts found in the knowledge base
- Helping organize and tag entries
"""
