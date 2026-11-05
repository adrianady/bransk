"""The plan must be shown in the chapters where it is used.

The plan (participants, instruments, hypotheses…) and the outline used to be two
disconnected documents. `plan_map` says which chapter each field belongs to,
computed deterministically from the chapter titles/descriptions.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

STRUCTURE = [
    {"id": "s1", "title": "Introducere", "description": "Context și problemă.", "target_words": 1000},
    {"id": "s2", "title": "Cadru conceptual", "description": "Model cauzal, variabile.", "target_words": 1200},
    {"id": "s3", "title": "Metodologia cercetării",
     "description": "Participanți, instrumente, etică, analiză statistică.", "target_words": 2000},
    {"id": "s4", "title": "Rezultate", "description": "Tabele și statistici.", "target_words": 2000},
    {"id": "s5", "title": "Discuții și concluzii",
     "description": "Interpretare, limite, riscuri.", "target_words": 1500},
]

PLAN = {
    "participants": "40 de adulți, criterii de includere.",
    "hypotheses": "H1: adversitatea reduce mentalizarea.",
    "risks": "Eșantion mic, attrition.",
}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_plan_map_assigns_fields_to_the_right_chapters(client):
    res = client.put("/api/project", json={"structure": STRUCTURE, "plan": PLAN})
    assert res.status_code == 200

    plan_map = client.get("/api/project").json()["plan_map"]
    assert plan_map["by_key"] == {
        "participants": "s3",
        "instruments": "s3",
        "hypotheses": "s2",
        "variables": "s2",
        "analysis": "s3",
        "ethics": "s3",
        "risks": "s5",
    }
    assert plan_map["unmapped"] == ["schedule"]
    assert plan_map["sections"]["s3"] == ["participants", "instruments", "analysis", "ethics"]
    assert plan_map["total"] == 8
    assert plan_map["filled"] == 3


def test_plan_map_is_empty_but_valid_without_an_outline(client):
    res = client.put("/api/project", json={"structure": [], "plan": PLAN})
    assert res.status_code == 200

    plan_map = client.get("/api/project").json()["plan_map"]
    assert plan_map["by_key"] == {}
    assert set(plan_map["unmapped"]) == {
        "participants", "instruments", "hypotheses", "variables",
        "analysis", "ethics", "schedule", "risks",
    }
    assert plan_map["filled"] == 3


def test_plan_map_follows_the_outline_when_it_changes(client):
    client.put("/api/project", json={"structure": STRUCTURE, "plan": PLAN})
    client.put(
        "/api/project",
        json={"structure": [{"id": "x1", "title": "Metodologie", "description": "", "target_words": 0}]},
    )

    plan_map = client.get("/api/project").json()["plan_map"]
    assert plan_map["by_key"]["participants"] == "x1"
    # A single methodology chapter cannot also carry the discussion/risks part.
    assert "risks" in plan_map["unmapped"]
