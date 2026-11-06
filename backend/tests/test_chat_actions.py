import json
import asyncio

from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.routers import chat as chat_mod
from app.web_search import search_web


def _parse(reply: str) -> list[dict]:
    return chat_mod._parse_actions(reply)


def _pending(acts: list[dict]) -> dict:
    return next(a for a in acts if a["type"] == "pending_confirm")


def _confirm(client: TestClient, pending: dict, approve: bool = True):
    return client.post(
        "/api/chat/confirm",
        json={"confirm_id": pending["confirm_id"], "approve": approve},
    )


def _db_count(sql: str, params: list) -> int:
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


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


def test_download_study_action_parses():
    acts = _parse("[DOWNLOAD_STUDY: 7]")
    assert any(a["type"] == "download_study_request" and a["entry_id"] == 7 for a in acts)


def test_detect_research_intent_extracts_topic():
    from app.routers.chat import _detect_research_intent

    assert _detect_research_intent("research the history of physics") == "the history of physics"
    assert _detect_research_intent("i want you to make research for studies regarding the mind theories") == "the mind theories"
    assert _detect_research_intent("what do you think about my notes?") is None


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


def test_chat_stream_accepts_long_history_messages():
    """Regression: an AI research reply over 10k chars used to 422 (string_too_long)."""
    with TestClient(app) as client:
        long_reply = "research pipeline plan " * 800  # ~26k chars
        assert len(long_reply) > 10000
        resp = client.post(
            "/api/chat/stream",
            json={
                "message": "did you finish?",
                "history": [{"role": "assistant", "content": long_reply}],
            },
        )
        assert resp.status_code == 200
        assert "data: " in resp.text


def test_history_item_extraction_clips_long_content():
    """LLM assembly truncates each history item to the 10k context budget."""
    from app.routers.chat import _extract_history_item

    item = _extract_history_item({"role": "assistant", "content": "z" * 15000})
    assert item["role"] == "assistant"
    assert len(item["content"]) == 10000


def test_web_search_empty_query_short_circuits():
    assert asyncio.run(search_web("")) == []


# ── Edit actions: applied immediately ──────────────────────────────────────

def test_update_entry_applies_whitelisted_fields_immediately():
    with TestClient(app) as client:
        entry = client.post(
            "/api/entries",
            json={"title": "Old", "content": "old body", "tags": "A,B", "importance": 2},
        ).json()
        acts = _parse(
            f"[UPDATE_ENTRY: {entry['id']} | title=New title | content=new body | "
            "tags= Alpha , BETA, alpha | importance=9 | doi= 10.1000/ABC | "
            "url=https://example.org/x | bib_year=2020]"
        )
        assert acts[0]["type"] == "entry_updated"
        assert acts[0]["fields"] == [
            "bib_year", "content", "doi", "importance", "tags", "title", "url",
        ]

        got = client.get(f"/api/entries/{entry['id']}").json()
        assert got["title"] == "New title"
        assert got["content"] == "new body"
        assert got["tags"] == "alpha, beta"
        assert got["importance"] == 5, "importance is clamped to 1-5"
        assert got["doi"] == "10.1000/abc", "DOI is lowercased and trimmed"
        assert got["source"] == "https://example.org/x"
        assert got["bib_year"] == "2020"


def test_update_entry_errors_are_retryable_and_change_nothing():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Keep", "content": "x"}).json()

        unknown = _parse(f"[UPDATE_ENTRY: {entry['id']} | nope=1]")[0]
        assert unknown["type"] == "error"
        assert "unknown key 'nope'" in unknown["detail"]
        assert "[UPDATE_ENTRY:" in unknown["detail"]

        bad_id = _parse("[UPDATE_ENTRY: notanumber | title=x]")[0]
        assert bad_id["type"] == "error" and "numeric id" in bad_id["detail"]

        missing = _parse("[UPDATE_ENTRY: 999999 | title=x]")[0]
        assert missing["type"] == "error" and "not found" in missing["detail"]

        bad_int = _parse(f"[UPDATE_ENTRY: {entry['id']} | importance=high]")[0]
        assert bad_int["type"] == "error" and "importance" in bad_int["detail"]

        bad_date = _parse(f"[UPDATE_ENTRY: {entry['id']} | entry_date=2026-13-40]")[0]
        assert bad_date["type"] == "error" and "entry_date" in bad_date["detail"]

        assert client.get(f"/api/entries/{entry['id']}").json()["title"] == "Keep"


