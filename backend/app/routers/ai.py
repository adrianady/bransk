"""AI observability endpoints: call event log + provider/embedding status."""
from fastapi import APIRouter

from app.db import get_db
from app.embeddings import (
    available_embed_models,
    embedding_available,
    embedding_dimension,
    embedding_model,
    embedding_model_installed,
)
from app.llm import DEEPSEEK_MODEL, LLM_PROVIDER, OLLAMA_EMBED_MODEL, OLLAMA_MODEL
from app.pdf_rag import pdf_chunk_count

router = APIRouter()


@router.get("/ai/events")
async def ai_events(limit: int = 50):
    """Recent AI calls (provider, latency, prompt/reply size, success/failure)."""
    if limit > 500:
        limit = 500
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM ai_events ORDER BY id DESC LIMIT ?", [limit]).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            for field in ("error", "endpoint"):
                if field in item and item[field]:
                    item[field] = str(item[field])[:300]
            out.append(item)
        return out
    finally:
        conn.close()


@router.get("/ai/status")
async def ai_status():
    """Current model configuration and embedding availability."""
    return {
        "provider_mode": LLM_PROVIDER,
        "deepseek_model": DEEPSEEK_MODEL,
        "ollama_model": OLLAMA_MODEL,
        "configured_embed_model": OLLAMA_EMBED_MODEL,
        "active_embed_model": embedding_model(),
        "semantic_retrieval": embedding_available(),
        "embedding_model_installed": embedding_model_installed(),
        "embedding_dimension": embedding_dimension(),
        "available_embed_models": available_embed_models(),
        "pdf_chunks_indexed": pdf_chunk_count(),
    }
