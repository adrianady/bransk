import json
import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.routers import chat as chat_mod
from app.web_search import search_web


def _parse(reply: str) -> list[dict]:
    return chat_mod._parse_actions(reply)


def test_all_ai_tools_parse_and_execute():
    with TestClient(app) as client:
        client.post("/api/entries", json={"title": "Physics note", "content": "about physics", "tags": "physics"})
        crafted = (
            "[FETCH_ENTRY: 1]\n"
            "[LIST_ENTRIES: physics]\n"
            "[LIST_RELATIONSHIPS: 1]\n"
            "[APPEND_ENTRY: 1 | extra]\n"
            "[CREATE_THEORY: Spaced practice | notes |  | supported]\n"
            "[LINK_STUDY: 1 | 1 | pro | replicated]\n"
            "[SEARCH_WEB: test query]"
        )
        acts = _parse(crafted)
        types = {a["type"] for a in acts}
        assert {
            "entry_fetched",
            "entries_listed",
            "relationships_listed",
            "entry_appended",
            "theory_created",
            "study_linked",
            "web_search_request",
        } <= types


def test_link_study_missing_target_returns_error_action():
    acts = _parse("[LINK_STUDY: 999 | 999 | pro | x]")
    assert any(a["type"] == "error" for a in acts)


def test_malformed_actions_never_crash_parser():
    """Regression: a bad numeric id used to raise ValueError and kill the whole reply."""
    # Must not raise, even for garbage inside action tags.
    acts = _parse(
        "[ADD_TODO: notanumber | important | 2026-08-01]\n"
        "[SPACED_REPETITION: xyz]\n"
        "[CREATE_RELATIONSHIP: a | b | label]\n"
        "[ADD_REMINDER: abc | title | 2026-08-01T09:00 | once]\n"
        "[FETCH_ENTRY: oops]\n"
        "[LINK_STUDY: x | y | pro | note]"
    )
    errors = [a for a in acts if a["type"] == "error"]
    # The permissive ADD_TODO pattern is the crash vector; it must degrade to an error.
    # Strictly-numeric patterns simply don't match and are safely ignored.
    assert len(errors) >= 1
    assert any("ADD_TODO" in e.get("detail", "") for e in errors)


def test_list_theories_read_tool():
    with TestClient(app) as client:
        client.post("/api/theories", json={"name": "Consolidation theory"})
        acts = _parse("[LIST_THEORIES: consolidation]")
        listed = next(a for a in acts if a["type"] == "theories_listed")
        assert any(r["name"] == "Consolidation theory" for r in listed["results"])


def test_chat_stream_works_offline_with_mock():
    with TestClient(app) as client:
        resp = client.post("/api/chat/stream", json={"message": "Hello", "history": []})
        assert resp.status_code == 200
        events = [l for l in resp.text.splitlines() if l.startswith("data: ")]
        assert any("chunk" in json.loads(l[6:])["type"] for l in events if l[6:].strip() != "[DONE]")


def test_chat_stream_triggers_research_action():
    with TestClient(app) as client:
        resp = client.post("/api/chat/stream", json={"message": "Research sleep", "history": []})
        assert resp.status_code == 200
        events = [l for l in resp.text.splitlines() if l.startswith("data: ")]
        types = [json.loads(l[6:])["type"] for l in events if l[6:].strip() != "[DONE]"]
        assert "research" in types


def test_web_search_empty_query_short_circuits():
    assert asyncio.run(search_web("")) == []
