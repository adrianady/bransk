"""Tests for the questionnaires and research instruments router."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "questionnaires.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def test_questionnaire_crud(client):
    # 1. Create questionnaire
    payload = {
        "title": "Scala de Reziliență CD-RISC",
        "description": "Măsoară reziliența psihologică în fața adversității.",
        "construct": "Reziliență",
        "target_population": "Adolescenți și adulți",
        "reliability_notes": "Alpha Cronbach > 0.85",
        "items": [
            {"id": "q1", "text": "Mă pot adapta la schimbări.", "type": "likert", "options": ["Niciodată", "Rareori", "Uneori", "Adesea", "Aproape întotdeauna"], "subscale": "Adaptabilitate"}
        ]
    }
    res = client.post("/api/questionnaires", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["project_id"] == 1
    assert data["title"] == "Scala de Reziliență CD-RISC"
    assert len(data["items"]) == 1
    qid = data["id"]
    # 2. Get questionnaire
    res_get = client.get(f"/api/questionnaires/{qid}")
    assert res_get.status_code == 200
    assert res_get.json()["construct"] == "Reziliență"

    # 3. List questionnaires
    res_list = client.get("/api/questionnaires")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # 4. Update questionnaire
    res_put = client.put(f"/api/questionnaires/{qid}", json={"title": "Scala CD-RISC Revizuită"})
    assert res_put.status_code == 200
    assert res_put.json()["title"] == "Scala CD-RISC Revizuită"

    # 5. AI generate & AI critique (mock mode)
    res_gen = client.post(f"/api/questionnaires/{qid}/ai-generate")
    assert res_gen.status_code == 200
    assert len(res_gen.json()["items"]) >= 1

    res_crit = client.post(f"/api/questionnaires/{qid}/ai-critique")
    assert res_crit.status_code == 200
    assert "critique" in res_crit.json()

    # 6. Delete questionnaire
    res_del = client.delete(f"/api/questionnaires/{qid}")
    assert res_del.status_code == 200
    assert client.get(f"/api/questionnaires/{qid}").status_code == 404


def test_questionnaire_project_scoping(client):
    # Create for project 1
    res1 = client.post("/api/questionnaires", json={"title": "Q Project 1"})
    assert res1.status_code == 201
    assert res1.json()["project_id"] == 1

    # Create for project 2 via query param
    res2 = client.post("/api/questionnaires?project_id=2", json={"title": "Q Project 2 via query"})
    assert res2.status_code == 201
    assert res2.json()["project_id"] == 2

    # Create for project 2 via body
    res3 = client.post("/api/questionnaires", json={"title": "Q Project 2 via body", "project_id": 2})
    assert res3.status_code == 201
    assert res3.json()["project_id"] == 2

    # Listing default (active project 1)
    list_default = client.get("/api/questionnaires").json()
    assert len(list_default) == 1
    assert list_default[0]["title"] == "Q Project 1"

    # Listing project 2
    list_p2 = client.get("/api/questionnaires?project_id=2").json()
    assert len(list_p2) == 2
    assert all(q["project_id"] == 2 for q in list_p2)

    # Listing project 3 (empty)
    list_p3 = client.get("/api/questionnaires?project_id=3").json()
    assert len(list_p3) == 0
