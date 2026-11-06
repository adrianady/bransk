"""Tests for PRISMA screening decisions."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "screening.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def _study(client, title):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201
    return res.json()


def test_screening_decision_roundtrip_and_summary(client):
    s1 = _study(client, "Study one")
    s2 = _study(client, "Study two")
    s3 = _study(client, "Study three")

    r1 = client.post(f"/api/studies/{s1['id']}/screening",
                     json={"decision": "include", "reason": "RCT pe populația țintă"})
    assert r1.status_code == 200
    client.post(f"/api/studies/{s2['id']}/screening", json={"decision": "exclude", "reason": "review"})
    client.post(f"/api/studies/{s3['id']}/screening", json={"decision": "maybe"})

    got = client.get(f"/api/studies/{s1['id']}/screening").json()
    assert got["decision"] == "include" and got["reason"] == "RCT pe populația țintă"

    summary = client.get("/api/studies/screening/summary").json()
    assert summary["studies_total"] == 3
    assert summary["included"] == 1 and summary["excluded"] == 1
    assert summary["maybe"] == 1 and summary["undecided"] == 0

    decisions = client.get("/api/studies/screening/decisions").json()
    assert str(s2["id"]) in decisions and decisions[str(s2["id"])]["decision"] == "exclude"

    # Re-screening overwrites the decision instead of failing.
    client.post(f"/api/studies/{s1['id']}/screening", json={"decision": "exclude"})
    assert client.get(f"/api/studies/{s1['id']}/screening").json()["decision"] == "exclude"

    cleared = client.delete(f"/api/studies/{s1['id']}/screening")
    assert cleared.status_code == 204
    assert client.get(f"/api/studies/{s1['id']}/screening").status_code == 404


def test_screening_rejects_non_studies_and_bad_values(client):
    plain = client.post("/api/entries", json={"title": "Just a note"}).json()
    assert client.post(f"/api/studies/{plain['id']}/screening",
                       json={"decision": "include"}).status_code == 404
    assert client.post(f"/api/studies/999/screening",
                       json={"decision": "include"}).status_code == 404
    assert client.post(f"/api/studies/999/screening",
                       json={"decision": "nonsense"}).status_code == 422
