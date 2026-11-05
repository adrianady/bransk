"""Native OpenCode HTTP provider (experimental V2 API, no OpenAI shim)."""
import asyncio
import json
import logging
import os
import re
import time
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.db import get_db

logger = logging.getLogger("chronokg.opencode")

SETTING_OPENCODE = "llm_opencode_config"
SETTING_MODEL = "llm_model_opencode"
API_VERSION = "v2.1.2"
REQUEST_TIMEOUT = 120.0


class OpenCodeError(RuntimeError):
    """Safe-to-return OpenCode error; never includes headers or secret values."""


def _read_config() -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [SETTING_OPENCODE]).fetchone()
        if not row:
            return {}
        try:
            value = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}

        # Earlier local builds stored the Basic Auth password beside the
        # connection settings. Strip that legacy field on first read: secrets
        # now come only from the backend process environment.
        if "password" in value:
            value.pop("password", None)
            conn.execute(
                "UPDATE app_settings SET value = ?, updated_at = datetime('now') WHERE key = ?",
                [json.dumps(value, ensure_ascii=False), SETTING_OPENCODE],
            )
            conn.commit()
        return value
    finally:
        conn.close()


def public_config() -> dict:
    """Configuration safe for the UI; the password is never returned."""
    config = _read_config()
    base_url = str(config.get("base_url") or "")
    directory = str(config.get("directory") or "")
    try:
        _normalize_base_url(base_url)
        _normalize_directory(directory)
        valid = True
    except ValueError:
        valid = False
    return {
        "configured": valid,
        "base_url": base_url,
        "directory": directory,
        "has_password": bool(os.getenv("OPENCODE_SERVER_PASSWORD", "")),
        "api_protocol": API_VERSION,
    }


def _normalize_base_url(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Adresa OpenCode trebuie să înceapă cu http:// sau https://")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Nu include date de autentificare în URL; parola se configurează separat")
    if parsed.query or parsed.fragment:
        raise ValueError("URL-ul OpenCode nu poate conține query sau fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("Introdu adresa serverului, fără cale; API-ul nativ folosește /api")

    # This integration is intentionally local-only. The app can be exposed on a
    # LAN without turning its API into a proxy to arbitrary hosts or services.
    host = parsed.hostname.lower().rstrip(".")
    is_loopback = host == "localhost" or host.endswith(".localhost")
    try:
        import ipaddress
        is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not is_loopback:
        raise ValueError("Pentru siguranță, providerul OpenCode acceptă doar localhost sau o adresă loopback")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Portul serverului OpenCode nu este valid") from exc

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _normalize_directory(value: str) -> str:
    directory = str(value or "").strip()
    if not directory:
        raise ValueError("Directorul OpenCode este obligatoriu și trebuie să fie o cale absolută")
    if len(directory) > 1000 or "\x00" in directory or not (
        directory.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", directory)
    ):
        raise ValueError("Directorul OpenCode trebuie să fie o cale absolută")
    return directory


def save_config(base_url: str, directory: str) -> dict:
    normalized = _normalize_base_url(base_url)
    directory = _normalize_directory(directory)

    serialized = json.dumps(
        {"base_url": normalized, "directory": directory},
        ensure_ascii=False,
    )
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            [SETTING_OPENCODE, serialized],
        )
        conn.commit()
    finally:
        conn.close()
    return public_config()


def is_configured() -> bool:
    config = _read_config()
    try:
        _normalize_base_url(str(config.get("base_url") or ""))
        _normalize_directory(str(config.get("directory") or ""))
        return True
    except ValueError:
        return False


def selected_model() -> str:
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [SETTING_MODEL]).fetchone()
        return str(row[0] or "").strip() if row else ""
    finally:
        conn.close()


def set_selected_model(model_id: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            [SETTING_MODEL, (model_id or "").strip()],
        )
        conn.commit()
    finally:
        conn.close()


def _api_root(config: dict | None = None) -> str:
    config = config or _read_config()
    try:
        base = _normalize_base_url(str(config.get("base_url") or ""))
    except ValueError as exc:
        raise OpenCodeError("Adresa OpenCode salvată nu este validă; verifică setările providerului") from exc
    return base + "/api"


def _request_config() -> tuple[str, dict, dict]:
    config = _read_config()
    if not is_configured():
        raise OpenCodeError("Configurează adresa serverului și directorul OpenCode")
    base_url = _normalize_base_url(str(config.get("base_url") or ""))
    try:
        directory = _normalize_directory(str(config.get("directory") or ""))
    except ValueError as exc:  # defensive: validated above, but persisted values are untrusted
        raise OpenCodeError("Directorul OpenCode salvat nu este valid; verifică setările providerului") from exc
    headers = {"Accept": "application/json"}
    password = os.getenv("OPENCODE_SERVER_PASSWORD", "")
    if password:
        pinned_url = (os.getenv("OPENCODE_SERVER_URL") or "").strip()
        if not pinned_url:
            raise OpenCodeError("Pentru Basic Auth, setează și OPENCODE_SERVER_URL în environment-ul backend-ului")
        try:
            pinned_url = _normalize_base_url(pinned_url)
        except ValueError as exc:
            raise OpenCodeError("OPENCODE_SERVER_URL trebuie să fie localhost sau o adresă loopback validă") from exc
        if pinned_url != base_url:
            raise OpenCodeError("Adresa OpenCode salvată nu corespunde cu OPENCODE_SERVER_URL; parola nu a fost trimisă")
    auth = httpx.BasicAuth(os.getenv("OPENCODE_SERVER_USERNAME", "opencode"), password) if password else None
    return _api_root(config), {"directory": directory}, {"headers": headers, "auth": auth}


