"""
Tests for the base CRUD and scheduling logic.

The 2026-10-25 audit measured 69% statement coverage and found four routers
with almost no tests at all (todos 18%, thoughts 23%, tags 34%, reminders'
recurrence arithmetic untested). These tests pin down the behaviour of each
one, including the error paths the UI never triggers.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "crud_audit.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def entry(client):
    res = client.post("/api/entries", json={
        "title": "Entry A", "content": "alpha beta gamma", "entry_date": "2026-01-10",
        "tags": "audit,other", "importance": 3,
    })
    assert res.status_code == 201
    return res.json()


# ─────────────────────────── entries ───────────────────────────

def test_entry_roundtrip_and_defaults(client, entry):
    assert entry["importance"] == 3
    assert entry["archived"] is False
    assert entry["url_validated"] is False
    got = client.get(f"/api/entries/{entry['id']}").json()
    assert got["title"] == "Entry A"
    assert got["tags"] == "audit, other"


def test_entry_title_is_required_and_bounded(client):
    assert client.post("/api/entries", json={"content": "x"}).status_code == 422
    assert client.post("/api/entries", json={"title": ""}).status_code == 422
    assert client.post("/api/entries", json={"title": "x" * 501}).status_code == 422


def test_entry_importance_range_is_enforced(client):
    assert client.post("/api/entries", json={"title": "t", "importance": 0}).status_code == 422
    assert client.post("/api/entries", json={"title": "t", "importance": 6}).status_code == 422


def test_empty_update_keeps_the_row_untouched(client, entry):
    res = client.put(f"/api/entries/{entry['id']}", json={})
    assert res.status_code == 200
    assert res.json()["title"] == entry["title"]
    assert res.json()["updated_at"] == entry["updated_at"]


def test_archive_hides_entry_from_listing_but_keeps_it_readable(client, entry):
    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get("/api/entries").json() == []
    assert client.get("/api/entries?archived=true").json()[0]["id"] == entry["id"]
    assert client.get(f"/api/entries/{entry['id']}").status_code == 200


def test_tag_filter_matches_whole_tags_only(client, entry):
    """'%ann%' would also match 'annual'; the filter must not."""
    client.post("/api/entries", json={"title": "Annual review", "tags": "annual"})
    assert [e["id"] for e in client.get("/api/entries?tag=audit").json()] == [entry["id"]]
    assert client.get("/api/entries?tag=ann").json() == []
    assert client.get("/api/entries?tag=annual").json() != []


def test_listing_pagination_and_limits(client, entry):
    for i in range(5):
        client.post("/api/entries", json={"title": f"E{i}", "entry_date": f"2026-02-0{i+1}"})
    assert len(client.get("/api/entries?limit=3").json()) == 3
    assert len(client.get("/api/entries?limit=3&offset=3").json()) >= 2
    assert client.get("/api/entries?limit=501").status_code == 422
    assert client.get("/api/entries?offset=-1").status_code == 422


def test_listing_orders_dated_first_then_newest(client):
    client.post("/api/entries", json={"title": "old", "entry_date": "1990-01-01"})
    client.post("/api/entries", json={"title": "new", "entry_date": "2026-05-05"})
    client.post("/api/entries", json={"title": "undated"})
    titles = [e["title"] for e in client.get("/api/entries").json()]
    assert titles[-1] == "undated", "undated entries must sort last"
    assert titles.index("new") < titles.index("old")


def test_missing_and_malformed_ids(client):
    assert client.get("/api/entries/999999").status_code == 404
    assert client.get("/api/entries/not-a-number").status_code == 422
    assert client.delete("/api/entries/999999").status_code == 404
    assert client.put("/api/entries/999999", json={"title": "x"}).status_code == 404


# ─────────────────────────── tags ───────────────────────────

def test_tag_create_is_idempotent_and_returns_the_same_row(client):
    first = client.post("/api/tags", json={"name": "risk", "color": "#ff0000"})
    second = client.post("/api/tags", json={"name": "risk", "color": "#00ff00"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["color"] == "#ff0000", "INSERT OR IGNORE keeps the first colour"


def test_tag_default_colour_and_validation(client):
    assert client.post("/api/tags", json={"name": "plain"}).json()["color"] == "#6366f1"
    assert client.post("/api/tags", json={"name": ""}).status_code == 422
    assert client.post("/api/tags", json={"name": "x" * 101}).status_code == 422


def test_tags_are_listed_alphabetically_and_deletable(client):
    for name in ("zeta", "alfa"):
        client.post("/api/tags", json={"name": name})
    assert [t["name"] for t in client.get("/api/tags").json()] == ["alfa", "zeta"]
    tag = client.get("/api/tags").json()[0]
    assert client.delete(f"/api/tags/{tag['id']}").status_code == 204
    assert client.delete(f"/api/tags/{tag['id']}").status_code == 404


# ─────────────────────────── thoughts ───────────────────────────

def test_thought_crud_cycle(client):
    created = client.post("/api/thoughts", json={
        "name": "Reziliență", "description": "despre", "tag_ids": "",
    })
    assert created.status_code == 201
    tid = created.json()["id"]
    assert client.get(f"/api/thoughts/{tid}").json()["name"] == "Reziliență"

    updated = client.put(f"/api/thoughts/{tid}", json={
        "name": "Reziliență 2", "description": "d2", "tag_ids": "1,2",
    })
    assert updated.json()["name"] == "Reziliență 2"
    assert updated.json()["tag_ids"] == "1,2"

    assert client.delete(f"/api/thoughts/{tid}").status_code == 204
    assert client.get(f"/api/thoughts/{tid}").status_code == 404
    assert client.delete(f"/api/thoughts/{tid}").status_code == 404
    assert client.put(f"/api/thoughts/{tid}", json={"name": "x"}).status_code == 404


def test_thought_without_tags_has_no_entries(client):
    tid = client.post("/api/thoughts", json={"name": "gol", "tag_ids": ""}).json()["id"]
    assert client.get(f"/api/thoughts/{tid}/entries").json() == []


def test_thought_collects_entries_by_its_tags(client, entry):
    tag = client.post("/api/tags", json={"name": "audit"}).json()
    tid = client.post("/api/thoughts", json={"name": "colectie", "tag_ids": str(tag["id"])}).json()["id"]
    ids = [e["id"] for e in client.get(f"/api/thoughts/{tid}/entries").json()]
    assert ids == [entry["id"]], "entry tagged 'audit,other' must be collected"


def test_thought_with_dangling_tag_id_returns_empty(client):
    tid = client.post("/api/thoughts", json={"name": "stricat", "tag_ids": "999"}).json()["id"]
    assert client.get(f"/api/thoughts/{tid}/entries").json() == []


def test_thought_entries_excludes_archived(client, entry):
    tag = client.post("/api/tags", json={"name": "audit"}).json()
    tid = client.post("/api/thoughts", json={"name": "c", "tag_ids": str(tag["id"])}).json()["id"]
    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get(f"/api/thoughts/{tid}/entries").json() == []


# ─────────────────────────── todos ───────────────────────────

def test_todo_create_patch_delete(client, entry):
    created = client.post(f"/api/entries/{entry['id']}/todo", json={"priority": "critical", "effort": "l"})
    assert created.status_code == 201
    assert created.json()["completed"] is False
    assert created.json()["priority"] == "critical"

    patched = client.patch(f"/api/entries/{entry['id']}/todo", json={"completed": True})
    assert patched.json()["completed"] is True
    assert patched.json()["effort"] == "l", "partial patch must not blank other fields"

    assert client.delete(f"/api/entries/{entry['id']}/todo").status_code == 204
    assert client.delete(f"/api/entries/{entry['id']}/todo").status_code == 404


def test_one_todo_per_entry(client, entry):
    client.post(f"/api/entries/{entry['id']}/todo", json={})
    assert client.post(f"/api/entries/{entry['id']}/todo", json={}).status_code == 409


def test_todo_requires_existing_entry(client):
    assert client.post("/api/entries/999999/todo", json={}).status_code == 404
    assert client.patch("/api/entries/999999/todo", json={}).status_code == 404


def test_todo_enum_validation(client, entry):
    assert client.post(f"/api/entries/{entry['id']}/todo", json={"priority": "urgent"}).status_code == 422
    assert client.post(f"/api/entries/{entry['id']}/todo", json={"effort": "xxl"}).status_code == 422
    client.post(f"/api/entries/{entry['id']}/todo", json={})
    assert client.patch(f"/api/entries/{entry['id']}/todo", json={"priority": "whenever"}).status_code == 422


def test_todo_listing_filters_and_orders(client):
    a = client.post("/api/entries", json={"title": "A"}).json()
    b = client.post("/api/entries", json={"title": "B"}).json()
    client.post(f"/api/entries/{a['id']}/todo", json={"due_date": "2026-03-01"})
    client.post(f"/api/entries/{b['id']}/todo", json={"due_date": "2026-01-01", "priority": "critical"})
    listed = client.get("/api/todos").json()
    assert [t["entry_id"] for t in listed] == [b["id"], a["id"]], "earliest due date first"
    assert [t["entry_id"] for t in client.get("/api/todos?priority=critical").json()] == [b["id"]]
    client.patch(f"/api/entries/{a['id']}/todo", json={"completed": True})
    assert [t["entry_id"] for t in client.get("/api/todos?completed=false").json()] == [b["id"]]


def test_todo_of_archived_entry_is_hidden(client, entry):
    client.post(f"/api/entries/{entry['id']}/todo", json={})
    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get("/api/todos").json() == []


# ─────────────────────────── relationships ───────────────────────────

def test_relationship_create_list_delete(client, entry):
    other = client.post("/api/entries", json={"title": "Entry B"}).json()
    created = client.post("/api/relationships", json={
        "from_id": entry["id"], "to_id": other["id"], "label": "supports",
    })
    assert created.status_code == 201
    rid = created.json()["id"]
    assert len(client.get("/api/relationships").json()) == 1
    assert len(client.get(f"/api/relationships?entry_id={entry['id']}").json()) == 1
    assert client.delete(f"/api/relationships/{rid}").status_code == 204
    assert client.delete(f"/api/relationships/{rid}").status_code == 404


def test_relationship_rejects_self_and_unknown_entries(client, entry):
    assert client.post("/api/relationships", json={
        "from_id": entry["id"], "to_id": entry["id"],
    }).status_code == 400
    assert client.post("/api/relationships", json={
        "from_id": entry["id"], "to_id": 999999,
    }).status_code == 404


def test_relationship_is_unique_per_direction_and_label(client, entry):
    other = client.post("/api/entries", json={"title": "B"}).json()
    body = {"from_id": entry["id"], "to_id": other["id"], "label": "supports"}
    assert client.post("/api/relationships", json=body).status_code == 201
    assert client.post("/api/relationships", json=body).status_code == 409
    reverse = {"from_id": other["id"], "to_id": entry["id"], "label": "supports"}
    assert client.post("/api/relationships", json=reverse).status_code == 201


def test_graph_excludes_archived_nodes_and_their_edges(client, entry):
    other = client.post("/api/entries", json={"title": "B", "importance": 5}).json()
    client.post("/api/relationships", json={"from_id": entry["id"], "to_id": other["id"], "label": "x"})
    graph = client.get("/api/relationships/graph").json()
    assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
    assert graph["nodes"][1]["color"] == "#8b5cf6", "importance 5 = violet"

    client.put(f"/api/entries/{other['id']}", json={"archived": True})
    graph = client.get("/api/relationships/graph").json()
    assert len(graph["nodes"]) == 1
    assert graph["edges"] == [], "an edge to an archived node must disappear"


def test_graph_edge_never_points_at_itself(client, entry):
    other = client.post("/api/entries", json={"title": "B"}).json()
    client.post("/api/relationships", json={"from_id": entry["id"], "to_id": other["id"], "label": "x"})
    graph = client.get("/api/relationships/graph").json()
    assert all(e["from"] != e["to"] for e in graph["edges"])


# ─────────────────────────── timeline ───────────────────────────

def test_timeline_orders_chronologically_and_counts(client):
    for title, date in (("c", "2026-03-01"), ("a", "2026-01-01"), ("b", "2026-02-01")):
        client.post("/api/entries", json={"title": title, "entry_date": date})
    res = client.get("/api/timeline").json()
    assert [e["title"] for e in res["entries"]] == ["a", "b", "c"]
    assert res["total"] == 3
    assert res["from_date"] is None and res["to_date"] is None


def test_timeline_range_filter_and_echo(client):
    for date in ("2020-01-01", "2026-01-01", "2030-01-01"):
        client.post("/api/entries", json={"title": date, "entry_date": date})
    res = client.get("/api/timeline?from_date=2025-01-01&to_date=2027-01-01").json()
    assert [e["title"] for e in res["entries"]] == ["2026-01-01"]
    assert res["from_date"] == "2025-01-01" and res["to_date"] == "2027-01-01"


def test_timeline_drops_free_text_dates_but_keeps_undated(client):
    client.post("/api/entries", json={"title": "prost", "entry_date": "nu e o dată"})
    client.post("/api/entries", json={"title": "fara", "entry_date": None})
    titles = [e["title"] for e in client.get("/api/timeline").json()["entries"]]
    assert "prost" not in titles, "free-text dates break vis-timeline"
    assert "fara" in titles, "undated entries are listed below the chart"


def test_timeline_excludes_archived(client, entry):
    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get("/api/timeline").json()["total"] == 0


def test_timeline_preview_and_relationship_count(client, entry):
    other = client.post("/api/entries", json={"title": "B", "entry_date": "2026-02-02"}).json()
    client.post("/api/relationships", json={"from_id": entry["id"], "to_id": other["id"], "label": "x"})
    entries = {e["id"]: e for e in client.get("/api/timeline").json()["entries"]}
    assert entries[entry["id"]]["relationship_count"] == 1
    assert entries[entry["id"]]["content_preview"] == "alpha beta gamma"


def test_timeline_stats_uses_iso_dates_only(client):
    client.post("/api/entries", json={"title": "a", "entry_date": "2026-01-01"})
    client.post("/api/entries", json={"title": "b", "entry_date": "2026-12-31"})
    client.post("/api/entries", json={"title": "rau", "entry_date": "text liber"})
    client.post("/api/entries", json={"title": "fara"})
    stats = client.get("/api/timeline/stats").json()
    assert stats == {"earliest": "2026-01-01", "latest": "2026-12-31", "total": 2}


def test_timeline_stats_on_empty_db(client):
    assert client.get("/api/timeline/stats").json() == {"earliest": None, "latest": None, "total": 0}


# ─────────────────────────── search ───────────────────────────

def test_search_finds_by_title_and_body(client, entry):
    assert [r["entry"]["id"] for r in client.get("/api/search?q=alpha").json()] == [entry["id"]]
    assert [r["entry"]["id"] for r in client.get("/api/search?q=Entry").json()] == [entry["id"]]


def test_search_ignores_archived(client, entry):
    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get("/api/search?q=alpha").json() == []


def test_search_suggest_ignores_archived(client, entry):
    """Suggest feeds the search box, so an archived entry must not be offered."""
    assert [r["id"] for r in client.get("/api/search/suggest?q=Entry").json()] == [entry["id"]]
    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get("/api/search/suggest?q=Entry").json() == []


@pytest.mark.parametrize("q", ['"AND*', "(", "*", "^x", "a-(b)", "NEAR", "OR", "()", "-"])
def test_search_survives_fts_syntax_garbage(client, entry, q):
    """Raw FTS5 operators must be sanitized, never a 500."""
    res = client.get("/api/search", params={"q": q})
    assert res.status_code == 200


def test_search_requires_a_query(client):
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search?q=").status_code == 422


def test_search_date_and_tag_filters(client, entry):
    assert len(client.get("/api/search?q=alpha&from_date=2026-01-01").json()) == 1
    assert client.get("/api/search?q=alpha&from_date=2027-01-01").json() == []
    assert len(client.get("/api/search?q=alpha&tags=audit").json()) == 1
    assert client.get("/api/search?q=alpha&tags=other").json() != []
    assert client.get("/api/search?q=alpha&tags=nope").json() == []


def test_search_limit_cap(client, entry):
    assert client.get("/api/search?q=alpha&limit=101").status_code == 422


def test_suggest_matches_title_substring(client, entry):
    assert [s["id"] for s in client.get("/api/search/suggest?q=Ent").json()] == [entry["id"]]
    assert client.get("/api/search/suggest?q=zzz").json() == []
    assert client.get("/api/search/suggest").status_code == 422


# ─────────────────────────── SM-2 ───────────────────────────

def test_sm2_first_successful_review_schedules_one_day():
    from app.routers.spaced_repetition import _sm2_calc

    ease, interval, reps = _sm2_calc(5, 2.5, 0, 0)
    assert (interval, reps) == (1, 1)
    assert ease == pytest.approx(2.6)


def test_sm2_second_review_schedules_six_days():
    from app.routers.spaced_repetition import _sm2_calc

    ease, interval, reps = _sm2_calc(4, 2.5, 1, 1)
    assert (interval, reps) == (6, 2)
    assert ease == pytest.approx(2.5)


def test_sm2_third_review_multiplies_by_ease():
    from app.routers.spaced_repetition import _sm2_calc

    ease, interval, reps = _sm2_calc(4, 2.5, 6, 2)
    assert interval == 15 and reps == 3


def test_sm2_failure_resets_repetitions_but_keeps_ease():
    from app.routers.spaced_repetition import _sm2_calc

    ease, interval, reps = _sm2_calc(2, 2.5, 15, 3)
    assert (ease, interval, reps) == (2.5, 1, 0)


def test_sm2_ease_never_below_one_point_three():
    from app.routers.spaced_repetition import _sm2_calc

    ease = 1.3
    for _ in range(10):
        ease, _, _ = _sm2_calc(3, ease, 1, 1)
    assert ease >= 1.3


def test_sm2_quality_boundaries():
    from app.routers.spaced_repetition import _sm2_calc

    assert _sm2_calc(3, 2.5, 0, 0)[2] == 1, "quality 3 is a pass"
    assert _sm2_calc(2, 2.5, 10, 4)[2] == 0, "quality 2 is a fail"


def test_sr_endpoints_flow(client, entry):
    assert client.post(f"/api/spaced-repetition/items/{entry['id']}").status_code == 200
    assert client.post(f"/api/spaced-repetition/items/{entry['id']}").status_code == 409
    item = [i for i in client.get("/api/spaced-repetition/items").json() if i["entry_id"] == entry["id"]][0]

    reviewed = client.post(f"/api/spaced-repetition/items/{item['id']}/review", json={"quality": 5}).json()
    assert reviewed["interval"] == 1 and reviewed["repetitions"] == 1
    assert reviewed["next_review"] is not None
    failed = client.post(f"/api/spaced-repetition/items/{item['id']}/review", json={"quality": 1}).json()
    assert failed["repetitions"] == 0 and failed["interval"] == 1

    assert client.post(f"/api/spaced-repetition/items/{item['id']}/review",
                       json={"quality": 9}).status_code == 422
    assert client.post(f"/api/spaced-repetition/items/{item['id']}/review", json={}).status_code == 422
    assert client.post("/api/spaced-repetition/items/999999/review", json={"quality": 3}).status_code == 404
    assert client.delete(f"/api/spaced-repetition/items/{entry['id']}").status_code == 204
    assert client.delete(f"/api/spaced-repetition/items/{entry['id']}").status_code == 404


def test_sr_item_for_unknown_entry(client):
    assert client.post("/api/spaced-repetition/items/999999").status_code == 404


def test_sr_queue_only_returns_due_items(client, entry):
    """An item reviewed today is scheduled tomorrow, so it leaves the queue."""
    client.post(f"/api/spaced-repetition/items/{entry['id']}")
    item = client.get("/api/spaced-repetition/items").json()[0]
    assert client.get("/api/spaced-repetition/queue").json()[0]["id"] == item["id"]
    client.post(f"/api/spaced-repetition/items/{item['id']}/review", json={"quality": 5})
    assert client.get("/api/spaced-repetition/queue").json() == []


def test_spaced_repetition_ignores_archived_entry(client, entry):
    """A card for an archived entry is still a read of that entry."""
    client.post(f"/api/spaced-repetition/items/{entry['id']}")
    assert [i["entry_id"] for i in client.get("/api/spaced-repetition/items").json()] == [entry["id"]]
    assert [i["entry_id"] for i in client.get("/api/spaced-repetition/queue").json()] == [entry["id"]]

    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get("/api/spaced-repetition/items").json() == []
    assert client.get("/api/spaced-repetition/queue").json() == []


def test_review_is_recorded_in_history(client, entry):
    from app.db import get_db

    client.post(f"/api/spaced-repetition/items/{entry['id']}")
    item = client.get("/api/spaced-repetition/items").json()[0]
    client.post(f"/api/spaced-repetition/items/{item['id']}/review", json={"quality": 4})
    conn = get_db()
    try:
        rows = conn.execute("SELECT quality FROM review_history WHERE item_id = ?", [item["id"]]).fetchall()
    finally:
        conn.close()
    assert [r["quality"] for r in rows] == [4]


def test_theory_evidence_ignores_archived_entry(client, entry):
    """Evidence from an archived entry must vanish from every read of it."""
    tid = client.post("/api/theories", json={"name": "Attachment theory"}).json()["id"]
    created = client.post(f"/api/theories/{tid}/evidence",
                          json={"entry_id": entry["id"], "stance": "pro"})
    assert created.status_code == 201
    assert [e["entry_id"] for e in client.get(f"/api/theories/{tid}/evidence").json()] == [entry["id"]]
    assert [e["entry_id"] for e in client.get("/api/theories/evidence").json()] == [entry["id"]]

    client.put(f"/api/entries/{entry['id']}", json={"archived": True})

    assert client.get(f"/api/theories/{tid}/evidence").json() == []
    assert client.get("/api/theories/evidence").json() == []
    tree = client.get("/api/theories/tree").json()
    assert tree[0]["pro_count"] == 0, "the tree must not count archived evidence"
    assert tree[0]["pro_weight"] == 0


# ─────────────────────────── reminders ───────────────────────────

def test_days_in_month_handles_leap_and_december():
    from app.routers.reminders import _days_in_month

    assert _days_in_month(2026, 1) == 31
    assert _days_in_month(2026, 2) == 28
    assert _days_in_month(2024, 2) == 29
    assert _days_in_month(2026, 12) == 31


def test_next_occurrence_daily_lands_in_the_future_on_the_same_clock_time():
    from app.routers.reminders import _next_future_occurrence

    yesterday = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    trigger = yesterday.strftime("%Y-%m-%dT09:00:00")
    nxt = _next_future_occurrence(trigger, "daily")
    assert nxt is not None
    assert nxt.endswith("T09:00:00"), "the time of day must never drift"
    assert nxt > datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def test_next_occurrence_weekly_lands_in_the_future_and_keeps_the_weekday():
    from app.routers.reminders import _next_future_occurrence

    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    trigger = past.replace(hour=8, minute=0, second=0, microsecond=0)
    nxt = datetime.fromisoformat(_next_future_occurrence(trigger.strftime("%Y-%m-%dT%H:%M:%S"), "weekly"))
    assert nxt > datetime.now(timezone.utc).replace(tzinfo=None)
    assert nxt.weekday() == trigger.weekday(), "same weekday every week"
    assert nxt.hour == 8 and nxt.minute == 0


def test_next_occurrence_always_consumes_the_current_period():
    """Documents an edge the docstring does not match (found 2026-10-25).

    `_next_future_occurrence` promises "the first occurrence in the future", but
    each branch adds at least one whole period. So a daily reminder whose
    trigger is later *today* advances to tomorrow, not to today.

    It is harmless in both real callers (they only ever pass reminders that are
    already past due), but `/reminders/{id}/notify` accepts a not-yet-due
    reminder through the public API, and there the current period is skipped.
    Changing it would be a behaviour change, so the behaviour is pinned here and
    reported instead: see PROJECT_STATE.md.
    """
    from app.routers import reminders as mod

    now = datetime(2026, 3, 25, 8, 0, 0)
    original = mod.datetime

    class FrozenDatetime(original):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)

    mod.datetime = FrozenDatetime
    try:
        later_today = mod._next_future_occurrence("2026-03-25T09:00:00", "daily")
        later_this_month = mod._next_future_occurrence("2026-03-30T09:00:00", "monthly")
    finally:
        mod.datetime = original

    assert later_today == "2026-03-26T09:00:00"
    assert later_this_month == "2026-04-30T09:00:00"


def test_next_occurrence_monthly_keeps_the_current_month_when_day_not_passed():
    """The regression the code comments describe: the 20th, advanced on the 5th."""
    from app.routers import reminders as mod

    now = datetime(2026, 3, 5, 12, 0, 0)
    original = mod.datetime

    class FrozenDatetime(original):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)

    mod.datetime = FrozenDatetime
    try:
        nxt = mod._next_future_occurrence("2026-02-20T09:00:00", "monthly")
    finally:
        mod.datetime = original
    assert nxt == "2026-03-20T09:00:00", "must not skip March because the 20th has not come"


def test_next_occurrence_monthly_clamps_short_months():
    from app.routers import reminders as mod

    now = datetime(2026, 2, 1, 0, 0, 0)
    original = mod.datetime

    class FrozenDatetime(original):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)

    mod.datetime = FrozenDatetime
    try:
        nxt = mod._next_future_occurrence("2026-01-31T09:00:00", "monthly")
    finally:
        mod.datetime = original
    assert nxt == "2026-02-28T09:00:00", "31 January must clamp to 28 February"


def test_next_occurrence_returns_none_for_garbage():
    from app.routers.reminders import _next_future_occurrence

    assert _next_future_occurrence("nu e o dată", "daily") is None
    assert _next_future_occurrence("2026-01-01T09:00:00", "yearly") is None
    assert _next_future_occurrence(None, "daily") is None


def test_reminder_crud_and_recurrence_inference(client, entry):
    created = client.post(f"/api/entries/{entry['id']}/reminders", json={
        "title": "repetă", "trigger_at": "2026-01-01T09:00:00", "reminder_type": "weekly",
    })
    assert created.status_code == 201
    rem = created.json()
    assert rem["recurrence"] == "weekly", "type imples recurrence when it is missing"
    assert [r["id"] for r in client.get(f"/api/entries/{entry['id']}/reminders").json()] == [rem["id"]]
    assert [r["id"] for r in client.get("/api/reminders").json()] == [rem["id"]]
    assert client.delete(f"/api/entries/{entry['id']}/reminders/{rem['id']}").status_code == 204
    assert client.delete(f"/api/entries/{entry['id']}/reminders/{rem['id']}").status_code == 404


def test_one_shot_reminder_is_marked_notified(client, entry):
    from app.db import get_db

    rid = client.post(f"/api/entries/{entry['id']}/reminders",
                      json={"trigger_at": "2026-01-01T09:00:00"}).json()["id"]
    assert client.post(f"/api/reminders/{rid}/notify").status_code == 204
    conn = get_db()
    try:
        row = conn.execute("SELECT notified, trigger_at FROM reminders WHERE id = ?", [rid]).fetchone()
    finally:
        conn.close()
    assert row["notified"] == 1
    assert row["trigger_at"] == "2026-01-01T09:00:00"


def test_recurring_reminder_reschedules_instead_of_deactivating(client, entry):
    from app.db import get_db

    rid = client.post(f"/api/entries/{entry['id']}/reminders", json={
        "trigger_at": "2026-01-01T09:00:00", "reminder_type": "daily",
    }).json()["id"]
    client.post(f"/api/reminders/{rid}/notify")
    conn = get_db()
    try:
        row = conn.execute("SELECT notified, trigger_at FROM reminders WHERE id = ?", [rid]).fetchone()
    finally:
        conn.close()
    assert row["notified"] == 0
    assert row["trigger_at"] > "2026-01-01T09:00:00"


def test_reminder_validation_and_unknown_ids(client, entry):
    assert client.post("/api/entries/999999/reminders",
                       json={"trigger_at": "2026-01-01T09:00:00"}).status_code == 404
    assert client.post(f"/api/entries/{entry['id']}/reminders", json={"trigger_at": ""}).status_code == 422
    assert client.post(f"/api/entries/{entry['id']}/reminders", json={
        "trigger_at": "2026-01-01T09:00:00", "reminder_type": "yearly"}).status_code == 422
    assert client.post("/api/reminders/999999/notify").status_code == 404


def test_due_listing_only_returns_past_unnotified(client, entry):
    old = client.post(f"/api/entries/{entry['id']}/reminders",
                      json={"trigger_at": "2020-01-01T09:00:00"}).json()["id"]
    client.post(f"/api/entries/{entry['id']}/reminders",
                json={"trigger_at": "2099-01-01T09:00:00"})
    assert [r["id"] for r in client.get("/api/reminders?due=true").json()] == [old]


def test_advance_overdue_recurring_moves_only_dismissed_ones(client, entry):
    from app.routers.reminders import advance_overdue_recurring

    rid = client.post(f"/api/entries/{entry['id']}/reminders", json={
        "trigger_at": "2020-01-01T09:00:00", "reminder_type": "daily",
    }).json()["id"]
    assert advance_overdue_recurring() == 0, "an un-notified reminder is left for the user"

    client.post(f"/api/reminders/{rid}/notify")
    # put it back in the past to simulate a missed firing
    from app.db import get_db
    conn = get_db()
    try:
        conn.execute("UPDATE reminders SET trigger_at = '2020-01-01T09:00:00', notified = 1 WHERE id = ?", [rid])
        conn.commit()
    finally:
        conn.close()

    assert advance_overdue_recurring() == 1
    conn = get_db()
    try:
        row = conn.execute("SELECT trigger_at, notified FROM reminders WHERE id = ?", [rid]).fetchone()
    finally:
        conn.close()
    assert row["notified"] == 0
    assert row["trigger_at"] > "2020-01-01T09:00:00"


def test_archived_entry_reminders_are_hidden(client, entry):
    client.post(f"/api/entries/{entry['id']}/reminders", json={"trigger_at": "2020-01-01T09:00:00"})
    client.put(f"/api/entries/{entry['id']}", json={"archived": True})
    assert client.get("/api/reminders?due=true").json() == []
    assert client.get("/api/reminders").json() == []