def test_write_section_replaces_and_appends_with_word_counts():
    with TestClient(app) as client:
        structure = client.get("/api/project").json()["structure"]
        section_id = structure[0]["id"]
        title = structure[0]["title"]

        replaced = _parse(
            rf"[WRITE_SECTION: {section_id} | replace | Primul paragraf.\nAl doilea paragraf.]"
        )[0]
        assert replaced["type"] == "section_updated"
        assert replaced["section_id"] == section_id
        assert replaced["section_title"] == title
        assert replaced["words"] == 5
        assert replaced["total_words"] == 5

        content = client.get("/api/project").json()["structure"][0]["content"]
        assert content == "Primul paragraf.\nAl doilea paragraf."

        appended = _parse(f"[WRITE_SECTION: {section_id} | append | Adăugat.]")[0]
        assert appended["type"] == "section_updated"
        assert appended["words"] == 1 and appended["total_words"] == 6
        content = client.get("/api/project").json()["structure"][0]["content"]
        assert content.endswith("\n\nAdăugat.")
        assert "Primul paragraf." in content

        bad_mode = _parse(f"[WRITE_SECTION: {section_id} | overwrite | x]")[0]
        assert bad_mode["type"] == "error" and "append or replace" in bad_mode["detail"]

        bad_section = _parse("[WRITE_SECTION: s99 | append | x]")[0]
        assert bad_section["type"] == "error" and "not found" in bad_section["detail"]


def test_update_project_and_set_plan():
    with TestClient(app) as client:
        acts = _parse(
            "[UPDATE_PROJECT: title=Teza mea | research_q=Ce influențează memoria? | "
            "work_type=doctorat | deadline=2026-06-30]\n"
            "[SET_PLAN: participants | 40 de studenți]"
        )
        assert [a["type"] for a in acts] == ["project_updated", "plan_updated"]

        project = client.get("/api/project").json()
        assert project["title"] == "Teza mea"
        assert project["research_q"] == "Ce influențează memoria?"
        assert project["work_type"] == "doctorat"
        assert project["deadline"] == "2026-06-30"
        assert project["plan"]["participants"] == "40 de studenți"

        bad_type = _parse("[UPDATE_PROJECT: work_type=roman]")[0]
        assert bad_type["type"] == "error" and "work_type" in bad_type["detail"]

        bad_plan_key = _parse("[SET_PLAN: nope | x]")[0]
        assert bad_plan_key["type"] == "error" and "unknown plan key" in bad_plan_key["detail"]


def test_update_study_add_study_and_duplicate_doi_refusal():
    with TestClient(app) as client:
        study = client.post(
            "/api/entries",
            json={"title": "Study A", "study_type": "study", "doi": "10.1000/abc"},
        ).json()
        updated = _parse(
            f"[UPDATE_STUDY: {study['id']} | year=2021 | quality=9 | "
            "authors=Premack & Woodruff | doi= 10.1000/ABC ]"
        )[0]
        assert updated["type"] == "study_updated"
        got = client.get(f"/api/entries/{study['id']}").json()
        assert got["bib_year"] == "2021"
        assert got["quality"] == 5
        assert got["authors"] == "Premack & Woodruff"
        assert got["doi"] == "10.1000/abc"

        plain = client.post("/api/entries", json={"title": "Not a study"}).json()
        not_study = _parse(f"[UPDATE_STUDY: {plain['id']} | year=2020]")[0]
        assert not_study["type"] == "error" and "not a study" in not_study["detail"]

        traversal = _parse(f"[UPDATE_STUDY: {study['id']} | pdf_path=../secret.pdf]")[0]
        assert traversal["type"] == "error" and "file name" in traversal["detail"]

        duplicate = _parse("[ADD_STUDY: Study A again | X | 2021 | 10.1000/ABC | ]")[0]
        assert duplicate["type"] == "error"
        assert f"#{study['id']}" in duplicate["detail"]
        assert "MERGE_ENTRIES" in duplicate["detail"]

        added = _parse(
            "[ADD_STUDY: New study | Author One | 2019 | 10.1000/new | https://example.org/new]"
        )[0]
        assert added["type"] == "study_added" and added["year"] == "2019"
        rows = client.get("/api/entries").json()
        row = next(r for r in rows if r["id"] == added["id"])
        assert row["study_type"] == "study"
        assert row["doi"] == "10.1000/new"
        assert row["entry_date"] == "2019-01-01"

        bad_year = _parse("[ADD_STUDY: Bad | A | 19 | | ]")[0]
        assert bad_year["type"] == "error" and "4-digit" in bad_year["detail"]


