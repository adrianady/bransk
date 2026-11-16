"""Hard CPU-only execution policy for Bransk's local AI paths."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

# Deliberately not environment-overridable: provider credentials in .env or
# stale database settings must never reopen a remote inference route.
CPU_ONLY_MODE = True

# Context of the bounded llama.cpp CPU server started by start-llm-cpu.sh.
# Kept here so chat prompts are trimmed to the model that actually answers,
# instead of growing until the local server rejects them with HTTP 400.
CPU_ONLY_LOCAL_CONTEXT = 4096

CPU_ONLY_PROVIDER_NOTICE = (
    "Modul CPU-only permite doar endpointuri AI locale (loopback) și Mock offline; "
    "furnizorii cloud și fallback-ul către ei sunt blocați."
)


def is_loopback_url(value: str) -> bool:
    """Accept only plain HTTP(S) URLs whose host is an explicit loopback name/IP."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlsplit(value.strip())
        # Accessing .port validates malformed/non-numeric port values.
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False

    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
