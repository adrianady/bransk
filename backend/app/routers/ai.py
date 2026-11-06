"""AI observability endpoints: call event log + provider/embedding status."""
import json
import logging

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import get_db
from app.embeddings import embedding_status
from app.llm import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    LLM_PROVIDER,
    OLLAMA_EMBED_MODEL,
    OLLAMA_MODEL,
    active_model,
    active_provider,
    model_catalogue,
    provider_catalogue,
    provider_order,
    set_active_model,
    set_active_provider,
)
from app.pdf_rag import vector_stats
from app.reembed_worker import reembed_state

logger = logging.getLogger("chronokg.ai")
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
    """Current model configuration and embedding availability.

    `semantic_retrieval` says a backend answers; `semantic_search_effective`
    says the vectors actually stored in `documents` can be compared with it.
    The two differ when the store was embedded by another model (768-dim
    Ollama vectors vs a 1024-dim llama.cpp model): search then returns [] and
    the reason is visible here instead of being swallowed.
    """
    emb = await embedding_status()
    stats = await anyio.to_thread.run_sync(vector_stats)
    return {
        "provider_mode": LLM_PROVIDER,
        "deepseek_model": active_model(),  # legacy key: the model /api/ai/model returns
        "active_provider": active_provider(),
        "deepseek_model_env_default": DEEPSEEK_MODEL,
        "ollama_model": OLLAMA_MODEL,
        "configured_embed_model": OLLAMA_EMBED_MODEL,
        **emb,
        "embedding_url": __import__("app.embeddings", fromlist=["LLAMA_EMBED_URL"]).LLAMA_EMBED_URL,
        "pdf_chunks_indexed": stats["chunks"],
        "pdf_vector_dims": stats["dims"],
        "pdf_chunks_matchable": stats["matchable"],
        "semantic_search_effective": stats["matchable"] > 0,
        # Background repair of vectors written by another embedding model:
        # "se reindexează: 340/1020" is visible instead of silent CPU work.
        "reembed": reembed_state(),
    }


class ModelChoice(BaseModel):
    model_id: str = Field(..., max_length=200)
    provider: str | None = Field(None, max_length=40)


class ProviderChoice(BaseModel):
    provider: str = Field(..., max_length=40)


class CustomProvider(BaseModel):
    """A self-hosted or unlisted OpenAI-compatible endpoint."""
    base_url: str = Field(..., max_length=500, description="ex. http://127.0.0.1:8080/v1")
    api_key: str = Field("", max_length=500)
    model: str = Field("", max_length=200)
    label: str = Field("Personalizat", max_length=40)
    activate: bool = True
    save: bool = True


class OpenCodeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    base_url: str = Field(..., max_length=500)
    directory: str = Field(..., max_length=1000)
    # Accepted only to fail safely for old clients; never persisted or returned.
    password: str | None = Field(None, exclude=True, description="Deprecated; use OPENCODE_SERVER_PASSWORD")


@router.get("/ai/opencode")
async def ai_opencode_get():
    """Return OpenCode connection metadata without ever returning its password."""
    from app.opencode_provider import public_config

    return public_config()