def test_add_tags_updates_csv_and_tags_table_without_duplicates():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Tagged", "tags": "alpha"}).json()
        acts = _parse(f"[ADD_TAGS: {entry['id']} | B, b, C]")
        assert acts[0]["type"] == "tags_added"
        assert acts[0]["added"] == ["b", "c"]
        assert client.get(f"/api/entries/{entry['id']}").json()["tags"] == "alpha, b, c"
        names = {tag["name"] for tag in client.get("/api/tags").json()}
        assert {"b", "c"} <= names

        again = _parse(f"[ADD_TAGS: {entry['id']} | b]")[0]
        assert again["added"] == []
        assert client.get(f"/api/entries/{entry['id']}").json()["tags"] == "alpha, b, c"

        missing = _parse("[ADD_TAGS: 999999 | x]")[0]
        assert missing["type"] == "error" and "not found" in missing["detail"]


def test_update_relationship_and_theory():
    with TestClient(app) as client:
        first = client.post("/api/entries", json={"title": "A"}).json()
        second = client.post("/api/entries", json={"title": "B"}).json()
        rel = client.post(
            "/api/relationships",
            json={"from_id": first["id"], "to_id": second["id"], "label": "related_to"},
        ).json()
        updated = _parse(f"[UPDATE_RELATIONSHIP: {rel['id']} | label=supports]")[0]
        assert updated["type"] == "relationship_updated"
        assert client.get("/api/relationships").json()[0]["label"] == "supports"
        bad_key = _parse(f"[UPDATE_RELATIONSHIP: {rel['id']} | to_id=3]")[0]
        assert bad_key["type"] == "error" and "label" in bad_key["detail"]

        theory = client.post("/api/theories", json={"name": "Initial"}).json()
        changed = _parse(
            f"[UPDATE_THEORY: {theory['id']} | name=Renamed | description=Descriere | status=supported]"
        )[0]
        assert changed["type"] == "theory_updated"
        row = next(t for t in client.get("/api/theories").json() if t["id"] == theory["id"])
        assert row["name"] == "Renamed"
        assert row["status"] == "supported"

        bad_status = _parse(f"[UPDATE_THEORY: {theory['id']} | status=maybe]")[0]
        assert bad_status["type"] == "error" and "status must be" in bad_status["detail"]
        self_parent = _parse(f"[UPDATE_THEORY: {theory['id']} | parent_id={theory['id']}]")[0]
        assert self_parent["type"] == "error" and "own parent" in self_parent["detail"]


def test_complete_todo_and_schedule_review_use_sm2():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Review me"}).json()
        _parse(f"[ADD_TODO: {entry['id']} | important | 2026-08-01]")
        todo = client.get("/api/todos").json()[0]
        completed = _parse(f"[COMPLETE_TODO: {todo['id']}]")[0]
        assert completed["type"] == "todo_completed"
        assert client.get("/api/todos").json()[0]["completed"] is True

        reviewed = _parse(f"[SCHEDULE_REVIEW: {entry['id']} | 4]")[0]
        assert reviewed["type"] == "sr_reviewed"
        assert reviewed["quality"] == 4
        assert reviewed["interval"] == 1
        assert reviewed["repetitions"] == 1
        items = client.get("/api/spaced-repetition/items").json()
        assert items[0]["entry_id"] == entry["id"]
        assert items[0]["next_review"] is not None
        assert _db_count("SELECT COUNT(*) FROM review_history", []) == 1

        bad_quality = _parse(f"[SCHEDULE_REVIEW: {entry['id']} | 9]")[0]
        assert bad_quality["type"] == "error" and "quality 0-5" in bad_quality["detail"]


# ── Destructive actions: only via confirmation ─────────────────────────────

