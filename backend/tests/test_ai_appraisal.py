"""Tests for the AI-assisted study appraisal endpoint."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "appraisal.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def test_appraisal_mock_provider_is_503(client):
    study = client.post("/api/entries", json={"title": "S", "study_type": "study"}).json()
    res = client.post(f"/api/studies/{study['id']}/ai-appraisal")
    assert res.status_code == 503


def test_appraisal_writes_methodology_fields(client, monkeypatch):
    study = client.post("/api/entries", json={
        "title": "Sleep RCT", "study_type": "study",
        "content": "Randomized controlled trial with 124 participants.",
    }).json()

    from app.routers import study_qa as sq

    async def fake_llm(messages, **kw):
        return ("", "", json.dumps({
            "design": "RCT",
            "effect_direction": "positive",
            "quality": 4,
            "justification": "RCT cu eșantion de 124 și grup de control real.",
        }))

    monkeypatch.setattr(sq, "llm_complete", fake_llm)
    monkeypatch.setattr(sq, "LLM_PROVIDER", "deepseek")
    res = client.post(f"/api/studies/{study['id']}/ai-appraisal")
    assert res.status_code == 200
    body = res.json()
    assert body["design"] == "RCT"
    assert body["effect_direction"] == "positive"
    assert body["quality"] == 4

    detail = client.get(f"/api/entries/{study['id']}").json()
    assert detail["design"] == "RCT"
    assert detail["effect_direction"] == "positive"
    assert detail["quality"] == 4


def test_appraisal_survives_prose_wrapped_json(client, monkeypatch):
    study = client.post("/api/entries", json={
        "title": "Wrapped", "study_type": "study",
    }).json()
    from app.routers import study_qa as sq

    async def fake_llm(messages, **kw):
        return ("", "", "Iată evaluarea:\n```json\n"
                + json.dumps({"design": "cohort", "effect_direction": "mixed", "quality": 3,
                              "justification": "Cohortă prospective."})
                + "\n```")

    monkeypatch.setattr(sq, "llm_complete", fake_llm)
    monkeypatch.setattr(sq, "LLM_PROVIDER", "deepseek")
    res = client.post(f"/api/studies/{study['id']}/ai-appraisal")
    assert res.status_code == 200
    assert res.json()["design"] == "cohort"
