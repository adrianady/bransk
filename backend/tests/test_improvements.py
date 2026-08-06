"""Regression tests for the 2026-08 improvement pass.

Covers: ISO timestamp due-query fixes (reminders + spaced repetition),
recurring reminder advancement, SSRF guard on PDF downloads, per-action
error isolation in the chat parser, and request validation.
"""
from fastapi.testclient import TestClient

from app.main import app
from app.routers import chat as chat_mod
from app.routers import research as research_mod
from app.routers.reminders import _next_future_occurrence, advance_overdue_recurring
from app.studies_store import assert_public_download_url, is_valid_pdf_file


def _parse(reply: str) -> list[dict]:
    return chat_mod._parse_actions(reply)


# ── Reminders ──────────────────────────────────────────────────────────────

def test_due_reminders_match_iso_timestamps():
    """Regression: '2020-01-01T09:00' was string-compared against SQLite's
    space-separated datetime('now') and never appeared as due."""
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        client.post(
            f"/api/entries/{entry['id']}/reminders",
            json={"title": "past", "trigger_at": "2020-01-01T09:00", "reminder_type": "once"},
        )
        client.post(
            f"/api/entries/{entry['id']}/reminders",
            json={"title": "future", "trigger_at": "2999-01-01T09:00", "reminder_type": "once"},
        )
        due = client.get("/api/reminders?due=true").json()
        titles = {r["title"] for r in due}
        assert "past" in titles
        assert "future" not in titles


def test_recurring_reminder_type_infers_recurrence():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        created = client.post(
            f"/api/entries/{entry['id']}/reminders",
            json={"title": "daily", "trigger_at": "2026-08-02T09:00", "reminder_type": "daily"},
        ).json()
        assert created["recurrence"] == "daily"
        once = client.post(
            f"/api/entries/{entry['id']}/reminders",
            json={"title": "once", "trigger_at": "2026-08-02T09:00", "reminder_type": "once"},
        ).json()
        assert once["recurrence"] is None


