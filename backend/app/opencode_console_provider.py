"""OpenCode Console inference API (Bearer key, OpenAI-compatible chat API)."""
from __future__ import annotations

import json
import ipaddress
import logging
import re
import secrets
from urllib.parse import unquote, urlsplit

import httpx

from app.db import get_db

logger = logging.getLogger("chronokg.opencode_console")

SETTING = "llm_opencode_console"
SETTING_SESSION = "llm_opencode_console_session"
SETTING_GO = "llm_opencode_go"
SETTING_GO_SESSION = "llm_opencode_go_session"
DEFAULT_API_URL = "https://opencode.ai/inference"
GO_API_URL = "https://opencode.ai/inference/go"
API_KEY_LIMIT = 500
FREE_MODEL_IDS = {"big-pickle"}

# Console (pay-as-you-go) and Go (subscription) are separate services behind the
# same opencode.ai host: own key, own saved model, own endpoint. Console blocks
# free models for external clients; Go includes them.
SERVICES: dict[str, dict] = {
    "zen": {
        "label": "OpenCode Console",
        "setting": SETTING,
        "session_setting": SETTING_SESSION,
        "default_url": DEFAULT_API_URL,
        "path": "/inference",
        "allows_free_models": False,
    },
    "go": {
        "label": "OpenCode Go",
        "setting": SETTING_GO,
        "session_setting": SETTING_GO_SESSION,
        "default_url": GO_API_URL,
        "path": "/inference/go",
        "allows_free_models": True,
    },
}


def _service(name: str = "zen") -> dict:
    try:
        return SERVICES[name]
    except KeyError as exc:
        raise ValueError(f"Serviciu OpenCode necunoscut: {name}") from exc


class OpenCodeConsoleError(RuntimeError):
    """Safe-to-return provider error; never includes a key or response body."""

    def __init__(self, message: str, *, no_fallback: bool = False):
        super().__init__(message)
        self.public_message = message
        self.no_fallback = no_fallback


def is_free_tier_model(model_id: str, metadata: dict | None = None) -> bool:
    """Free Console models are supported only by the official OpenCode client."""
    normalized = (model_id or "").strip().lower()
    if normalized in FREE_MODEL_IDS or normalized.endswith("-free"):
        return True

    cost = metadata.get("cost") if isinstance(metadata, dict) else None
    text_cost = cost.get("text") if isinstance(cost, dict) else None
    if not isinstance(text_cost, dict):
        return False
    input_cost = text_cost.get("input")
    output_cost = text_cost.get("output")
    input_price = input_cost.get("price") if isinstance(input_cost, dict) else None
    output_price = output_cost.get("price") if isinstance(output_cost, dict) else None
    return (
        isinstance(input_price, (int, float))
        and not isinstance(input_price, bool)
        and input_price == 0
        and isinstance(output_price, (int, float))
        and not isinstance(output_price, bool)
        and output_price == 0
    )


def free_model_message(model_id: str) -> str:
    display_name = "Big Pickle" if model_id.strip().lower() == "big-pickle" else model_id
    return (
        f"Modelul gratuit {display_name} este disponibil numai prin clientul oficial OpenCode. "
        "Pentru Console direct din Bransk alege un model pay-as-you-go sau folosește OpenCode local."
    )


def validate_response(response: httpx.Response, *, model_id: str | None = None,
                      service: str = "zen") -> None:
    """Translate service status codes into safe, actionable messages."""
    spec = _service(service)
    label = spec["label"]
    status = response.status_code
    if 300 <= status < 400:
        raise OpenCodeConsoleError(
            f"{label} a redirecționat cererea; redirecturile sunt dezactivate."
        )
    if status == 401:
        raise OpenCodeConsoleError(
            f"Cheia API {label} este invalidă sau expirată (HTTP 401)."
        )
    if status == 402:
        if spec["allows_free_models"]:
            reason = ("Verifică abonamentul Go (Go, Go Plus sau Go Teams), limita lunară "
                      "și opțiunea Extra Usage în OpenCode Console.")
        else:
            reason = ("Verifică facturarea, metoda de plată și soldul/creditele pay-as-you-go "
                      "în OpenCode Console.")
        raise OpenCodeConsoleError(
            f"{label} a cerut plată (HTTP 402) pentru inferență. {reason} Accesul la catalog "
            "nu confirmă că inferența este activată.",
            no_fallback=True,
        )
    if status == 403:
        raise OpenCodeConsoleError(
            f"{label} a refuzat accesul (HTTP 403). Modelele Free-tier, precum Big Pickle și "
            "Beam Free, nu sunt disponibile din clienți externi prin endpointul pay-as-you-go. "
            "Folosește un model plătit, un abonament OpenCode Go sau providerul OpenCode local.",
            no_fallback=True,
        )
    if status == 429:
        if model_id and is_free_tier_model(model_id):
            raise OpenCodeConsoleError(
                f"{label} a respins cererea pentru {model_id} (HTTP 429). "
                f"{free_model_message(model_id)}",
                no_fallback=True,
            )
        raise OpenCodeConsoleError(
            f"{label} a limitat cererea (HTTP 429); verifică limita de rată, cota/soldul "
            "și pagina oficială de stare.",
            no_fallback=True,
        )
    if response.is_error:
        raise OpenCodeConsoleError(
            f"{label} a respins cererea (HTTP {status}); verifică modelul și configurația."
        )