def test_delete_entry_requires_confirmation_and_removes_dependencies():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Doomed note", "content": "x"}).json()
        other = client.post("/api/entries", json={"title": "Other"}).json()
        client.post(
            "/api/relationships",
            json={"from_id": entry["id"], "to_id": other["id"], "label": "links"},
        )
        theory = client.post("/api/theories", json={"name": "T"}).json()
        _parse(f"[LINK_STUDY: {theory['id']} | {entry['id']} | pro | note]")
        _parse(f"[ADD_TODO: {entry['id']} | important | 2026-08-01]")
        _parse(f"[ADD_REMINDER: {entry['id']} | Call | 2026-08-01T09:00 | once]")
        _parse(f"[SCHEDULE_REVIEW: {entry['id']} | 4]")
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO documents (entry_id, chunk_index, content) VALUES (?, 0, 'chunk')",
                [entry["id"]],
            )
            conn.commit()
        finally:
            conn.close()

        pending = _pending(_parse(f"[DELETE_ENTRY: {entry['id']}]"))
        assert "Doomed note" in pending["message"]
        assert "legături" in pending["message"]
        assert pending["links"]["relationships"] == 1
        assert client.get(f"/api/entries/{entry['id']}").status_code == 200, "not deleted before confirmation"

        resp = _confirm(client, pending)
        assert resp.status_code == 200
        assert resp.json()["status"] == "executed"
        result = resp.json()["result"]
        assert result["type"] == "entry_deleted"
        assert result["title"] == "Doomed note"
        assert client.get(f"/api/entries/{entry['id']}").status_code == 404

        checks = {
            "relationships": ("SELECT COUNT(*) FROM relationships WHERE from_id = ? OR to_id = ?", [entry["id"], entry["id"]]),
            "theory_evidence": ("SELECT COUNT(*) FROM theory_evidence WHERE entry_id = ?", [entry["id"]]),
            "documents": ("SELECT COUNT(*) FROM documents WHERE entry_id = ?", [entry["id"]]),
            "todos": ("SELECT COUNT(*) FROM todos WHERE entry_id = ?", [entry["id"]]),
            "reminders": ("SELECT COUNT(*) FROM reminders WHERE entry_id = ?", [entry["id"]]),
            "spaced_repetition": ("SELECT COUNT(*) FROM spaced_repetition WHERE entry_id = ?", [entry["id"]]),
        }
        for name, (sql, params) in checks.items():
            assert _db_count(sql, params) == 0, f"{name} was not removed"
        assert _db_count("SELECT COUNT(*) FROM review_history", []) == 0


