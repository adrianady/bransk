"""
Shared LLM provider layer.

Centralizes configuration and provider calls (DeepSeek -> Ollama failover) so
chat, research and tool-use follow one canonical path with a single timeout /
retry policy. Mock mode is honoured for offline development and testing;
callers keep their own mock *content* and branch on LLM_PROVIDER where needed.
"""
import logging
import os
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
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")


async def complete(
    messages: list,
    *,
    temperature: float = 0.2,
    json_mode: bool = False,
    max_tokens: int = 8192,
) -> Tuple[str, str, str]:
    """Single non-streaming completion (DeepSeek -> Ollama). Returns (provider, model, text)."""
    if LLM_PROVIDER == "mock":
        return ("mock", "mock-model", "[mock]")
    if LLM_PROVIDER == "deepseek" or (LLM_PROVIDER == "auto" and DEEPSEEK_API_KEY):
        try:
            text = await _call_deepseek(messages, temperature, json_mode, max_tokens)
            return ("deepseek", DEEPSEEK_MODEL, text)
        except Exception as exc:
            logger.info("DeepSeek unavailable (%s), falling back to Ollama", exc)
    text = await _call_ollama(messages, temperature, max_tokens)
    return ("ollama", OLLAMA_MODEL, text)


async def stream_complete(
    messages: list,
    *,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> AsyncGenerator[Tuple[str, str, str], None]:
    """Stream tokens (DeepSeek -> Ollama). Yields (provider, model, chunk)."""
    if LLM_PROVIDER == "mock":
        yield ("mock", "mock-model", "[mock]")
        return
    if LLM_PROVIDER == "deepseek" or (LLM_PROVIDER == "auto" and DEEPSEEK_API_KEY):
        try:
            async for chunk in _stream_deepseek(messages, temperature, max_tokens):
                yield ("deepseek", DEEPSEEK_MODEL, chunk)
            return
        except Exception as exc:
            logger.info("DeepSeek stream unavailable (%s), falling back to Ollama", exc)
    async for chunk in _stream_ollama(messages, temperature, max_tokens):
        yield ("ollama", OLLAMA_MODEL, chunk)


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

async def _call_deepseek(messages: list, temperature: float, json_mode: bool, max_tokens: int) -> str:
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
        return resp.json()["choices"][0]["message"]["content"]


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

async def _call_ollama(messages: list, temperature: float, max_tokens: int) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


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
