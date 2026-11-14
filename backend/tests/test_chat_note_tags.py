"""Chat agent wiring for verified note dossiers (NOTE_* tags + reading context)."""
import asyncio

from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
import app.chat as chat_mod
from app.chat.context import reading_context


ABSENT = (
    "Ipoteza câmpului cuantic determină scorurile de anxietate "
    "la adolescenții din mediul urban."
)


def _study(client, title="Chat dossier study"):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201, res.text
    return res.json()


def _insert_page(conn, entry_id, page, text, printed="", source="native"):
    conn.execute(
        """INSERT OR REPLACE INTO page_texts
           (entry_id, page_number, text, source, status, char_count, printed_page)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [entry_id, page, text, source, source, len(text), printed],
    )
    conn.commit()


def _parse(reply):
    return chat_mod._parse_actions(reply)


# ── 1. NOTE_CAPTURE parsing ──────────────────────────────────────────────

def test_note_capture_parses_with_explicit_entry_id():
    text = "Am notat că procesele proximale sunt motoarele dezvoltării."
    acts = _parse(f"[NOTE_CAPTURE: 7 | {text}]")
    assert len(acts) == 1
    req = acts[0]
    assert req["type"] == "note_capture_request"
    assert req["entry_id"] == 7
    assert req["text"] == text
    assert req["page"] is None


def test_note_capture_empty_entry_id_means_reading_context():
    acts = _parse("[NOTE_CAPTURE: | o notiță fără id explicit]")
    assert len(acts) == 1
    assert acts[0]["type"] == "note_capture_request"
    assert acts[0]["entry_id"] is None
    assert acts[0]["text"] == "o notiță fără id explicit"


# ── 2. NOTE_VERIFY / LIST_NOTE_DOSSIERS parsing ──────────────────────────

def test_note_verify_and_list_parse_to_request_types():
    assert _parse("[NOTE_VERIFY: 3]") == [
        {"type": "note_verify_request", "dossier_id": 3}
    ]
    assert _parse("[LIST_NOTE_DOSSIERS: ]") == [{"type": "note_list_request"}]


# ── 3. async dispatch against a real entry + page_texts ──────────────────

def test_dispatch_note_capture_uses_reading_entry_and_creates_dossier():
    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                "Eșantionul a cuprins două sute de profesori din școli rurale.",
                printed="3",
            )
        finally:
            conn.close()

        actions = [
            {"type": "note_capture_request", "entry_id": None, "text": ABSENT, "page": None}
        ]
        asyncio.run(chat_mod._dispatch_note_actions(actions, entry_id=study["id"]))

        assert len(actions) == 1
        result = actions[0]
        assert result["type"] == "note_dossier_updated"
        assert result["entry_id"] == study["id"]
        assert result["summary"]["total"] >= 1

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT id FROM note_dossiers WHERE entry_id = ?", [study["id"]]
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert result["dossier_id"] == row["id"]


def test_dispatch_note_capture_without_any_entry_is_an_error():
    actions = [
        {"type": "note_capture_request", "entry_id": None, "text": ABSENT, "page": None}
    ]
    asyncio.run(chat_mod._dispatch_note_actions(actions))
    assert actions[0]["type"] == "error"
    assert "entry_id" in actions[0]["detail"]


def test_reading_context_block_mentions_entry_dossier_and_page():
    with TestClient(app) as client:
        study = _study(client, title="Reading block study")
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                "Eșantionul a cuprins două sute de profesori din școli rurale.",
                printed="3",
            )
        finally:
            conn.close()

        asyncio.run(
            chat_mod._dispatch_note_actions(
                [{"type": "note_capture_request", "entry_id": study["id"], "text": ABSENT, "page": None}]
            )
        )

        conn = get_db()
        try:
            assert reading_context(conn, None, None) == ""
            block = reading_context(conn, study["id"], 1)
        finally:
            conn.close()
        assert "Citești:" in block
        assert "Reading block study" in block
        assert "Dosar de notițe" in block
        assert "tipărită 3" in block
        assert "profesori din școli rurale" in block


# ── 5. NOTE_MAP / NOTE_EXPORT parsing ─────────────────────────────────

def test_note_map_parses_dossier_and_project():
    assert _parse("[NOTE_MAP: 5]") == [
        {"type": "note_map_request", "dossier_id": 5}
    ]
    assert _parse("[NOTE_MAP: project]") == [
        {"type": "note_map_request", "dossier_id": None, "project": True}
    ]
    err = _parse("[NOTE_MAP: ]")
    assert len(err) == 1 and err[0]["type"] == "error" and "NOTE_MAP" in err[0]["detail"]


def test_note_export_parses_format_default_and_project():
    assert _parse("[NOTE_EXPORT: 5 | md]") == [
        {"type": "note_export_request", "dossier_id": 5, "format": "md"}
    ]
    assert _parse("[NOTE_EXPORT: 5]") == [
        {"type": "note_export_request", "dossier_id": 5, "format": "html"}
    ]
    assert _parse("[NOTE_EXPORT: 5 | txt]") == [
        {"type": "note_export_request", "dossier_id": 5, "format": "html"}
    ]
    assert _parse("[NOTE_EXPORT: project | html]") == [
        {"type": "note_export_request", "dossier_id": None, "project": True, "format": "html"}
    ]
    err = _parse("[NOTE_EXPORT: | md]")
    assert len(err) == 1 and err[0]["type"] == "error" and "NOTE_EXPORT" in err[0]["detail"]


# ── 6. dispatch note_map_request (dossier) with one valid edge ────────────

VERBATIM_TWO = (
    "Procesele proximale sunt motoarele principale ale dezvoltării. "
    "Contextul familial sprijină învățarea copilului."
)


def test_dispatch_note_map_dossier_accepts_one_valid_edge(monkeypatch):
    import app.notes.map as notes_map

    with TestClient(app) as client:
        study = _study(client, title="Map dossier study")
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, VERBATIM_TWO, printed="3")
        finally:
            conn.close()

        asyncio.run(
            chat_mod._dispatch_note_actions(
                [{"type": "note_capture_request", "entry_id": study["id"],
                  "text": VERBATIM_TWO, "page": None}]
            )
        )
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT id FROM note_dossiers WHERE entry_id = ?", [study["id"]]
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        did = int(row["id"])

        async def fake_edges(payload, api_note=""):
            return [{"from_code": "A1", "to_code": "A2",
                     "motive": "A2 detaliază mecanismul afirmat de A1",
                     "page": "3", "claim_code": "A2"}]

        monkeypatch.setattr(notes_map, "_llm_edges", fake_edges)

        actions = [{"type": "note_map_request", "dossier_id": did}]
        asyncio.run(chat_mod._dispatch_note_actions(actions))
        assert len(actions) == 1
        result = actions[0]
        assert result["type"] == "note_map_updated"
        assert result["scope"] == "dossier"
        assert result["dossier_id"] == did
        assert result["accepted"] == 1

        conn = get_db()
        try:
            stored = conn.execute(
                "SELECT COUNT(*) AS n FROM note_edges WHERE dossier_id = ?", [did]
            ).fetchone()["n"]
        finally:
            conn.close()
        assert stored == 1


# ── 7. dispatch note_export_request (dossier) ─────────────────────────────

def test_dispatch_note_export_dossier_returns_download_action():
    with TestClient(app) as client:
        study = _study(client, title="Export dossier study")
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                "Eșantionul a cuprins două sute de profesori din școli rurale.",
                printed="3",
            )
        finally:
            conn.close()

        asyncio.run(
            chat_mod._dispatch_note_actions(
                [{"type": "note_capture_request", "entry_id": study["id"],
                  "text": ABSENT, "page": None}]
            )
        )
        conn = get_db()
        try:
            did = int(conn.execute(
                "SELECT id FROM note_dossiers WHERE entry_id = ?", [study["id"]]
            ).fetchone()["id"])
        finally:
            conn.close()

        actions = [{"type": "note_export_request", "dossier_id": did, "format": "html"}]
        asyncio.run(chat_mod._dispatch_note_actions(actions))
        assert len(actions) == 1
        result = actions[0]
        assert result["type"] == "note_export_ready"
        assert result["scope"] == "dossier"
        assert result["format"] == "html"
        assert result["url"] == f"/api/notes/dossiers/{did}/export?format=html"
        assert result["filename"].endswith(".html")
        assert result["bytes"] > 0
        assert "<html" not in str(actions)


# ── 8. _tool_results_text renders the map compactly ───────────────────────

def test_tool_results_text_renders_note_map_counts_and_arrows():
    text = chat_mod._tool_results_text([
        {"type": "note_map_updated", "scope": "dossier", "dossier_id": 9,
         "edges": [{"from_code": "A1", "to_code": "A3",
                    "motive": "A3 explică mecanismul din A1", "page": "12"}],
         "accepted": 7, "rejected": 2},
    ])
    assert "7 muchii validate" in text
    assert "2 respinse" in text
    assert "A1 → A3" in text


# ── 4. POST /api/chat integration (monkeypatched reply) ─────────────────

def test_chat_endpoint_lists_note_dossiers(monkeypatch):
    with TestClient(app) as client:
        _study(client)

        async def fake_complete(messages, **kwargs):
            return ("fake", "fake-model", "[LIST_NOTE_DOSSIERS: ]")

        monkeypatch.setattr(chat_mod, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(chat_mod, "llm_complete", fake_complete)

        resp = client.post("/api/chat", json={"message": "arată-mi dosarele", "history": []})
        assert resp.status_code == 200
        assert any(
            a["type"] == "note_dossiers_listed" for a in resp.json()["actions"]
        )