def test_merge_entries_moves_dependencies_and_drops_the_duplicate():
    with TestClient(app) as client:
        keep = client.post("/api/entries", json={"title": "Premack & Woodruff 1978"}).json()
        drop = client.post("/api/entries", json={"title": "Premack and Woodruff (1978)"}).json()
        third = client.post("/api/entries", json={"title": "Third"}).json()
        for from_id, to_id in ((keep["id"], third["id"]), (drop["id"], third["id"])):
            client.post("/api/relationships", json={"from_id": from_id, "to_id": to_id, "label": "same"})
        client.post("/api/relationships", json={"from_id": keep["id"], "to_id": drop["id"], "label": "part_of"})

        theory_one = client.post("/api/theories", json={"name": "Theory one"}).json()
        theory_two = client.post("/api/theories", json={"name": "Theory two"}).json()
        _parse(f"[LINK_STUDY: {theory_one['id']} | {keep['id']} | pro | keep]")
        _parse(f"[LINK_STUDY: {theory_one['id']} | {drop['id']} | pro | duplicate]")
        _parse(f"[LINK_STUDY: {theory_two['id']} | {drop['id']} | contra | moved]")

        _parse(f"[ADD_TODO: {drop['id']} | important | 2026-08-01]")
        _parse(f"[ADD_REMINDER: {drop['id']} | Call | 2026-08-01T09:00 | once]")
        _parse(f"[SPACED_REPETITION: {keep['id']}]")
        _parse(f"[SCHEDULE_REVIEW: {drop['id']} | 4]")
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO documents (entry_id, chunk_index, content) VALUES (?, 0, 'chunk')",
                [drop["id"]],
            )
            conn.commit()
        finally:
            conn.close()

        pending = _pending(_parse(f"[MERGE_ENTRIES: {keep['id']} | {drop['id']}]"))
        assert keep["title"] in pending["message"]
        assert drop["title"] in pending["message"]
        assert "Mut pe" in pending["message"]
        assert client.get(f"/api/entries/{drop['id']}").status_code == 200

        resp = _confirm(client, pending)
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["type"] == "entries_merged"
        assert client.get(f"/api/entries/{drop['id']}").status_code == 404
        assert client.get(f"/api/entries/{keep['id']}").status_code == 200

        assert result["moved"]["documents"] == 1
        assert result["moved"]["theory_evidence"] == 1
        assert result["moved"]["todos"] == 1
        assert result["moved"]["reminders"] == 1
        assert result["moved"]["review_history"] == 1
        assert result["skipped"]["relationships_duplicate"] == 1
        assert result["skipped"]["relationships_self"] == 1
        assert result["skipped"]["theory_evidence_duplicate"] == 1

        rels = client.get("/api/relationships").json()
        assert len(rels) == 1, "only keep --same--> third survives"
        assert rels[0]["from_id"] == keep["id"] and rels[0]["to_id"] == third["id"]
        assert _db_count("SELECT COUNT(*) FROM documents WHERE entry_id = ?", [keep["id"]]) == 1
        assert _db_count("SELECT COUNT(*) FROM todos WHERE entry_id = ?", [keep["id"]]) == 1
        assert _db_count("SELECT COUNT(*) FROM reminders WHERE entry_id = ?", [keep["id"]]) == 1
        keep_item = client.get("/api/spaced-repetition/items").json()
        assert len(keep_item) == 1 and keep_item[0]["entry_id"] == keep["id"]
        assert _db_count(
            "SELECT COUNT(*) FROM review_history WHERE item_id != ?", [keep_item[0]["id"]]
        ) == 0

        evidence_ids = [e["id"] for e in client.get(f"/api/theories/{theory_one['id']}/evidence").json()]
        assert len(evidence_ids) == 1


def test_delete_theory_requires_unlinking_evidence_first():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Evidence"}).json()
        theory = client.post("/api/theories", json={"name": "With evidence"}).json()
        _parse(f"[LINK_STUDY: {theory['id']} | {entry['id']} | pro | note]")
        evidence = client.get(f"/api/theories/{theory['id']}/evidence").json()[0]

        refused = _parse(f"[DELETE_THEORY: {theory['id']}]")[0]
        assert refused["type"] == "error"
        assert "UNLINK_STUDY" in refused["detail"]
        assert f"#{evidence['id']}" in refused["detail"]
        assert any(t["id"] == theory["id"] for t in client.get("/api/theories").json())

        unlink = _pending(_parse(f"[UNLINK_STUDY: {evidence['id']}]"))
        assert _confirm(client, unlink).json()["result"]["type"] == "study_unlinked"
        assert client.get(f"/api/theories/{theory['id']}/evidence").json() == []

        pending = _pending(_parse(f"[DELETE_THEORY: {theory['id']}]"))
        resp = _confirm(client, pending)
        assert resp.json()["result"]["type"] == "theory_deleted"
        assert not any(t["id"] == theory["id"] for t in client.get("/api/theories").json())


def test_delete_relationship_todo_and_reminder_need_confirmation():
    with TestClient(app) as client:
        first = client.post("/api/entries", json={"title": "A"}).json()
        second = client.post("/api/entries", json={"title": "B"}).json()
        rel = client.post(
            "/api/relationships",
            json={"from_id": first["id"], "to_id": second["id"], "label": "x"},
        ).json()
        _parse(f"[ADD_TODO: {first['id']} | normal | 2026-08-01]")
        _parse(f"[ADD_REMINDER: {first['id']} | Ping | 2026-08-01T09:00 | once]")
        todo = client.get("/api/todos").json()[0]
        reminder = client.get("/api/reminders").json()[0]

        for tag, item_id, list_url in (
            ("DELETE_RELATIONSHIP", rel["id"], "/api/relationships"),
            ("DELETE_TODO", todo["id"], "/api/todos"),
            ("DELETE_REMINDER", reminder["id"], "/api/reminders"),
        ):
            pending = _pending(_parse(f"[{tag}: {item_id}]"))
            assert client.get(list_url).json(), f"{tag}: removed before confirmation"
            resp = _confirm(client, pending)
            assert resp.status_code == 200
            assert resp.json()["result"]["type"] in (
                "relationship_deleted", "todo_deleted", "reminder_deleted",
            )
            assert client.get(list_url).json() == []

        missing = _parse("[DELETE_TODO: 999999]")[0]
        assert missing["type"] == "error" and "not found" in missing["detail"]


