import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.db import get_db, init_db
from app.main import app
from app.routers.chat import _build_context, _parse_actions


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_notes.db")
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_conn(client):
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def test_study_notes_crud(client, db_conn):
    # 1. Insert a study entry
    cursor = db_conn.cursor()
    cursor.execute(
        """
        INSERT INTO entries (title, entry_date, study_type, content, source, doi, pdf_path)
        VALUES ('Bliss & Lomo 1973 LTP', '1973-01-01', 'study', 'Potentiation of synaptic transmission.', 'Nature', '10.1113/jphysiol.1973.sp010273', 'studies/bliss1973.pdf')
        """
    )
    db_conn.commit()
    entry_id = cursor.lastrowid

    # 2. POST /api/studies/{entry_id}/notes
    create_payload = {
        "course_context": "Cursul de Neurobiologie Celulară — Modulul 2",
        "content": "Stimularea tetanică de înaltă frecvență produce potențare pe termen lung (LTP).",
        "page_number": 4,
    }
    res = client.post(f"/api/studies/{entry_id}/notes", json=create_payload)
    assert res.status_code == 200, res.text
    note = res.json()
    assert note["id"] is not None
    assert note["entry_id"] == entry_id
    assert note["course_context"] == create_payload["course_context"]
    assert note["content"] == create_payload["content"]
    assert note["page_number"] == 4
    assert note["created_at"] is not None
    note_id = note["id"]

    # 3. GET /api/studies/{entry_id}/notes
    res = client.get(f"/api/studies/{entry_id}/notes")
    assert res.status_code == 200
    notes = res.json()
    assert len(notes) == 1
    assert notes[0]["id"] == note_id
    assert notes[0]["course_context"] == create_payload["course_context"]

    # 4. GET /api/studies/notes/all
    res = client.get("/api/studies/notes/all")
    assert res.status_code == 200
    all_notes = res.json()
    assert len(all_notes) == 1
    assert all_notes[0]["study_title"] == "Bliss & Lomo 1973 LTP"

    # 5. PUT /api/studies/notes/{note_id}
    update_payload = {
        "course_context": "Cursul de Neurobiologie — Capitolul Sinapse",
        "content": "Notiță revizuită: mecanismul implică influx masiv de Ca2+ prin NMDA.",
        "page_number": 5,
    }
    res = client.put(f"/api/studies/notes/{note_id}", json=update_payload)
    assert res.status_code == 200
    updated_note = res.json()
    assert updated_note["id"] == note_id
    assert updated_note["course_context"] == update_payload["course_context"]
    assert updated_note["content"] == update_payload["content"]
    assert updated_note["page_number"] == 5

    # 6. DELETE /api/studies/notes/{note_id}
    res = client.delete(f"/api/studies/notes/{note_id}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True

    # Verify empty
    res = client.get(f"/api/studies/{entry_id}/notes")
    assert res.status_code == 200
    assert res.json() == []


def test_study_notes_cascade_deletion(client, db_conn):
    cursor = db_conn.cursor()
    cursor.execute(
        """
        INSERT INTO entries (title, entry_date, study_type, content)
        VALUES ('Temporary Study', '2020-01-01', 'study', 'Some content')
        """
    )
    db_conn.commit()
    entry_id = cursor.lastrowid

    # Create note
    res = client.post(
        f"/api/studies/{entry_id}/notes",
        json={"course_context": "Seminar 1", "content": "Test note to be deleted"},
    )
    assert res.status_code == 200
    note_id = res.json()["id"]

    # Delete study entry via DB
    cursor.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    db_conn.commit()

    # Verify note was removed via foreign key cascade
    cursor.execute("SELECT COUNT(*) FROM study_notes WHERE id = ?", (note_id,))
    assert cursor.fetchone()[0] == 0


def test_chat_study_notes_context_integration(client, db_conn):
    cursor = db_conn.cursor()
    cursor.execute(
        """
        INSERT INTO entries (title, entry_date, study_type, content)
        VALUES ('Squire 1992 Memory Systems', '1992-01-01', 'study', 'Declarative and nondeclarative memory systems of the brain.')
        """
    )
    db_conn.commit()
    entry_id = cursor.lastrowid

    # Add note with course context
    client.post(
        f"/api/studies/{entry_id}/notes",
        json={
            "course_context": "Cursul de Psihologie Cognitivă — Curs 3",
            "content": "Hipocampul consolidează memoria declarativă dar nu este sediul stocării permanente.",
            "page_number": 8,
        },
    )

    # Query asking about notes from this course
    context_items = _build_context("Ce notite am notat la cursul de psihologie cognitiva despre memorie?")
    assert len(context_items) > 0
    full_text = "\n".join(c["content"] for c in context_items)

    assert "NOTIȚE SCRISE DE TINE (UTILIZATORUL)" in full_text
    assert "Cursul de Psihologie Cognitivă — Curs 3" in full_text
    assert "Hipocampul consolidează memoria declarativă" in full_text
    assert "Pagina 8" in full_text


def test_chat_execute_study_note_action(client, db_conn):
    cursor = db_conn.cursor()
    cursor.execute(
        """
        INSERT INTO entries (title, entry_date, study_type, content)
        VALUES ('Kandel 2001 Synaptic Plasticity', '2001-01-01', 'study', 'Molecular mechanisms of synaptic plasticity.')
        """
    )
    db_conn.commit()
    entry_id = cursor.lastrowid

    # Test adding note via chat action tag
    ai_reply = f"""
    Am analizat studiul tău. Voi salva o notiță legată de mecanismul descris:
    [ADD_STUDY_NOTE: {entry_id} | Master Neuro | Rolul cascadei cAMP-PKA in facilitarea presinaptica]
    """
    actions = _parse_actions(ai_reply)
    assert any(a.get("type") == "study_note_added" for a in actions), actions

    # Verify in DB
    cursor.execute("SELECT course_context, content FROM study_notes WHERE entry_id = ?", (entry_id,))
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "Master Neuro"
    assert "cAMP-PKA" in row[1]
