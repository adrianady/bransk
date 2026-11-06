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


class LLMProviderError(RuntimeError):
    """Failure with a deliberately sanitized message safe to show in the UI."""

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


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

SETTING_MODEL = "deepseek_model"

# Two distinct services behind opencode.ai: Console (pay-as-you-go) and Go
# (subscription). Same Bearer protocol, separate endpoint, key and model.
OPENCODE_DIRECT_PROVIDERS = ("opencode_console", "opencode_go")


def _console_service(provider: str) -> str:
    return "go" if provider == "opencode_go" else "zen"

# Used when the provider cannot be asked (offline, no key). Deliberately
# conservative: 8k is the smallest window any DeepSeek model has had, so a
# prompt built on this guess is never rejected for being too long.
FALLBACK_CONTEXT_WINDOW = 8192
# The reply needs room of its own inside the window.
DEFAULT_MAX_OUTPUT = 4096

_catalogue_cache: dict = {"at": 0.0, "models": {}}
_CATALOGUE_TTL = 600.0  # seconds; the list barely changes


# The catalogue is already cached, but spinning a fresh event loop on every
# call still costs milliseconds per chat message. Cache the resolved number
# per model name, so switching providers/models cannot reuse a stale window.
_CONTEXT_WINDOW_TTL = 600.0
_context_window_cache: dict = {}


def active_context_window() -> int:
    """Tokens the active model accepts, as the provider reports it.

    Read from the provider's own catalogue when it is reachable, cached for a
    few minutes; falls back to the conservative constant otherwise. Never
    guessed upward: too small a number wastes context, too large a one gets
    the whole request rejected.
    """
    import asyncio
    import time

    name = active_model()
    cached = _context_window_cache.get(name)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CONTEXT_WINDOW_TTL:
        return cached[1]

    try:
        loop = asyncio.new_event_loop()
        try:
            models = loop.run_until_complete(_catalogue_cached())
        finally:
            loop.close()
        info = models.get(name) or models.get(name.split(":")[0])
        if info and info.get("context_window"):
            value = int(info["context_window"])
            _context_window_cache[name] = (now, value)
            return value
    except Exception as exc:  # noqa: BLE001 - a failed probe must not break a chat
        logger.info("Context window unavailable (%s); assuming %d",
                    type(exc).__name__, FALLBACK_CONTEXT_WINDOW)
    return FALLBACK_CONTEXT_WINDOW