# ── Pipeline triggers ──────────────────────────────────────────────────────

def test_index_pdf_request_and_missing_pdf_error():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "No PDF"}).json()
        request = _parse(f"[INDEX_PDF: {entry['id']}]")[0]
        assert request == {"type": "index_pdf_request", "entry_id": entry["id"]}

        result = asyncio.run(chat_mod._execute_index_pdf(entry["id"]))
        assert result["type"] == "error"
        assert "DOWNLOAD_STUDY" in result["detail"]

        bad = _parse("[INDEX_PDF: nope]")[0]
        assert bad["type"] == "error" and "numeric entry id" in bad["detail"]


def test_reanalyze_issues_is_a_read_only_scan():
    with TestClient(app) as client:
        client.post(
            "/api/entries", json={"title": "Duplicate paper", "study_type": "study"}
        ).json()
        # The API now rejects duplicate titles (server-side dedup), so the
        # pre-existing duplicate this scan targets is written directly.
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO entries (title, study_type) VALUES ('Duplicate paper', 'study')"
            )
            conn.commit()
        finally:
            conn.close()
        before = len(client.get("/api/entries").json())

        request = _parse("[REANALYZE_DATA: issues]")[0]
        assert request == {"type": "reanalyze_request", "mode": "issues"}
        result = asyncio.run(chat_mod._execute_reanalyze("issues"))
        assert result["type"] == "reanalysis_done"
        assert result["mode"] == "issues"
        assert len(result["summary"]) <= 10
        assert result["issues"]["duplicate_groups"] == 1
        assert result["issues"]["entries"] == 2
        assert len(client.get("/api/entries").json()) == before, "issues mode must not mutate"

        invalid = _parse("[REANALYZE_DATA: everything]")[0]
        assert invalid["type"] == "error" and "full or issues" in invalid["detail"]


def test_reanalyze_full_reuses_the_maintenance_pipeline():
    with TestClient(app) as client:
        client.post("/api/entries", json={"title": "Same title", "study_type": "study"})
        # Written directly: the API rejects duplicate titles since server-side
        # dedup landed, but the maintenance pipeline must still merge old ones.
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO entries (title, study_type) VALUES ('Same title', 'study')"
            )
            conn.commit()
        finally:
            conn.close()

        result = asyncio.run(chat_mod._execute_reanalyze("full"))
        assert result["type"] == "reanalysis_done"
        assert result["mode"] == "full"
        assert len(result["summary"]) <= 10
        assert result["report"]["duplicates_merged"] >= 1


# ── Safety and prompt contract ─────────────────────────────────────────────

def test_new_tags_inside_code_or_prose_never_execute():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Safe", "content": "x"}).json()
        reply = (
            "Example:\n"
            f"```\n[DELETE_ENTRY: {entry['id']}]\n[UPDATE_ENTRY: {entry['id']} | title=Hacked]\n```\n"
            f"and inline `[DELETE_THEORY: 1]`\n"
            f"You asked: [DELETE_ENTRY: {entry['id']}] — done.\n"
        )
        acts = _parse(reply)
        assert not any(a["type"] == "pending_confirm" for a in acts)
        assert not any(a["type"] == "entry_updated" for a in acts)
        assert client.get(f"/api/entries/{entry['id']}").json()["title"] == "Safe"