def test_dismissing_recurring_reminder_schedules_next_occurrence(monkeypatch):
    """Weekly reminder dismissed on Aug 6 must schedule Aug 9. The clock is
    pinned so the assertion never drifts as wall-clock time moves."""
    import datetime as _dt

    from app.routers import reminders as rem_mod

    class FakeDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(2026, 8, 6, 10, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    monkeypatch.setattr(rem_mod, "datetime", FakeDateTime)

    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        reminder = client.post(
            f"/api/entries/{entry['id']}/reminders",
            json={"title": "weekly", "trigger_at": "2026-08-02T09:00", "reminder_type": "weekly"},
        ).json()
        client.post(f"/api/reminders/{reminder['id']}/notify")
        refreshed = client.get(f"/api/entries/{entry['id']}/reminders").json()[0]
        assert refreshed["notified"] is False
        assert refreshed["trigger_at"].startswith("2026-08-09")


def test_scheduler_advances_overdue_dismissed_recurring_reminders():
    from app.db import get_db

    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        reminder = client.post(
            f"/api/entries/{entry['id']}/reminders",
            json={"title": "daily", "trigger_at": "2026-08-02T09:00", "reminder_type": "daily"},
        ).json()
        # Simulate a reminder that was dismissed but never advanced (legacy
        # state, or the app was closed between occurrences).
        conn = get_db()
        try:
            conn.execute(
                "UPDATE reminders SET trigger_at = '2020-01-01T09:00', notified = 1 WHERE id = ?",
                [reminder["id"]],
            )
            conn.commit()
        finally:
            conn.close()
        advanced = advance_overdue_recurring()
        assert advanced >= 1
        rows = client.get(f"/api/entries/{entry['id']}/reminders").json()
        assert all(r["notified"] is False for r in rows)
        assert rows[0]["trigger_at"] > "2026-08-02"


def test_next_occurrence_handles_sqlite_and_iso_inputs(monkeypatch):
    import datetime as _dt

    from app.routers import reminders as rem_mod

    class FakeDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            # Fixed "now" so assertions don't drift as wall-clock time moves.
            base = cls(2026, 8, 6, 10, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    monkeypatch.setattr(rem_mod, "datetime", FakeDateTime)

    # daily: 4 days overdue → next is 5 days after the trigger (Aug 7)
    assert _next_future_occurrence("2026-08-02T09:00", "daily") == "2026-08-07T09:00:00"
    # weekly: 4 days overdue → next week (Aug 9)
    assert _next_future_occurrence("2026-08-02 09:00:00", "weekly") == "2026-08-09T09:00:00"
    # monthly: 31st clamps to 30th of next month
    assert _next_future_occurrence("2026-08-31T09:00", "monthly") == "2026-09-30T09:00:00"
    assert _next_future_occurrence("2026-08-02T09:00", "once") is None


def test_monthly_recurrence_skips_no_occurrence_when_day_still_ahead(monkeypatch):
    """Regression: monthly reminders fired one month late. A reminder due the
    20th, dismissed/advanced on the 5th of the next month, must schedule the
    SAME month's 20th — not the month after."""
    import datetime as _dt

    from app.routers import reminders as rem_mod

    class FakeDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(2026, 9, 5, 10, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    monkeypatch.setattr(rem_mod, "datetime", FakeDateTime)

    # Due the 20th, advanced on Sep 5 → next is Sep 20 (old code returned Oct 20)
    assert _next_future_occurrence("2026-08-20T09:00", "monthly") == "2026-09-20T09:00:00"
    # Far overdue → first future occurrence is this month's 15th, not further out
    assert _next_future_occurrence("2026-01-15T09:00", "monthly") == "2026-09-15T09:00:00"
    assert _next_future_occurrence("2026-06-15T09:00", "monthly") == "2026-09-15T09:00:00"
    # Exact same instant is NOT in the future → next month
    assert _next_future_occurrence("2026-09-05T09:00", "monthly") == "2026-10-05T09:00:00"


def test_monthly_recurrence_clamps_to_short_months(monkeypatch):
    """Day 31 clamps to the target month's length and stays in the future."""
    import datetime as _dt

    from app.routers import reminders as rem_mod

    class FakeDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(2026, 2, 15, 10, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    monkeypatch.setattr(rem_mod, "datetime", FakeDateTime)

    assert _next_future_occurrence("2026-01-31T09:00", "monthly") == "2026-02-28T09:00:00"
    # Clamped day persists (no drift): 28th stays the 28th
    assert _next_future_occurrence("2026-02-28T09:00", "monthly") == "2026-03-28T09:00:00"


# ── Spaced repetition ──────────────────────────────────────────────────────

def test_review_queue_matches_iso_next_review():
    """Regression: next_review '...T...Z' never compared <= datetime('now')."""
    from app.db import get_db

    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        client.post(f"/api/spaced-repetition/items/{entry['id']}")
        item = client.get("/api/spaced-repetition/items").json()[0]
        client.post(f"/api/spaced-repetition/items/{item['id']}/review", json={"quality": 5})
        assert client.get("/api/spaced-repetition/queue").json() == []

        conn = get_db()
        try:
            conn.execute(
                "UPDATE spaced_repetition SET next_review = '2020-01-01T00:00:00Z' WHERE id = ?",
                [item["id"]],
            )
            conn.commit()
        finally:
            conn.close()
        queue = client.get("/api/spaced-repetition/queue").json()
        assert any(i["id"] == item["id"] for i in queue)


# ── SSRF guard ─────────────────────────────────────────────────────────────

def test_pdf_download_rejects_private_and_loopback_urls():
    for bad in (
        "http://127.0.0.1/file.pdf",
        "http://localhost:11434/file.pdf",
        "http://192.168.1.10/file.pdf",
        "http://10.0.0.5/file.pdf",
        "http://[::1]/file.pdf",
        "http://169.254.169.254/latest/meta-data/",
        "http://example.com:8080/file.pdf",
        "ftp://example.com/file.pdf",
    ):
        try:
            assert_public_download_url(bad)
            raise AssertionError(f"expected rejection: {bad}")
        except ValueError:
            pass


def test_pdf_download_allows_public_urls():
    assert_public_download_url("http://8.8.8.8/file.pdf")
    assert_public_download_url("https://8.8.4.4/file.pdf")


def test_is_valid_pdf_file_rejects_html_masquerading_as_pdf(tmp_path):
    from app.studies_store import STUDIES_DIR

    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.7\n%%EOF")
    assert is_valid_pdf_file(good) is True

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"<!DOCTYPE html><html><body>Preparing to download...</body></html>")
    assert is_valid_pdf_file(bad) is False

    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert is_valid_pdf_file(empty) is False


def test_download_study_endpoint_indexes_pdf(monkeypatch):
    from pathlib import Path

    async def fake_download(entry_id, url, title):
        return "999-test.pdf"

    async def fake_index(entry_id, path):
        return 12

    monkeypatch.setattr(research_mod, "download_study_pdf", fake_download)
    monkeypatch.setattr(research_mod, "index_pdf", fake_index)
    monkeypatch.setattr(research_mod, "pdf_path_for", lambda entry_id: Path("C:/bransk/chronokg/backend/data/studies/999-test.pdf"))

    with TestClient(app) as client:
        entry = client.post(
            "/api/entries",
            json={"title": "A real study", "content": "x", "study_type": "study", "source": "https://8.8.8.8/paper.pdf"},
        ).json()
        resp = client.post(f"/api/studies/{entry['id']}/download")
        assert resp.status_code == 200
        assert resp.json()["indexed_chunks"] == 12


def test_download_all_indexes_each_pdf(monkeypatch):
    from pathlib import Path

    async def fake_download(entry_id, url, title):
        return f"{entry_id}-paper.pdf"

    async def fake_index(entry_id, path):
        return 5

    monkeypatch.setattr(research_mod, "download_study_pdf", fake_download)
    monkeypatch.setattr(research_mod, "index_pdf", fake_index)
    monkeypatch.setattr(research_mod, "pdf_path_for", lambda entry_id: Path(f"C:/bransk/chronokg/backend/data/studies/{entry_id}-paper.pdf"))

    with TestClient(app) as client:
        a = client.post("/api/entries", json={"title": "Study A", "content": "x", "study_type": "study", "source": "https://8.8.8.8/a.pdf"}).json()
        b = client.post("/api/entries", json={"title": "Study B", "content": "x", "study_type": "study", "source": "https://8.8.4.4/b.pdf"}).json()
        resp = client.post("/api/studies/download-all")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["downloaded"]) == {a["id"], b["id"]}
        assert body["indexed_chunks"] == 10


def test_research_save_dedupes_by_normalized_title():
    from app.models import ResearchResult, ResearchStudy

    with TestClient(app) as client:
        first = ResearchResult(
            topic="Dedup topic",
            summary="s",
            synthesis="syn",
            studies=[ResearchStudy(title="The Same Study (2020)", authors="A", year=2020)],
        )
        research_mod._save_research(first, "Dedup topic")
        assert len(first.studies) == 1 and first.studies[0].entry_id
        kept = first.studies[0].entry_id

        second = ResearchResult(
            topic="Dedup topic",
            summary="s",
            synthesis="syn",
            studies=[ResearchStudy(title="the same study (2020).", authors="A", year=2020)],
        )
        research_mod._save_research(second, "Dedup topic")
        assert second.studies[0].entry_id == kept

        rows = client.get("/api/entries").json()
        matches = [e for e in rows if e["title"].lower().startswith("the same study")]
        assert len(matches) == 1


def test_graph_excludes_edges_to_archived_entries():
    with TestClient(app) as client:
        a = client.post("/api/entries", json={"title": "A", "content": "x"}).json()
        b = client.post("/api/entries", json={"title": "B", "content": "y"}).json()
        client.post("/api/relationships", json={"from_id": a["id"], "to_id": b["id"], "label": "links_to"})
        client.put(f"/api/entries/{b['id']}", json={"archived": True})

        graph = client.get("/api/relationships/graph").json()
        node_ids = {n["id"] for n in graph["nodes"]}
        assert b["id"] not in node_ids
        assert all(e["from"] in node_ids and e["to"] in node_ids for e in graph["edges"])


def test_timeline_ignores_free_text_dates():
    """A description pasted into entry_date must not break timeline stats or
    ordering (lexicographic MAX used to surface it as the 'latest' date)."""
    with TestClient(app) as client:
        client.post("/api/entries", json={"title": "Dated", "content": "x", "entry_date": "2020-05-05"})
        client.post(
            "/api/entries",
            json={"title": "Broken date", "content": "x", "entry_date": "Mind as prediction machine"},
        )
        stats = client.get("/api/timeline/stats").json()
        assert stats["latest"] == "2020-05-05"

        timeline = client.get("/api/timeline").json()
        titles = [e["title"] for e in timeline["entries"]]
        assert "Dated" in titles
        assert "Broken date" not in titles


# ── Chat action isolation ──────────────────────────────────────────────────

def test_one_bad_action_does_not_kill_other_actions():
    """Regression: an unparseable ADD_TODO (invalid priority) used to abort
    parsing of the whole reply and lose every other action."""
    with TestClient(app) as client:
        acts = _parse(
            "[CREATE_ENTRY: Good title | good content | 2026-08-02 | test | 3]\n"
            "[ADD_TODO: 1 | very-important | 2026-08-15]\n"
        )
        types = {a["type"] for a in acts}
        assert "entry_created" in types
        assert "error" in types
        assert any("ADD_TODO" in a.get("detail", "") for a in acts if a["type"] == "error")


def test_add_reminder_action_passes_recurrence():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        acts = _parse(f"[ADD_REMINDER: {entry['id']} | daily note | 2026-08-02T09:00 | daily]")
        assert any(a["type"] == "reminder_added" for a in acts)
        reminders = client.get(f"/api/entries/{entry['id']}/reminders").json()
        assert reminders and reminders[0]["recurrence"] == "daily"


# ── Request validation ─────────────────────────────────────────────────────

def test_entry_importance_out_of_range_returns_422():
    with TestClient(app) as client:
        resp = client.post("/api/entries", json={"title": "E", "content": "c", "importance": 9})
        assert resp.status_code == 422


def test_todo_invalid_priority_returns_422():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        resp = client.post(
            f"/api/entries/{entry['id']}/todo",
            json={"priority": "urgent"},
        )
        assert resp.status_code == 422


# ── Search: archived exclusion + exact tag matching ─────────────────────────

def test_search_excludes_archived_entries():
    """Regression: FTS search surfaced archived entries (both the FTS and the
    fallback branch lacked `archived = 0`)."""
    with TestClient(app) as client:
        live = client.post(
            "/api/entries", json={"title": "Zebraquarium live study", "content": "zebraquarium cells"}
        ).json()
        archived = client.post(
            "/api/entries", json={"title": "Zebraquarium archived note", "content": "zebraquarium old"}
        ).json()
        client.put(f"/api/entries/{archived['id']}", json={"archived": True})

        results = client.get("/api/search", params={"q": "zebraquarium"}).json()
        ids = {r["entry"]["id"] for r in results}
        assert live["id"] in ids
        assert archived["id"] not in ids


def test_validate_url_rejects_private_hosts_without_network():
    """Regression: validate_url used to HEAD/GET any URL (incl. redirects to
    internal hosts) — an SSRF probe. It must refuse private targets up front."""
    import asyncio

    from app.web_search import validate_url

    assert asyncio.run(validate_url("http://127.0.0.1/x")) is False
    assert asyncio.run(validate_url("http://localhost:11434/x")) is False
    assert asyncio.run(validate_url("http://192.168.1.5/x")) is False
    assert asyncio.run(validate_url("ftp://example.com/x")) is False
    assert asyncio.run(validate_url("not-a-url")) is False


def test_tag_filter_matches_exact_token_not_substring():
    """Regression: `tags LIKE '%ann%'` matched 'annual'; the filter must match
    whole comma-separated tags only."""
    with TestClient(app) as client:
        annual = client.post("/api/entries", json={"title": "Annual review", "content": "x", "tags": "annual"}).json()
        ann = client.post("/api/entries", json={"title": "Ann tag", "content": "x", "tags": "ann"}).json()
        multi = client.post("/api/entries", json={"title": "Multi", "content": "x", "tags": "study,annual"}).json()

        by_ann = client.get("/api/entries", params={"tag": "ann"}).json()
        by_ann_ids = {e["id"] for e in by_ann}
        assert ann["id"] in by_ann_ids
        assert annual["id"] not in by_ann_ids
        assert multi["id"] not in by_ann_ids

        by_annual = client.get("/api/entries", params={"tag": "annual"}).json()
        by_annual_ids = {e["id"] for e in by_annual}
        assert annual["id"] in by_annual_ids
        assert multi["id"] in by_annual_ids
        assert ann["id"] not in by_annual_ids