def active_max_output() -> int:
    """How many tokens the reply may use, inside the same window.

    A quarter of the window, never below 256, never above the default. The
    floor cannot win over the window itself: on a 2k model the reply gets 500,
    not 512, because 512 would not leave room for the prompt.
    """
    window = active_context_window()
    return min(DEFAULT_MAX_OUTPUT, max(256, window // 4))


async def _catalogue_cached() -> dict:
    """Model id → {context_window, max_output_tokens}, cached briefly."""
    global _catalogue_cache
    now = time.monotonic()
    if _catalogue_cache["models"] and now - _catalogue_cache["at"] < _CATALOGUE_TTL:
        return _catalogue_cache["models"]
    cat = await model_catalogue()
    models = {m["id"]: m for m in cat.get("models", [])}
    if models:
        _catalogue_cache = {"at": now, "models": models}
    return models


def active_model() -> str:
    """The model to call: the one chosen in the UI, else the .env default.

    Read on every call, not once at import, so changing it in the app takes
    effect immediately instead of after a restart. A broken settings table
    must never break generation: any error falls back to the env default.
    """
    try:
        from app.db import get_db

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", [SETTING_MODEL]
            ).fetchone()
        finally:
            conn.close()
        chosen = (row[0] or "").strip() if row else ""
        return chosen or DEEPSEEK_MODEL
    except Exception:  # noqa: BLE001 - a missing DB must not stop the model call
        return DEEPSEEK_MODEL


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


def _candidate_chain() -> list[str]:
    """Providers to try, in order: the chosen one, then the failover list."""
    chosen = active_provider()
    chain = [chosen]
    for name in provider_order():
        if name not in chain:
            chain.append(name)
    return chain


async def _call_provider(provider: str, messages: list, temperature: float,
                         json_mode: bool, max_tokens: int) -> tuple[str, dict]:
    if provider == "mock":
        return "[mock]", {}
    if provider == "ollama":
        return await _call_ollama(messages, temperature, max_tokens, json_mode)
    if provider == "deepseek":
        return await _call_deepseek(messages, temperature, json_mode, max_tokens)
    if provider == "opencode":
        from app.opencode_provider import complete as opencode_complete

        return await opencode_complete(messages), {}
    return await _call_openai_compatible(provider, messages, temperature, json_mode, max_tokens)


def _model_for(provider: str) -> str:
    return provider_model(provider)


async def complete(
    messages: list,
    *,
    temperature: float = 0.2,
    json_mode: bool = False,
    max_tokens: int = 8192,
) -> Tuple[str, str, str]:
    """Single completion over the provider chain. Returns (provider, model, text)."""
    t0 = time.monotonic()
    chars = _prompt_chars(messages)
    if LLM_PROVIDER == "mock":
        log_ai_event("mock", "mock-model", "complete", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=6)
        return ("mock", "mock-model", "[mock]")

    errors: list[str] = []
    public_error: str | None = None
    for provider in _candidate_chain():
        if provider not in ("ollama", "mock") and not _provider_is_configured(provider):
            # Not configured: say so once, then move on. A missing key is a
            # configuration fact, not a failed request to retry.
            log_ai_event(provider, "", "complete", False, error="no_api_key",
                         latency_ms=0, prompt_chars=chars)
            errors.append(f"{provider}: no_api_key")
            continue
        try:
            text, usage = await _with_retry(
                lambda p=provider: _call_provider(p, messages, temperature, json_mode, max_tokens)
            )
            pt, ct = _usage_tokens(usage)
            model = _model_for(provider)
            log_ai_event(provider, model, "complete", True,
                         latency_ms=int((time.monotonic() - t0) * 1000),
                         prompt_chars=chars, reply_chars=len(text),
                         prompt_tokens=pt, completion_tokens=ct)
            return (provider, model, text)
        except Exception as exc:  # noqa: BLE001 - each provider may fail differently
            if isinstance(getattr(exc, "public_message", None), str):
                public_error = exc.public_message
            log_ai_event(provider, _model_for(provider), "complete", False, error=str(exc),
                         latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars)
            errors.append(f"{provider}: {type(exc).__name__}")
            if getattr(exc, "no_fallback", False):
                raise LLMProviderError(public_error or str(exc)) from exc
            logger.info("Provider %s unavailable (%s), trying the next one", provider, exc)
    logger.error("All LLM providers failed: %s", "; ".join(errors))
    if public_error:
        raise LLMProviderError(public_error)
    raise RuntimeError("No LLM provider answered (" + "; ".join(errors) + ")")


async def _stream_provider(provider: str, messages: list, temperature: float, max_tokens: int):
    if provider == "mock":
        yield "[mock]"
        return
    if provider == "ollama":
        async for chunk in _stream_ollama(messages, temperature, max_tokens):
            yield chunk
        return
    if provider == "deepseek":
        async for chunk in _stream_deepseek(messages, temperature, max_tokens):
            yield chunk
        return
    if provider == "opencode":
        from app.opencode_provider import stream_complete as opencode_stream

        async for chunk in opencode_stream(messages):
            yield chunk
        return
    async for chunk in _stream_openai_compatible(provider, messages, temperature, max_tokens):
        yield chunk


async def stream_complete(
    messages: list,
    *,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> AsyncGenerator[Tuple[str, str, str], None]:
    """Stream tokens over the provider chain. Yields (provider, model, chunk)."""
    t0 = time.monotonic()
    chars = _prompt_chars(messages)
    reply_chars = 0
    provider, model = "", ""
    public_error: str | None = None
    try:
        if LLM_PROVIDER == "mock":
            yield ("mock", "mock-model", "[mock]")
            log_ai_event("mock", "mock-model", "stream", True, latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars, reply_chars=6)
            return

        errors: list[str] = []
        for candidate in _candidate_chain():
            if candidate not in ("ollama", "mock") and not _provider_is_configured(candidate):
                log_ai_event(candidate, "", "stream", False, error="no_api_key",
                             latency_ms=0, prompt_chars=chars)
                errors.append(f"{candidate}: no_api_key")
                continue
            yielded = False
            try:
                async for chunk in _stream_provider(candidate, messages, temperature, max_tokens):
                    yielded = True
                    reply_chars += len(chunk)
                    provider, model = candidate, _model_for(candidate)
                    yield (provider, model, chunk)
                log_ai_event(candidate, _model_for(candidate), "stream", True,
                             latency_ms=int((time.monotonic() - t0) * 1000),
                             prompt_chars=chars, reply_chars=reply_chars)
                return
            except Exception as exc:  # noqa: BLE001
                if isinstance(getattr(exc, "public_message", None), str):
                    public_error = exc.public_message
                log_ai_event(candidate, _model_for(candidate), "stream", False, error=str(exc),
                             latency_ms=int((time.monotonic() - t0) * 1000),
                             prompt_chars=chars, reply_chars=reply_chars)
                if getattr(exc, "no_fallback", False):
                    raise LLMProviderError(public_error or str(exc)) from exc
                if yielded:
                    # Tokens already reached the screen; switching provider now
                    # would duplicate the answer.
                    logger.error("Stream from %s failed after partial output (%s)", candidate, exc)
                    raise
                logger.info("Stream from %s unavailable (%s), trying the next one", candidate, exc)
                errors.append(f"{candidate}: {type(exc).__name__}")
        if public_error:
            raise LLMProviderError(public_error)
        raise RuntimeError("No LLM provider answered (" + "; ".join(errors) + ")")
    except Exception as exc:
        log_ai_event(provider or "unknown", model, "stream", False, error=str(exc),
                     latency_ms=int((time.monotonic() - t0) * 1000), prompt_chars=chars,
                     reply_chars=reply_chars)
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
            resp = await client.get(_provider_url(provider, models=True), headers=_provider_headers(provider))
            return resp.status_code == 200
    except Exception:
        return False


async def model_catalogue(for_provider: str | None = None) -> dict:
    """The models the provider offers right now, with what each can do.

    Read from the provider, never hard-coded: the list changes (deepseek-chat
    is already an alias the API resolves to deepseek-flash), so a menu written
    into the source would offer models that no longer exist. When the provider
    cannot be reached the current choice is returned alone, marked as such —
    the screen then still shows what is in use instead of an empty select.
    """
    provider = (for_provider or active_provider()).strip().lower()
    current = _model_for(provider)
    if provider == "opencode":
        from app.opencode_provider import OpenCodeError, model_catalogue as opencode_models

        try:
            return await opencode_models()
        except OpenCodeError as exc:
            return {
                "models": [], "active": current, "provider": provider,
                "from_provider": False, "note": str(exc),
            }
    if provider in OPENCODE_DIRECT_PROVIDERS:
        from app.opencode_console_provider import (
            OpenCodeConsoleError,
            free_model_message,
            is_free_tier_model,
            model_catalogue as opencode_console_models,
        )

        service = _console_service(provider)
        label = "OpenCode Go" if service == "go" else "OpenCode Console"
        try:
            result = await opencode_console_models(service=service)
        except OpenCodeConsoleError as exc:
            unsupported = service == "zen" and is_free_tier_model(current)
            return {
                "models": [], "active": current, "provider": provider,
                "from_provider": False, "active_supported": not unsupported,
                "free_model_ids": [], "blocked_free_model_ids": [],
                "note": free_model_message(current) if unsupported else str(exc),
            }
        models = [
            {**model, "is_active": model["id"] == current}
            for model in result["models"]
        ]
        ids = {model["id"] for model in models}
        free_ids = set(result.get("free_model_ids", []))
        blocked_ids = set(result.get("blocked_free_model_ids", []))
        active_supported = current not in blocked_ids
        if not active_supported:
            note = free_model_message(current)
        elif models:
            note = ""
        elif blocked_ids:
            note = "Catalogul Console conține doar modele gratuite, disponibile numai prin clientul oficial OpenCode."
        else:
            note = f"{label} nu a returnat modele text compatibile."
        return {
            "models": models,
            "active": current,
            "provider": provider,
            "from_provider": bool(result.get("from_provider", True)),
            "active_listed": current in ids,
            "active_supported": active_supported,
            "free_model_ids": sorted(free_ids),
            "blocked_free_model_ids": sorted(blocked_ids),
            "note": note,
        }
    if not provider_key(provider):
        return {"models": [], "active": current, "provider": provider, "from_provider": False,
                "note": f"Fără {_OPENAI_COMPATIBLE.get(provider, {}).get('key_env', 'DEEPSEEK_API_KEY')} nu pot citi lista de modele."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_provider_url(provider, models=True), headers=_provider_headers(provider))
            resp.raise_for_status()
            raw = resp.json().get("data", [])
    except Exception as exc:  # noqa: BLE001 - network errors are heterogeneous here
        logger.info("Model catalogue unavailable (%s)", type(exc).__name__)
        return {"models": [], "active": current, "provider": provider, "from_provider": False,
                "note": "Nu am putut citi lista de modele de la provider; merge modelul activ."}

    models = []
    for m in raw:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        effort = (m.get("effort") or {})
        models.append({
            "id": str(m["id"]),
            "name": str(m.get("name") or m["id"]),
            "context_window": m.get("context_window"),
            "max_output_tokens": m.get("max_output_tokens"),
            "input_modalities": m.get("input_modalities") or [],
            "output_modalities": m.get("output_modalities") or [],
            "effort_levels": effort.get("supported_levels") or [],
            "is_active": str(m["id"]) == current,
        })
    if not models:
        return {"models": [], "active": current, "provider": provider, "from_provider": False,
                "note": "Providerul nu a returnat niciun model."}
    ids = {m["id"] for m in models}
    return {
        "models": models,
        "active": current,
        "provider": provider,
        "from_provider": True,
        # A model set in .env may be an alias the list does not show.
        "active_listed": current in ids,
        "note": "" if current in ids else
                f"Modelul activ ({current}) nu apare în listă — poate fi un alias.",
    }


def set_active_model(model_id: str, provider: str | None = None) -> str:
    """Remember a model for the chosen provider; empty clears its override."""
    from app.db import get_db

    target = (provider or active_provider()).strip().lower()
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = excluded.updated_at""",
            # `deepseek_model` stays the DeepSeek slot it always was, so a
            # choice made before multi-provider support keeps working.
            ["deepseek_model" if target == "deepseek" else f"llm_model_{target}", model_id.strip()],
        )
        conn.commit()
    finally:
        conn.close()
    return active_model() if target == "deepseek" else provider_model(target)



# ── Provider registry ──────────────────────────────────────────────────────
# Every cloud provider below speaks the OpenAI chat-completions shape, so one
# client serves them all. DeepSeek keeps its own settings (key, URL, default
# model) for backwards compatibility with existing .env files; the rest are
# configured with their standard variable names.
#
# Failover order: the provider chosen in the UI first, then LLM_PROVIDERS from
# .env, then DeepSeek (if a key exists), then a local Ollama. A provider with
# no API key is skipped with a log line, never an exception: an unconfigured
# key must not take the chat down.

_OPENAI_COMPATIBLE = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "key_env": "OPENROUTER_API_KEY",
        "default_model": "anthropic/claude-3.5-sonnet",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1/chat/completions",
        "models_url": "https://api.mistral.ai/v1/models",
        "key_env": "MISTRAL_API_KEY",
        "default_model": "mistral-large-latest",
    },
    "together": {
        "label": "Together",
        "base_url": "https://api.together.xyz/v1/chat/completions",
        "models_url": "https://api.together.xyz/v1/models",
        "key_env": "TOGETHER_API_KEY",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
}

# Recommendation for OpenRouter: their terms ask for an identifying referer.
_EXTRA_HEADERS = {
    "openrouter": {"HTTP-Referer": "http://localhost:8000", "X-Title": "Bransk"},
}

SETTING_PROVIDER = "llm_provider"

# A provider the user adds from the screen: any OpenAI-compatible endpoint
# (a local llama.cpp server, a proxy, a service we do not ship). The key is
# stored in app_settings, not .env: .env is for secrets a restart may reload,
# this is a setting the app must be able to change without one.
SETTING_CUSTOM = "llm_custom_provider"


def _setting(key: str, default: str = "") -> str:
    try:
        from app.db import get_db

        conn = get_db()
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [key]).fetchone()
        finally:
            conn.close()
        return (row[0] or "").strip() if row else default
    except Exception:  # noqa: BLE001 - a broken settings table must not stop a chat
        return default


def _set_setting(key: str, value: str) -> None:
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = excluded.updated_at""",
            [key, value],
        )
        conn.commit()
    finally:
        conn.close()


def custom_provider() -> dict | None:
    """The user-defined provider, or None when it was not set up."""
    import json

    raw = _setting(SETTING_CUSTOM)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not data.get("base_url"):
        return None
    return data


def set_custom_provider(base_url: str, api_key: str, model: str = "", label: str = "Personalizat") -> dict:
    """Validate and remember a custom OpenAI-compatible endpoint."""
    import json

    base = (base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("URL-ul trebuie să înceapă cu http:// sau https://")
    if not base.endswith("/chat/completions") and not base.endswith("/v1"):
        base = base + "/v1"
    data = {"base_url": base, "api_key": (api_key or "").strip(), "model": (model or "").strip(),
            "label": (label or "Personalizat").strip()[:40]}
    _set_setting(SETTING_CUSTOM, json.dumps(data))
    return data


def _custom_models_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url[: -len("/chat/completions")] + "/models"
    return base_url.rstrip("/") + "/models"


def _custom_chat_url(base_url: str) -> str:
    return base_url if base_url.endswith("/chat/completions") else base_url.rstrip("/") + "/chat/completions"


def provider_key(provider: str) -> str:
    """The API key for a provider, read at call time (tests and .env edits)."""
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_API_KEY", "") or DEEPSEEK_API_KEY
    if provider == "custom":
        data = custom_provider()
        return (data or {}).get("api_key", "")
    if provider in OPENCODE_DIRECT_PROVIDERS:
        from app.opencode_console_provider import api_key

        return api_key(service=_console_service(provider))
    spec = _OPENAI_COMPATIBLE.get(provider)
    return os.getenv(spec["key_env"], "") if spec else ""


def _provider_is_configured(provider: str) -> bool:
    if provider in ("ollama", "mock"):
        return True
    if provider == "opencode":
        from app.opencode_provider import is_configured

        return is_configured()
    if provider in OPENCODE_DIRECT_PROVIDERS:
        from app.opencode_console_provider import is_configured

        return (is_configured(service=_console_service(provider))
                and bool(provider_model(provider)))
    return bool(provider_key(provider))


def provider_model_value(provider: str) -> str:
    """The model typed for a provider in the UI ("" when none)."""
    return _setting(f"llm_model_{provider}")


def provider_model(provider: str) -> str:
    """Model for a provider: the choice made in the UI, else its default."""
    chosen = provider_model_value(provider)
    if chosen:
        return chosen
    if provider == "ollama":
        return OLLAMA_MODEL
    if provider == "mock":
        return "mock-model"
    if provider == "deepseek":
        return active_model()
    if provider == "custom":
        saved = (custom_provider() or {}).get("model", "")
        return saved or provider_model_value(provider) or ""
    spec = _OPENAI_COMPATIBLE.get(provider)
    return spec["default_model"] if spec else ""


def active_provider() -> str:
    """Provider chosen in the UI, else the first configured one.

    `auto` keeps the historical behaviour: DeepSeek when a key exists, Ollama
    otherwise — but an explicit choice always wins.
    """
    try:
        from app.db import get_db

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", [SETTING_PROVIDER]
            ).fetchone()
        finally:
            conn.close()
        if row and (row[0] or "").strip():
            return (row[0] or "").strip()
    except Exception:  # noqa: BLE001
        pass
    # No explicit choice: keep the historical default (DeepSeek, whose model the
    # UI reads through active_model()); Ollama stays the failover, not the
    # default, so a saved model choice is never silently overridden.
    if provider_key("deepseek"):
        return "deepseek"
    for name in provider_order():
        if name != "deepseek" and (name == "ollama" or provider_key(name)):
            return name
    return "deepseek"


def set_active_provider(provider: str) -> str:
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = excluded.updated_at""",
            [SETTING_PROVIDER, (provider or "").strip()],
        )
        conn.commit()
    finally:
        conn.close()
    return active_provider()


def provider_order() -> list[str]:
    """Failover order: the explicit LLM_PROVIDERS list, else stock + custom.

    A local Ollama is *not* appended here: this project runs its models on
    llama.cpp / a custom endpoint, and advertising Ollama as the reserve step
    was a leftover, not a decision. Ollama still works when named explicitly
    in LLM_PROVIDERS or chosen in the screen.
    """
    raw = os.getenv("LLM_PROVIDERS", "").strip()
    if raw:
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    order = ["deepseek"]
    if custom_provider():
        order.append("custom")
    return order


def provider_catalogue() -> list[dict]:
    """Every provider the app knows about, and whether it is usable now."""
    out = []
    for pid in ("deepseek", *_OPENAI_COMPATIBLE.keys(), "custom", "opencode_console", "opencode_go", "ollama", "opencode", "mock"):
        if pid == "deepseek":
            label, key, model = "DeepSeek", provider_key("deepseek"), active_model()
        elif pid == "ollama":
            label, key, model = "Ollama (local)", "", provider_model(pid)
        elif pid == "custom":
            data = custom_provider() or {}
            label, key, model = data.get("label") or "Personalizat", data.get("api_key", ""), data.get("model", "")
            configured = bool(data.get("base_url"))
        elif pid == "opencode":
            from app.opencode_provider import is_configured

            label, key, model = "OpenCode (nativ)", "", provider_model(pid)
            configured = is_configured()
        elif pid in OPENCODE_DIRECT_PROVIDERS:
            from app.opencode_console_provider import is_configured

            label = "OpenCode Go" if pid == "opencode_go" else "OpenCode Console"
            key, model = "", provider_model(pid)
            configured = is_configured(service=_console_service(pid)) and bool(model)
        elif pid == "mock":
            label = "Mock (offline)"
            amount = ("", "mock-model")
        else:
            spec = _OPENAI_COMPATIBLE[pid]
            label, key, model = spec["label"], provider_key(pid), provider_model(pid)
        if pid not in ("custom", "opencode", *OPENCODE_DIRECT_PROVIDERS):
            if pid == "mock":
                label, key, model = "Mock (offline)", "", "mock-model"
            configured = pid in ("ollama", "mock") or bool(key)
        out.append({
            "id": pid,
            "label": label,
            "configured": configured,
            "model": model,
            "active": pid == active_provider(),
        })
    return out


def _provider_headers(provider: str) -> dict:
    headers = {"Content-Type": "application/json"}
    key = provider_key(provider)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(_EXTRA_HEADERS.get(provider, {}))
    return headers


def _provider_url(provider: str, *, models: bool = False) -> str:
    if provider == "custom":
        data = custom_provider() or {}
        base = data.get("base_url", "")
        if not base:
            return ""
        return _custom_models_url(base) if models else _custom_chat_url(base)
    if provider in OPENCODE_DIRECT_PROVIDERS:
        from app.opencode_console_provider import chat_url, models_url

        service = _console_service(provider)
        return (models_url(service=service) if models
                else chat_url(service=service))
    if provider == "deepseek":
        return DEEPSEEK_API_URL.replace("/chat/completions", "/models") if models else DEEPSEEK_API_URL
    spec = _OPENAI_COMPATIBLE.get(provider, {})
    return spec.get("models_url" if models else "base_url", "")


async def _call_openai_compatible(provider: str, messages: list, temperature: float,
                                  json_mode: bool, max_tokens: int) -> tuple[str, dict]:
    model_id = provider_model(provider)
    service = ""
    if provider in OPENCODE_DIRECT_PROVIDERS:
        from app.opencode_console_provider import OpenCodeConsoleError, free_model_message, is_free_tier_model

        service = _console_service(provider)
        if service == "zen" and is_free_tier_model(model_id):
            raise OpenCodeConsoleError(free_model_message(model_id), no_fallback=True)
    body = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if service:
        from app.opencode_console_provider import request_details

        url, headers = request_details(service=service)
    else:
        url, headers = _provider_url(provider), _provider_headers(provider)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        resp = await client.post(url, headers=headers, json=body)
        if service:
            from app.opencode_console_provider import validate_response

            validate_response(resp, model_id=model_id, service=service)
        else:
            resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], data.get("usage") or {}


async def _stream_openai_compatible(provider: str, messages: list, temperature: float, max_tokens: int):
    model_id = provider_model(provider)
    service = ""
    if provider in OPENCODE_DIRECT_PROVIDERS:
        from app.opencode_console_provider import (
            OpenCodeConsoleError,
            free_model_message,
            is_free_tier_model,
            request_details,
        )

        service = _console_service(provider)
        if service == "zen" and is_free_tier_model(model_id):
            raise OpenCodeConsoleError(free_model_message(model_id), no_fallback=True)
        url, headers = request_details(service=service)
    else:
        url, headers = _provider_url(provider), _provider_headers(provider)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json={
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
        ) as resp:
            if service:
                from app.opencode_console_provider import validate_response

                validate_response(resp, model_id=model_id, service=service)
            else:
                resp.raise_for_status()
            async for chunk in _iter_sse_content(resp):
                yield chunk


# ── DeepSeek ────────────────────────────────────────────────────────────────

async def _call_deepseek(messages: list, temperature: float, json_mode: bool, max_tokens: int) -> tuple[str, dict]:
    body = {
        "model": active_model(),
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
                "model": active_model(),
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
        "model": provider_model("ollama"),
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
                "model": provider_model("ollama"),
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