@router.post("/ai/opencode")
async def ai_opencode_save(body: OpenCodeConfig):
    """Save connection metadata; Basic Auth secrets are environment-only."""
    from app.opencode_provider import save_config

    if body.password:
        raise HTTPException(400, "Parola OpenCode se configurează numai în environment-ul backend-ului")
    try:
        return save_config(body.base_url, body.directory)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _save_direct_config(request: Request, service: str, label: str):
    """Persist one Console/Go URL and key; only key presence is returned."""
    from app.opencode_console_provider import save_config

    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 2048:
                raise HTTPException(413, f"Cererea de configurare {label} este prea mare.")
            raw.extend(chunk)
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"Corpul cererii pentru {label} nu este JSON valid.") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, f"Corpul cererii pentru {label} trebuie să fie un obiect JSON.")
    if set(body) - {"base_url", "api_key", "clear_key"}:
        raise HTTPException(400, f"Corpul cererii pentru {label} conține câmpuri necunoscute.")
    base_url = body.get("base_url")
    api_key = body.get("api_key")
    clear_key = body.get("clear_key", False)
    if not isinstance(base_url, str):
        raise HTTPException(400, f"Adresa API {label} trebuie să fie text.")
    if api_key is not None and not isinstance(api_key, str):
        raise HTTPException(400, f"Cheia API {label} trebuie să fie text.")
    if not isinstance(clear_key, bool):
        raise HTTPException(400, "Opțiunea de ștergere a cheii trebuie să fie booleană.")
    try:
        return save_config(base_url, api_key, clear_key=clear_key, service=service)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/ai/opencode-console")
async def ai_opencode_console_get():
    """Return Console settings without ever returning the Bearer key."""
    from app.opencode_console_provider import public_config

    return public_config()


@router.post("/ai/opencode-console")
async def ai_opencode_console_save(request: Request):
    return await _save_direct_config(request, "zen", "OpenCode Console")


@router.get("/ai/opencode-go")
async def ai_opencode_go_get():
    """Return OpenCode Go settings; the subscription key itself is never returned."""
    from app.opencode_console_provider import public_config

    return public_config(service="go")


@router.post("/ai/opencode-go")
async def ai_opencode_go_save(request: Request):
    return await _save_direct_config(request, "go", "OpenCode Go")


@router.get("/ai/custom")
async def ai_custom_get():
    """The saved custom provider, without ever echoing the key back."""
    from app.llm import custom_provider

    data = custom_provider()
    if not data:
        return {"configured": False}
    return {
        "configured": True,
        "base_url": data.get("base_url", ""),
        "label": data.get("label", ""),
        "model": data.get("model", ""),
        "has_key": bool(data.get("api_key")),
    }


