"""Optional shared-token gate for network exposure.

Bransk is local-first: by default it listens on 127.0.0.1 and needs no
credentials. When it is exposed on the LAN (phone, Tailscale) every device that
reaches the port can read and write the whole database. Setting
`CHRONOKG_AUTH_TOKEN` turns on a single shared token for `/api/*`; the token is
never required on loopback unless it was set there too.

The token is read from the environment, not from a file, so a process cannot
start authenticating by accident because of a leftover file. `start.sh` owns
the generation/persistence of the value for the launch it performs.
"""
from __future__ import annotations

import hmac
import os

# Paths that must stay reachable without a token: health so the launcher can
# distinguish "Bransk is up" from "something else owns the port", and the
# status endpoint so the SPA knows it has to ask for a token at all.
PUBLIC_API_PATHS = frozenset({"/api/health", "/api/auth/status"})

ENV_VAR = "CHRONOKG_AUTH_TOKEN"


def configured_token() -> str:
    """The token this process enforces, or "" when auth is off."""
    return (os.environ.get(ENV_VAR) or "").strip()


def token_required() -> bool:
    return bool(configured_token())


def token_matches(candidate: str | None) -> bool:
    """Constant-time comparison, so a wrong token cannot be timed out."""
    expected = configured_token()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.strip(), expected)


def extract_token(headers, query: dict | None = None) -> str | None:
    """Read the token from the Authorization header, X-ChronoKG-Token or ?token=.

    The query-string form exists for URLs the browser opens on its own (the
    study PDF in a new tab or iframe), where headers cannot be attached.
    """
    authorization = headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        value = authorization[7:].strip()
        if value:
            return value
    value = (headers.get("x-chronokg-token") or "").strip()
    if value:
        return value
    if query:
        value = (query.get("token") or "").strip()
        if value:
            return value
    return None
