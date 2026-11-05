"""The optional shared-token gate.

Local-first is the default: with no CHRONOKG_AUTH_TOKEN the API behaves exactly
as before. Exposing on the network sets one, and then every /api call needs it
while health, auth-status and the SPA shell stay reachable so the login screen
can load.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

TOKEN = "test-token-9f3c"


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

    assert client.get("/api/auth/status").json() == {"required": True}
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
