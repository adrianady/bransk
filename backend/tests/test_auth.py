"""The optional shared-token gate.

Local-first is the default: with no CHRONOKG_AUTH_TOKEN the API behaves exactly
as before. Exposing on the network sets one, and then every /api call needs it
while health, auth-status and the SPA shell stay reachable so the login screen
can load.
"""
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app

TOKEN = "test-token-9f3c"
ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CHRONOKG_AUTH_TOKEN", raising=False)
    with TestClient(app) as c:
        yield c


def test_no_token_means_no_gate(client):
    res = client.get("/api/entries")
    assert res.status_code == 200
    assert client.get("/api/auth/status").json() == {"required": False}


def test_token_required_and_enforced(client, monkeypatch):
    monkeypatch.setenv("CHRONOKG_AUTH_TOKEN", TOKEN)

    auth_status = client.get("/api/auth/status").json()
    assert auth_status["required"] is True
    assert auth_status["password_configured"] is False
    assert auth_status["authenticated"] is False
    assert client.get("/api/health").status_code == 200

    assert client.get("/api/entries").status_code == 401
    bad = client.get("/api/entries", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert bad.headers["www-authenticate"] == "Bearer"

    ok = client.get("/api/entries", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


def test_token_accepted_from_header_and_query(client, monkeypatch):
    monkeypatch.setenv("CHRONOKG_AUTH_TOKEN", TOKEN)

    via_header = client.get("/api/entries", headers={"X-ChronoKG-Token": TOKEN})
    assert via_header.status_code == 200

    # PDFs are opened by the browser itself; a header cannot travel there.
    via_query = client.get(f"/api/entries?token={TOKEN}")
    assert via_query.status_code == 200


def test_spa_shell_stays_public(client, monkeypatch):
    monkeypatch.setenv("CHRONOKG_AUTH_TOKEN", TOKEN)
    # The login screen lives inside the SPA, so the shell cannot be gated.
    assert client.get("/").status_code == 200


def test_health_reports_ok_even_when_gated(client, monkeypatch):
    """The launcher probes health to tell ChronoKG from a foreign process."""
    monkeypatch.setenv("CHRONOKG_AUTH_TOKEN", TOKEN)
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_password_setup_requires_bearer_and_is_one_time(client, monkeypatch):
    monkeypatch.setenv("CHRONOKG_AUTH_TOKEN", TOKEN)
    payload = {"username": "Owner", "password": PASSWORD}

    assert client.post("/api/auth/setup", json=payload).status_code == 401
    invalid = client.post(
        "/api/auth/setup",
        headers={"Authorization": "Bearer wrong"},
        json=payload,
    )
    assert invalid.status_code == 401

    setup = client.post(
        "/api/auth/setup",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json=payload,
    )
    assert setup.status_code == 201
    assert setup.json() == {"configured": True, "username": "owner"}
    assert PASSWORD not in setup.text

    # Setup is one-time even for a later request; password reset is a distinct route.
    repeated = client.post(
        "/api/auth/setup",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"username": "owner", "password": "another-secure-password"},
    )
    assert repeated.status_code == 409
    assert client.get("/api/entries").status_code == 401

    conn = get_db()
    try:
        stored = conn.execute("SELECT password_hash FROM auth_account WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
    assert stored.startswith("scrypt$")
    assert PASSWORD not in stored


def _setup_password_account(client, monkeypatch):
    monkeypatch.setenv("CHRONOKG_AUTH_TOKEN", TOKEN)
    return client.post(
        "/api/auth/setup",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"username": "owner", "password": PASSWORD},
    )


def _login(client, username="owner", password=PASSWORD, origin=ORIGIN):
    return client.post(
        "/api/auth/login",
        headers={"Origin": origin},
        json={"username": username, "password": password},
    )


def test_password_login_creates_http_only_session_and_authorizes_api(client, monkeypatch):
    assert _setup_password_account(client, monkeypatch).status_code == 201
    bad = _login(client, password="wrong-password-123")
    assert bad.status_code == 401

    logged_in = _login(client, username="Owner")
    assert logged_in.status_code == 200
    assert logged_in.json() == {"authenticated": True, "username": "owner"}
    cookie = logged_in.headers["set-cookie"].lower()
    assert "chronokg_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/api" in cookie
    assert "max-age=2592000" in cookie
    assert "secure" not in cookie  # current tailnet URL is HTTP
    assert PASSWORD not in logged_in.text

    status = client.get("/api/auth/status").json()
    assert status["session_authenticated"] is True
    assert status["token_authenticated"] is False
    assert client.get("/api/entries").status_code == 200

    conn = get_db()
    try:
        stored_session = conn.execute("SELECT session_hash FROM auth_sessions").fetchone()[0]
    finally:
        conn.close()
    assert client.cookies.get("chronokg_session") not in stored_session


def test_session_expiry_logout_and_password_reset_revoke_sessions(client, monkeypatch):
    from app.auth import SESSION_TTL_SECONDS

    assert _setup_password_account(client, monkeypatch).status_code == 201
    assert _login(client).status_code == 200
    assert client.get("/api/entries").status_code == 200

    import app.auth as auth_module

    now = auth_module.time.time()
    monkeypatch.setattr(auth_module.time, "time", lambda: now + SESSION_TTL_SECONDS + 1)
    assert client.get("/api/entries").status_code == 401
    assert client.get("/api/auth/status").json()["session_authenticated"] is False
    monkeypatch.setattr(auth_module.time, "time", lambda: now)

    # Log in again and reset through the explicitly authenticated bearer path.
    assert _login(client).status_code == 200
    denied_reset = client.post(
        "/api/auth/password",
        headers={"Origin": ORIGIN},
        json={"password": "session-only-reset-is-not-allowed"},
    )
    assert denied_reset.status_code == 403
    changed = client.post(
        "/api/auth/password",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"password": "new-correct-horse-battery"},
    )
    assert changed.status_code == 200
    assert client.get("/api/entries").status_code == 401
    assert _login(client, password=PASSWORD).status_code == 401
    assert _login(client, password="new-correct-horse-battery").status_code == 200

    logout = client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert logout.status_code == 204
    assert client.get("/api/entries").status_code == 401


def test_cookie_auth_enforces_origin_and_login_rate_limit(client, monkeypatch):
    assert _setup_password_account(client, monkeypatch).status_code == 201
    rejected_origin = _login(client, origin="http://attacker.invalid")
    assert rejected_origin.status_code == 403

    assert _login(client).status_code == 200
    csrf_mutation = client.post(
        "/api/tags",
        headers={"Origin": "http://attacker.invalid"},
        json={"name": "csrf-tag"},
    )
    assert csrf_mutation.status_code == 403
    accepted_mutation = client.post(
        "/api/tags",
        headers={"Origin": ORIGIN},
        json={"name": "trusted-tag"},
    )
    assert accepted_mutation.status_code == 201

    for _ in range(5):
        assert _login(client, password="wrong-password-123").status_code == 401
    limited = _login(client)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"


def test_https_session_cookie_is_secure(client, monkeypatch):
    assert _setup_password_account(client, monkeypatch).status_code == 201
    with TestClient(app, base_url="https://testserver") as secure_client:
        response = _login(secure_client, origin="https://testserver")
    assert "secure" in response.headers["set-cookie"].lower()


def test_loopback_vite_origin_can_use_password_login(client, monkeypatch):
    assert _setup_password_account(client, monkeypatch).status_code == 201
    with TestClient(app, base_url="http://localhost:8000") as vite_client:
        response = _login(vite_client, origin="http://localhost:5173")
    assert response.status_code == 200
