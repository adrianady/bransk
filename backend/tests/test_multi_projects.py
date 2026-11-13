"""Tests for multi-project isolation in ChronoKG."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "multiprojects.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def test_multi_project_isolation(client):
    # 1. Get default project list (should have 1 default project)
    res = client.get("/api/projects")
    assert res.status_code == 200
    data = res.json()
    assert len(data["projects"]) == 1
    default_id = data["active_id"]

    # 2. Create a new isolated project
    new_proj = {
        "title": "Studiu Neuroștiințe și Memorie",
        "work_type": "disertatie",
        "field": "Psihologie",
        "research_q": "Cum influențează somnul consolidarea memoriei de lungă durată?"
    }
    res_create = client.post("/api/projects", json=new_proj)
    assert res_create.status_code == 201
    created = res_create.json()
    new_id = created["id"]
    assert created["title"] == "Studiu Neuroștiințe și Memorie"
    assert created["work_type"] == "disertatie"

    # 3. Verify active project changed
    res_list = client.get("/api/projects")
    assert res_list.json()["active_id"] == new_id
    assert len(res_list.json()["projects"]) == 2

    # 4. Switch back to default project
    res_act = client.post(f"/api/projects/{default_id}/activate")
    assert res_act.status_code == 200
    assert res_act.json()["active_id"] == default_id

    # 5. Delete the new project
    res_del = client.delete(f"/api/projects/{new_id}")
    assert res_del.status_code == 200
    assert client.get("/api/projects").json()["active_id"] == default_id
    assert len(client.get("/api/projects").json()["projects"]) == 1


def test_legacy_single_row_project_table_is_migrated(tmp_path, monkeypatch):
    """Databases created before multi-project support still accept new projects."""
    import sqlite3

    import app.db as db_module

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE project (
            id             INTEGER PRIMARY KEY CHECK (id = 1),
            title          TEXT NOT NULL DEFAULT '',
            work_type      TEXT NOT NULL DEFAULT 'licenta',
            field          TEXT NOT NULL DEFAULT '',
            description    TEXT NOT NULL DEFAULT '',
            research_q     TEXT NOT NULL DEFAULT '',
            methodology    TEXT NOT NULL DEFAULT '',
            deadline       TEXT DEFAULT '',
            structure_json TEXT NOT NULL DEFAULT '[]',
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("INSERT INTO project (id, title) VALUES (1, 'Legacy project')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()

    with TestClient(app) as c:
        created = c.post("/api/projects", json={"title": "Project two"})
        assert created.status_code == 201, created.text
        assert created.json()["id"] == 2
        assert created.json()["title"] == "Project two"

        listed = c.get("/api/projects").json()
        assert [p["title"] for p in listed["projects"]] == ["Legacy project", "Project two"]
        assert listed["active_id"] == 2
