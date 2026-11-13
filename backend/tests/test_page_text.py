"""Tests for canonical page texts + highlight offsets (slice 1)."""
import asyncio
import re

from fastapi.testclient import TestClient

from app.main import app


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _study(client, title="Offset study"):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201
    return res.json()


def _make_pdf(path, pages):
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_ensure_page_texts_idempotent_and_preserves_manual(tmp_path):
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        pdf = tmp_path / "idem.pdf"
        _make_pdf(pdf, ["Pagina unu cu text.", "Pagina doi cu alt text."])
        conn = get_db()
        try:
            from app.page_text import ensure_page_texts

            n1 = ensure_page_texts(conn, study["id"], pdf)
            assert n1 == 2
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM page_texts WHERE entry_id = ?",
                [study["id"]],
            ).fetchone()["n"]
            assert count == 2
            n2 = ensure_page_texts(conn, study["id"], pdf)
            assert n2 == 0
            count2 = conn.execute(
                "SELECT COUNT(*) AS n FROM page_texts WHERE entry_id = ?",
                [study["id"]],
            ).fetchone()["n"]
            assert count2 == 2

            conn.execute(
                "UPDATE page_texts SET source = 'manual', status = 'manual', "
                "text = 'EDITAT MANUAL', char_count = 13 "
                "WHERE entry_id = ? AND page_number = 1",
                [study["id"]],
            )
            conn.commit()
            n3 = ensure_page_texts(conn, study["id"], pdf, force=True)
            row = conn.execute(
                "SELECT text, source FROM page_texts WHERE entry_id = ? AND page_number = 1",
                [study["id"]],
            ).fetchone()
            assert dict(row)["text"] == "EDITAT MANUAL"
            assert dict(row)["source"] == "manual"
        finally:
            conn.close()


def test_locate_quote_offsets_roundtrip():
    from app.db import get_db
    from app.page_text import locate_quote

    with TestClient(app) as client:
        study = _study(client)
        text = "Start here.\nMiddle   with   spaces\nand line\nbreak end."
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO page_texts "
                "(entry_id, page_number, text, source, status, char_count) "
                "VALUES (?, 1, ?, 'native', 'native', ?)",
                [study["id"], text, len(text)],
            )
            conn.commit()
            # Quote at the start.
            found = locate_quote(conn, study["id"], 1, "Start here.")
            assert found is not None
            s, e = found
            assert _norm(text[s:e]) == _norm("Start here.")
            # Quote in the middle after a multi-space run.
            found = locate_quote(conn, study["id"], 1, "with spaces")
            assert found is not None
            s, e = found
            assert _norm(text[s:e]) == _norm("with spaces")
            # Quote spanning a line break.
            found = locate_quote(conn, study["id"], 1, "spaces\nand line")
            assert found is not None
            s, e = found
            assert _norm(text[s:e]) == _norm("spaces and line")
            assert "\n" in text[s:e]
            # Missing quote.
            assert locate_quote(conn, study["id"], 1, "absent xyz quote") is None
        finally:
            conn.close()


def test_highlight_post_with_valid_and_invalid_offsets():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        text = "Alpha beta gamma delta epsilon."
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO page_texts "
                "(entry_id, page_number, text, source, status, char_count) "
                "VALUES (?, 2, ?, 'native', 'native', ?)",
                [study["id"], text, len(text)],
            )
            conn.commit()
        finally:
            conn.close()
        # Valid offsets: slice "beta gamma" at 6..17.
        ok = client.post(
            f"/api/studies/{study['id']}/highlights",
            json={"quote": "beta gamma", "page_number": 2,
                  "start_offset": 6, "end_offset": 17},
        )
        assert ok.status_code == 201, ok.text
        body = ok.json()
        assert body["start_offset"] == 6
        assert body["end_offset"] == 17

        # Slice does not match the quote -> 422.
        bad = client.post(
            f"/api/studies/{study['id']}/highlights",
            json={"quote": "epsilon zeta", "page_number": 2,
                  "start_offset": 6, "end_offset": 17},
        )
        assert bad.status_code == 422

        # end_offset beyond page length -> 422.
        over = client.post(
            f"/api/studies/{study['id']}/highlights",
            json={"quote": "beta gamma", "page_number": 2,
                  "start_offset": 0, "end_offset": len(text) + 50},
        )
        assert over.status_code == 422


def test_highlight_post_best_effort_offsets():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        text = "Somnul consolidează memoria declarativă la adulți."
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO page_texts "
                "(entry_id, page_number, text, source, status, char_count) "
                "VALUES (?, 1, ?, 'native', 'native', ?)",
                [study["id"], text, len(text)],
            )
            conn.commit()
        finally:
            conn.close()
        res = client.post(
            f"/api/studies/{study['id']}/highlights",
            json={"quote": "consolidează memoria", "page_number": 1},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["start_offset"] is not None
        assert body["end_offset"] is not None
        assert _norm(text[body["start_offset"]:body["end_offset"]]) == _norm("consolidează memoria")


def test_index_pdf_creates_page_texts(tmp_path):
    from app.db import get_db
    from app.pdf_rag import index_pdf

    with TestClient(app) as client:
        study = _study(client, "Index fixture")
        pdf = tmp_path / "fixture.pdf"
        _make_pdf(pdf, ["First page content here.", "Second page other words."])
        indexed = asyncio.run(index_pdf(study["id"], pdf))
        assert indexed >= 1
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT page_number, text FROM page_texts WHERE entry_id = ? ORDER BY page_number",
                [study["id"]],
            ).fetchall()
            assert [r["page_number"] for r in rows] == [1, 2]
            assert "First page" in rows[0]["text"]
            assert "Second page" in rows[1]["text"]
        finally:
            conn.close()