@router.post("/ai/custom/models")
async def ai_custom_models(body: CustomProvider):
    """Fetch the model list from a custom endpoint, before saving anything.

    This is the "fetch models" button: it tells the truth about whether the URL
    and key work, which is the one question a text field cannot answer.
    """
    from app.llm import _custom_models_url

    base = (body.base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(400, "URL-ul trebuie să înceapă cu http:// sau https://")
    if not base.endswith(("/v1", "/chat/completions")):
        base = base + "/v1"
    url = _custom_models_url(base)
    headers = {"Content-Type": "application/json"}
    if body.api_key:
        headers["Authorization"] = f"Bearer {body.api_key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Nu am putut contacta {url}: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        detail = resp.text[:200].replace("\n", " ")
        raise HTTPException(resp.status_code if resp.status_code < 500 else 502,
                            f"Endpointul a răspuns {resp.status_code} la {url}: {detail}")
    try:
        raw = resp.json().get("data", [])
    except ValueError as exc:
        raise HTTPException(502, f"Răspunsul de la {url} nu e JSON OpenAI-compatible") from exc
    models = [{"id": str(m["id"]), "owned_by": m.get("owned_by", "")} for m in raw if isinstance(m, dict) and m.get("id")]
    return {"url": url, "models": models, "count": len(models)}


@router.post("/ai/custom")
async def ai_custom_save(body: CustomProvider):
    """Save a custom endpoint, optionally making it the provider in use."""
    from app.llm import set_active_provider, set_custom_provider

    try:
        data = set_custom_provider(body.base_url, body.api_key, body.model, body.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    active = set_active_provider("custom") if body.activate else active_provider()
    if body.model:
        set_active_model(body.model, provider="custom")
    return {
        "saved": True,
        "base_url": data["base_url"],
        "label": data["label"],
        "model": data["model"],
        "active": active,
    }


@router.get("/ai/providers")
async def ai_providers():
    """Every provider the app knows, which one is in use, and the failover order.

    `configured` means the API key (or a local server) makes the provider
    usable right now; a provider can be listed without being configured, so the
    screen can show what an extra key would buy.
    """
    providers = provider_catalogue()
    return {
        "providers": providers,
        "active": active_provider(),
        "order": provider_order(),
        "configured": [p["id"] for p in providers if p["configured"]],
    }


@router.post("/ai/provider")
async def ai_provider_choose(body: ProviderChoice):
    """Switch the provider the chat uses, and remember it after a restart."""
    known = {p["id"] for p in provider_catalogue()}
    wanted = body.provider.strip().lower()
    if wanted and wanted not in known:
        raise HTTPException(400, f"Provider necunoscut: {wanted}. Cunoscute: {', '.join(sorted(known))}")
    if wanted in ("opencode_console", "opencode_go"):
        label = "OpenCode Go" if wanted == "opencode_go" else "OpenCode Console"
        if not next(
            (provider["configured"] for provider in provider_catalogue() if provider["id"] == wanted), False
        ):
            raise HTTPException(409, f"Configurează cheia API {label} și alege un model înainte de activare.")
        if wanted == "opencode_console":
            from app.llm import model_catalogue, provider_model
            from app.opencode_console_provider import free_model_message, is_free_tier_model

            selected_model = provider_model(wanted)
            if is_free_tier_model(selected_model):
                raise HTTPException(409, free_model_message(selected_model))
            catalogue = await model_catalogue(wanted)
            if selected_model in catalogue.get("blocked_free_model_ids", []):
                raise HTTPException(409, free_model_message(selected_model))
    chosen = set_active_provider(wanted)
    return {"active": chosen, "cleared": not wanted}


@router.get("/ai/models")
async def ai_models(provider: str | None = None):
    """The models the provider offers now, and which one is in use.

    `?provider=custom` (or any id) asks that provider instead of the active
    one, which is what the custom-endpoint panel needs after "fetch models".
    """
    if provider:
        from app.llm import provider_model

        wanted = provider.strip().lower()
        catalogue = await model_catalogue(wanted)
        catalogue["active"] = provider_model(wanted)
        return {**catalogue, "env_default": DEEPSEEK_MODEL}
    catalogue = await model_catalogue()
    return {**catalogue, "env_default": DEEPSEEK_MODEL}


@router.post("/ai/model")
async def ai_model_choose(body: ModelChoice):
    """Choose the model the chat uses, from now on and after a restart.

    The id is checked against the provider's own list when that list is
    readable: a typo must fail here, loudly, instead of turning into a 400
    from DeepSeek in the middle of writing a paragraph. An empty id clears
    the choice and returns to the .env default.
    """
    wanted = body.model_id.strip()
    provider = body.provider.strip().lower() if body.provider else active_provider()
    known_providers = {provider_info["id"] for provider_info in provider_catalogue()}
    if provider not in known_providers:
        raise HTTPException(400, f"Provider necunoscut: {provider}")
    if wanted:
        if provider == "opencode_console":
            from app.opencode_console_provider import free_model_message, is_free_tier_model

            if is_free_tier_model(wanted):
                raise HTTPException(400, free_model_message(wanted))
        catalogue = await model_catalogue(provider) if body.provider else await model_catalogue()
        if provider == "opencode_console" and wanted in catalogue.get("blocked_free_model_ids", []):
            from app.opencode_console_provider import free_model_message

            raise HTTPException(400, free_model_message(wanted))
        if catalogue.get("from_provider"):
            known = {m["id"] for m in catalogue["models"]}
            if wanted not in known:
                raise HTTPException(
                    status_code=400,
                    detail=f"Model necunoscut: {wanted}. Disponibile: {', '.join(sorted(known))}",
                )
    chosen = set_active_model(wanted, provider=provider) if body.provider else set_active_model(wanted)
    return {"active": chosen, "cleared": not wanted}
