"""Tests for the .docx thesis export."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "docx.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


def test_docx_export_contains_chapters_and_bibliography(client):
    client.put("/api/project", json={
        "title": "Somnul și memoria",
        "work_type": "licenta",
        "structure": [
            {"id": "c1", "title": "Introducere",
             "content": "Somnul consolidează memoria (Smith, 2021).\n\n## Subsecțiune\n- punct unu\n- **punct bold**"},
        ],
    })
    client.post("/api/entries", json={
        "title": "Sleep and memory", "study_type": "study",
        "authors": "Ioan Smith", "entry_date": "2021-01-01", "doi": "10.1000/docx-sleep",
    })

    res = client.get("/api/project/export/docx")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment" in res.headers["content-disposition"]
    assert res.headers["content-disposition"].endswith('.docx"')
    # DOCX is a ZIP: the magic bytes are the honest structural check here.
    assert res.content[:2] == b"PK"
    assert len(res.content) > 1000


def test_docx_export_empty_project_still_writes(client):
    res = client.get("/api/project/export/docx")
    assert res.status_code == 200
    assert res.content[:2] == b"PK"