def _safe_http_error(response: httpx.Response) -> OpenCodeError:
    status = response.status_code
    if status == 401:
        return OpenCodeError("Autentificarea la serverul OpenCode a eșuat (HTTP 401)")
    if status == 403:
        return OpenCodeError("Serverul OpenCode a refuzat accesul (HTTP 403)")
    if status >= 500:
        return OpenCodeError(f"Serverul OpenCode este indisponibil (HTTP {status})")
    return OpenCodeError(f"Serverul OpenCode a respins cererea (HTTP {status})")


async def _request_json(method: str, path: str, *, body: dict | None = None, params: dict | None = None) -> dict | list:
    root, directory, options = _request_config()
    query = dict(params or {})
    if directory.get("directory"):
        query["location[directory]"] = directory["directory"]
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(
                method, root + path, params=query, json=body, **options,
            )
    except httpx.TimeoutException as exc:
        raise OpenCodeError("Serverul OpenCode nu a răspuns înainte de timeout") from exc
    except httpx.HTTPError as exc:
        logger.info("OpenCode request failed (%s)", type(exc).__name__)
        raise OpenCodeError("Nu mă pot conecta la serverul OpenCode configurat") from exc
    if response.is_error:
        raise _safe_http_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise OpenCodeError("Serverul OpenCode a returnat un răspuns JSON invalid") from exc
    if not isinstance(data, (dict, list)):
        raise OpenCodeError("Serverul OpenCode a returnat un răspuns cu format neașteptat")
    return data


def _model_id(model: dict) -> str:
    provider_id = str(model.get("providerID") or "")
    model_id = str(model.get("modelID") or "")
    if not provider_id or not model_id:
        return ""
    return f"{provider_id}::{model_id}"


def split_model_id(value: str) -> tuple[str, str] | None:
    provider_id, delimiter, model_id = (value or "").partition("::")
    if not delimiter or not provider_id or not model_id:
        return None
    return provider_id, model_id


async def model_catalogue() -> dict:
    data = await _request_json("GET", "/model")
    raw = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise OpenCodeError("Lista modelelor OpenCode are un format neașteptat")

    default_model = ""
    try:
        default_data = await _request_json("GET", "/model/default")
        default_info = default_data.get("data", default_data) if isinstance(default_data, dict) else None
        if isinstance(default_info, dict):
            default_model = _model_id(default_info)
    except OpenCodeError:
        # Model discovery remains useful if this server version has no default route.
        pass

    selected = selected_model() or default_model
    models = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        identifier = _model_id(item)
        if not identifier:
            continue
        limit = item.get("limit")
        if not isinstance(limit, dict):
            limit = {}
        models.append({
            "id": identifier,
            "name": str(item.get("name") or item.get("modelID")),
            "context_window": limit.get("context"),
            "max_output_tokens": limit.get("output"),
            "input_modalities": [],
            "output_modalities": [],
            "effort_levels": [],
            "is_active": identifier == selected,
        })
    if selected and models and selected not in {model["id"] for model in models}:
        selected = default_model if default_model in {model["id"] for model in models} else ""
    return {
        "models": models,
        "active": selected,
        "provider": "opencode",
        "from_provider": True,
        "active_listed": not selected or selected in {model["id"] for model in models},
        "note": "" if models else "Serverul OpenCode nu a returnat modele de chat.",
    }


def _format_messages(messages: list[dict]) -> str:
    lines = [
        "Răspunde la cererea de mai jos ca model text. Acesta este un context furnizat de Bransk.",
        "Nu executa comenzi și nu modifica fișiere. Răspunde numai cu text.",
    ]
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().upper()
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        lines.extend([f"\n--- {role} ---", str(content)])
    lines.append("\n--- RĂSPUNS ---")
    return "\n".join(lines)


def _decode_sse_data(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("data"), str):
            try:
                payload = json.loads(data["data"])
                if isinstance(payload, dict):
                    data = payload
            except ValueError:
                return None
        return data
    except (TypeError, ValueError):
        return None


async def _iter_sse_events(response: httpx.Response):
    data_lines: list[str] = []
    event_name = ""
    async for line in response.aiter_lines():
        if line.startswith(":"):
            continue
        if line == "":
            if data_lines:
                event = _decode_sse_data("\n".join(data_lines))
                if event is not None:
                    event.setdefault("type", event_name)
                    yield event
            data_lines = []
            event_name = ""
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith("event:"):
            event_name = line[6:].strip()
    if data_lines:
        event = _decode_sse_data("\n".join(data_lines))
        if event is not None:
            event.setdefault("type", event_name)
            yield event


