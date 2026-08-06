"""
Shared LLM provider layer.

Centralizes configuration and provider calls (DeepSeek -> Ollama failover) so
chat, research and tool-use follow one canonical path with a single timeout /
retry policy. Mock mode is honoured for offline development and testing;
callers keep their own mock *content* and branch on LLM_PROVIDER where needed.
"""
import logging
import asyncio
import os
import time
from pathlib import Path
from typing import AsyncGenerator, Tuple

import httpx
from dotenv import load_dotenv

logger = logging.getLogger("chronokg.llm")

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")


def _is_retryable(exc: Exception) -> bool:
    """Transient network/5xx/429 errors are worth one retry; 4xx are not."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


async def _with_retry(coro_factory, attempts: int = 3, base_delay: float = 0.6):
    """Run an async provider call, retrying transient failures with backoff."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 - provider errors are heterogeneous
            last_exc = exc
            if not _is_retryable(exc) or attempt >= attempts - 1:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))
    raise last_exc


def log_ai_event(
    provider: str,
    model: str,
    endpoint: str,
    ok: bool,
    error: str = "",
    latency_ms: int = 0,
    prompt_chars: int = 0,
    reply_chars: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    """Best-effort observability row per LLM call. Never raises."""
    try:
        from app.db import get_db

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO ai_events (provider, model, endpoint, ok, error, latency_ms, prompt_chars, reply_chars, prompt_tokens, completion_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [provider, model, endpoint, 1 if ok else 0, error[:500], latency_ms, prompt_chars, reply_chars, prompt_tokens, completion_tokens],
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Failed to log AI event: %s", exc)


def _prompt_chars(messages: list) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))


def _usage_tokens(usage: dict) -> tuple[int, int]:
    """Extract (prompt_tokens, completion_tokens) from OpenAI or Ollama usage."""
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("prompt_tokens") or usage.get("prompt_eval_count") or 0
    completion = usage.get("completion_tokens") or usage.get("eval_count") or 0
    return int(prompt or 0), int(completion or 0)


async def complete(
    messages: list,
    *,
    temperature: float = 0.2,
    json_mode: bool = False,
    max_tokens: int = 8192,
) -> Tuple[str, str, str]:
    """Single non-streaming completion (DeepSeek -> Ollama). Returns (provider, model, text)."""
    t0 = time.monotonic()
    chars = _prompt_chars(messages)
    if LLM_PROVIDER == "mock":
        log_ai_event("mock", "mock-model", "complete", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=6)
        return ("mock", "mock-model", "[mock]")
    if LLM_PROVIDER == "deepseek" or (LLM_PROVIDER == "auto" and DEEPSEEK_API_KEY):
        try:
            text, usage = await _with_retry(
                lambda: _call_deepseek(messages, temperature, json_mode, max_tokens)
            )
            pt, ct = _usage_tokens(usage)
            log_ai_event("deepseek", DEEPSEEK_MODEL, "complete", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=len(text), prompt_tokens=pt, completion_tokens=ct)
            return ("deepseek", DEEPSEEK_MODEL, text)
        except Exception as exc:
            log_ai_event(
                "deepseek",
                DEEPSEEK_MODEL,
                "complete",
                False,
                error=str(exc),
                latency_ms=int((time.monotonic() - t0) * 1000),
                prompt_chars=chars,
            )
            if LLM_PROVIDER == "deepseek" and not _is_retryable(exc):
                # In explicit deepseek mode a non-retryable (typically auth)
                # failure is a config problem, not a transient outage. Make the
                # misconfiguration visible while still failing over to Ollama.
                logger.warning(
                    "Configured DeepSeek provider failed with a non-retryable error (%s). "
                    "Check DEEPSEEK_API_KEY / DEEPSEEK_API_URL.", exc
                )
            logger.info("DeepSeek unavailable (%s), falling back to Ollama", exc)
    try:
        text, usage = await _with_retry(
            lambda: _call_ollama(messages, temperature, max_tokens, json_mode)
        )
        pt, ct = _usage_tokens(usage)
        log_ai_event("ollama", OLLAMA_MODEL, "complete", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=len(text), prompt_tokens=pt, completion_tokens=ct)
        return ("ollama", OLLAMA_MODEL, text)
    except Exception as exc:
        log_ai_event("ollama", OLLAMA_MODEL, "complete", False, error=str(exc), latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars)
        raise


