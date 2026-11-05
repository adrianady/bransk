"""Tag vocabulary: normalization on write and the repair endpoint."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tagging import is_noise_tag, normalize_tags, sync_tags, tag_names


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_normalize_drops_dates_stopwords_and_duplicates():
    assert normalize_tags("1973-01-01, the, Study, study, memory") == "Study, memory"
    assert normalize_tags("-0350-01-01, philosophy, of, mind") == "philosophy, mind"
    assert normalize_tags(' "x" , y | X ; z ') == "x, y, z"


def test_normalize_keeps_rare_real_tags():
    assert normalize_tags("Carey, Fodor, modularity") == "Carey, Fodor, modularity"


def test_normalize_is_bounded_and_accepts_lists():
    assert normalize_tags(None) == ""
    assert normalize_tags(" , , ") == ""
    assert normalize_tags(["memory", "Memory", "sleep"]) == "memory, sleep"
    long = normalize_tags([f"tag{i}" for i in range(200)])
    assert len(long) <= 500


def test_is_noise():
    assert is_noise_tag("1973-01-01")
    assert is_noise_tag("the")
    assert is_noise_tag("---")
    assert not is_noise_tag("2023")  # a bare year is not a date
    assert not is_noise_tag("mentalization")


def test_sync_tags_fills_the_table_idempotently(client):
    from app.db import get_db

    conn = get_db()
    try:
        assert sync_tags(conn, "study, memory") == 2
        conn.commit()
        assert sync_tags(conn, "study, sleep") == 1
        conn.commit()
        names = {r["name"] for r in conn.execute("SELECT name FROM tags").fetchall()}
        assert {"study", "memory", "sleep"} <= names
        assert tag_names("study, memory") == ["study", "memory"]
    finally:
        conn.close()


def test_entry_create_normalizes_and_syncs(client):
    res = client.post(
        "/api/entries",
        json={
            "title": "A paper",
            "content": "text",
            "tags": "the, 2020-01-01, Study, study, memory",
        },
    )
    assert res.status_code == 201
    assert res.json()["tags"] == "Study, memory"

    listed = client.get("/api/tags").json()
    assert {"Study", "memory"} <= {t["name"] for t in listed}
    assert "the" not in {t["name"] for t in listed}


def test_entry_update_normalizes_and_syncs(client):
    created = client.post("/api/entries", json={"title": "t", "tags": "old"}).json()
    updated = client.put(
        f"/api/entries/{created['id']}",
        json={"tags": "of, 1999-01-01, New, new"},
    )
    assert updated.status_code == 200
    assert updated.json()["tags"] == "New"
    assert "New" in {t["name"] for t in client.get("/api/tags").json()}


def test_tag_repair_endpoint_is_dry_by_default(client):
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO entries (title, tags) VALUES (?, ?)",
            ["dirty", "the, 1973-01-01, Memory, memory, of, mind"],
        )
        conn.commit()
    finally:
        conn.close()

    preview = client.post("/api/maintenance/tags").json()
    assert preview["dry_run"] is True
    assert preview["entries_changed"] == 1
    assert preview["tags_inserted"] == 0

    conn = get_db()
    try:
        row = conn.execute("SELECT tags FROM entries WHERE title = 'dirty'").fetchone()
        assert row["tags"].startswith("the, 1973")
    finally:
        conn.close()


def test_tag_repair_endpoint_writes_when_asked(client):
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO entries (title, tags) VALUES (?, ?)",
            ["dirty", "the, 1973-01-01, Memory, memory, of, mind"],
        )
        conn.commit()
    finally:
        conn.close()

    applied = client.post("/api/maintenance/tags?dry_run=false").json()
    assert applied["dry_run"] is False
    assert applied["entries_changed"] == 1
    assert applied["tags_inserted"] >= 2

    conn = get_db()
    try:
        row = conn.execute("SELECT tags FROM entries WHERE title = 'dirty'").fetchone()
        assert row["tags"] == "Memory, mind"
        names = {r["name"] for r in conn.execute("SELECT name FROM tags").fetchall()}
        assert {"Memory", "mind"} <= names
    finally:
        conn.close()
