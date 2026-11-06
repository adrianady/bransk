"""Authentication helpers for network-exposed ChronoKG instances.

Bransk is local-first: by default it listens on 127.0.0.1 and needs no
credentials. When it is exposed on the LAN (phone, Tailscale) every device that
reaches the port can read and write the whole database. Setting
`CHRONOKG_AUTH_TOKEN` enables the existing bearer-token gate and permits an
optional password account with server-managed browser sessions.

The token is read from the environment, not from a file, so a process cannot
start authenticating by accident because of a leftover file. `start.sh` owns
the generation/persistence of the value for the launch it performs.
"""
from __future__ import annotations

import hmac
import hashlib
import os
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from app.db import get_db

# Paths that must stay reachable without a token: health so the launcher can
# distinguish "Bransk is up" from "something else owns the port", and the
# status endpoint so the SPA knows it has to ask for a token at all.
PUBLIC_API_PATHS = frozenset({
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
})

ENV_VAR = "CHRONOKG_AUTH_TOKEN"
SESSION_COOKIE = "chronokg_session"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_PER_IP = 5
LOGIN_MAX_GLOBAL = 50
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


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


def bearer_token_matches(headers) -> bool:
    """Return true only for an existing bearer credential in an HTTP header."""
    authorization = (headers.get("authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        return False
    return token_matches(authorization[7:])


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a fresh random salt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=64,
        maxmem=64 * 1024 * 1024,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        salt.hex(),
        derived.hex(),
    )


def password_matches(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded_hash.split("$", 5)
        if (
            algorithm != "scrypt"
            or int(n) != _SCRYPT_N
            or int(r) != _SCRYPT_R
            or int(p) != _SCRYPT_P
        ):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        if len(salt) != 16 or len(expected) != 64:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=64,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(derived.hex(), expected_hex)
    except (ValueError, TypeError, OverflowError):
        return False


def session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def session_matches(session_id: str | None) -> bool:
    """Validate an opaque session without storing or exposing its raw value."""
    if not session_id or len(session_id) > 128:
        return False
    try:
        digest = session_digest(session_id)
    except UnicodeError:
        return False
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT expires_at FROM auth_sessions WHERE session_hash = ?",
            (digest,),
        ).fetchone()
        if row is None:
            return False
        if int(row["expires_at"]) <= int(time.time()):
            conn.execute(
                "DELETE FROM auth_sessions WHERE session_hash = ?",
                (digest,),
            )
            conn.commit()
            return False
        return True
    finally:
        conn.close()


def account_username() -> str | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT username FROM auth_account WHERE id = 1").fetchone()
        return str(row["username"]) if row else None
    finally:
        conn.close()


def account_password_hash(username: str) -> str | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT password_hash FROM auth_account WHERE id = 1 AND username = ?",
            (username,),
        ).fetchone()
        return str(row["password_hash"]) if row else None
    finally:
        conn.close()


def create_initial_account(username: str, encoded_hash: str) -> bool:
    """Atomically create the one shared account; never overwrite an account."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute("SELECT 1 FROM auth_account WHERE id = 1").fetchone()
        if exists:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO auth_account (id, username, password_hash, created_at) VALUES (1, ?, ?, ?)",
            (username, encoded_hash, int(time.time())),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    finally:
        conn.close()


def update_account_password(encoded_hash: str) -> bool:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE auth_account SET password_hash = ?, updated_at = ? WHERE id = 1",
            (encoded_hash, int(time.time())),
        ).rowcount
        if changed:
            # A password change invalidates every device session immediately.
            conn.execute("DELETE FROM auth_sessions")
        conn.commit()
        return changed == 1
    finally:
        conn.close()


def create_session(username: str) -> tuple[str, int]:
    session_id = secrets.token_urlsafe(32)
    now = int(time.time())
    expires_at = now + SESSION_TTL_SECONDS
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO auth_sessions (session_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (session_digest(session_id), username, now, expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return session_id, expires_at


def revoke_session(session_id: str | None) -> None:
    if not session_id:
        return
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM auth_sessions WHERE session_hash = ?",
            (session_digest(session_id),),
        )
        conn.commit()
    finally:
        conn.close()


def reserve_login_attempt(remote_host: str) -> int | None:
    """Persistently rate-limit password attempts by peer and for the account."""
    now = int(time.time())
    cutoff = now - LOGIN_WINDOW_SECONDS
    ip_key = hashlib.sha256(remote_host.encode("utf-8", errors="replace")).hexdigest()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_login_attempts WHERE attempted_at <= ?", (cutoff,))
        per_ip = conn.execute(
            "SELECT COUNT(*) FROM auth_login_attempts WHERE ip_key = ? AND attempted_at > ?",
            (ip_key, cutoff),
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM auth_login_attempts WHERE attempted_at > ?",
            (cutoff,),
        ).fetchone()[0]
        if per_ip >= LOGIN_MAX_PER_IP or total >= LOGIN_MAX_GLOBAL:
            conn.rollback()
            return None
        cursor = conn.execute(
            "INSERT INTO auth_login_attempts (ip_key, attempted_at) VALUES (?, ?)",
            (ip_key, now),
        )
        attempt_id = int(cursor.lastrowid)
        conn.commit()
        return attempt_id
    finally:
        conn.close()


def complete_login_attempt(attempt_id: int) -> None:
    """Remove the reservation for a successful login; failures remain counted."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM auth_login_attempts WHERE id = ?", (attempt_id,))
        conn.commit()
    finally:
        conn.close()


def trusted_request_origin(request) -> bool:
    """Accept same-origin requests and the documented loopback Vite dev ports."""
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        origin_host = (parsed.hostname or "").lower()
        request_host = (request.url.hostname or "").lower()
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
        if parsed.scheme != request.url.scheme or origin_host != request_host:
            return False
        if origin_port == request_port:
            return True
        loopback_hosts = {"localhost", "127.0.0.1", "::1"}
        return (
            origin_host in loopback_hosts
            and request_host in loopback_hosts
            and origin_port in {3000, 5173}
            and request_port == 8000
        )
    except ValueError:
        return False