async def stream_complete(
    messages: list,
    *,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> AsyncGenerator[Tuple[str, str, str], None]:
    """Stream tokens (DeepSeek -> Ollama). Yields (provider, model, chunk)."""
    t0 = time.monotonic()
    chars = _prompt_chars(messages)
    reply_chars = 0
    provider, model = "", ""
    try:
        if LLM_PROVIDER == "mock":
            yield ("mock", "mock-model", "[mock]")
            log_ai_event("mock", "mock-model", "stream", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=6)
            return
        if LLM_PROVIDER == "deepseek" or (LLM_PROVIDER == "auto" and DEEPSEEK_API_KEY):
            yielded = False
            try:
                async for chunk in _stream_deepseek(messages, temperature, max_tokens):
                    yielded = True
                    reply_chars += len(chunk)
                    provider, model = "deepseek", DEEPSEEK_MODEL
                    yield ("deepseek", DEEPSEEK_MODEL, chunk)
                log_ai_event("deepseek", DEEPSEEK_MODEL, "stream", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=reply_chars)
                return
            except Exception as exc:
                if yielded:
                    # Tokens were already sent: restarting on Ollama would
                    # duplicate partial output, so surface the failure instead.
                    logger.error("DeepSeek stream failed after partial output (%s)", exc)
                    raise
                logger.info("DeepSeek stream unavailable (%s), falling back to Ollama", exc)
        async for chunk in _stream_ollama(messages, temperature, max_tokens):
            reply_chars += len(chunk)
            provider, model = "ollama", OLLAMA_MODEL
            yield ("ollama", OLLAMA_MODEL, chunk)
        log_ai_event("ollama", OLLAMA_MODEL, "stream", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=reply_chars)
    except Exception as exc:
        log_ai_event(provider or "unknown", model, "stream", False, error=str(exc), latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=reply_chars)
        raise


async def ping_ollama() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


async def check_deepseek() -> bool:
    if not DEEPSEEK_API_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{DEEPSEEK_API_URL.replace('/chat/completions', '/models')}",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            )
            return resp.status_code == 200
    except Exception:
        return False


# ── DeepSeek ────────────────────────────────────────────────────────────────

async def _call_deepseek(messages: list, temperature: float, json_mode: bool, max_tokens: int) -> tuple[str, dict]:
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            DEEPSEEK_API_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], data.get("usage") or {}


async def _stream_deepseek(messages: list, temperature: float, max_tokens: int):
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            DEEPSEEK_API_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for chunk in _iter_sse_content(resp):
                yield chunk


# ── Ollama ──────────────────────────────────────────────────────────────────

async def _call_ollama(messages: list, temperature: float, max_tokens: int, json_mode: bool = False) -> tuple[str, dict]:
    body = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        # Ollama supports OpenAI-style format: "json" on both /api/chat and the
        # OpenAI-compatible endpoint; harmless if the provider ignores it.
        body["format"] = "json"
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], data.get("usage") or {}


async def _stream_ollama(messages: list, temperature: float, max_tokens: int):
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for chunk in _iter_sse_content(resp):
                yield chunk


async def _iter_sse_content(resp: httpx.Response):
    """Parse OpenAI-compatible SSE lines and yield content deltas."""
    import json

    async for line in resp.aiter_lines():
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str in ("[DONE]", ""):
            break
        try:
            delta = json.loads(data_str).get("choices", [{}])[0].get("delta", {}).get("content", "")
        except (json.JSONDecodeError, IndexError, KeyError, TypeError):
            continue
        if delta:
            yield delta