def _event_data(event: dict) -> tuple[str, dict]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    event_type = str(payload.get("type") or payload.get("event") or "")
    data = payload.get("data") or payload.get("properties") or {}
    return event_type, data if isinstance(data, dict) else {}


async def stream_complete(messages: list[dict], *, timeout: float = REQUEST_TIMEOUT):
    """Create a permission-denied ephemeral session and stream its text events."""
    root, location, options = _request_config()
    model_choice = split_model_id(selected_model())
    permission_rules = [{"action": "*", "resource": "*", "effect": "deny"}]

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0, read=None, write=15.0, pool=5.0)
        ) as client:
            async with asyncio.timeout(timeout):
                created = await client.post(
                    root + "/session",
                    params={"location[directory]": location["directory"]},
                    json={"title": "Bransk response"},
                    **options,
                )
                if created.is_error:
                    raise _safe_http_error(created)
                try:
                    session_data = created.json()
                    session = session_data.get("data", session_data)
                    session_id = str(session["id"])
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise OpenCodeError("OpenCode nu a returnat un identificator de sesiune valid") from exc

                # OpenCode v2.1.2 applies permission rules through session.update,
                # not the create request. Patch before any prompt is sent.
                permission_response = await client.patch(
                    f"{root}/session/{quote(session_id, safe='')}",
                    json={"permissions": permission_rules},
                    **options,
                )
                if permission_response.is_error:
                    raise _safe_http_error(permission_response)

                prompt_text = _format_messages(messages)
                total_chars = 0
                terminal_error: str | None = None
                completed = False
                event_url = root + "/event"
                async with client.stream("GET", event_url, **options) as event_response:
                    if event_response.is_error:
                        raise _safe_http_error(event_response)

                    prompt_body = {
                        "parts": [{"type": "text", "text": prompt_text}],
                    }
                    if model_choice:
                        prompt_body["model"] = {
                            "providerID": model_choice[0],
                            "modelID": model_choice[1],
                        }
                    prompt_response = await client.post(
                        f"{root}/session/{quote(session_id, safe='')}/prompt_async",
                        json=prompt_body,
                        **options,
                    )
                    if prompt_response.is_error:
                        raise _safe_http_error(prompt_response)

                    async for event in _iter_sse_events(event_response):
                        event_type, event_data = _event_data(event)
                        if event_data.get("sessionID") != session_id:
                            continue
                        if event_type == "session.text.delta":
                            delta = event_data.get("delta")
                            if isinstance(delta, str) and delta:
                                total_chars += len(delta)
                                yield delta
                        elif event_type == "session.execution.failed":
                            terminal_error = "Execuția OpenCode a eșuat"
                            completed = True
                            break
                        elif event_type == "session.execution.interrupted":
                            terminal_error = "Execuția OpenCode a fost întreruptă"
                            completed = True
                            break
                        elif event_type in {"session.idle", "session.execution.succeeded"}:
                            completed = True
                            break

                if not completed:
                    raise OpenCodeError("Fluxul SSE OpenCode s-a închis înainte de finalizarea răspunsului")
                if terminal_error:
                    raise OpenCodeError(terminal_error)
                if not total_chars:
                    raise OpenCodeError("OpenCode a terminat fără a returna text")
    except TimeoutError as exc:
        raise OpenCodeError("Răspunsul OpenCode a depășit limita de timp") from exc
    except httpx.TimeoutException as exc:
        raise OpenCodeError("Serverul OpenCode nu a răspuns înainte de timeout") from exc
    except httpx.HTTPError as exc:
        logger.info("OpenCode stream failed (%s)", type(exc).__name__)
        raise OpenCodeError("Fluxul serverului OpenCode a fost întrerupt") from exc
    finally:
        if "session_id" in locals():
            try:
                async with httpx.AsyncClient(timeout=5.0) as cleanup_client:
                    session_url = f"{root}/session/{quote(session_id, safe='')}"
                    if not locals().get("completed", False):
                        try:
                            aborted = await cleanup_client.post(session_url + "/abort", **options)
                            if aborted.is_error:
                                logger.info("OpenCode abort returned HTTP %s", aborted.status_code)
                        except Exception as exc:  # noqa: BLE001 - deletion still must run
                            logger.info("OpenCode session abort failed (%s)", type(exc).__name__)
                    await cleanup_client.delete(session_url, **options)
            except Exception as exc:  # cleanup must not mask a useful result/error
                logger.info("OpenCode session cleanup failed (%s)", type(exc).__name__)


async def complete(messages: list[dict], *, timeout: float = REQUEST_TIMEOUT) -> str:
    parts = []
    async for chunk in stream_complete(messages, timeout=timeout):
        parts.append(chunk)
    return "".join(parts)
