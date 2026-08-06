"""
Hybrid retrieval embeddings (Ollama, no API key).

Adds semantic re-ranking on top of FTS5 keyword retrieval: candidate entries
from the chat context builder are embedded in one batched call and re-ranked by
cosine similarity to the user's message. Everything degrades gracefully to the
plain FTS5 order when Ollama is unreachable or no embedding model is installed.

The embedding model is resolved automatically: the configured
OLLAMA_EMBED_MODEL is used when installed; otherwise a list of common local
embedding models is tried. Run `ollama pull nomic-embed-text` to enable it.
"""
import logging
import os
import re
import time

import httpx

logger = logging.getLogger("chronokg.embeddings")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBED_TIMEOUT = 15.0
EMBED_TEXT_LIMIT = 600
EMBED_CACHE_CAP = 4000

# Common local embedding models tried in order when the configured one is
# not installed (each is small and free to pull).
EMBED_FALLBACKS = [
    "nomic-embed-text",
    "all-minilm",
    "mxbai-embed-large",
    "snowflake-arctic-embed",
    "bge-m3",
    "paraphrase-multilingual",
]

_cache: dict[str, list[float]] = {}
_active_model: str | None = None
_tags: list[str] | None = None
_dimension: int | None = None
# Cooldown after a transient Ollama failure or a "no embed model installed"
# discovery so chat latency doesn't spike with repeated 15s timeouts while the
# local server is briefly unreachable / before a model is pulled. After the
# cooldown expires the discovery re-resolves so `ollama pull nomic-embed-text`
# mid-session is picked up.
_cooldown_until: float = 0.0
_COOLDOWN_SECONDS = 30.0


def _ollama_tags() -> list[str] | None:
    global _tags
    if _tags is not None:
        return _tags
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            _tags = [m.get("name", "") for m in resp.json().get("models", [])]
            return _tags
    except Exception as exc:
        logger.info("Could not reach Ollama tags (%s)", type(exc).__name__)
        return None


def _resolve_model() -> str | None:
    """Return the embedding model to use, or None (disables embeddings)."""
    global _active_model, _tags, _cooldown_until
    if _active_model:
        return _active_model
    if time.monotonic() < _cooldown_until:
        # Within cooldown: stay offline promptly without re-hitting Ollama so
        # embedding_available() returns False fast when genuinely unavailable.
        return None
    # Past cooldown: re-discover. Clear cached tags so a model that was pulled
    # mid-session (e.g. `ollama pull nomic-embed-text`) is picked up instead of
    # being latched by the stale cache.
    _tags = None
    tags = _ollama_tags()
    if tags is None:
        # Ollama unreachable — transient; enter cooldown so we re-check shortly
        # rather than hammering a down server on every call.
        _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        return None
    candidates = [OLLAMA_EMBED_MODEL, *EMBED_FALLBACKS]
    for name in candidates:
        match = next((t for t in tags if t == name or t.startswith(name + ":")), None)
        if match:
            _active_model = match
            logger.info("Embedding model resolved: %s", match)
            return match
    logger.warning(
        "No embedding model installed (configured: %s). Run `ollama pull nomic-embed-text` to enable semantic retrieval.",
        OLLAMA_EMBED_MODEL,
    )
    # Ollama is up but no embed model installed — enter cooldown so we re-check
    # on a later call instead of latching permanently (see comment above).
    _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
    return None


def embedding_available() -> bool:
    """Whether semantic retrieval is currently active."""
    return _resolve_model() is not None


def embedding_model_installed() -> bool:
    """Whether a usable embedding model exists in Ollama."""
    return _resolve_model() is not None


def embedding_model() -> str | None:
    return _active_model


def embedding_dimension() -> int | None:
    return _dimension


def available_embed_models() -> list[str]:
    """Names of installed models that look like embedding models."""
    tags = _ollama_tags() or []
    keywords = ("embed", "minilm", "bge", "mxbai", "arctic", "paraphrase")
    candidates = [t for t in tags if any(k in t.lower() for k in keywords)]
    return candidates or tags[:20]


def _batch_embed(texts: list[str]) -> list[list[float]] | None:
    global _dimension, _cooldown_until
    model = _resolve_model()
    if model is None:
        return None
    try:
        with httpx.Client(timeout=EMBED_TIMEOUT) as client:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={"model": model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            vectors = [list(map(float, e)) for e in data.get("embeddings", [])]
        if vectors and _dimension is None:
            _dimension = len(vectors[0])
        return vectors
    except Exception as exc:
        logger.info("Embeddings unavailable (%s) — using FTS5-only retrieval", type(exc).__name__)
        # Transient failure (Ollama down, timeout): do NOT permanently disable.
        # Re-enable after a short cooldown so a restart of Ollama is picked up.
        _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        return None


def embed(texts: list[str]) -> list[list[float]] | None:
    """Batch-embed texts with an in-memory cache. Returns None when offline."""
    if time.monotonic() < _cooldown_until:
        return None
    # Mock provider means offline development/testing — never hit the network.
    from app.llm import LLM_PROVIDER

    if LLM_PROVIDER == "mock":
        return None

    cleaned = [re.sub(r"\s+", " ", (t or ""))[:EMBED_TEXT_LIMIT] for t in texts]
    if not any(cleaned):
        return None

    missing = [(i, t) for i, t in enumerate(cleaned) if t and t not in _cache]
    if missing:
        vectors = _batch_embed([t for _, t in missing])
        if vectors is None:
            return None
        for (i, t), v in zip(missing, vectors):
            _cache[t] = v
        if len(_cache) > EMBED_CACHE_CAP:
            for key in list(_cache)[: EMBED_CACHE_CAP // 4]:
                _cache.pop(key, None)
    return [_cache.get(t) for t in cleaned]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Re-rank candidates by cosine similarity of title+content to the query.

    Returns candidates unchanged (original FTS5/importance order) when the
    embedding backend is unavailable.
    """
    if not candidates:
        return candidates
    vectors = embed(
        [query]
        + [f"{c.get('title', '')} {c.get('content', '')[:300]}" for c in candidates]
    )
    if vectors is None:
        return candidates
    qv = vectors[0]
    scored = [
        (candidates[i], cosine(qv, vectors[i + 1]))
        for i in range(len(candidates))
        if vectors[i + 1] is not None
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [c for c, _ in scored]
