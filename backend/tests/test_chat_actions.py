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
