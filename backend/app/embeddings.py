"""
Hybrid retrieval embeddings on a local server (llama.cpp on CPU, first choice;
Ollama only if it happens to be installed).

Adds semantic re-ranking on top of FTS5 keyword retrieval: candidate entries
from the chat context builder are embedded in one batched call and re-ranked by
cosine similarity to the user's message. Everything degrades gracefully to the
plain FTS5 order when no local embedding server answers.

The embedding model is resolved automatically: the configured
OLLAMA_EMBED_MODEL is used when installed; otherwise a list of common local
embedding models is tried. Run `ollama pull nomic-embed-text` to enable it.
"""
import logging
import os
import re
import time

import anyio
import httpx

logger = logging.getLogger("chronokg.embeddings")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# llama.cpp serves embeddings locally on CPU with a GGUF model from Hugging
# Face — no Ollama, no cloud. It speaks the OpenAI shape at /v1/embeddings, so
# one small server (llama-server --embeddings) is enough. Tried before Ollama:
# when it answers, semantic retrieval works on a machine with no Ollama at all.
LLAMA_EMBED_URL = os.getenv("LLAMA_EMBED_URL", "http://127.0.0.1:8199")
LLAMA_EMBED_MODEL = os.getenv("LLAMA_EMBED_MODEL", "bge-m3")

EMBED_TIMEOUT = 15.0
EMBED_TEXT_LIMIT = 600
EMBED_CACHE_CAP = 4000

# How long a FAILED llama.cpp probe is remembered before it is retried. Short
# enough that starting the embedding server right after the app heals itself,
# long enough that a status poll every few seconds does not hammer the port.
LLAMA_RECHECK_SECONDS = float(os.getenv("LLAMA_RECHECK_SECONDS", "20"))

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
_llama_checked: bool = False
_llama_ok: bool = False
# Monotonic timestamp of the last FAILED llama.cpp probe; a failure is retried
# after LLAMA_RECHECK_SECONDS instead of being cached for the process lifetime.
_llama_failed_at: float | None = None
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
        "Neither llama.cpp nor Ollama offers an embedding model (configured: %s). "
        "Start ./start-embeddings.sh (llama.cpp on CPU, the project default).",
        OLLAMA_EMBED_MODEL,
    )
    # Ollama is up but no embed model installed — enter cooldown so we re-check
    # on a later call instead of latching permanently (see comment above).
    _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
    return None


def embedding_available() -> bool:
    """Whether semantic retrieval is currently active.

    llama.cpp is the backend this project is built for (start-embeddings.sh,
    CPU, no Ollama). Ollama is only a fallback for machines that happen to run
    it; it is never probed when llama.cpp answers, so a dead Ollama cannot make
    the status screen claim the feature is off.
    """
    if _llama_available():
        return True
    return _resolve_model() is not None


def embedding_model_installed() -> bool:
    """Whether a usable embedding model is reachable (llama.cpp first)."""
    return embedding_available()


def embedding_model() -> str | None:
    if _llama_available():
        return LLAMA_EMBED_MODEL
    return _active_model


def embedding_dimension() -> int | None:
    """Dimension of the active vectors, measured once with a real call."""
    if _dimension is not None:
        return _dimension
    if _llama_available():
        probe = _batch_embed_llama(["dimension probe"])
        if probe:
            return len(probe[0])
    # Ollama only: without this the caller cannot tell a 768-dim store from a
    # 1024-dim one and would have to call every stored vector unusable.
    if _resolve_model() is not None:
        probe = _batch_embed(["dimension probe"])
        if probe:
            return len(probe[0])
    return None


def embedding_backend() -> str:
    """Which backend answers, so the UI can say the truth."""
    if _llama_available():
        return f"llama.cpp ({LLAMA_EMBED_URL})"
    if _resolve_model() is not None:
        return "ollama"
    return "none"


def available_embed_models() -> list[str]:
    """Names of installed models that look like embedding models."""
    tags = _ollama_tags() or []
    keywords = ("embed", "minilm", "bge", "mxbai", "arctic", "paraphrase")
    candidates = [t for t in tags if any(k in t.lower() for k in keywords)]
    return candidates or tags[:20]


async def embedding_status() -> dict:
    """Probe the embedding stack without blocking the event loop.

    Every probe below is a synchronous HTTP call to llama.cpp or Ollama
    (56–105 ms measured on `/api/ai/status`), so each one runs in a worker
    thread: a status poll must not stall every other request behind it. The
    keys match the fields the endpoint already served, so this is a drop-in
    replacement for calling the probes inline.
    """
    return {
        "active_embed_model": await anyio.to_thread.run_sync(embedding_model),
        "semantic_retrieval": await anyio.to_thread.run_sync(embedding_available),
        "embedding_backend": await anyio.to_thread.run_sync(embedding_backend),
        "embedding_model_installed": await anyio.to_thread.run_sync(embedding_model_installed),
        "embedding_dimension": await anyio.to_thread.run_sync(embedding_dimension),
        "available_embed_models": await anyio.to_thread.run_sync(available_embed_models),
    }


def _llama_available() -> bool:
    """Whether a llama.cpp embedding server answers on LLAMA_EMBED_URL.

    A successful probe latches for the process lifetime: llama-server is a
    supervised local process, so once it answers it keeps answering. A FAILED
    probe is retried at most every ``LLAMA_RECHECK_SECONDS`` instead of being
    cached forever — otherwise starting the app a second before the embedding
    server would disable semantic search until the next restart.
    """
    global _llama_checked, _llama_ok, _llama_failed_at
    if _llama_ok:
        return True
    if _llama_checked and _llama_failed_at is not None:
        if time.monotonic() - _llama_failed_at < LLAMA_RECHECK_SECONDS:
            return False
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{LLAMA_EMBED_URL}/health")
            _llama_ok = resp.status_code == 200
    except Exception as exc:
        logger.info("llama.cpp embedding server not reachable (%s)", type(exc).__name__)
        _llama_ok = False
    _llama_checked = True
    if _llama_ok:
        _llama_failed_at = None
        logger.info("Embedding backend: llama.cpp at %s (model %s)", LLAMA_EMBED_URL, LLAMA_EMBED_MODEL)
    else:
        _llama_failed_at = time.monotonic()
    return _llama_ok


def _batch_embed_llama(texts: list[str]) -> list[list[float]] | None:
    """Embed through llama.cpp's OpenAI-compatible /v1/embeddings."""
    global _dimension
    try:
        with httpx.Client(timeout=EMBED_TIMEOUT) as client:
            resp = client.post(
                f"{LLAMA_EMBED_URL}/v1/embeddings",
                json={"input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            vectors = [list(map(float, e.get("embedding", []))) for e in data.get("data", [])]
        if vectors and _dimension is None:
            _dimension = len(vectors[0])
        return vectors
    except Exception as exc:
        logger.info("llama.cpp embeddings failed (%s) — trying Ollama", type(exc).__name__)
        return None


def _batch_embed(texts: list[str]) -> list[list[float]] | None:
    global _dimension, _cooldown_until
    # llama.cpp first: it needs no Ollama and runs on CPU.
    if _llama_available():
        vectors = _batch_embed_llama(texts)
        if vectors:
            return vectors
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