def test_new_action_payloads_never_raise():
    garbage = "\n".join([
        "[UPDATE_ENTRY: ]",
        "[WRITE_SECTION: x]",
        "[UPDATE_PROJECT: =x]",
        "[SET_PLAN: ]",
        "[UPDATE_STUDY: x]",
        "[ADD_STUDY: ]",
        "[ADD_TAGS: x]",
        "[UPDATE_RELATIONSHIP: x]",
        "[UPDATE_THEORY: x]",
        "[COMPLETE_TODO: x]",
        "[SCHEDULE_REVIEW: x]",
        "[DELETE_ENTRY: x]",
        "[MERGE_ENTRIES: x]",
        "[DELETE_RELATIONSHIP: x]",
        "[DELETE_THEORY: x]",
        "[UNLINK_STUDY: x]",
        "[DELETE_TODO: x]",
        "[DELETE_REMINDER: x]",
        "[INDEX_PDF: x]",
        "[REANALYZE_DATA: x]",
    ])
    acts = _parse(garbage)
    assert len(acts) >= 15
    assert all(a["type"] == "error" for a in acts)
    assert all(a.get("detail") for a in acts)


def test_system_prompt_documents_the_new_actions_and_the_confirm_rule():
    prompt = chat_mod._build_system_prompt([], "mock", "mock-model")
    for needle in (
        "[UPDATE_ENTRY:", "[WRITE_SECTION:", "[UPDATE_PROJECT:", "[SET_PLAN:",
        "[UPDATE_STUDY:", "[ADD_STUDY:", "[ADD_TAGS:", "[UPDATE_RELATIONSHIP:",
        "[UPDATE_THEORY:", "[COMPLETE_TODO:", "[SCHEDULE_REVIEW:", "[DELETE_ENTRY:",
        "[MERGE_ENTRIES:", "[DELETE_RELATIONSHIP:", "[DELETE_THEORY:",
        "[UNLINK_STUDY:", "[DELETE_TODO:", "[DELETE_REMINDER:", "[INDEX_PDF:",
        "[REANALYZE_DATA:",
    ):
        assert needle in prompt, f"prompt lost {needle}"
    assert "applied immediately" in prompt
    assert "confirmation" in prompt


def test_chat_endpoint_delete_entry_only_prepares_confirmation(monkeypatch):
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Endpoint delete", "content": "x"}).json()

        async def fake_complete(messages, **kwargs):
            return ("fake", "fake-model", f"[DELETE_ENTRY: {entry['id']}]")

        monkeypatch.setattr(chat_mod, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(chat_mod, "llm_complete", fake_complete)

        resp = client.post("/api/chat", json={"message": "delete it", "history": []})
        assert resp.status_code == 200
        pending = _pending(resp.json()["actions"])
        assert "Endpoint delete" in pending["message"]
        assert client.get(f"/api/entries/{entry['id']}").status_code == 200

        confirmed = _confirm(client, pending)
        assert confirmed.json()["result"]["type"] == "entry_deleted"
        assert client.get(f"/api/entries/{entry['id']}").status_code == 404


def test_chat_endpoint_executes_a_reanalyze_request(monkeypatch):
    with TestClient(app) as client:
        client.post("/api/entries", json={"title": "Something", "content": "x"})

        async def fake_complete(messages, **kwargs):
            return ("fake", "fake-model", "[REANALYZE_DATA: issues]")

        monkeypatch.setattr(chat_mod, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(chat_mod, "llm_complete", fake_complete)

        resp = client.post("/api/chat", json={"message": "check the data", "history": []})
        assert resp.status_code == 200
        assert any(
            a["type"] == "reanalysis_done" and a["mode"] == "issues"
            for a in resp.json()["actions"]
        )


def test_tool_results_text_labels_new_actions():
    text = chat_mod._tool_results_text([
        {"type": "entry_updated", "entry_id": 1, "title": "T", "fields": ["title"]},
        {"type": "section_updated", "section_id": "s1", "section_title": "Introducere",
         "words": 42, "total_words": 100},
        {"type": "study_added", "id": 2, "title": "S", "doi": "10.1/x"},
        {"type": "tags_added", "entry_id": 2, "added": ["a", "b"]},
        {"type": "sr_reviewed", "entry_id": 1, "quality": 4, "interval": 6},
        {"type": "pdf_indexed", "entry_id": 1, "chunks": 12},
        {"type": "reanalysis_done", "mode": "issues", "summary": ["Linia unu.", "Linia doi."]},
    ])
    assert "Am scris 42 cuvinte în «Introducere» (total 100 cuvinte)" in text
    assert "STUDIU NOU #2" in text
    assert "ETICHETE adăugate la #2: a, b" in text
    assert "REVIZUIRE #1" in text
    assert "12 chunk-uri" in text
    assert "Linia unu." in text

