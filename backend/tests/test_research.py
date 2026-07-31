from fastapi.testclient import TestClient

from app.main import app


def test_meta_analysis_pipeline_end_to_end():
    with TestClient(app) as client:
        resp = client.post("/api/research", json={"topic": "sleep and memory", "depth": "quick", "save": True})
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["studies"]) >= 1
        assert data["protocol"]["research_question"]
        assert data["protocol"]["inclusion_criteria"]
        assert data["prisma"]["studies_included"] == len(data["studies"])
        assert data["confidence"] in ("high", "moderate", "low", "very low")
        assert data["studies"][0]["design"]
        assert data["studies"][0]["effect_direction"] in ("positive", "negative", "mixed", "null")
        assert isinstance(data["studies"][0]["risk_of_bias"], dict)

        # Studies persisted as dated entries
        studies = client.get("/api/studies").json()
        assert len(studies) == len(data["studies"])
        assert all(s["study_type"] and s["entry_date"] for s in studies)

        # Theory map persisted
        tree = client.get("/api/theories/tree").json()
        assert any(t["name"] == "sleep and memory" for t in tree)


def test_research_dedupe_on_rerun():
    with TestClient(app) as client:
        first = client.post("/api/research", json={"topic": "sleep and memory", "depth": "quick", "save": True}).json()
        study_count_after_first = len(client.get("/api/studies").json())
        second = client.post("/api/research", json={"topic": "sleep and memory", "depth": "quick", "save": True}).json()
        study_count_after_second = len(client.get("/api/studies").json())

        assert study_count_after_second == study_count_after_first  # no duplicate studies
        assert len(second["entries_created"]) == 1  # only the new report entry
        assert len(second["studies"]) == len(first["studies"])