def _normalize_api_url(value: str, *, service: str = "zen") -> str:
    expected_path = _service(service)["path"]
    raw = (value or "").strip()
    if not raw or len(raw) > 500 or any(ord(char) <= 32 or ord(char) == 127 for char in raw):
        raise ValueError("Adresa OpenCode Console nu este un URL valid")
    if "?" in raw or "#" in raw or "\\" in raw:
        raise ValueError("Adresa API nu poate conține query, fragment sau backslash")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Adresa OpenCode Console nu este un URL valid") from exc

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Nu include credențiale în URL; cheia se introduce separat")

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("Numele gazdei din URL nu este valid")
    official_host = host == "opencode.ai"
    try:
        address = ipaddress.ip_address(host)
        loopback = address.is_loopback
        host_for_url = f"[{host}]" if address.version == 6 else host
    except ValueError:
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError("Numele gazdei din URL nu este valid")
        loopback = host == "localhost" or host.endswith(".localhost")
        host_for_url = host

    if official_host:
        if parsed.scheme != "https":
            raise ValueError("OpenCode Console remote necesită HTTPS")
        if port not in (None, 443):
            raise ValueError("OpenCode Console remote acceptă doar portul HTTPS standard")
        trimmed = parsed.path.rstrip("/")
        if trimmed == expected_path or (service == "zen" and trimmed == ""):
            path = expected_path
        else:
            raise ValueError(
                f"Pentru {_service(service)['label']} folosește {_service(service)['default_url']}. "
                f"OpenCode Go are URL-ul propriu {GO_API_URL}."
            )
    elif loopback:
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Serverele locale acceptă doar HTTP sau HTTPS")
        path = parsed.path.rstrip("/")
        decoded_path = unquote(path)
        if any(part == ".." for part in decoded_path.split("/")):
            raise ValueError("Calea URL nu poate traversa directoare")
    else:
        raise ValueError("Pentru siguranță, folosește opencode.ai sau un server localhost/loopback")

    netloc = host_for_url
    default_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != default_port:
        netloc += f":{port}"
    return f"{parsed.scheme}://{netloc}{path}"


def _read_config(*, service: str = "zen") -> dict[str, str]:
    spec = _service(service)
    default = {"base_url": spec["default_url"], "api_key": ""}
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [spec["setting"]]).fetchone()
        if not row:
            return default
        try:
            value = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            return default
        if not isinstance(value, dict):
            return default
        return {
            "base_url": str(value.get("base_url") or spec["default_url"]),
            "api_key": str(value.get("api_key") or ""),
        }
    finally:
        conn.close()


def public_config(*, service: str = "zen") -> dict:
    """Settings visible to the UI. The key itself is never returned."""
    spec = _service(service)
    config = _read_config(service=service)
    try:
        api_url = _normalize_api_url(config["base_url"], service=service)
        valid_url = True
    except ValueError:
        api_url = spec["default_url"]
        valid_url = False
    has_key = bool(config["api_key"])
    return {
        "configured": valid_url and has_key,
        "base_url": api_url,
        "has_key": has_key,
    }


def _validated_key(value: str) -> str:
    raw = value or ""
    if len(raw) > API_KEY_LIMIT:
        raise ValueError("Cheia API nu poate depăși 500 de caractere")
    if any(ord(char) < 33 or ord(char) > 126 for char in raw):
        raise ValueError("Cheia API trebuie să conțină doar caractere ASCII vizibile")
    return raw.strip()


def save_config(base_url: str, api_key: str | None = None, *, clear_key: bool = False,
                service: str = "zen") -> dict:
    """Save one service's URL and key locally, returning only key presence."""
    spec = _service(service)
    if clear_key and api_key:
        raise ValueError("Trimite fie o cheie nouă, fie clear_key, nu ambele")
    normalized = _normalize_api_url(base_url, service=service)
    previous = _read_config(service=service)
    key = "" if clear_key else previous["api_key"]
    submitted_key = _validated_key(api_key) if api_key is not None else ""
    try:
        previous_url = _normalize_api_url(previous["base_url"], service=service)
    except ValueError:
        previous_url = ""
    reset_session = (
        clear_key
        or previous_url != normalized
        or bool(submitted_key and submitted_key != previous["api_key"])
    )
    if not clear_key and not submitted_key:
        try:
            old_origin = urlsplit(_normalize_api_url(previous["base_url"], service=service))[:2]
            new_origin = urlsplit(normalized)[:2]
            if old_origin != new_origin:
                key = ""
        except ValueError:
            key = ""
    if submitted_key:
        key = submitted_key

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            [spec["setting"], json.dumps({"base_url": normalized, "api_key": key})],
        )
        if reset_session:
            conn.execute("DELETE FROM app_settings WHERE key = ?", [spec["session_setting"]])
        conn.commit()
    finally:
        conn.close()
    return public_config(service=service)


