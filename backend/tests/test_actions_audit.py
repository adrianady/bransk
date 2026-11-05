"""
Tests for the AI action parser and the project ranking rules.

`_parse_actions` turns model text into writes on the real database, so it is the
most dangerous function in the app: a tag echoed inside prose, a fenced code
example or a malformed importance must never reach the KB. None of that was
tested before; chat.py stood at 59% coverage on 2026-10-25.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "actions_audit.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def entry(client):
    return client.post("/api/entries", json={"title": "Entry A", "content": "body"}).json()


# ─────────────────── action parser: safety ───────────────────

def test_fenced_and_inline_code_never_executes(client):
    """A code sample in the reply is documentation, not a tool call."""
    from app.routers.chat import _parse_actions

    reply = (
        "Here is how the tag looks:\n\n"
        "```\n[CREATE_ENTRY: From a fence | x | | | 3]\n```\n\n"
        "and inline `[CREATE_ENTRY: From backticks | y | | | 3]` too."
    )
    assert _parse_actions(reply) == []
    assert client.get("/api/entries").json() == []


def test_tag_echoed_inside_prose_does_not_run(client):
    from app.routers.chat import _parse_actions

    actions = _parse_actions("You asked: [ARCHIVE_ENTRY: 1] so I will not do it.")
    assert actions == []


def test_line_tag_payload_may_contain_brackets(client):
    from app.routers.chat import _extract_line_tags

    assert _extract_line_tags("[SEARCH_KB: brain [regions] and memory]", "SEARCH_KB") == [
        "brain [regions] and memory"
    ]
    assert _extract_line_tags("  [SEARCH_KB: indented]", "SEARCH_KB") == ["indented"]
    assert _extract_line_tags("[OTHER: x]", "SEARCH_KB") == []


def test_create_entry_runs_and_stores_the_row(client):
    from app.routers.chat import _parse_actions

    actions = _parse_actions("[CREATE_ENTRY: Learned thing | the body | 2026-02-02 | alpha,beta | 4]")
    assert [a["type"] for a in actions] == ["entry_created"]
    rows = client.get("/api/entries").json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Learned thing"
    assert rows[0]["entry_date"] == "2026-02-02"
    assert rows[0]["tags"] == "alpha, beta"
    assert rows[0]["importance"] == 4


def test_create_entry_importance_is_clamped(client):
    from app.routers.chat import _parse_actions

    _parse_actions("[CREATE_ENTRY: Nine | body | | | 9]")
    _parse_actions("[CREATE_ENTRY: Zero | body | | | 0]")
    importances = sorted(r["importance"] for r in client.get("/api/entries").json())
    assert importances == [1, 5], "out-of-range importance is clamped, not rejected"


def test_create_entry_with_blank_date_stores_no_date(client):
    from app.routers.chat import _parse_actions

    _parse_actions("[CREATE_ENTRY: No date | body | | | 3]")
    assert client.get("/api/entries").json()[0]["entry_date"] is None


def test_identical_create_entry_runs_only_once(client):
    from app.routers.chat import _parse_actions

    tag = "[CREATE_ENTRY: Twice | body | | | 3]"
    actions = _parse_actions(f"{tag}\n{tag}")
    assert len(actions) == 1
    assert len(client.get("/api/entries").json()) == 1


def test_near_miss_create_entry_returns_a_corrective_error(client):
    from app.routers.chat import _parse_actions

    actions = _parse_actions("[CREATE_ENTRY: Bad | body | | | five stars]")
    assert actions[0]["type"] == "error"
    assert "numeric importance" in actions[0]["detail"]
    assert client.get("/api/entries").json() == []


def test_search_kb_is_read_only_and_limited(client, entry):
    from app.routers.chat import _parse_actions

    actions = _parse_actions("[SEARCH_KB: Entry]")
    assert actions[0]["type"] == "search_results"
    assert actions[0]["results"][0]["id"] == entry["id"]
    assert _parse_actions("[SEARCH_KB: nothing-matches-this]")[0]["results"] == []


def test_relationship_tag_needs_two_numeric_ids(client, entry):
    from app.routers.chat import _parse_actions

    other = client.post("/api/entries", json={"title": "B"}).json()
    ok = _parse_actions(f"[CREATE_RELATIONSHIP: {entry['id']} | {other['id']} | supports]")
    assert ok[0]["type"] == "relationship_created"
    assert len(client.get("/api/relationships").json()) == 1

    bad = _parse_actions("[CREATE_RELATIONSHIP: one | two | supports]")
    assert bad[0]["type"] == "error"


def test_theory_tags_create_and_link(client, entry):
    from app.routers.chat import _parse_actions

    created = _parse_actions("[CREATE_THEORY: Theory X | describes | | emerging]")
    assert created[0]["type"] == "theory_created"
    tid = created[0]["id"]

    linked = _parse_actions(f"[LINK_STUDY: {tid} | {entry['id']} | pro | it supports]")
    assert linked[0]["type"] == "study_linked"
    evidence = client.get(f"/api/theories/{tid}/evidence").json()
    assert len(evidence) == 1 and evidence[0]["stance"] == "pro"

    missing = _parse_actions("[LINK_STUDY: 999999 | 999999 | pro | x]")
    assert missing[0]["type"] == "error"


def test_list_theories_tag(client):
    from app.routers.chat import _parse_actions

    client.post("/api/theories", json={"name": "Attachment theory"})
    client.post("/api/theories", json={"name": "Resilience"})
    everything = _parse_actions("[LIST_THEORIES: ]")
    assert everything[0]["type"] == "theories_listed"
    assert len(everything[0]["results"]) == 2

    filtered = _parse_actions("[LIST_THEORIES: Attach]")
    assert [r["name"] for r in filtered[0]["results"]] == ["Attachment theory"]


def test_research_and_download_tags_become_requests_not_writes(client):
    from app.routers.chat import _parse_actions

    actions = _parse_actions("[RESEARCH: adverse childhood experiences]\n[DOWNLOAD_STUDY: 7]")
    assert actions[0] == {"type": "research_request", "topic": "adverse childhood experiences"}
    assert actions[1] == {"type": "download_study_request", "entry_id": 7}
    assert client.get("/api/entries").json() == []


def test_download_tag_without_a_number_is_an_error(client):
    from app.routers.chat import _parse_actions

    assert _parse_actions("[DOWNLOAD_STUDY: the third one]")[0]["type"] == "error"


def test_archive_and_append_write_the_entry(client, entry):
    from app.routers.chat import _parse_actions

    appended = _parse_actions(f"[APPEND_ENTRY: {entry['id']} | more text]")
    assert appended[0]["type"] == "entry_appended"
    assert "more text" in client.get(f"/api/entries/{entry['id']}").json()["content"]

    # archiving is destructive: it needs explicit confirmation, never runs alone
    pending = _parse_actions(f"[ARCHIVE_ENTRY: {entry['id']}]")
    assert pending[0]["type"] == "pending_confirm"
    assert pending[0]["action"] == {"type": "archive_entry", "entry_id": entry["id"]}
    assert client.get("/api/entries").json() != [], "the entry is still visible until confirmed"

    approved = client.post("/api/chat/confirm", json={"confirm_id": pending[0]["confirm_id"]})
    assert approved.status_code == 200
    assert approved.json()["status"] == "executed"
    assert client.get("/api/entries").json() == []

    # a destructive action can also be refused
    pending2 = _parse_actions(f"[ARCHIVE_ENTRY: {entry['id']}]")
    rejected = client.post("/api/chat/confirm", json={"confirm_id": pending2[0]["confirm_id"], "approve": False})
    assert rejected.json()["status"] == "rejected"


def test_unparseable_reply_yields_no_actions(client):
    from app.routers.chat import _parse_actions

    assert _parse_actions("") == []
    assert _parse_actions("Just a normal sentence, no tags.") == []
    assert _parse_actions(None) == []


def test_pending_action_round_trip_and_expiry(client, entry):
    from app.routers import chat as chat_mod

    chat_mod._PENDING_ACTIONS.clear()
    nonce = chat_mod._store_pending_action({"type": "archive_entry", "entry_id": entry["id"]})
    assert nonce in chat_mod._PENDING_ACTIONS
    assert len(nonce) == 16

    # an old pending action is swept away on the next store
    chat_mod._PENDING_ACTIONS[nonce]["created_at"] -= chat_mod._PENDING_TTL_SECONDS + 1
    fresh = chat_mod._store_pending_action({"type": "noop"})
    assert nonce not in chat_mod._PENDING_ACTIONS
    assert fresh in chat_mod._PENDING_ACTIONS
    chat_mod._PENDING_ACTIONS.clear()


def test_confirm_rejects_unknown_and_malformed_ids(client):
    from app.routers import chat as chat_mod

    chat_mod._PENDING_ACTIONS.clear()
    assert client.post("/api/chat/confirm", json={"confirm_id": "deadbeef"}).status_code == 404
    # Note: an expired nonce is swept only when the next action is stored, so a
    # stale id currently answers 400 "Unknown pending action type" instead of a
    # 410 "expired". Pinned as it behaves today; see PROJECT_STATE.md.
    nonce = chat_mod._store_pending_action({"type": "noop"})
    assert client.post("/api/chat/confirm", json={"confirm_id": nonce}).status_code == 400
    assert client.post("/api/chat/confirm", json={"confirm_id": nonce}).status_code == 404, "single use"
    chat_mod._PENDING_ACTIONS.clear()


# ─────────────────── chat flow with the mock provider ───────────────────

def test_mock_chat_saves_and_returns_a_reply(client):
    res = client.post("/api/chat", json={"message": "salut, ce pot face?"})
    assert res.status_code == 200
    assert res.json()["reply"]
    history = client.get("/api/chat/history").json()
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_mock_chat_creates_an_entry_from_a_save_request(client):
    res = client.post("/api/chat", json={"message": "save this: mitochondria is the powerhouse"})
    assert res.status_code == 200
    rows = client.get("/api/entries").json()
    assert len(rows) == 1
    assert "mitochondria" in rows[0]["title"].lower()


def test_chat_rejects_empty_and_oversized_messages(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 100001}).status_code == 422
    assert client.post("/api/chat", json={"message": "ok", "history": []}).status_code == 200


def test_chat_rejects_a_bad_role_in_history(client):
    res = client.post("/api/chat", json={
        "message": "hi", "history": [{"role": "hacker", "content": "x"}],
    })
    assert res.status_code == 422


def test_chat_history_is_persisted_between_calls(client):
    client.post("/api/chat", json={"message": "one"})
    client.post("/api/chat", json={"message": "two"})
    history = client.get("/api/chat/history").json()
    assert len(history) == 4
    assert [m["content"] for m in history if m["role"] == "user"] == ["one", "two"]


def test_chat_history_limit_and_clear(client):
    client.post("/api/chat", json={"message": "one"})
    assert len(client.get("/api/chat/history?limit=1").json()) == 1
    assert client.delete("/api/chat/history").status_code == 204
    assert client.get("/api/chat/history").json() == []


def test_chat_stream_emits_sse_and_finishes(client):
    res = client.post("/api/chat/stream", json={"message": "salut"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "[DONE]" in res.text


def test_chat_health_reports_provider_shape(client):
    body = client.get("/api/chat/health").json()
    assert set(body) >= {"deepseek", "ollama", "active"}


def test_research_intent_detection():
    """Every natural way of asking must start the pipeline — fixed 2026-10-26.

    `_RESEARCH_VERBS` used to have no "research" in it, so "please research on X",
    "can you research on X" and "I want you to research about X" silently did
    nothing while the awkward "run a research on X" worked. The politeness strip
    also left "you to" behind because it matched "i want" before "i want you to".
    """
    from app.routers.chat import _detect_research_intent

    assert _detect_research_intent("please research on theory of mind") == "theory of mind"
    assert _detect_research_intent("can you research on theory of mind") == "theory of mind"
    assert _detect_research_intent("I want you to research about resilience") == "resilience"
    assert _detect_research_intent("research for studies about resilience") == "resilience"
    assert _detect_research_intent("research theory of mind") == "theory of mind"
    assert _detect_research_intent("run a research on theory of mind") == "theory of mind"
    assert _detect_research_intent("find studies regarding adverse experiences") == "adverse experiences"
    assert _detect_research_intent("look up studies on memory consolidation") == "memory consolidation"

    # nothing to research
    assert _detect_research_intent("research") is None
    assert _detect_research_intent("I read a study yesterday") is None, "no request verb, no pipeline"
    assert _detect_research_intent("nothing to see") is None


def test_mock_reply_picks_the_right_action_shape():
    from app.routers.chat import _mock_reply

    assert "[RESEARCH:" in _mock_reply("research on memory")
    assert "[CREATE_ENTRY:" in _mock_reply("save this fact")
    assert "[ADD_REMINDER:" in _mock_reply("remind me")
    assert "[ADD_TODO:" in _mock_reply("add a task")
    assert "[CREATE_RELATIONSHIP:" in _mock_reply("link these two")
    assert "[ARCHIVE_ENTRY:" in _mock_reply("archive it")
    assert "[RESEARCH:" not in _mock_reply("hello there")


def test_mock_stream_chunks_reassemble_to_the_reply():
    from app.routers.chat import _mock_reply, _mock_stream_chunks

    message = "hello there, how are you today"
    streamed = "".join(_mock_stream_chunks(message))
    assert streamed.strip() == _mock_reply(message).strip()


def test_history_item_extraction_clips_and_tolerates_shapes():
    from app.routers.chat import _extract_history_item

    assert _extract_history_item({"role": "user", "content": "abc"}) == {"role": "user", "content": "abc"}
    clipped = _extract_history_item({"role": "user", "content": "x" * 20000})
    assert len(clipped["content"]) == 10000

    class Obj:
        role = "assistant"
        content = "from object"

    assert _extract_history_item(Obj()) == {"role": "assistant", "content": "from object"}
    assert _extract_history_item("bare string")["role"] == "user"


def test_tool_results_text_covers_every_action_type():
    from app.routers.chat import _tool_results_text

    actions = [
        {"type": "entry_fetched", "entry": {"id": 1, "title": "T", "entry_date": None, "tags": "a", "content": "c"}},
        {"type": "entries_listed", "results": [{"id": 2, "title": "L", "tags": ""}]},
        {"type": "relationships_listed", "results": [{"id": 3, "from_id": 1, "to_id": 2, "label": "x"}]},
        {"type": "theories_listed", "results": [{"id": 4, "name": "Th", "status": "emerging", "pro_count": 1, "contra_count": 0}]},
        {"type": "web_results", "results": [{"title": "W", "url": "https://w", "snippet": "s"}]},
        {"type": "documents_results", "results": [{"entry_title": "P", "content": "chunk"}]},
        {"type": "entry_created", "id": 5, "title": "New"},
        {"type": "relationship_created", "from": 1, "to": 2, "label": "y"},
        {"type": "todo_added", "entry_id": 1, "priority": "normal"},
        {"type": "reminder_added", "entry_id": 1},
        {"type": "entry_appended", "entry_id": 1},
        {"type": "theory_created", "id": 6, "name": "Th2"},
        {"type": "study_linked", "entry_id": 1, "theory_id": 4, "stance": "pro"},
    ]
    text = _tool_results_text(actions)
    for needle in ("ENTRY #1", "- [2] L", "relationship 3", "theory #4",
                   "W | https://w", "PDF CHUNK", "CREATED entry #5",
                   "CREATED relationship", "CREATED task", "CREATED reminder",
                   "APPENDED", "CREATED theory #6", "LINKED entry #1"):
        assert needle in text, f"tool results lost: {needle}"
    assert _tool_results_text([]) == "(no results found)"


def test_context_builder_finds_the_matching_entry(client, entry):
    from app.routers.chat import _build_context

    ctx = _build_context("Entry")
    assert any(c["id"] == entry["id"] for c in ctx)
    assert _build_context("") == [] or isinstance(_build_context(""), list)


def test_system_prompt_lists_context_and_forbids_echo(client, entry):
    from app.routers.chat import _build_context, _build_system_prompt

    prompt = _build_system_prompt(_build_context("Entry"), "mock", "mock-model")
    assert "Bransk AI" in prompt
    assert "Entry A" in prompt


def test_summarize_old_history_clips_a_long_conversation():
    from app.routers.chat import _summarize_old_history

    assert _summarize_old_history([]) == ""
    history = [{"role": "user", "content": f"message {i}"} for i in range(30)]
    summary = _summarize_old_history(history)
    assert isinstance(summary, str)


def test_answer_with_tools_offline_never_calls_a_provider(client):
    from app.routers.chat import _answer_with_tools

    answer = asyncio.run(_answer_with_tools("q", [], [], "first", [{"type": "entries_listed", "results": []}]))
    assert "knowledge base" in answer


def test_chunk_text_helper_splits_on_huge_words():
    from app.routers.chat import _chunk_text

    assert list(_chunk_text("a b c", size=2)) == ["a b ", "c "]


# ─────────────────── project ranking rules ───────────────────

def test_keywords_fold_diacritics_and_drop_stopwords():
    from app.routers.project import _keywords

    terms = _keywords("Experiențele adverse și reziliența copilului")
    assert "experientele" in terms
    assert "rezilienta" in terms
    assert "si" not in terms
    for stop in ("care", "este", "pentru", "the", "and"):
        assert stop not in _keywords(f"x {stop} y")
    assert _keywords("2019 2020") == [], "bare numbers are not terms"
    assert len(_keywords("a a a b")) == len(set(_keywords("a a a b"))), "terms are deduped"


def test_score_study_weights_titles_three_times():
    from app.routers.project import _score_study

    title_hit, _ = _score_study({"title": "memory study"}, ["memory"])
    body_hit, _ = _score_study({"title": "other", "content": "memory"}, ["memory"])
    assert title_hit > body_hit
    assert _score_study({"title": "nothing"}, ["memory"]) == (0.0, [])


def test_score_study_adds_a_quality_bonus():
    from app.routers.project import _score_study

    assert _score_study({"title": "t", "quality": 5}, []) == (2.0, [])
    assert _score_study({"title": "t"}, []) == (0.0, [])


def test_rank_studies_without_project_text_returns_top_quality(client):
    client.post("/api/entries", json={"title": "Mediocre", "study_type": "paper", "quality": 2})
    client.post("/api/entries", json={"title": "Excellent", "study_type": "paper", "quality": 5})
    body = client.get("/api/project/studies").json()
    assert body["studies"][0]["title"] == "Excellent"
    assert body["studies"][0]["reason"].startswith("Fără titlu")
    assert body["total_library"] == 2


def test_rank_studies_matches_the_project_topic(client):
    client.put("/api/project", json={
        "title": "Adverse childhood experiences and resilience",
        "research_q": "How does resilience buffer adverse experiences?",
    })
    client.post("/api/entries", json={"title": "Resilience after adversity", "study_type": "paper"})
    client.post("/api/entries", json={"title": "Quantum chromodynamics", "study_type": "paper"})

    body = client.get("/api/project/studies").json()
    titles = [s["title"] for s in body["studies"]]
    assert titles[0] == "Resilience after adversity"
    assert "Quantum chromodynamics" not in titles, "unrelated studies are not recommended"
    assert body["studies"][0]["matched_terms"]
    assert "Se potrivește pe:" in body["studies"][0]["reason"]
    assert "resilience" in body["terms"]


def test_rank_studies_respects_the_limit_cap(client):
    for i in range(60):
        client.post("/api/entries", json={"title": f"Study {i}", "study_type": "paper"})
    assert len(client.get("/api/project/studies?limit=500").json()["studies"]) <= 50
    assert len(client.get("/api/project/studies?limit=1").json()["studies"]) == 1


def test_project_analysis_stats_count_library_shape(client):
    client.put("/api/project", json={"title": "Resilience and adversity"})
    client.post("/api/entries", json={
        "title": "Resilience RCT", "study_type": "paper", "design": "RCT",
        "effect_direction": "positive", "pdf_path": "1-a.pdf",
    })
    client.post("/api/entries", json={
        "title": "Resilience cohort", "study_type": "paper", "design": "Cohort",
        "effect_direction": "negative",
    })
    body = client.get("/api/project/analysis").json()
    stats = body["stats"]
    assert stats["library_studies"] == 2
    assert stats["studies_with_pdf"] == 1
    assert stats["effect_directions"] == {"positive": 1, "negative": 1}
    assert set(stats["designs"]) == {"rct", "cohort"}
    assert body["from_llm"] is False
    assert "doar pe potriviri de cuvinte" in body["analysis"]


def test_doi_is_written_through_the_api_and_counted(client):
    """Regression guard for the gap found on 2026-10-25.

    `studies_with_doi` used to count a column no HTTP route ever wrote: the 51
    DOIs in the real library came from the one-off scripts in backend/scripts/,
    while EntryCreate and EntryUpdate had no `doi` field. The field now exists,
    is validated, and the counter follows it.
    """
    eid = client.post("/api/entries", json={
        "title": "Has a DOI", "study_type": "paper",
    }).json()["id"]
    assert client.get("/api/project/analysis").json()["stats"]["studies_with_doi"] == 0

    updated = client.put(f"/api/entries/{eid}", json={"doi": "10.1000/xyz"}).json()
    assert updated["doi"] == "10.1000/xyz"
    assert updated["doi_source"] == "manual"

    assert client.get("/api/project/analysis").json()["stats"]["studies_with_doi"] == 1
    assert client.get(f"/api/entries/{eid}").json()["doi"] == "10.1000/xyz"


def test_project_analysis_without_a_title_asks_for_one(client):
    body = client.get("/api/project/analysis").json()
    assert "adaugă un titlu" in body["analysis"]


def test_project_analysis_reports_matching_not_library_count(client):
    client.put("/api/project", json={"title": "resilience"})
    client.post("/api/entries", json={"title": "unrelated", "study_type": "paper"})
    stats = client.get("/api/project/analysis").json()["stats"]
    assert stats["library_studies"] == 1
    assert stats["matching_studies"] == 0


def test_structural_feedback_flags_title_length(client):
    from app.routers.project import _structural_feedback

    short = _structural_feedback({"title": "Two words", "research_q": "q", "methodology": "m",
                                  "structure": [{"title": "s", "content": "x", "target_words": 10}]})
    assert any(i["level"] == "warning" and "scurt" in i["message"] for i in short)


def test_structural_feedback_ignores_sections_without_a_target():
    from app.routers.project import _structural_feedback

    items = _structural_feedback({
        "title": "A properly long academic title here", "research_q": "q", "methodology": "m",
        "structure": [{"title": "Bibliografie", "content": "", "target_words": 0}],
    })
    assert not any("goale" in i["message"] for i in items), "the bibliography has no target"


def test_structural_feedback_counts_words():
    from app.routers.project import _structural_feedback

    items = _structural_feedback({
        "title": "A properly long academic title here", "research_q": "q", "methodology": "m",
        "structure": [{"title": "s", "content": "one two three four", "target_words": 10}],
    })
    assert any("aproximativ 4 cuvinte" in i["message"] for i in items)


def test_project_update_ignores_unknown_and_keeps_old_values(client):
    client.put("/api/project", json={"title": "First", "field": "psychology"})
    body = client.put("/api/project", json={"field": "neuroscience"}).json()
    assert body["title"] == "First"
    assert body["field"] == "neuroscience"


def test_project_structure_survives_a_reload_of_the_page(client):
    """The acceptance criterion from PROJECT_STATE.md, as a test."""
    structure = [{"id": "s1", "title": "Introducere", "target_words": 1200,
                  "description": "d", "content": "Text scris aici."}]
    client.put("/api/project", json={"structure": structure})
    reloaded = client.get("/api/project").json()
    assert reloaded["structure"][0]["content"] == "Text scris aici."


def test_project_row_corruption_degrades_to_an_empty_structure(client):
    from app.db import get_db
    from app.routers.project import _row_to_project

    assert _row_to_project({"structure_json": "not json"})["structure"] == []
    assert _row_to_project({"structure_json": '{"not": "a list"}'})["structure"] == []
    assert _row_to_project({"structure_json": None})["structure"] == []
    assert _row_to_project({})["structure"] == []

    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO project (id) VALUES (1)")
        conn.execute("UPDATE project SET structure_json = 'broken' WHERE id = 1")
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/project").json()["structure"] == []


def test_structure_example_offline_is_usable(client):
    body = client.post("/api/project/structure/example", json={"title": "My thesis"}).json()
    assert body["from_llm"] is False
    assert "standard" in body["note"]
    assert len(body["structure"]) >= 6
    assert all(s["id"].startswith("s") for s in body["structure"])
    assert sum(s["target_words"] for s in body["structure"]) >= 8000


def test_project_feedback_offline_has_rules_and_no_reviewer(client):
    client.put("/api/project", json={"title": "A long enough academic title",
                                     "research_q": "q", "methodology": "m"})
    body = client.get("/api/project/feedback").json()
    assert body["from_llm"] is False
    assert body["reviewer"] is None
    assert body["items"]


# ─────────────────── research helpers ───────────────────

def test_research_mock_json_is_topic_aware():
    from app.routers.research import _mock_json

    body = _mock_json("system mentioning protocol", "topic: resilience in children")
    assert isinstance(body, dict)


def test_research_study_normalisation_requires_a_title():
    from app.routers.research import _norm_study

    assert _norm_study({"title": ""}, "topic") is None
    assert _norm_study({}, "topic") is None
    study = _norm_study({"title": "A study", "authors": "A", "year": "2019"}, "topic")
    assert study is not None and study.title == "A study"


def test_research_study_normalisation_fills_tags_and_bounds_quality():
    from app.routers.research import _norm_study

    study = _norm_study({"title": "T", "quality": 99, "tags": ""}, "memory")
    assert study.quality == 5
    assert "memory" in study.tags


def test_research_report_markdown_has_the_expected_sections():
    from app.models import ResearchResult, ResearchStudy
    from app.routers.research import _render_report_markdown

    result = ResearchResult(
        topic="t", summary="s", synthesis="syn",
        studies=[ResearchStudy(title="Study one", authors="A", year=2020)],
    )
    md = _render_report_markdown(result, "t")
    assert "Study one" in md
    assert len(md) > 50


def test_research_list_studies_pagination(client):
    for i in range(5):
        client.post("/api/entries", json={"title": f"Study {i}", "study_type": "paper"})
    assert len(client.get("/api/studies?limit=2").json()) == 2
    assert len(client.get("/api/studies?offset=4").json()) == 1


def test_studies_report_the_real_total_outside_the_page(client):
    """A page of 2 must still say the library holds 5 studies.

    The screen printed the page length as the library size ("100 of 100"), so
    every study past the cap was invisible. X-Total-Count carries the truth.
    """
    for i in range(5):
        client.post("/api/entries", json={"title": f"Study {i}", "study_type": "paper"})
    res = client.get("/api/studies?limit=2")
    assert len(res.json()) == 2
    assert res.headers["X-Total-Count"] == "5"
    # The last page still reports the same total, not the rows it returned.
    last = client.get("/api/studies?limit=2&offset=4")
    assert len(last.json()) == 1
    assert last.headers["X-Total-Count"] == "5"
    # Walking the pages reaches every study, none left behind the cap.
    seen = []
    while True:
        page = client.get(f"/api/studies?limit=2&offset={len(seen)}").json()
        if not page:
            break
        seen.extend(page)
    assert len(seen) == 5
    assert len({s["id"] for s in seen}) == 5


def test_research_requires_a_topic(client):
    assert client.post("/api/research", json={}).status_code == 422
    assert client.post("/api/research", json={"topic": "ab"}).status_code == 422
    assert client.post("/api/research", json={"topic": "ab", "depth": "shallow"}).status_code == 422


def test_research_stream_emits_a_report_offline(client):
    res = client.post("/api/research/stream", json={"topic": "resilience and adversity", "depth": "quick"})
    assert res.status_code == 200
    assert "data: " in res.text
    assert "[DONE]" in res.text or '"error"' in res.text


def test_research_pipeline_offline_returns_a_result(client):
    body = client.post("/api/research", json={
        "topic": "resilience after adversity", "depth": "quick", "save": False,
    })
    if body.status_code == 200:
        data = body.json()
        assert data["topic"] == "resilience after adversity"
        assert "studies" in data
        assert data["entries_created"] == []
    else:
        assert body.status_code in (500, 503), "a failed offline pipeline must report, not crash silently"


# ─────────────────── duplicate studies: the real defect ───────────────────

def test_research_dedup_merges_a_title_truncated_by_the_llm(client, monkeypatch):
    """Found on 2026-10-25 against the real library: 10 entries duplicated by URL.

    `_save_research` used to merge a new study into an existing one only when the
    *normalized titles were equal*. When the model returned the same paper with
    the title cut short ("... and the brain: A systematic review of ..."), the
    strings differed and a second row was inserted — which is how ids 92/95,
    93/96 and 94/97 came to point at the same article.

    Fixed by `app.study_dedup`: a truncation, a near-identical title or a shared
    source URL now merge. This test is the regression guard.
    """
    from app.models import ResearchResult, ResearchStudy
    from app.routers.research import _save_research

    long_title = ("Mindfulness and the brain: A systematic review of the effects "
                  "of meditation on brain structure and function")
    short_title = "Mindfulness and the brain: A systematic review of ..."
    url = "https://www.sciencedirect.com/science/article/pii/S2212958826000066"

    def run(title):
        _save_research(ResearchResult(
            topic="mindfulness", summary="s", synthesis="x",
            studies=[ResearchStudy(title=title, authors="Unknown", year=2026,
                                   study_type="peer-review", url=url)],
        ), "mindfulness")

    run(long_title)
    run(short_title)

    rows = client.get("/api/entries").json()
    studies = [r for r in rows if r["study_type"] == "peer-review"]
    assert len(studies) == 1, "the truncated title is the same paper: it must merge"
    assert studies[0]["title"] == long_title, "the complete title wins"


def test_research_dedup_does_merge_an_identical_retitle(client):
    """The half that works: an identical title merges instead of duplicating."""
    from app.models import ResearchResult, ResearchStudy
    from app.routers.research import _save_research

    def run():
        _save_research(ResearchResult(
            topic="memory", summary="s", synthesis="x",
            studies=[ResearchStudy(title="Working Memory (Baddeley & Hitch 1974)",
                                   authors="A", year=1974,
                                   study_type="peer-review", url="https://x/a")],
        ), "memory")

    run()
    run()
    studies = [r for r in client.get("/api/entries").json() if r["study_type"] == "peer-review"]
    assert len(studies) == 1
