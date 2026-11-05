"""
Tests for serving the built frontend from the backend.

The phone gets one single address: the same origin serves the React app and
the API. These tests pin that contract down, including the SPA fallback and
the fact that unknown API routes still answer with JSON 404 (never HTML).
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "spa_test.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="frontend not built")
def test_root_serves_the_app_shell(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert '<div id="root">' in body
    assert "/assets/" in body


@pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="frontend not built")
def test_unknown_client_route_falls_back_to_the_app(client):
    """Deep links (e.g. a reload on a sub-page) must not 404 for the browser."""
    res = client.get("/project")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_unknown_api_route_stays_json(client):
    res = client.get("/api/definitely-not-a-route")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["detail"] == "Not Found"


def test_health_is_untouched_by_the_spa(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