def api_key(*, service: str = "zen") -> str:
    config = _read_config(service=service)
    try:
        _normalize_api_url(config["base_url"], service=service)
        return _validated_key(config["api_key"])
    except ValueError:
        return ""


def _base_url(*, service: str = "zen") -> str:
    config = _read_config(service=service)
    try:
        return _normalize_api_url(config["base_url"], service=service)
    except ValueError as exc:
        raise OpenCodeConsoleError(
            f"Adresa {_service(service)['label']} salvată nu este validă; verifică setările."
        ) from exc


def is_configured(*, service: str = "zen") -> bool:
    config = _read_config(service=service)
    try:
        _normalize_api_url(config["base_url"], service=service)
        return bool(_validated_key(config["api_key"]))
    except ValueError:
        return False


def chat_url(*, service: str = "zen") -> str:
    return f"{_base_url(service=service)}/openai/v1/chat/completions"


def models_url(*, service: str = "zen") -> str:
    return f"{_base_url(service=service)}/v1/models"


def request_details(*, models: bool = False, service: str = "zen") -> tuple[str, dict[str, str]]:
    """Read URL and key together so concurrent config edits cannot cross-wire them."""
    spec = _service(service)
    config = _read_config(service=service)
    try:
        base_url = _normalize_api_url(config["base_url"], service=service)
        key = _validated_key(config["api_key"])
    except ValueError as exc:
        raise OpenCodeConsoleError(f"Setările {spec['label']} salvate nu sunt valide.") from exc
    if not key:
        raise OpenCodeConsoleError(f"Configurează cheia API {spec['label']}.")
    url = f"{base_url}/v1/models" if models else f"{base_url}/openai/v1/chat/completions"
    # Go asks clients to identify themselves instead of using a bare HTTP library UA.
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "bransk/1.0 (chronokg; local-first knowledge graph)",
    }
    if not models:
        headers["x-opencode-session"] = _session_id(service=service)
    return url, headers


def _session_id(*, service: str = "zen") -> str:
    """Return one persistent, non-secret session ID per service and Bransk DB."""
    setting = _service(service)["session_setting"]
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [setting]).fetchone()
        value = str(row[0] or "") if row else ""
        if re.fullmatch(r"ses_[a-f0-9]{32}", value):
            return value
        value = f"ses_{secrets.token_hex(16)}"
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            [setting, value],
        )
        conn.commit()
        return value
    finally:
        conn.close()


async def model_catalogue(*, service: str = "zen") -> dict:
    """Fetch available text chat models for one service.

    Console blocks zero-price models for external clients, so they are listed in
    `blocked_free_model_ids` and left out of `models`. Go includes them, so they
    stay selectable and are only reported in `free_model_ids`.
    """
    spec = _service(service)
    label = spec["label"]
    url, headers = request_details(models=True, service=service)

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.info("%s model discovery failed (%s)", label, type(exc).__name__)
        raise OpenCodeConsoleError(f"Nu mă pot conecta la {label}.") from exc

    validate_response(response, service=service)
    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenCodeConsoleError(f"{label} a returnat un răspuns JSON invalid.") from exc
    raw = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise OpenCodeConsoleError(f"Lista de modele {label} are un format neașteptat.")

    models = []
    free_model_ids = []
    blocked_free_model_ids = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if item.get("type") not in (None, "chat"):
            continue
        model_id = str(item["id"])
        is_free = is_free_tier_model(model_id, item)
        if is_free:
            free_model_ids.append(model_id)
            if not spec["allows_free_models"]:
                blocked_free_model_ids.append(model_id)
                continue
        modalities = item.get("modalities") if isinstance(item.get("modalities"), dict) else {}
        inputs = modalities.get("input") if isinstance(modalities.get("input"), list) else []
        if inputs and "text" not in inputs:
            continue
        limit = item.get("limit") if isinstance(item.get("limit"), dict) else {}
        capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
        reasoning = capabilities.get("reasoning") if isinstance(capabilities.get("reasoning"), dict) else {}
        models.append({
            "id": model_id,
            "name": str(item.get("name") or item["id"]),
            "context_window": limit.get("context"),
            "max_output_tokens": limit.get("output"),
            "input_modalities": inputs,
            "output_modalities": modalities.get("output") or [],
            "effort_levels": reasoning.get("effort") or [],
            "free": is_free,
        })
    return {
        "models": models,
        "from_provider": True,
        "free_model_ids": free_model_ids,
        "blocked_free_model_ids": blocked_free_model_ids,
        "service": service,
    }
